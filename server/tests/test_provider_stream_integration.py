"""Exercise provider routing, SSE parsing and billed usage on a local wire."""

import json

import httpx
import pytest

from server.config import Settings
from server import moonshot_stream  # Load SDK classes before replacing the local HTTP client.
from server.providers import MoonshotAccounting, MoonshotPricing, moonshot_completion


def test_unlimited_provider_selects_stream_without_losing_controls_or_billing(tmp_path, monkeypatch):
    settings = Settings(
        records_dir=tmp_path, model="moonshot/kimi-k3.1-pro", max_cost=None,
        request_timeout=None, moonshot_stream=True, moonshot_reasoning_effort="max",
        moonshot_max_output_tokens=131072, moonshot_usd_per_cny=.16,
    )
    monkeypatch.setenv("MOONSHOT_API_KEY", "offline-test-key")
    history = [
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,dGVzdA=="}}]},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "previous", "type": "function", "function": {"name": "read_build", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "previous", "content": "existing source"},
    ]
    chunks = [
        {"choices": [{"index": 0, "delta": {"reasoning_content": "synthetic context"}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "next", "type": "function", "function": {"name": "validate", "arguments": "{"}}]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "}"}}]}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130, "prompt_tokens_details": {"cached_tokens": 20}, "completion_tokens_details": {"reasoning_tokens": 10}}},
    ]
    wire = "".join("data: " + json.dumps({"id": "offline", "model": "kimi-k3.1-pro", **chunk}) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"

    def handler(request):
        body = json.loads(request.content)
        assert str(request.url) == "https://api.moonshot.cn/v1/chat/completions"
        assert request.extensions["timeout"] == {"connect": None, "read": None, "write": None, "pool": None}
        assert body["model"] == "kimi-k3.1-pro" and body["reasoning_effort"] == "max"
        assert body["max_completion_tokens"] == 131072 and body["stream"] is True
        assert body["stream_options"] == {"include_usage": True}
        assert body["messages"][1]["reasoning_content"] == " "
        assert body["messages"][0] == history[0]
        assert "api_key" not in body and "progress_path" not in body
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, text=wire)

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: real_client(**{**kwargs, "transport": httpx.MockTransport(handler)}))
    billing = MoonshotAccounting(MoonshotPricing.from_settings(settings))
    response = moonshot_completion(settings, accounting=billing, checkpoint_path=tmp_path / "conversation.json")(messages=history)
    assert response.choices[0].message.tool_calls[0].function.arguments == "{}"
    assert response.choices[0].message.reasoning_content == "synthetic context"
    assert response.usage.completion_tokens_details.reasoning_tokens == 10
    assert billing.snapshot()["costCny"] == pytest.approx(.003106)
    assert response._hidden_params["response_cost"] == pytest.approx(.003106 * .16)
    assert "reasoning_content" not in history[1]
    progress = json.loads((tmp_path / "conversation-progress.json").read_text())
    assert progress["stage"] == "complete" and "synthetic context" not in str(progress)
