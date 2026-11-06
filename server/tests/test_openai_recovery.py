"""Retry real OpenAI wire failures only when a job explicitly has no budget cap."""

import importlib
import json

import httpx
import litellm
import pytest
from litellm.llms.custom_httpx.http_handler import HTTPHandler

from server import providers
from server.config import Settings
from server.providers import OpenAIAccounting, worker_completion_provider
from server.tests.test_openai_stream import sse_events


class Disconnected(httpx.SyncByteStream):
    def __iter__(self):
        # Even valid-looking tools in a failed stream must never be returned.
        yield sse_events(None).replace("validate", "discarded_partial_tool").encode()
        raise httpx.ReadError("offline simulated disconnect with private provider body")


def failure_response(mode):
    if mode == "disconnect":
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=Disconnected())
    return httpx.Response(int(mode), json={"error": {"message": "private provider body sk-offline-secret", "type": "server_error"}})


def invoke(tmp_path, monkeypatch, transport, *, budget=None, agent="loop", waits=None, announcements=None, accounting_out=None):
    settings = Settings(records_dir=tmp_path, model="openai/gpt-6-astra", max_cost=budget,
                        request_timeout=None, openai_stream=True)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key")
    monkeypatch.setenv("OPENAI_API_BASE", "https://offline.example/v1")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://offline.example/v1")
    waits = waits if waits is not None else []
    announcements = announcements if announcements is not None else []
    monkeypatch.setattr(providers.time, "sleep", waits.append)
    original_complete = litellm.completion
    with httpx.Client(transport=httpx.MockTransport(transport), timeout=1) as http:
        handler = HTTPHandler(client=http)
        monkeypatch.setattr(litellm, "completion", lambda **kwargs: original_complete(client=handler, **kwargs))
        with worker_completion_provider(settings, checkpoint_path=tmp_path / "conversation-checkpoint.json", on_wait=announcements.append) as accounting:
            if accounting_out is not None:
                accounting_out.append(accounting)
            response = importlib.import_module(f"brickwright.driver.{agent}").litellm_complete()(
                model=settings.model, messages=[{"role": "user", "content": "Keep this exact original request."}],
                reasoning_effort="high",
            )
    return response, accounting


@pytest.mark.parametrize("agent", ["loop", "critique", "viewpoint"])
@pytest.mark.parametrize("mode", ["500", "disconnect"])
def test_unlimited_job_retries_failed_wire_and_discards_partial_tools(tmp_path, monkeypatch, mode, agent):
    captured, waits, announcements = [], [], []
    def transport(request):
        captured.append(request)
        if len(captured) == 1:
            return failure_response(mode)
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, text=sse_events())
    response, accounting = invoke(tmp_path, monkeypatch, transport, agent=agent, waits=waits, announcements=announcements)
    assert len(captured) == 2 and captured[0].content == captured[1].content
    assert waits == announcements == [30]
    assert [tool.function.name for tool in response.choices[0].message.tool_calls] == ["validate", "render"]
    snapshot = accounting.snapshot()
    assert snapshot["responses"] == 1 and snapshot["attempts"] == 2
    assert snapshot["failedAttempts"] == snapshot["serverErrorRetries"] == snapshot["unknownCostAttempts"] == 1
    assert snapshot["knownEstimatedCostUsd"] == response._hidden_params["response_cost"] > 0
    assert snapshot["estimatedCostUsd"] is None and snapshot["actualCostUsd"] is None
    assert snapshot["usdIsEstimate"] is True and snapshot["estimateComplete"] is False
    assert snapshot["usageCostScope"] == "successful_responses"
    assert snapshot["lastFailure"]["statusCode"] == 500
    assert "private provider body" not in json.dumps(snapshot) and "sk-offline-secret" not in json.dumps(snapshot)
    if agent == "loop":
        checkpoint = json.loads((tmp_path / "conversation-checkpoint.json").read_text())
        assert checkpoint["messages"] == [{"role": "user", "content": "Keep this exact original request."}]


@pytest.mark.parametrize("mode", ["500", "disconnect"])
def test_finite_budget_does_not_retry_a_possibly_billed_failed_request(tmp_path, monkeypatch, mode):
    captured, waits, accounting = [], [], []
    def transport(request):
        captured.append(request)
        return failure_response(mode)
    with pytest.raises(Exception):
        invoke(tmp_path, monkeypatch, transport, budget=1, waits=waits, accounting_out=accounting)
    assert len(captured) == 1 and waits == []
    snapshot = accounting[0].snapshot()
    assert snapshot["failedAttempts"] == 1 and snapshot["serverErrorRetries"] == 0
    assert snapshot["responses"] == 0 and snapshot["estimatedCostUsd"] is None


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_transient_retries_are_limited_to_two_and_wait_30_then_60(tmp_path, monkeypatch, status):
    captured, waits, announcements, accounting = [], [], [], []
    def transport(request):
        captured.append(request)
        return failure_response(str(status))
    with pytest.raises(Exception):
        invoke(tmp_path, monkeypatch, transport, waits=waits, announcements=announcements, accounting_out=accounting)
    assert len(captured) == 3 and waits == announcements == [30, 60]
    assert len({request.content for request in captured}) == 1
    snapshot = accounting[0].snapshot()
    assert snapshot["failedAttempts"] == 3 and snapshot["serverErrorRetries"] == 2
    assert snapshot["unknownCostAttempts"] == 3 and snapshot["estimatedCostUsd"] is None


@pytest.mark.parametrize("status", [400, 401, 403, 429])
def test_nontransient_responses_are_not_retried_even_with_unlimited_budget(tmp_path, monkeypatch, status):
    captured, waits = [], []
    def transport(request):
        captured.append(request)
        return failure_response(str(status))
    with pytest.raises(Exception):
        invoke(tmp_path, monkeypatch, transport, waits=waits)
    assert len(captured) == 1 and waits == []


def test_successful_estimates_still_disclose_unverified_actual_provider_price():
    accounting = OpenAIAccounting()
    from types import SimpleNamespace
    accounting.record(SimpleNamespace(_hidden_params={"response_cost": .25}))
    snapshot = accounting.snapshot()
    assert snapshot["estimatedCostUsd"] == snapshot["knownEstimatedCostUsd"] == .25
    assert snapshot["estimateComplete"] is True and snapshot["actualCostUsd"] is None
    assert "unverified" in snapshot["pricingBasis"]
