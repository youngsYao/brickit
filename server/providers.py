"""Worker-local provider controls and explicit Moonshot USD costing.

The official driver retains its tool, validation, critique and budget logic. Only
its completion factories are replaced, for the duration of one isolated worker
run. OpenAI keeps the original completion seam with explicit HTTP timeouts.
"""

from __future__ import annotations

import copy
import importlib
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from math import isfinite
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

if TYPE_CHECKING:
    from .config import Settings


class ProviderBudgetExceeded(RuntimeError):
    """A safe stop before another charge after a spent or unpriced response."""


class ProviderRequestTimeout(TimeoutError):
    """A provider request timed out; its possible charge remains unknown."""


class ProviderRateLimited(RuntimeError):
    """The provider rejected requests after a bounded rate-limit retry window."""


class ProviderIncompleteResponse(RuntimeError):
    """A stream cannot be returned to the SDK as a complete model response."""

    def __init__(self, reason: str):
        self.reason = reason if reason in {"missing_usage", "missing_completion", "content_filter", "empty_response"} else "unknown"
        super().__init__(f"OpenAI stream rejected: {self.reason}")


MAX_RATE_LIMIT_RETRIES = 2
MAX_RATE_LIMIT_WAIT_SECONDS = 60
MAX_SERVER_ERROR_RETRIES = 2
RETRYABLE_SERVER_STATUSES = {500, 502, 503, 504}
KNOWN_RATE_ERROR_TYPES = {"engine_overloaded_error", "rate_limit_reached_error", "exceeded_current_quota_error"}


def _retry_after_seconds(exc: Exception) -> float | None:
    for headers in (getattr(exc, "headers", None), getattr(getattr(exc, "response", None), "headers", None)):
        if not headers:
            continue
        raw = next((value for key, value in headers.items() if key.lower() == "retry-after"), None)
        if raw is None:
            continue
        try:
            seconds = float(raw)
        except (TypeError, ValueError):
            try:
                reset = parsedate_to_datetime(str(raw))
                if reset.tzinfo is None:
                    reset = reset.replace(tzinfo=timezone.utc)
                seconds = (reset - datetime.now(timezone.utc)).total_seconds()
            except (TypeError, ValueError, OverflowError):
                continue
        if isfinite(seconds):
            return max(0, seconds)
    return None


def _rate_error_type(exc: Exception) -> str | None:
    bodies = [getattr(exc, "body", None)]
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            bodies.append(response.json())
        except (TypeError, ValueError):
            pass
    for body in bodies:
        if isinstance(body, dict):
            error = body.get("error", body)
            if isinstance(error, dict):
                for key in ("type", "code"):
                    if isinstance(error.get(key), str) and error[key] in KNOWN_RATE_ERROR_TYPES:
                        return error[key]
    # LiteLLM sometimes keeps the vendor's error type only in its message. It is
    # inspected locally; only these fixed type names are ever recorded.
    message = str(exc)
    return next((kind for kind in KNOWN_RATE_ERROR_TYPES if kind in message), None)


def _failure_details(exc: Exception) -> dict[str, Any]:
    import litellm

    status = getattr(exc, "status_code", None)
    if type(status) is not int or not 100 <= status <= 599:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    if type(status) is not int or not 100 <= status <= 599:
        status = None
    if isinstance(exc, ProviderIncompleteResponse):
        category = "incomplete_response"
    elif status == 429:
        category = "rate_limit"
    elif isinstance(exc, (TimeoutError, litellm.Timeout)):
        category = "timeout"
    elif status in {401, 403}:
        category = "authentication"
    elif status is not None and 400 <= status < 500:
        category = "request_rejected"
    elif isinstance(exc, (ConnectionError, litellm.APIConnectionError)):
        category = "connection"
    else:
        category = "provider_error"
    name = type(exc).__name__
    result: dict[str, Any] = {
        "category": category,
        "exceptionType": name if name.isascii() and name.isidentifier() and len(name) <= 64 else "ProviderError",
        "statusCode": status,
    }
    if isinstance(exc, ProviderIncompleteResponse):
        result["reason"] = exc.reason
    if status == 429:
        result.update(errorType=_rate_error_type(exc), retryAfterSeconds=_retry_after_seconds(exc))
    return result


def safe_failure_details(exc: Exception) -> dict[str, Any]:
    """Retain only fixed categories, bounded type names, and HTTP status codes."""
    return _failure_details(exc)


def _retryable_rate_limit(exc: Exception, failure: dict[str, Any]) -> bool:
    if failure.get("errorType") == "exceeded_current_quota_error":
        return False
    # The same rate-limit type can describe a daily allowance. Waiting seconds
    # cannot resolve that condition. Raw provider text never enters metadata.
    message = str(exc).lower()
    return not any(marker in message for marker in ("tpd", "tokens per day", "daily", "每日", "每天", "insufficient_quota"))


def normalize_moonshot_api_base(value: str) -> str:
    """Accept a host or versioned base, without credentials in the URL."""
    parsed = urlsplit(value.strip())
    local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if (
        (parsed.scheme != "https" and not local_http)
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Invalid MOONSHOT_API_BASE")
    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions"):
        path = path.removesuffix("/chat/completions")
    if not path.endswith("/v1"):
        path += "/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


@dataclass(frozen=True)
class MoonshotPricing:
    usd_per_cny: float
    # Official China pricing verified 2026-11-02. The default five-minute cache
    # write is included in the noncached input price; reasoning is output usage.
    input_cny_per_million: float = 13
    output_cny_per_million: float = 68
    cached_input_cny_per_million: float = 1.30

    @classmethod
    def from_settings(cls, settings: Settings) -> MoonshotPricing | None:
        rate = settings.moonshot_usd_per_cny
        base = normalize_moonshot_api_base(settings.moonshot_api_base)
        model = settings.model.split("/", 1)[-1]
        if (
            rate is None or not isfinite(rate) or not 0 < rate <= 1
            or model != "kimi-k3.1-pro"
            or urlsplit(base).hostname != "api.moonshot.cn"
        ):
            return None
        return cls(rate)

    def response_cost(self, response: Any) -> float | None:
        cny = self.response_cost_cny(response)
        return cny * self.usd_per_cny if cny is not None else None

    def response_cost_cny(self, response: Any) -> float | None:
        usage = getattr(response, "usage", None)
        prompt = _token_count(getattr(usage, "prompt_tokens", None))
        output = _token_count(getattr(usage, "completion_tokens", None))
        details = getattr(usage, "prompt_tokens_details", None)
        cached_value = getattr(details, "cached_tokens", None)
        if cached_value is None:
            cached_value = getattr(usage, "cached_tokens", 0)
        cached = _token_count(cached_value)
        # A successful chat request has input tokens. LiteLLM can fill an absent
        # usage object with zeroes; that must never masquerade as a free request.
        if prompt is None or prompt == 0 or output is None or cached is None or cached > prompt:
            return None
        return (
            (prompt - cached) * self.input_cny_per_million
            + cached * self.cached_input_cny_per_million
            + output * self.output_cny_per_million
        ) / 1_000_000


@dataclass
class MoonshotAccounting:
    pricing: MoonshotPricing | None
    known_cost_cny: float = 0
    responses: int = 0
    unknown_responses: int = 0
    failed_attempts: int = 0
    rate_limited_attempts: int = 0
    rate_limit_retries: int = 0
    server_error_retries: int = 0
    last_failure: dict[str, Any] | None = None

    def ensure_budget(self, max_cost_usd: float | None) -> None:
        if self.unknown_responses or (max_cost_usd is not None and self.failed_attempts):
            raise ProviderBudgetExceeded("Cannot continue after an unpriced Moonshot response")
        if max_cost_usd is not None and self.pricing is not None and self.known_cost_cny * self.pricing.usd_per_cny >= max_cost_usd:
            raise ProviderBudgetExceeded("Moonshot generation budget has been reached")

    def record(self, response: Any) -> None:
        self.responses += 1
        cny = self.pricing.response_cost_cny(response) if self.pricing else None
        if cny is None:
            self.unknown_responses += 1
        else:
            self.known_cost_cny += cny

    def record_failed_attempt(self, failure: dict[str, Any]) -> None:
        # A timeout, disconnected stream, or malformed response cannot establish
        # whether the provider billed work. Retain confirmed costs separately.
        self.failed_attempts += 1
        self.last_failure = failure

    def record_rate_limited_attempt(self, failure: dict[str, Any]) -> None:
        # An explicit HTTP 429 rejects the request. It contributes no confirmed
        # token charge and does not make successful-response accounting unknown.
        self.rate_limited_attempts += 1
        self.last_failure = failure

    def snapshot(self) -> dict[str, Any]:
        known = self.pricing is not None and self.unknown_responses == 0 and self.failed_attempts == 0
        return {
            "currency": "CNY",
            "costCny": self.known_cost_cny if known else None,
            "knownCostCny": self.known_cost_cny,
            "estimatedCostUsd": self.known_cost_cny * self.pricing.usd_per_cny if known else None,
            "usdPerCny": self.pricing.usd_per_cny if self.pricing else None,
            "usdIsEstimate": True,
            "responses": self.responses,
            "attempts": self.responses + self.failed_attempts + self.rate_limited_attempts,
            "unknownCostResponses": self.unknown_responses,
            "unknownCostAttempts": self.unknown_responses + self.failed_attempts,
            "failedAttempts": self.failed_attempts,
            "rateLimitedAttempts": self.rate_limited_attempts,
            "rateLimitRetries": self.rate_limit_retries,
            "serverErrorRetries": self.server_error_retries,
            "usageCostScope": "successful_responses",
            "lastFailure": self.last_failure,
            "inputCnyPerMillion": 13,
            "outputCnyPerMillion": 68,
            "cachedInputCnyPerMillion": 1.30,
            "pricingSource": "https://platform.kimi.com/docs/pricing/chat",
            "pricingVerifiedDate": "2026-11-02",
        }


def _token_count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not isfinite(value) or value < 0 or int(value) != value:
        return None
    return int(value)


def moonshot_completion(
    settings: Settings,
    *,
    complete: Callable[..., Any] | None = None,
    accounting: MoonshotAccounting | None = None,
    sleep: Callable[[float], None] | None = None,
    checkpoint_path: Path | None = None,
    on_wait: Callable[[float], None] | None = None,
) -> Callable[..., Any]:
    """Build a narrow Moonshot request adapter, without changing LiteLLM globals."""
    if complete is None:
        if settings.moonshot_stream:
            from .moonshot_stream import stream_completion

            complete = stream_completion
        else:
            import litellm

            complete = litellm.completion
    api_base = normalize_moonshot_api_base(settings.moonshot_api_base)
    pricing = MoonshotPricing.from_settings(settings)
    accounting = accounting if accounting is not None else MoonshotAccounting(pricing)
    sleep = sleep if sleep is not None else time.sleep

    def call(**kwargs: Any) -> Any:
        # The SDK's builder has its own post-response guard. This shared check
        # also covers every request inside the multi-call visual critique loop.
        accounting.ensure_budget(settings.max_cost)
        model = str(kwargs.get("model") or settings.model)
        name = model.split("/", 1)[1] if "/" in model else model
        if name != settings.model.split("/", 1)[-1]:
            raise ValueError("Moonshot helper model requires its own verified pricing")
        api_key = os.environ.get("MOONSHOT_API_KEY", "").strip()
        if not api_key:
            # LiteLLM's OpenAI-compatible route can otherwise fall back to an
            # unrelated OPENAI_API_KEY. Never send another provider's credential.
            raise ValueError("Missing MOONSHOT_API_KEY")
        # Passing None inherits LiteLLM's default. Its Moonshot timeout resolver
        # also changes Timeout(None) into 600 seconds. For an explicitly unlimited
        # request, use its OpenAI-compatible route to the same verified CN URL;
        # this route preserves the HTTPX object through every transport phase.
        if settings.request_timeout is None:
            import httpx

            request_timeout = httpx.Timeout(None)
        else:
            request_timeout = min(float(kwargs.get("timeout") or settings.request_timeout), settings.request_timeout)
        routed: dict[str, Any] = {
            "model": f"openai/{name}" if settings.request_timeout is None else f"moonshot/{name}",
            "messages": copy.deepcopy(kwargs.get("messages", [])),
            "api_key": api_key,
            "api_base": api_base,
            "max_completion_tokens": settings.moonshot_max_output_tokens,
            "reasoning_effort": settings.moonshot_reasoning_effort,
            "timeout": request_timeout,
            "num_retries": 0,
            "max_retries": 0,
            # The driver exposes ordinary function tools, never proxy MCP tools.
            # Avoid importing LiteLLM's optional MCP/proxy extras for this path.
            "_skip_mcp_handler": True,
        }
        if settings.request_timeout is None:
            routed["allowed_openai_params"] = ["reasoning_effort"]
            # The official transcript omits empty reasoning. The native Moonshot
            # transformer fills that API-required field; do the same on this
            # compatibility route while preserving all actual reasoning verbatim.
            for message in routed["messages"]:
                if message.get("role") == "assistant" and message.get("tool_calls") and not message.get("reasoning_content"):
                    message["reasoning_content"] = " "
        if settings.moonshot_stream and checkpoint_path is not None:
            routed["progress_path"] = checkpoint_path.with_name(checkpoint_path.stem + "-progress.json")
        for name in ("tools", "tool_choice"):
            if kwargs.get(name) is not None:
                routed[name] = copy.deepcopy(kwargs[name])
        # The critic's prompt already describes its exact JSON shape. Moonshot
        # accepts JSON mode without the SDK-specific Pydantic schema extension.
        if kwargs.get("response_format") is not None:
            routed["response_format"] = {"type": "json_object"}
        if checkpoint_path is not None:
            from .storage import atomic_json

            # Keep the exact pre-request conversation for recovery. It contains
            # private image/reasoning context, never credentials or HTTP headers.
            agent = kwargs.get("_checkpoint_agent", "loop")
            target = checkpoint_path if agent == "loop" else checkpoint_path.with_name(
                f"{checkpoint_path.stem}-{agent}{checkpoint_path.suffix}"
            )
            atomic_json(target, {"model": settings.model, "agent": "builder" if agent == "loop" else agent,
                                 "messages": kwargs.get("messages", [])})
            target.chmod(0o600)
        rate_retries = server_retries = 0
        while True:
            accounting.ensure_budget(settings.max_cost)
            try:
                response = complete(**routed)
                break
            except Exception as exc:
                failure = _failure_details(exc)
                if checkpoint_path is not None:
                    # Private diagnostics explain account-side rejections without
                    # leaking raw provider text into public job/error reports.
                    diagnostic_message = str(exc).replace(api_key, "[redacted]")[:2000]
                    diagnostic = checkpoint_path.with_name(checkpoint_path.stem + "-last-error.json")
                    atomic_json(diagnostic, {**failure, "message": diagnostic_message,
                                             "at": datetime.now(timezone.utc).isoformat()})
                    diagnostic.chmod(0o600)
                if failure["statusCode"] == 429:
                    accounting.record_rate_limited_attempt(failure)
                    wait = failure["retryAfterSeconds"]
                    if wait is None:
                        wait = 30 * (2 ** rate_retries)
                    unlimited_wait = (
                        settings.max_cost is None and settings.timeout is None
                        and settings.request_timeout is None
                        and failure["retryAfterSeconds"] is not None
                        and failure.get("errorType") != "exceeded_current_quota_error"
                    )
                    if (
                        rate_retries == MAX_RATE_LIMIT_RETRIES
                        or (wait > MAX_RATE_LIMIT_WAIT_SECONDS and not unlimited_wait)
                        or (not unlimited_wait and not _retryable_rate_limit(exc, failure))
                    ):
                        raise ProviderRateLimited("Moonshot rate limit prevented the request") from exc
                    rate_retries += 1
                    accounting.rate_limit_retries += 1
                    if on_wait is not None:
                        on_wait(wait)
                    if routed.get("progress_path") is not None:
                        from datetime import timedelta

                        progress_path = Path(routed["progress_path"])
                        atomic_json(progress_path, {
                            "stage": "waiting_rate_limit", "waitSeconds": wait,
                            "resumeAt": (datetime.now(timezone.utc) + timedelta(seconds=wait)).isoformat(),
                        })
                        progress_path.chmod(0o600)
                    remaining = wait
                    while remaining > 0:
                        interval = min(60, remaining)
                        sleep(interval)
                        remaining -= interval
                    continue
                accounting.record_failed_attempt(failure)
                if (
                    settings.max_cost is None
                    and failure["statusCode"] in RETRYABLE_SERVER_STATUSES
                    and server_retries < MAX_SERVER_ERROR_RETRIES
                ):
                    # An explicitly unlimited run may recover from transient
                    # server failures. Their possible charges remain unknown.
                    wait = 30 * (2 ** server_retries)
                    server_retries += 1
                    accounting.server_error_retries += 1
                    sleep(wait)
                    continue
                if failure["category"] == "timeout":
                    raise ProviderRequestTimeout("Moonshot request timed out") from exc
                raise
        # Do not inherit a price for a different billing region from LiteLLM's
        # catalog. Unknown pricing/usage stays unknown, preserving SDK stop guards.
        hidden = getattr(response, "_hidden_params", None)
        if not isinstance(hidden, dict):
            hidden = {}
            response._hidden_params = hidden
        hidden["response_cost"] = pricing.response_cost(response) if pricing else None
        accounting.record(response)
        return response

    return call


def openai_stream_completion(**kwargs: Any) -> Any:
    """Collect a complete OpenAI stream without changing SDK response semantics."""
    import litellm
    from brickwright.driver.completion import _merge_choices, _route

    routed = _route(kwargs)
    chunks = list(litellm.completion(**{
        **routed, "stream": True, "stream_options": {"include_usage": True},
    }))
    # Do not let an empty/truncated stream or guessed local token count become
    # a successful paid response. Transport exceptions propagate before here.
    if not any(
        _token_count(getattr(getattr(chunk, "usage", None), "prompt_tokens", None)) not in {None, 0}
        and _token_count(getattr(getattr(chunk, "usage", None), "completion_tokens", None)) is not None
        for chunk in chunks
    ):
        raise ProviderIncompleteResponse("missing_usage")
    if not any(
        # A metered length response belongs to the SDK's native truncation
        # recovery loop. Its retries must not be mistaken for transport failure.
        getattr(choice, "finish_reason", None) in {"stop", "tool_calls", "function_call", "length"}
        for chunk in chunks for choice in getattr(chunk, "choices", [])
    ):
        filtered = any(getattr(choice, "finish_reason", None) == "content_filter" for chunk in chunks for choice in getattr(chunk, "choices", []))
        raise ProviderIncompleteResponse("content_filter" if filtered else "missing_completion")
    response = litellm.stream_chunk_builder(chunks, messages=kwargs.get("messages"))
    if response is None:
        raise ProviderIncompleteResponse("empty_response")
    # stream_chunk_builder does not always preserve the response cost used by
    # BrickWright's accounting. Retain unknown pricing as unknown, never zero.
    try:
        cost = litellm.completion_cost(completion_response=response)
    except Exception:
        cost = None
    response._hidden_params["response_cost"] = cost if isinstance(cost, (float, int)) and isfinite(cost) and cost >= 0 else None
    if str(routed.get("model", "")).startswith("openai/responses/"):
        response = _merge_choices(response)
    for choice in response.choices:
        if choice.finish_reason == "length":
            # SDK truncation recovery runs only when no tool calls are present.
            # Never execute a partial batch from an output-limited response.
            choice.message.tool_calls = None
            choice.message.function_call = None
    return response


@dataclass
class OpenAIAccounting:
    """Standard-model estimates, never a claim about a proxy's actual bill."""

    known_estimated_cost_usd: float = 0
    responses: int = 0
    unknown_responses: int = 0
    failed_attempts: int = 0
    server_error_retries: int = 0
    last_failure: dict[str, Any] | None = None

    def ensure_budget(self, max_cost_usd: float | None) -> None:
        if self.unknown_responses or (max_cost_usd is not None and self.failed_attempts):
            raise ProviderBudgetExceeded("Cannot continue after an unpriced OpenAI response")
        if max_cost_usd is not None and self.known_estimated_cost_usd >= max_cost_usd:
            raise ProviderBudgetExceeded("OpenAI generation estimate budget has been reached")

    def record(self, response: Any) -> None:
        self.responses += 1
        cost = (getattr(response, "_hidden_params", None) or {}).get("response_cost")
        if isinstance(cost, (float, int)) and not isinstance(cost, bool) and isfinite(cost) and cost >= 0:
            self.known_estimated_cost_usd += cost
        else:
            self.unknown_responses += 1

    def snapshot(self) -> dict[str, Any]:
        estimate_complete = self.unknown_responses == 0 and self.failed_attempts == 0
        return {
            "currency": "USD", "usdIsEstimate": True,
            "pricingBasis": "LiteLLM standard model catalog; provider billing unverified",
            "actualCostUsd": None,
            "estimatedCostUsd": self.known_estimated_cost_usd if estimate_complete else None,
            "knownEstimatedCostUsd": self.known_estimated_cost_usd,
            "estimateComplete": estimate_complete,
            "responses": self.responses,
            "attempts": self.responses + self.failed_attempts,
            "failedAttempts": self.failed_attempts,
            "unknownCostResponses": self.unknown_responses,
            "unknownCostAttempts": self.unknown_responses + self.failed_attempts,
            "serverErrorRetries": self.server_error_retries,
            "usageCostScope": "successful_responses",
            "lastFailure": self.last_failure,
        }


def openai_completion(
    settings: Settings, *, complete: Callable[..., Any], accounting: OpenAIAccounting,
    on_wait: Callable[[float], None] | None = None, sleep: Callable[[float], None] | None = None,
) -> Callable[..., Any]:
    """Retry discarded failed requests only for an explicitly unlimited budget."""
    sleep = sleep if sleep is not None else time.sleep

    def call(**kwargs: Any) -> Any:
        # Disable nested client retries: this adapter owns and counts every
        # possible paid attempt, including failures midway through a stream.
        routed = {**kwargs, "num_retries": 0, "max_retries": 0}
        retries = 0
        while True:
            accounting.ensure_budget(settings.max_cost)
            try:
                response = complete(**copy.deepcopy(routed))
            except Exception as exc:
                failure = safe_failure_details(exc)
                accounting.failed_attempts += 1
                accounting.last_failure = failure
                if settings.max_cost is None and failure["statusCode"] in RETRYABLE_SERVER_STATUSES and retries < MAX_SERVER_ERROR_RETRIES:
                    wait = 30 * (2 ** retries)
                    retries += 1
                    accounting.server_error_retries += 1
                    if on_wait is not None:
                        on_wait(wait)
                    sleep(wait)
                    continue
                raise
            accounting.record(response)
            return response

    return call


@contextmanager
def worker_completion_provider(
    settings: Settings, *, checkpoint_path: Path | None = None,
    on_wait: Callable[[float], None] | None = None,
) -> Iterator[MoonshotAccounting | OpenAIAccounting | None]:
    """Install only inside the dedicated worker process, then restore every seam."""
    if settings.provider not in {"moonshot", "openai"}:
        yield None
        return
    accounting = MoonshotAccounting(MoonshotPricing.from_settings(settings)) if settings.provider == "moonshot" else OpenAIAccounting()
    provider_complete = (
        moonshot_completion(settings, accounting=accounting, checkpoint_path=checkpoint_path, on_wait=on_wait)
        if settings.provider == "moonshot" else None
    )
    originals: list[tuple[Any, Callable[..., Any]]] = []
    try:
        for name in ("loop", "critique", "viewpoint"):
            module = importlib.import_module(f"brickwright.driver.{name}")
            original = module.litellm_complete
            originals.append((module, original))

            def factory(original=original, agent=name):
                fallback = original()
                openai_complete = (
                    openai_completion(settings, complete=openai_stream_completion if settings.openai_stream else fallback,
                                      accounting=accounting, on_wait=on_wait)
                    if settings.provider == "openai" else None
                )

                def complete(**kwargs: Any) -> Any:
                    prefix = str(kwargs.get("model", "")).split("/", 1)[0]
                    if provider_complete is not None and prefix in {"moonshot", "moonshotai"}:
                        return provider_complete(_checkpoint_agent=agent, **kwargs)
                    if settings.provider == "openai":
                        import httpx

                        if checkpoint_path is not None and agent == "loop":
                            from .storage import atomic_json

                            # The builder needs full render/image context for
                            # recovery. HTTP keys, headers and other call kwargs
                            # are deliberately excluded from this private file.
                            atomic_json(checkpoint_path, {"model": settings.model, "agent": "builder",
                                                         "messages": kwargs.get("messages", [])})
                            checkpoint_path.chmod(0o600)

                        # None is treated as "use the client default" by both
                        # LiteLLM and OpenAI. A Timeout object survives the GPT-6
                        # Responses bridge and disables every HTTP phase.
                        kwargs["timeout"] = (
                            httpx.Timeout(None) if settings.request_timeout is None
                            else settings.request_timeout
                        )
                        return openai_complete(**kwargs)
                    return fallback(**kwargs)

                return complete

            module.litellm_complete = factory
        yield accounting
    finally:
        for module, original in reversed(originals):
            module.litellm_complete = original
