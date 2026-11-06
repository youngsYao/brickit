"""Check OpenAI timeout controls on the real GPT-6 Responses transport."""

import importlib
import json

import httpx
import litellm
import pytest
from litellm.llms.custom_httpx.http_handler import HTTPHandler

from server.config import Settings
from server.providers import OpenAIAccounting, worker_completion_provider


@pytest.mark.parametrize("agent", ["loop", "critique", "viewpoint"])
@pytest.mark.parametrize("timeout", [None, 42])
def test_openai_timeout_survives_responses_bridge(tmp_path, monkeypatch, agent, timeout):
    settings = Settings(records_dir=tmp_path, model="openai/gpt-6-astra", request_timeout=timeout)
    captured = []

    def transport(request):
        captured.append(request)
        return httpx.Response(200, json={
            "id": "resp_offline", "object": "response", "created_at": 1,
            "model": "gpt-6-astra", "status": "completed",
            "output": [
                {"id": "msg_offline", "type": "message", "role": "assistant", "status": "completed",
                 "content": [{"type": "output_text", "text": "Checking the build.", "annotations": []}]},
                {"id": "fc_offline", "type": "function_call", "call_id": "call_offline",
                 "name": "validate", "arguments": "{}", "status": "completed"},
            ],
            "usage": {"input_tokens": 100, "output_tokens": 30, "total_tokens": 130,
                      "input_tokens_details": {"cached_tokens": 0},
                      "output_tokens_details": {"reasoning_tokens": 10}},
        })

    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key")
    monkeypatch.setenv("OPENAI_API_BASE", "https://offline.example/v1")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://offline.example/v1")
    original_complete = litellm.completion
    module = importlib.import_module(f"brickwright.driver.{agent}")
    original_factory = module.litellm_complete
    with httpx.Client(transport=httpx.MockTransport(transport), timeout=1) as http:
        handler = HTTPHandler(client=http)
        monkeypatch.setattr(litellm, "completion", lambda **kwargs: original_complete(client=handler, **kwargs))
        with worker_completion_provider(settings) as accounting:
            response = module.litellm_complete()(
                model=settings.model,
                messages=[{"role": "user", "content": "Validate the build."}],
                reasoning_effort="high",
                tools=[{"type": "function", "function": {"name": "validate", "parameters": {"type": "object", "properties": {}}}}],
                timeout=999,
                _skip_mcp_handler=True,
                num_retries=0,
            )
            assert isinstance(accounting, OpenAIAccounting)
    assert module.litellm_complete is original_factory
    assert len(captured) == 1
    assert str(captured[0].url) == "https://offline.example/v1/responses"
    assert captured[0].extensions["timeout"] == dict.fromkeys(["connect", "read", "write", "pool"], timeout)
    body = json.loads(captured[0].content)
    assert body["model"] == "gpt-6-astra"
    assert body["reasoning"]["effort"] == "high"
    assert len(response.choices) == 1
    assert response.choices[0].message.content == "Checking the build."
    assert response.choices[0].message.tool_calls[0].function.name == "validate"
    assert response.usage.prompt_tokens == 100
    assert response.usage.completion_tokens == 30
    assert response._hidden_params["response_cost"] is not None
