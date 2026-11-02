"""Central configuration. Missing AI configuration disables generation, not editing."""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib.util import find_spec
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_LDRAW_LIBRARY = Path.home() / ".cache" / "brick-it" / "ldraw"

ROOT = Path(__file__).resolve().parent.parent
PART_LIMITS = {"small": 80, "medium": 250, "large": 600}
PROVIDERS = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
}


def bounded_env(name: str, default: float, low: float, high: float) -> float:
    value = float(os.environ.get(name, str(default)))
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def boolean_env(name: str, default: bool) -> bool:
    value = os.environ.get(name, "true" if default else "false").strip().lower()
    if value not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"{name} must be true or false")
    return value in {"true", "1", "yes"}


@dataclass(frozen=True)
class Settings:
    records_dir: Path
    model: str = ""
    max_cost: float = 1.0
    max_turns: int = 80
    max_validations: int = 30
    timeout: float = 900
    request_timeout: float = 120
    max_active: int = 2
    max_image_bytes: int = 8 * 1024 * 1024
    max_body_bytes: int = 12 * 1024 * 1024
    auto_download_ldraw: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(ROOT / ".env", override=False)
        # A blank sample setting should still resolve to the same cache for the
        # parts browser, renderer, generated builds, and the bootstrap command.
        library = Path(os.environ.get("LDRAW_LIBRARY_PATH", "").strip() or DEFAULT_LDRAW_LIBRARY).expanduser()
        if not library.is_absolute():
            library = ROOT / library
        os.environ["LDRAW_LIBRARY_PATH"] = str(library.resolve())
        records = Path(os.environ.get("BRICK_IT_RECORDS_DIR", ".brick-it-records"))
        if not records.is_absolute():
            records = ROOT / records
        return cls(
            records_dir=records,
            model=os.environ.get("BRICKWRIGHT_GENERATION_MODEL", "").strip(),
            max_cost=bounded_env("BRICK_IT_MAX_COST", 1, .05, 5),
            max_turns=int(bounded_env("BRICK_IT_MAX_TURNS", 80, 5, 100)),
            max_validations=int(bounded_env("BRICK_IT_MAX_VALIDATIONS", 30, 1, 50)),
            timeout=bounded_env("BRICK_IT_JOB_TIMEOUT", 900, 30, 1800),
            request_timeout=bounded_env("BRICK_IT_REQUEST_TIMEOUT", 120, 10, 180),
            auto_download_ldraw=boolean_env("BRICK_IT_AUTO_DOWNLOAD_LDRAW", True),
        )

    @property
    def provider(self) -> str:
        prefix = self.model.split("/", 1)[0]
        if prefix in PROVIDERS:
            return prefix
        if self.model.startswith(("gpt-", "o1", "o3", "o4")):
            return "openai"
        if self.model.startswith("claude-"):
            return "anthropic"
        if self.model.startswith("gemini-"):
            return "gemini"
        return ""

    def capabilities(self) -> dict:
        result = {"available": False, "reason": ""}
        if self.provider:
            result["provider"] = self.provider
        if not self.model:
            result["reason"] = "尚未配置生成模型。请在本地 .env 中设置 BRICKWRIGHT_GENERATION_MODEL 和服务商 API Key。"
        elif not self.provider:
            result["reason"] = "生成模型需要使用 openai/、anthropic/ 或 gemini/ 前缀。"
        elif not any(os.environ.get(key, "").strip() for key in PROVIDERS[self.provider]):
            result["reason"] = "尚未配置所选模型服务商的 API Key。"
        elif find_spec("brickwright") is None or find_spec("bricklayer") is None:
            result["reason"] = "尚未安装 BrickWright。请运行 uv sync 安装后端依赖。"
        else:
            try:
                from brickwright.tools.rendering import render_available

                if render_available():
                    result.update(available=True, reason="照片生成、BrickLayer 校验与渲染反馈已就绪。")
                else:
                    result["reason"] = "尚未配置 LDraw 零件库。设置 LDRAW_LIBRARY_PATH 后可启用照片渲染与视觉反馈。"
            except Exception:
                result["reason"] = "BrickWright 渲染依赖无法加载，请检查安装和 LDRAW_LIBRARY_PATH。"
        result["limits"] = {"parts": PART_LIMITS, "costUsd": self.max_cost, "timeoutSeconds": self.timeout}
        return result
