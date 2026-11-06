"""Central configuration. Missing AI configuration disables generation, not editing."""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib.util import find_spec
from math import isfinite
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_LDRAW_LIBRARY = Path.home() / ".cache" / "brick-it" / "ldraw"

ROOT = Path(__file__).resolve().parent.parent
PART_LIMITS = {"small": 80, "medium": 250, "large": 600}
MAX_CRITIQUE_ROUNDS = 1
PROVIDERS = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "moonshot": ("MOONSHOT_API_KEY",),
}


def bounded_env(name: str, default: float, low: float, high: float) -> float:
    value = float(os.environ.get(name, str(default)))
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def optional_limit_env(name: str, default: float, low: float, high: float) -> float | None:
    """Zero or ``unlimited`` explicitly removes a limit; defaults stay bounded."""
    raw = os.environ.get(name, str(default)).strip().lower()
    if raw == "unlimited":
        return None
    value = float(raw)
    if value == 0:
        return None
    if not isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be 0/unlimited or between {low} and {high}")
    return value


def boolean_env(name: str, default: bool) -> bool:
    value = os.environ.get(name, "true" if default else "false").strip().lower()
    if value not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"{name} must be true or false")
    return value in {"true", "1", "yes"}


def optional_exchange_rate_env(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    value = float(raw)
    if not isfinite(value) or not 0 < value <= 1:
        raise ValueError(f"{name} must be a finite USD-per-CNY exchange rate between 0 and 1")
    return value


def moonshot_reasoning_effort_env() -> str:
    value = os.environ.get("MOONSHOT_REASONING_EFFORT", "high").strip().lower()
    if value not in {"low", "high", "max"}:
        raise ValueError("MOONSHOT_REASONING_EFFORT must be low, high or max")
    return value


@dataclass(frozen=True)
class Settings:
    records_dir: Path
    model: str = ""
    max_cost: float | None = 1.0
    max_turns: int = 80
    max_validations: int = 30
    max_critique_rounds: int = MAX_CRITIQUE_ROUNDS
    max_viewpoint_iterations: int = 3
    timeout: float | None = 900
    request_timeout: float | None = 120
    max_active: int = 2
    max_image_bytes: int = 8 * 1024 * 1024
    max_body_bytes: int = 12 * 1024 * 1024
    auto_download_ldraw: bool = True
    openai_stream: bool = False
    moonshot_api_base: str = "https://api.moonshot.cn/v1"
    moonshot_max_output_tokens: int = 16384
    moonshot_usd_per_cny: float | None = None
    moonshot_reasoning_effort: str = "high"
    moonshot_stream: bool = False

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
            max_cost=optional_limit_env("BRICK_IT_MAX_COST", 1, .05, 5),
            max_turns=int(optional_limit_env("BRICK_IT_MAX_TURNS", 80, 5, 100) or 0),
            max_validations=int(optional_limit_env("BRICK_IT_MAX_VALIDATIONS", 30, 1, 50) or 0),
            max_critique_rounds=int(bounded_env("BRICK_IT_MAX_CRITIQUE_ROUNDS", MAX_CRITIQUE_ROUNDS, 1, 20)),
            max_viewpoint_iterations=int(bounded_env("BRICK_IT_MAX_VIEWPOINT_ITERATIONS", 3, 1, 12)),
            timeout=optional_limit_env("BRICK_IT_JOB_TIMEOUT", 900, 30, 1800),
            request_timeout=optional_limit_env("BRICK_IT_REQUEST_TIMEOUT", 120, 10, 180),
            auto_download_ldraw=boolean_env("BRICK_IT_AUTO_DOWNLOAD_LDRAW", True),
            openai_stream=boolean_env("OPENAI_STREAM", False),
            moonshot_api_base=os.environ.get("MOONSHOT_API_BASE", "").strip() or "https://api.moonshot.cn/v1",
            moonshot_max_output_tokens=int(bounded_env("MOONSHOT_MAX_OUTPUT_TOKENS", 16384, 256, 1048576)),
            moonshot_usd_per_cny=optional_exchange_rate_env("MOONSHOT_USD_PER_CNY"),
            moonshot_reasoning_effort=moonshot_reasoning_effort_env(),
            moonshot_stream=boolean_env("MOONSHOT_STREAM", False),
        )

    @property
    def provider(self) -> str:
        prefix = self.model.split("/", 1)[0]
        if prefix in PROVIDERS:
            return prefix
        if prefix == "moonshotai":
            return "moonshot"
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
            result["reason"] = "尚未配置生成模型。请通过右上角的 AI 模型设置完成配置。"
        elif not self.provider:
            result["reason"] = "生成模型配置无效，请通过右上角的 AI 模型设置重新选择。"
        elif not any(os.environ.get(key, "").strip() for key in PROVIDERS[self.provider]):
            result["reason"] = "尚未配置 API Key，请通过右上角的 AI 模型设置填写。"
        elif self.provider == "moonshot" and self._moonshot_config_reason():
            result["reason"] = self._moonshot_config_reason()
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

    def _moonshot_config_reason(self) -> str:
        from .providers import MoonshotPricing, normalize_moonshot_api_base

        try:
            normalize_moonshot_api_base(self.moonshot_api_base)
        except ValueError:
            return "MOONSHOT_API_BASE 需要使用有效的服务商 API 地址。"
        if self.moonshot_usd_per_cny is None:
            return "请配置 MOONSHOT_USD_PER_CNY，将人民币模型费用换算成美元预算估算。"
        if MoonshotPricing.from_settings(self) is None:
            return "尚未验证此 Moonshot 模型或端点的费用；当前支持中国区 kimi-k3.1-pro。"
        return ""
