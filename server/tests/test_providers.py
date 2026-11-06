"""Moonshot protocol and billing checks; all provider responses are local fakes."""

import importlib
import json
from dataclasses import replace
from types import SimpleNamespace

import httpx
import litellm
import openai
import pytest
from brickwright.driver.messages import assistant_message
from brickwright.driver.tracking import RunTracker

from server import config
from server.config import Settings
from server.providers import (
    MoonshotAccounting,
    MoonshotPricing,
    ProviderBudgetExceeded,
    ProviderRequestTimeout,
    ProviderRateLimited,
    moonshot_completion,
    normalize_moonshot_api_base,
    worker_completion_provider,
)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "unit-test-only")
    return Settings(records_dir=tmp_path, model="moonshot/kimi-k3.1-pro", moonshot_usd_per_cny=.16)


def response(**usage):
    return litellm.ModelResponse(
        model="kimi-k3.1-pro",
        choices=[{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "apple"}}],
        usage={"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200, **usage},
    )


@pytest.mark.parametrize("base", [
    "https://api.moonshot.cn", "https://api.moonshot.cn/", "https://api.moonshot.cn/v1/",
    "https://api.moonshot.cn/v1/chat/completions",
])
def test_normalizes_china_endpoint(base):
    assert normalize_moonshot_api_base(base) == "https://api.moonshot.cn/v1"


@pytest.mark.parametrize("base", [
    "api.moonshot.cn", "http://api.moonshot.cn", "https://user:secret@api.moonshot.cn",
    "https://api.moonshot.cn?api_key=secret", "https://api.moonshot.cn/#secret",
])
def test_rejects_unsafe_endpoint_without_echoing_secret(base):
    with pytest.raises(ValueError) as error:
        normalize_moonshot_api_base(base)
    assert "secret" not in str(error.value)


def test_explicit_fx_is_required_and_other_region_prices_are_not_assumed(settings, monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "unit-test-only")
    assert settings.provider == "moonshot"
    assert replace(settings, model="moonshotai/kimi-k3.1-pro").provider == "moonshot"
    missing = replace(settings, moonshot_usd_per_cny=None)
    assert "MOONSHOT_USD_PER_CNY" in missing.capabilities()["reason"]
    assert MoonshotPricing.from_settings(replace(settings, moonshot_api_base="https://api.moonshot.ai/v1")) is None
    assert MoonshotPricing.from_settings(replace(settings, model="moonshot/unknown-model")) is None
    assert "unit-test-only" not in json.dumps(settings.capabilities())


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf", "2"])
def test_invalid_fx_fails_configuration_without_reading_dotenv(monkeypatch, value):
    monkeypatch.setattr(config, "load_dotenv", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("MOONSHOT_USD_PER_CNY", value)
    with pytest.raises(ValueError, match="MOONSHOT_USD_PER_CNY"):
        Settings.from_env()


@pytest.mark.parametrize("effort", ["low", "high", "max"])
def test_reasoning_setting_is_validated_and_applies_to_every_agent(settings, monkeypatch, effort):
    monkeypatch.setattr(config, "load_dotenv", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("MOONSHOT_REASONING_EFFORT", effort)
    assert Settings.from_env().moonshot_reasoning_effort == effort
    settings = replace(settings, moonshot_reasoning_effort=effort)
    calls = []

    def complete(**kwargs):
        calls.append(kwargs)
        return response()

    monkeypatch.setattr(litellm, "completion", complete)
    with worker_completion_provider(settings):
        for agent in ("loop", "viewpoint", "critique"):
            importlib.import_module(f"brickwright.driver.{agent}").litellm_complete()(
                model=settings.model, messages=[], reasoning_effort="high" if effort != "high" else "low",
            )
    assert [call["reasoning_effort"] for call in calls] == [effort] * 3


def test_reasoning_defaults_high_and_rejects_unsupported_levels(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *_args, **_kwargs: None)
    monkeypatch.delenv("MOONSHOT_REASONING_EFFORT", raising=False)
    assert Settings.from_env().moonshot_reasoning_effort == "high"
    monkeypatch.setenv("MOONSHOT_REASONING_EFFORT", "medium")
    with pytest.raises(ValueError, match="MOONSHOT_REASONING_EFFORT"):
        Settings.from_env()


@pytest.mark.parametrize("usage", [
    {"prompt_tokens_details": {"cached_tokens": 400}},
    {"cached_tokens": 400},
    {"prompt_tokens_details": {"cached_tokens": None}, "cached_tokens": 400},
])
def test_cached_tokens_priced_once_and_reasoning_included_in_output(settings, usage):
    result = response(**usage, completion_tokens_details={"reasoning_tokens": 180})
    pricing = MoonshotPricing.from_settings(settings)
    # 600 uncached + 400 cached + 200 total output; reasoning is already in 200.
    cny = (600 * 13 + 400 * 1.3 + 200 * 68) / 1_000_000
    assert pricing.response_cost_cny(result) == pytest.approx(cny)
    assert pricing.response_cost(result) == pytest.approx(cny * .16)


@pytest.mark.parametrize("usage", [
    {"prompt_tokens": 0, "completion_tokens": 0},
    {"prompt_tokens_details": {"cached_tokens": 1001}},
    {"prompt_tokens": -1},
])
def test_unusable_usage_preserves_unknown_cost_stop_guard(settings, usage):
    result = response(**usage)
    result._hidden_params["response_cost"] = 99  # Wrong-region LiteLLM catalog price.
    adapted = moonshot_completion(settings, complete=lambda **_kwargs: result)(messages=[])
    tracker = RunTracker()
    tracker.record(adapted)
    assert tracker.has_unknown_cost()
    assert adapted._hidden_params["response_cost"] is None


def test_missing_usage_or_fx_never_becomes_a_free_response(settings):
    result = litellm.ModelResponse()
    result._hidden_params["response_cost"] = .01
    moonshot_completion(settings, complete=lambda **_kwargs: result)(messages=[])
    assert result._hidden_params["response_cost"] is None
    result = response()
    moonshot_completion(replace(settings, moonshot_usd_per_cny=None), complete=lambda **_kwargs: result)(messages=[])
    assert result._hidden_params["response_cost"] is None


def test_missing_moonshot_key_cannot_fall_back_to_another_provider(settings, monkeypatch):
    monkeypatch.delenv("MOONSHOT_API_KEY")
    monkeypatch.setenv("OPENAI_API_KEY", "different-provider-secret")
    calls = []
    with pytest.raises(ValueError, match="Missing MOONSHOT_API_KEY"):
        moonshot_completion(settings, complete=lambda **kwargs: calls.append(kwargs))(messages=[])
    assert calls == []


def test_bounded_filtered_requests_and_private_accounting(settings, monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "unit-test-only")
    calls = []
    billing = MoonshotAccounting(MoonshotPricing.from_settings(settings))

    def fake(**kwargs):
        calls.append(kwargs)
        return response()

    complete = moonshot_completion(settings, complete=fake, accounting=billing)
    complete(messages=[], model="moonshotai/kimi-k3.1-pro", timeout=999, reasoning_effort="high",
             temperature=.1, top_p=.1, cache_control_injection_points=[{}], response_format=SimpleNamespace)
    request = calls[0]
    assert request["model"] == "moonshot/kimi-k3.1-pro"
    assert request["api_base"] == "https://api.moonshot.cn/v1"
    assert request["api_key"] == "unit-test-only"
    assert request["timeout"] == settings.request_timeout
    assert request["max_completion_tokens"] == 16384
    assert request["reasoning_effort"] == "high"
    assert request["num_retries"] == 0
    assert request["response_format"] == {"type": "json_object"}
    assert not {"temperature", "top_p", "thinking", "cache_control_injection_points"} & request.keys()
    snapshot = billing.snapshot()
    assert snapshot["costCny"] == pytest.approx(.0266)
    assert snapshot["estimatedCostUsd"] == pytest.approx(.0266 * .16)
    assert snapshot["usdIsEstimate"] is True
    assert "unit-test-only" not in json.dumps(snapshot)


@pytest.mark.parametrize("request_timeout", [120, None])
def test_real_litellm_wire_keeps_image_tools_and_reasoning_roundtrip(settings, monkeypatch, request_timeout):
    settings = replace(settings, request_timeout=request_timeout)
    monkeypatch.setenv("MOONSHOT_API_KEY", "unit-test-only")
    captured = []
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,dGVzdA=="}}
    tools = [{"type": "function", "function": {"name": "describe_object", "parameters": {"type": "object", "properties": {}}}}]
    tool_calls = [{"id": "call_1", "type": "function", "function": {"name": "describe_object", "arguments": "{}"}}]

    def transport(request):
        assert str(request.url) == "https://api.moonshot.cn/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer unit-test-only"
        captured.append(json.loads(request.content))
        first = len(captured) == 1
        message = {"role": "assistant", "content": None if first else "An apple", "reasoning_content": "provider reasoning"}
        if first:
            message["tool_calls"] = tool_calls
        return httpx.Response(200, json={
            "id": "local-test", "object": "chat.completion", "created": 1, "model": "kimi-k3.1-pro",
            "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if first else "stop"}],
            "usage": {"prompt_tokens": 260, "completion_tokens": 455, "total_tokens": 715,
                      "cached_tokens": 0, "prompt_tokens_details": {"cached_tokens": 0}},
        })

    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        client = openai.OpenAI(api_key="unit-test-only", base_url="https://api.moonshot.cn/v1", http_client=http)
        complete = moonshot_completion(settings, complete=lambda **kwargs: litellm.completion(client=client, **kwargs))
        messages = [{"role": "user", "content": [{"type": "text", "text": "Describe this image"}, image]}]
        result = complete(messages=messages, tools=tools, tool_choice="auto", reasoning_effort="high")
        messages.append(assistant_message(result.choices[0].message))
        messages.append({"role": "tool", "tool_call_id": "call_1", "name": "describe_object", "content": "done"})
        complete(messages=messages, tools=tools, tool_choice="none", reasoning_effort="high")

    assert captured[0]["messages"][0]["content"][1] == image
    assert captured[0]["tools"] == tools
    output_key = "max_completion_tokens" if request_timeout is None else "max_tokens"
    assert captured[0][output_key] == 16384
    assert captured[0]["reasoning_effort"] == "high"
    assert captured[1]["messages"][1]["reasoning_content"] == "provider reasoning"
    assert captured[1]["messages"][1]["tool_calls"] == tool_calls
    assert result._hidden_params["response_cost"] == pytest.approx((260 * 13 + 455 * 68) / 1_000_000 * .16)


def test_worker_factories_cover_all_agents_and_restore_after_failure(settings, monkeypatch):
    modules = [importlib.import_module(f"brickwright.driver.{name}") for name in ("loop", "critique", "viewpoint")]
    originals = []
    for module in modules:
        monkeypatch.setattr(module, "litellm_complete", lambda: lambda **_kwargs: "other-provider")
        originals.append(module.litellm_complete)
    monkeypatch.setattr(litellm, "completion", lambda **_kwargs: response())
    with pytest.raises(RuntimeError, match="stop"):
        with worker_completion_provider(settings) as billing:
            for module in modules:
                complete = module.litellm_complete()
                assert complete(model="openai/gpt-test") == "other-provider"
                assert complete(model=settings.model, messages=[])._hidden_params["response_cost"] is not None
            assert billing.snapshot()["responses"] == 3
            raise RuntimeError("stop")
    assert [module.litellm_complete for module in modules] == originals


def test_other_providers_do_not_install_hooks(settings):
    module = importlib.import_module("brickwright.driver.loop")
    original = module.litellm_complete
    with worker_completion_provider(replace(settings, model="anthropic/claude-test")) as billing:
        assert billing is None
        assert module.litellm_complete is original


@pytest.mark.parametrize("first_agent", ["loop", "viewpoint", "critique"])
@pytest.mark.parametrize("next_agent", ["loop", "viewpoint", "critique"])
def test_crossing_budget_blocks_next_transport_across_all_agent_seams(settings, monkeypatch, first_agent, next_agent):
    settings = replace(settings, max_cost=.05)
    requests = []

    def transport(request):
        requests.append(request)
        return httpx.Response(200, json={
            "id": "budget-test", "object": "chat.completion", "created": 1, "model": "kimi-k3.1-pro",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 5000, "total_tokens": 5001},
        })

    real_completion = litellm.completion
    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        client = openai.OpenAI(api_key="unit-test-only", base_url="https://api.moonshot.cn/v1", http_client=http)
        monkeypatch.setattr(litellm, "completion", lambda **kwargs: real_completion(client=client, **kwargs))
        with worker_completion_provider(settings) as billing:
            first = importlib.import_module(f"brickwright.driver.{first_agent}").litellm_complete()
            following = importlib.import_module(f"brickwright.driver.{next_agent}").litellm_complete()
            first(model=settings.model, messages=[{"role": "user", "content": "test"}])
            assert billing.snapshot()["estimatedCostUsd"] > settings.max_cost
            with pytest.raises(ProviderBudgetExceeded):
                following(model=settings.model, messages=[{"role": "user", "content": "must not be sent"}])
            assert billing.snapshot()["responses"] == 1
    assert len(requests) == 1


def test_unknown_cost_blocks_next_helper_and_exact_limit_stops_before_request(settings, monkeypatch):
    calls = []

    def unpriced(**kwargs):
        calls.append(kwargs)
        return litellm.ModelResponse()

    monkeypatch.setattr(litellm, "completion", unpriced)
    with worker_completion_provider(settings):
        importlib.import_module("brickwright.driver.viewpoint").litellm_complete()(model=settings.model, messages=[])
        with pytest.raises(ProviderBudgetExceeded, match="unpriced"):
            importlib.import_module("brickwright.driver.critique").litellm_complete()(model=settings.model, messages=[])
    assert len(calls) == 1

    accounting = MoonshotAccounting(MoonshotPricing.from_settings(settings), known_cost_cny=settings.max_cost / .16)
    with pytest.raises(ProviderBudgetExceeded):
        moonshot_completion(settings, complete=unpriced, accounting=accounting)(messages=[])
    assert len(calls) == 1


@pytest.mark.parametrize("failure,code", [
    (ProviderBudgetExceeded("Moonshot generation budget has been reached"), "BUDGET_EXCEEDED"),
    (ProviderRequestTimeout("upstream-secret-must-stay-private"), "PROVIDER_TIMEOUT"),
    (ProviderRateLimited("upstream-secret-must-stay-private"), "PROVIDER_RATE_LIMITED"),
])
def test_worker_publishes_safe_provider_failure_and_keeps_billing(settings, monkeypatch, failure, code):
    from PIL import Image

    from server import worker

    settings.records_dir.joinpath("request.json").write_text(json.dumps({"size": "small", "style": "classic", "prompt": "apple"}))
    Image.new("RGB", (4, 4), "red").save(settings.records_dir / "input.png")

    def exhausted(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(importlib.import_module("brickwright.driver.loop"), "run_agent", exhausted)
    monkeypatch.setattr(worker.Settings, "from_env", lambda: settings)
    monkeypatch.setattr(worker.sys, "argv", ["worker", str(settings.records_dir)])
    assert worker.main() == 1
    outcome = json.loads(settings.records_dir.joinpath("outcome.json").read_text())
    assert outcome["code"] == code
    assert "unit-test-only" not in json.dumps(outcome)
    assert "upstream-secret" not in json.dumps(outcome)
    assert settings.records_dir.joinpath("provider-billing.json").is_file()
    assert not settings.records_dir.joinpath("project.json").exists()


@pytest.mark.parametrize("completed_first", [False, True])
@pytest.mark.parametrize("error_type", [TimeoutError, ConnectionError])
def test_failed_attempt_makes_total_unknown_and_preserves_confirmed_cost(settings, completed_first, error_type):
    billing = MoonshotAccounting(MoonshotPricing.from_settings(settings))
    calls = []

    def provider(**kwargs):
        calls.append(kwargs)
        if completed_first and len(calls) == 1:
            return response()
        raise error_type("upstream-secret-must-stay-private")

    complete = moonshot_completion(settings, complete=provider, accounting=billing)
    if completed_first:
        complete(messages=[])
    with pytest.raises(error_type):
        complete(messages=[])
    snapshot = billing.snapshot()
    assert snapshot["costCny"] is None
    assert snapshot["estimatedCostUsd"] is None
    assert snapshot["knownCostCny"] == pytest.approx(.0266 if completed_first else 0)
    assert snapshot["responses"] == int(completed_first)
    assert snapshot["attempts"] == int(completed_first) + 1
    assert snapshot["failedAttempts"] == 1
    assert snapshot["unknownCostAttempts"] == 1
    assert "upstream-secret" not in json.dumps(snapshot)
    with pytest.raises(ProviderBudgetExceeded, match="unpriced"):
        complete(messages=[])
    assert len(calls) == int(completed_first) + 1
    assert billing.snapshot()["attempts"] == snapshot["attempts"], "Preflight blocks are not provider attempts"


def test_litellm_timeout_is_safe_and_marks_attempt_cost_unknown(settings):
    billing = MoonshotAccounting(MoonshotPricing.from_settings(settings))

    def timeout(**_kwargs):
        raise litellm.Timeout(message="upstream-secret-must-stay-private", model="kimi-k3.1-pro", llm_provider="moonshot")

    with pytest.raises(ProviderRequestTimeout) as error:
        moonshot_completion(settings, complete=timeout, accounting=billing)(messages=[])
    assert str(error.value) == "Moonshot request timed out"
    assert billing.snapshot()["failedAttempts"] == 1
    assert billing.snapshot()["costCny"] is None


def rate_limit_error(*, kind="rate_limit_reached_error", message="upstream-secret-must-stay-private", headers=None):
    return litellm.RateLimitError(
        message=message, model="kimi-k3.1-pro", llm_provider="moonshot",
        response=httpx.Response(429, json={"error": {"type": kind, "message": message}}, headers=headers),
    )


def test_real_429_retries_are_bounded_and_rejected_attempts_do_not_poison_cost(settings):
    billing = MoonshotAccounting(MoonshotPricing.from_settings(settings))
    calls = []
    waits = []

    def transport(request):
        calls.append(request)
        if len(calls) < 3:
            return httpx.Response(429, json={"error": {"type": "rate_limit_reached_error", "message": "private-upstream-error"}})
        return httpx.Response(200, json={
            "id": "retry-test", "object": "chat.completion", "created": 1, "model": "kimi-k3.1-pro",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200},
        })

    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        # The adapter must disable the SDK's retries as well as LiteLLM's ones.
        client = openai.OpenAI(api_key="unit-test-only", base_url="https://api.moonshot.cn/v1", http_client=http, max_retries=2)
        complete = moonshot_completion(settings, complete=lambda **kwargs: litellm.completion(client=client, **kwargs),
                                       accounting=billing, sleep=waits.append)
        complete(messages=[{"role": "user", "content": "test"}])
    assert len(calls) == 3
    assert waits == [30, 60]
    snapshot = billing.snapshot()
    assert snapshot["attempts"] == 3
    assert snapshot["responses"] == 1
    assert snapshot["rateLimitedAttempts"] == 2
    assert snapshot["rateLimitRetries"] == 2
    assert snapshot["unknownCostAttempts"] == 0
    assert snapshot["costCny"] == pytest.approx(.0266)
    assert snapshot["lastFailure"]["statusCode"] == 429
    assert snapshot["lastFailure"]["category"] == "rate_limit"
    assert "private-upstream-error" not in json.dumps(snapshot)


def test_429_honors_retry_after_and_stops_after_two_retries(settings):
    billing = MoonshotAccounting(MoonshotPricing.from_settings(settings))
    calls = []
    waits = []

    def rejected(**kwargs):
        calls.append(kwargs)
        raise rate_limit_error(headers={"Retry-After": "1.25"})

    with pytest.raises(ProviderRateLimited) as error:
        moonshot_completion(settings, complete=rejected, accounting=billing, sleep=waits.append)(messages=[])
    assert str(error.value) == "Moonshot rate limit prevented the request"
    assert waits == [1.25, 1.25]
    assert len(calls) == 3
    assert billing.snapshot()["costCny"] == 0
    assert billing.snapshot()["unknownCostAttempts"] == 0
    assert billing.snapshot()["lastFailure"]["retryAfterSeconds"] == 1.25


@pytest.mark.parametrize("kind,message,headers", [
    ("exceeded_current_quota_error", "quota unavailable", None),
    ("rate_limit_reached_error", "organization TPD exceeded", None),
    ("engine_overloaded_error", "overloaded", {"Retry-After": "61"}),
])
def test_daily_quota_and_long_retry_after_stop_without_early_retry(settings, kind, message, headers):
    calls = []
    waits = []

    def rejected(**kwargs):
        calls.append(kwargs)
        raise rate_limit_error(kind=kind, message=message, headers=headers)

    with pytest.raises(ProviderRateLimited):
        moonshot_completion(settings, complete=rejected, sleep=waits.append)(messages=[])
    assert len(calls) == 1
    assert waits == []


def test_http_date_retry_after_is_supported(settings):
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    from server.providers import _retry_after_seconds

    reset = datetime.now(timezone.utc) + timedelta(seconds=40)
    seconds = _retry_after_seconds(rate_limit_error(headers={"Retry-After": format_datetime(reset, usegmt=True)}))
    assert 38 <= seconds <= 40


def test_non_429_failure_is_not_retried_and_only_safe_details_are_kept(settings):
    billing = MoonshotAccounting(MoonshotPricing.from_settings(settings))
    calls = []
    waits = []

    def bad_request(**kwargs):
        calls.append(kwargs)
        raise litellm.BadRequestError(message="upstream-secret-must-stay-private", model="kimi-k3.1-pro", llm_provider="moonshot")

    with pytest.raises(litellm.BadRequestError):
        moonshot_completion(settings, complete=bad_request, accounting=billing, sleep=waits.append)(messages=[])
    assert len(calls) == 1
    assert waits == []
    snapshot = billing.snapshot()
    assert snapshot["unknownCostAttempts"] == 1
    assert snapshot["lastFailure"] == {"category": "request_rejected", "exceptionType": "BadRequestError", "statusCode": 400}
    assert "upstream-secret" not in json.dumps(snapshot)
