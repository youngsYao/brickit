"""Local OpenAI-compatible connection settings and read-only model discovery."""

from __future__ import annotations

import asyncio
import os
import re
import tempfile
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
from dotenv import set_key
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import ROOT
from .jobs import JobManager, ServiceError

DEFAULT_BASE_URL = "https://api.openai.com/v1"


def normalize_base_url(value: str) -> str:
    value = value.strip()
    try:
        parsed = urlsplit(value)
        _ = parsed.port  # Reject invalid ports before constructing a request.
        httpx.URL(value)  # Validate IDNA hostnames and HTTP URL syntax too.
    except (ValueError, httpx.InvalidURL) as exc:
        raise ValueError("请输入有效的 API 地址。") from exc
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or "${" in value
            or any(char.isspace() or ord(char) < 32 for char in value)):
        raise ValueError("请输入完整的 HTTP 或 HTTPS API 地址，不包含密钥、查询参数或片段。")
    path = parsed.path.rstrip("/")
    if not re.search(r"/v\d+(?:[a-z0-9.-]*)?$", path, re.I):
        path += "/v1"
    # Preserve proxy path prefixes, IPv6 brackets and explicitly supplied ports.
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


class ModelsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    baseUrl: str = Field(min_length=1, max_length=2048)
    apiKey: str = Field(default="", max_length=4096)

    @field_validator("baseUrl")
    @classmethod
    def valid_base(cls, value: str) -> str:
        return normalize_base_url(value)

    @field_validator("apiKey")
    @classmethod
    def valid_key(cls, value: str) -> str:
        if "${" in value or (value and any(ord(char) < 33 or ord(char) > 126 for char in value)):
            raise ValueError("API Key 包含无效字符。")
        return value


class SaveSettingsRequest(ModelsRequest):
    model: str = Field(min_length=1, max_length=256)

    @field_validator("model")
    @classmethod
    def valid_model(cls, value: str) -> str:
        if "${" in value or any(char.isspace() or ord(char) < 32 for char in value):
            raise ValueError("请选择有效的模型。")
        return value


class AISettings:
    def __init__(self, manager: JobManager, env_path: Path | None = None):
        self.manager = manager
        self.env_path = env_path or ROOT / ".env"
        # Also used by generation submission, so saving cannot change a key or
        # model while a new job is still decoding its image and reserving a slot.
        self.lock = asyncio.Lock()

    def public(self, *, image: bool = False) -> dict:
        if image:
            return {
                "baseUrl": self.manager.settings.image_api_base or DEFAULT_BASE_URL,
                "model": self.manager.settings.image_model,
                "hasApiKey": bool(os.environ.get("BRICK_IT_IMAGE_API_KEY", "").strip()),
            }
        model = self.manager.settings.model
        return {
            "baseUrl": os.environ.get("OPENAI_BASE_URL", "").strip()
            or os.environ.get("OPENAI_API_BASE", "").strip() or DEFAULT_BASE_URL,
            "model": model.removeprefix("openai/") if self.manager.settings.provider == "openai" else "",
            "hasApiKey": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
        }

    @staticmethod
    def api_key(provided: str, *, image: bool = False) -> str:
        key = provided or os.environ.get("BRICK_IT_IMAGE_API_KEY" if image else "OPENAI_API_KEY", "").strip()
        if not key:
            raise ServiceError("API_KEY_REQUIRED", "请先填写 API Key。", 422)
        return key

    async def models(self, request: ModelsRequest, *, image: bool = False) -> dict:
        key = self.api_key(request.apiKey, image=image)
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
                response = await client.get(request.baseUrl + "/models", headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
        except (httpx.HTTPError, httpx.InvalidURL):
            raise ServiceError("MODEL_CONNECTION_FAILED", "无法连接模型服务，请检查 API 地址和网络后重试。", 502) from None
        if response.status_code in {401, 403}:
            raise ServiceError("MODEL_AUTH_FAILED", "API Key 无效或没有读取模型列表的权限。", 422)
        if not response.is_success:
            raise ServiceError("MODEL_LIST_FAILED", "模型服务暂时无法提供列表，请检查 API 地址后重试。", 502)
        try:
            data = response.json()
            entries = data.get("data") if isinstance(data, dict) else None
            if not isinstance(entries, list):
                raise ValueError
            ids = {entry["id"].strip() for entry in entries
                   if isinstance(entry, dict) and isinstance(entry.get("id"), str)
                   and 0 < len(entry["id"].strip()) <= 256
                   and "${" not in entry["id"]
                   and not any(char.isspace() or ord(char) < 32 for char in entry["id"].strip())}
        except (ValueError, TypeError):
            raise ServiceError("INVALID_MODEL_LIST", "该地址没有返回有效的模型列表，请确认它支持 OpenAI 兼容接口。", 502) from None
        if not ids:
            raise ServiceError("EMPTY_MODEL_LIST", "此 API Key 暂无可选模型，请检查服务商的模型权限。", 422)
        return {"baseUrl": request.baseUrl, "models": [{"id": value} for value in sorted(ids, key=str.casefold)]}

    async def save(self, request: SaveSettingsRequest, *, image: bool = False) -> dict:
        async with self.lock:
            if any(not task.done() for task in self.manager.tasks.values()):
                raise ServiceError("GENERATION_ACTIVE", "请等待当前生成完成，或取消后再修改 AI 模型设置。", 409)
            if image:
                values = {"BRICK_IT_IMAGE_API_BASE": request.baseUrl,
                          "BRICK_IT_IMAGE_API_KEY": self.api_key(request.apiKey, image=True),
                          "BRICK_IT_IMAGE_MODEL": request.model}
                updates = {"image_model": request.model, "image_api_base": request.baseUrl}
            else:
                values = {"OPENAI_BASE_URL": request.baseUrl, "OPENAI_API_BASE": request.baseUrl,
                          "OPENAI_API_KEY": self.api_key(request.apiKey),
                          "BRICKWRIGHT_GENERATION_MODEL": "openai/" + request.model}
                updates = {"model": values["BRICKWRIGHT_GENERATION_MODEL"]}
            try:
                self._persist(values)
            except OSError:
                raise ServiceError("SETTINGS_SAVE_FAILED", "无法保存 AI 模型设置，请检查本地配置文件是否可写。", 503) from None
            os.environ.update(values)
            self.manager.settings = replace(self.manager.settings, **updates)
            return self.public(image=image)

    def _persist(self, values: dict[str, str]) -> None:
        """Replace the local file atomically; retain unrelated controls/comments."""
        original = self.env_path.read_text() if self.env_path.exists() else ""
        descriptor, temporary = tempfile.mkstemp(prefix=".ai-settings-", dir=self.env_path.parent)
        path = Path(temporary)
        try:
            with os.fdopen(descriptor, "w") as stream:
                stream.write(original)
            for name, value in values.items():
                set_key(path, name, value, quote_mode="always")
            path.chmod(0o600)
            path.replace(self.env_path)
        finally:
            path.unlink(missing_ok=True)
