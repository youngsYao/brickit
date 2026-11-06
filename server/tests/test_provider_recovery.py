"""Unlimited runs recover from server failures without hiding uncertain charges."""

import importlib
import json
import stat
from dataclasses import replace

import httpx
import litellm
import openai
import pytest

from server.config import Settings
from server.providers import (
    MoonshotAccounting,
    MoonshotPricing,
    ProviderBudgetExceeded,
    ProviderRequestTimeout,
    moonshot_completion,
    worker_completion_provider,
)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "unit-test-only")
    return Settings(records_dir=tmp_path, model="moonshot/kimi-k3.1-pro",
                    max_cost=None, request_timeout=None, moonshot_usd_per_cny=.16,
                    moonshot_reasoning_effort="max")


def response():
    return litellm.ModelResponse(
        model="kimi-k3.1-pro",
        choices=[{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "apple"}}],
        usage={"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200},
    )


def test_no_limit_run_honors_long_provider_retry_window(settings, tmp_path):
    settings = replace(settings, timeout=None)
    calls, waits, notices = [], [], []

    def complete(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            body = {"error": {"type": "rate_limit_reached_error", "message": "TPD allowance resets later"}}
            raise litellm.RateLimitError(
                message=body["error"]["message"], model="kimi-k3.1-pro", llm_provider="moonshot",
                response=httpx.Response(429, headers={"Retry-After": "3493"}, json=body), body=body,
            )
        return response()

    billing = MoonshotAccounting(MoonshotPricing.from_settings(settings))
    result = moonshot_completion(settings, complete=complete, accounting=billing,
        sleep=waits.append, on_wait=notices.append)(messages=[])
    assert len(calls) == 2 and notices == [3493]
    assert sum(waits) == 3493 and max(waits) <= 60
    assert result.usage.prompt_tokens == 1000
    assert billing.snapshot()["unknownCostAttempts"] == 0


def test_even_unlimited_mode_does_not_retry_missing_account_quota(settings):
    settings = replace(settings, timeout=None)
    calls, waits = [], []

    def complete(**kwargs):
        calls.append(kwargs)
        body = {"error": {"type": "exceeded_current_quota_error", "message": "account quota unavailable"}}
        raise litellm.RateLimitError(message="account quota unavailable", model="kimi-k3.1-pro", llm_provider="moonshot",
            response=httpx.Response(429, headers={"Retry-After": "3493"}, json=body), body=body)

    from server.providers import ProviderRateLimited
    with pytest.raises(ProviderRateLimited):
        moonshot_completion(settings, complete=complete, sleep=waits.append)(messages=[])
    assert len(calls) == 1 and not waits


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_unlimited_server_errors_retry_through_real_wire_and_keep_total_unknown(settings, status):
    requests = []
    waits = []
    billing = MoonshotAccounting(MoonshotPricing.from_settings(settings))

    def transport(request):
        requests.append(request)
        if len(requests) <= 2:
            return httpx.Response(status, json={"error": {"message": "private-upstream-error", "type": "server_error"}})
        return httpx.Response(200, json={
            "id": "recovery-test", "object": "chat.completion", "created": 1, "model": "kimi-k3.1-pro",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "apple"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200},
        })

    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        client = openai.OpenAI(api_key="unit-test-only", base_url="https://api.moonshot.cn/v1",
                               http_client=http, max_retries=2)
        provider = moonshot_completion(settings, complete=lambda **kwargs: litellm.completion(client=client, **kwargs),
                                       accounting=billing, sleep=waits.append)
        result = provider(messages=[{"role": "user", "content": "apple"}])
        # A later builder/helper call remains allowed after recovered failures.
        provider(messages=[{"role": "user", "content": "continue apple"}])
    assert len(requests) == 4
    assert waits == [30, 60]
    assert result._hidden_params["response_cost"] == pytest.approx(.0266 * .16)
    snapshot = billing.snapshot()
    assert snapshot["responses"] == 2 and snapshot["failedAttempts"] == 2
    assert snapshot["serverErrorRetries"] == 2 and snapshot["unknownCostAttempts"] == 2
    assert snapshot["costCny"] is None and snapshot["estimatedCostUsd"] is None
    assert snapshot["knownCostCny"] == pytest.approx(.0532)
    assert snapshot["lastFailure"]["statusCode"] == status
    assert "private-upstream-error" not in json.dumps(snapshot)


def test_finite_budget_preserves_stop_after_unpriced_server_failure(settings):
    settings = replace(settings, max_cost=1)
    calls, waits = [], []
    billing = MoonshotAccounting(MoonshotPricing.from_settings(settings))

    def failing(**kwargs):
        calls.append(kwargs)
        raise litellm.InternalServerError(message="private-upstream-error", model="kimi-k3.1-pro", llm_provider="moonshot")

    provider = moonshot_completion(settings, complete=failing, accounting=billing, sleep=waits.append)
    with pytest.raises(litellm.InternalServerError):
        provider(messages=[])
    with pytest.raises(ProviderBudgetExceeded):
        provider(messages=[])
    assert len(calls) == 1 and waits == []
    assert billing.snapshot()["serverErrorRetries"] == 0


def test_unlimited_server_retries_are_bounded(settings):
    calls, waits = [], []
    billing = MoonshotAccounting(MoonshotPricing.from_settings(settings))

    def failing(**kwargs):
        calls.append(kwargs)
        raise litellm.InternalServerError(message="private-upstream-error", model="kimi-k3.1-pro", llm_provider="moonshot")

    with pytest.raises(litellm.InternalServerError):
        moonshot_completion(settings, complete=failing, accounting=billing, sleep=waits.append)(messages=[])
    assert len(calls) == 3 and waits == [30, 60]
    assert billing.snapshot()["unknownCostAttempts"] == 3


@pytest.mark.parametrize("kind", ["timeout", "bad_request", "connection"])
def test_unlimited_other_failures_are_not_automatically_retried(settings, kind):
    calls, waits = [], []
    errors = {
        "timeout": litellm.Timeout(message="private-error", model="kimi-k3.1-pro", llm_provider="moonshot"),
        "bad_request": litellm.BadRequestError(message="private-error", model="kimi-k3.1-pro", llm_provider="moonshot"),
        "connection": ConnectionError("private-error"),
    }

    def failing(**kwargs):
        calls.append(kwargs)
        raise errors[kind]

    expected = ProviderRequestTimeout if kind == "timeout" else type(errors[kind])
    with pytest.raises(expected):
        moonshot_completion(settings, complete=failing, sleep=waits.append)(messages=[])
    assert len(calls) == 1 and waits == []


def test_unlimited_still_blocks_unpriced_successful_response(settings):
    billing = MoonshotAccounting(MoonshotPricing.from_settings(settings))
    billing.unknown_responses = 1
    calls = []
    with pytest.raises(ProviderBudgetExceeded):
        moonshot_completion(settings, complete=lambda **kwargs: calls.append(kwargs), accounting=billing)(messages=[])
    assert calls == []


def test_private_checkpoint_exists_before_request_and_preserves_complete_history(settings, tmp_path):
    checkpoint = tmp_path / "conversation.json"
    history = [
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,dGVzdA=="}}]},
        {"role": "assistant", "content": "", "reasoning_content": "synthetic-test-context",
         "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "explore", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "catalog result"},
    ]

    def complete(**kwargs):
        data = json.loads(checkpoint.read_text())
        assert data == {"model": settings.model, "agent": "builder", "messages": history}
        assert stat.S_IMODE(checkpoint.stat().st_mode) == 0o600
        assert "unit-test-only" not in checkpoint.read_text()
        return response()

    moonshot_completion(settings, complete=complete, checkpoint_path=checkpoint)(messages=history)


def test_helper_checkpoints_do_not_overwrite_builder_history(settings, tmp_path, monkeypatch):
    checkpoint = tmp_path / "conversation.json"
    monkeypatch.setattr(litellm, "completion", lambda **kwargs: response())
    with worker_completion_provider(settings, checkpoint_path=checkpoint):
        for agent in ("loop", "viewpoint", "critique"):
            module = importlib.import_module(f"brickwright.driver.{agent}")
            module.litellm_complete()(model=settings.model, messages=[{"role": "user", "content": agent}])
    assert json.loads(checkpoint.read_text())["messages"][0]["content"] == "loop"
    assert json.loads((tmp_path / "conversation-viewpoint.json").read_text())["agent"] == "viewpoint"
    assert json.loads((tmp_path / "conversation-critique.json").read_text())["agent"] == "critique"
