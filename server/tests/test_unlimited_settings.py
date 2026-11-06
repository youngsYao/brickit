"""Explicit unlimited settings reach the real HTTP transport, without paid calls."""

import httpx
import litellm
import openai
import pytest

from server import config
from server.config import Settings
from server.providers import (
    MoonshotAccounting,
    MoonshotPricing,
    ProviderBudgetExceeded,
    moonshot_completion,
)


LIMITS = {
    "BRICK_IT_MAX_COST": ("max_cost", 1),
    "BRICK_IT_JOB_TIMEOUT": ("timeout", 900),
    "BRICK_IT_REQUEST_TIMEOUT": ("request_timeout", 120),
}


@pytest.fixture
def isolated_env(monkeypatch):
    # Never load a developer's real credentials or test preferences.
    monkeypatch.setattr(config, "load_dotenv", lambda *_args, **_kwargs: None)
    for key in (*LIMITS, "BRICK_IT_MAX_TURNS", "BRICK_IT_MAX_VALIDATIONS",
                "BRICK_IT_MAX_CRITIQUE_ROUNDS", "BRICK_IT_MAX_VIEWPOINT_ITERATIONS",
                "MOONSHOT_MAX_OUTPUT_TOKENS", "MOONSHOT_REASONING_EFFORT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MOONSHOT_API_KEY", "unit-test-only")
    return monkeypatch


@pytest.mark.parametrize("value", ["0", "0.0", "unlimited", "UNLIMITED"])
def test_explicit_unlimited_limits_have_json_safe_none(isolated_env, value):
    for key in LIMITS:
        isolated_env.setenv(key, value)
    settings = Settings.from_env()
    assert settings.max_cost is None
    assert settings.timeout is None
    assert settings.request_timeout is None
    assert settings.capabilities()["limits"]["costUsd"] is None
    assert settings.capabilities()["limits"]["timeoutSeconds"] is None


def test_original_defaults_and_finite_limits_are_preserved(isolated_env):
    settings = Settings.from_env()
    for attribute, expected in LIMITS.values():
        assert getattr(settings, attribute) == expected
    assert settings.max_turns == 80
    assert settings.max_validations == 30
    assert settings.max_critique_rounds == 1
    assert settings.max_viewpoint_iterations == 3
    for key, value in {"BRICK_IT_MAX_COST": "3", "BRICK_IT_JOB_TIMEOUT": "1800",
                       "BRICK_IT_REQUEST_TIMEOUT": "180"}.items():
        isolated_env.setenv(key, value)
    settings = Settings.from_env()
    assert (settings.max_cost, settings.timeout, settings.request_timeout) == (3, 1800, 180)


@pytest.mark.parametrize("name", LIMITS)
@pytest.mark.parametrize("value", ["-1", "nan", "inf"])
def test_invalid_limit_still_rejected(isolated_env, name, value):
    isolated_env.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        Settings.from_env()


@pytest.mark.parametrize("value", ["0", "unlimited"])
def test_builder_and_validation_caps_can_be_explicitly_removed(isolated_env, value):
    isolated_env.setenv("BRICK_IT_MAX_TURNS", value)
    isolated_env.setenv("BRICK_IT_MAX_VALIDATIONS", value)
    settings = Settings.from_env()
    assert settings.max_turns == settings.max_validations == 0


def test_fidelity_options_can_use_maximum_supported_effort_and_room(isolated_env):
    isolated_env.setenv("MOONSHOT_REASONING_EFFORT", "max")
    isolated_env.setenv("MOONSHOT_MAX_OUTPUT_TOKENS", "131072")
    isolated_env.setenv("BRICK_IT_MAX_CRITIQUE_ROUNDS", "10")
    isolated_env.setenv("BRICK_IT_MAX_VIEWPOINT_ITERATIONS", "6")
    settings = Settings.from_env()
    assert settings.moonshot_reasoning_effort == "max"
    assert settings.moonshot_max_output_tokens == 131072
    assert settings.max_critique_rounds == 10
    assert settings.max_viewpoint_iterations == 6
    isolated_env.setenv("MOONSHOT_MAX_OUTPUT_TOKENS", "1048576")
    assert Settings.from_env().moonshot_max_output_tokens == 1048576


def response():
    return litellm.ModelResponse(
        model="kimi-k3.1-pro",
        choices=[{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "apple"}}],
        usage={"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200},
    )


def test_unlimited_budget_keeps_costing_and_unknown_usage_stop(tmp_path, isolated_env):
    settings = Settings(records_dir=tmp_path, model="moonshot/kimi-k3.1-pro",
                        max_cost=None, moonshot_usd_per_cny=.16)
    accounting = MoonshotAccounting(MoonshotPricing.from_settings(settings), known_cost_cny=10_000_000)
    calls = []

    def complete(**kwargs):
        calls.append(kwargs)
        return response()

    provider = moonshot_completion(settings, complete=complete, accounting=accounting)
    first = provider(messages=[])
    second = provider(messages=[])
    assert len(calls) == 2
    assert first._hidden_params["response_cost"] == pytest.approx(.0266 * .16)
    assert accounting.snapshot()["costCny"] == pytest.approx(10_000_000 + 2 * .0266)
    assert second._hidden_params["response_cost"] is not None
    accounting.unknown_responses += 1
    with pytest.raises(ProviderBudgetExceeded):
        provider(messages=[])
    assert len(calls) == 2


def test_unlimited_request_disables_every_real_http_timeout(tmp_path, isolated_env):
    settings = Settings(records_dir=tmp_path, model="moonshot/kimi-k3.1-pro",
                        max_cost=None, request_timeout=None, moonshot_usd_per_cny=.16,
                        moonshot_reasoning_effort="max", moonshot_max_output_tokens=131072)
    captured = []

    def transport(request):
        captured.append(request)
        return httpx.Response(200, json={
            "id": "local-test", "object": "chat.completion", "created": 1, "model": "kimi-k3.1-pro",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "apple"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200},
        })

    with httpx.Client(transport=httpx.MockTransport(transport), timeout=1) as http:
        client = openai.OpenAI(api_key="unit-test-only", base_url="https://api.moonshot.cn/v1",
                               http_client=http, timeout=1, max_retries=0)
        provider = moonshot_completion(settings, complete=lambda **kwargs: litellm.completion(client=client, **kwargs))
        provider(messages=[{"role": "user", "content": "apple"}], timeout=120)
    assert len(captured) == 1
    assert captured[0].extensions["timeout"] == {"connect": None, "read": None, "write": None, "pool": None}
    import json
    body = json.loads(captured[0].content)
    assert body["reasoning_effort"] == "max"
    assert body["model"] == "kimi-k3.1-pro"
    assert body["max_completion_tokens"] == 131072


def test_finite_request_timeout_remains_bounded(tmp_path, isolated_env):
    settings = Settings(records_dir=tmp_path, model="moonshot/kimi-k3.1-pro", moonshot_usd_per_cny=.16)
    calls = []
    provider = moonshot_completion(settings, complete=lambda **kwargs: (calls.append(kwargs), response())[1])
    provider(messages=[], timeout=999)
    assert calls[0]["timeout"] == 120
    provider(messages=[], timeout=30)
    assert calls[1]["timeout"] == 30


def test_unlimited_route_fills_empty_tool_reasoning_without_mutating_history(tmp_path, isolated_env):
    settings = Settings(records_dir=tmp_path, model="moonshot/kimi-k3.1-pro",
                        request_timeout=None, moonshot_usd_per_cny=.16)
    calls = []
    provider = moonshot_completion(settings, complete=lambda **kwargs: (calls.append(kwargs), response())[1])
    history = [{"role": "assistant", "content": "", "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "render", "arguments": "{}"}},
    ]}]
    provider(messages=history)
    assert "reasoning_content" not in history[0]
    assert calls[0]["messages"][0]["reasoning_content"] == " "
    assert calls[0]["api_base"] == "https://api.moonshot.cn/v1"
