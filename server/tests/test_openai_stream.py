"""OpenAI Responses streams retain tools, billed usage, and completion checks."""

import importlib
import json

import httpx
import litellm
import pytest
from litellm.llms.custom_httpx.http_handler import HTTPHandler

from server.config import Settings
from server.providers import worker_completion_provider


def sse_events(terminal="response.completed", incomplete_reason="max_output_tokens"):
    output = [{"id": "msg_offline", "type": "message", "role": "assistant", "status": "completed",
               "content": [{"type": "output_text", "text": "Checking the build.", "annotations": []}]}]
    response = {"id": "resp_offline", "object": "response", "created_at": 1,
                "model": "gpt-6-astra", "status": "in_progress", "output": []}
    events = [{"type": "response.created", "response": response},
              {"type": "response.output_text.delta", "item_id": "msg_offline", "output_index": 0,
               "content_index": 0, "delta": "Checking the build."}]
    for index, name in enumerate(["validate", "render"], 1):
        item = {"id": f"fc_{index}", "type": "function_call", "call_id": f"call_{index}",
                "name": name, "arguments": "", "status": "in_progress"}
        events.append({"type": "response.output_item.added", "output_index": index, "item": item})
        events.append({"type": "response.function_call_arguments.delta", "output_index": index,
                       "item_id": item["id"], "delta": "{}"})
        output.append({**item, "arguments": "{}", "status": "completed"})
        events.append({"type": "response.output_item.done", "output_index": index, "item": output[-1]})
    if terminal:
        completed = {**response, "status": "completed" if terminal == "response.completed" else "incomplete",
                     "output": output, "usage": {"input_tokens": 100, "output_tokens": 30, "total_tokens": 130,
                         "input_tokens_details": {"cached_tokens": 20},
                         "output_tokens_details": {"reasoning_tokens": 10}}}
        if terminal == "response.incomplete":
            completed["incomplete_details"] = {"reason": incomplete_reason}
        events.append({"type": terminal, "response": completed})
    return "".join("event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n" for event in events)


@pytest.mark.parametrize("agent", ["loop", "critique", "viewpoint"])
def test_real_responses_stream_preserves_parallel_tools_usage_and_unlimited_timeout(tmp_path, monkeypatch, agent):
    captured = []
    def transport(request):
        captured.append(request)
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, text=sse_events())
    response = invoke_stream(tmp_path, monkeypatch, transport, agent)
    assert len(captured) == 1
    assert str(captured[0].url) == "https://offline.example/v1/responses"
    assert captured[0].extensions["timeout"] == dict.fromkeys(["connect", "read", "write", "pool"], None)
    body = json.loads(captured[0].content)
    assert body["model"] == "gpt-6-astra" and body["stream"] is True
    assert len(response.choices) == 1
    message = response.choices[0].message
    assert message.content == "Checking the build."
    assert [(tool.id, tool.function.name, tool.function.arguments) for tool in message.tool_calls] == [
        ("fc_1", "validate", "{}"), ("fc_2", "render", "{}")]
    assert response.usage.prompt_tokens == 100 and response.usage.completion_tokens == 30
    assert response.usage.prompt_tokens_details.cached_tokens == 20
    assert response.usage.completion_tokens_details.reasoning_tokens == 10
    assert response._hidden_params["response_cost"] == pytest.approx(litellm.completion_cost(completion_response=response))
    assert response._hidden_params["response_cost"] > 0


def invoke_stream(tmp_path, monkeypatch, transport, agent="loop"):
    settings = Settings(records_dir=tmp_path, model="openai/gpt-6-astra", request_timeout=None, openai_stream=True)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key")
    monkeypatch.setenv("OPENAI_API_BASE", "https://offline.example/v1")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://offline.example/v1")
    original_complete = litellm.completion
    with httpx.Client(transport=httpx.MockTransport(transport), timeout=1) as http:
        handler = HTTPHandler(client=http)
        monkeypatch.setattr(litellm, "completion", lambda **kwargs: original_complete(client=handler, **kwargs))
        with worker_completion_provider(settings):
            return importlib.import_module(f"brickwright.driver.{agent}").litellm_complete()(
                model=settings.model, messages=[{"role": "user", "content": "Validate the build."}],
                reasoning_effort="high", num_retries=0,
            )


@pytest.mark.parametrize("terminal", [None, "response.incomplete"])
def test_interrupted_or_filtered_responses_stream_is_not_a_success(tmp_path, monkeypatch, terminal):
    def transport(request):
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, text=sse_events(terminal, "content_filter"))
    with pytest.raises(Exception, match="missing_usage|terminal event|content_filter"):
        invoke_stream(tmp_path, monkeypatch, transport)


def test_metered_length_response_reaches_sdk_truncation_recovery(tmp_path, monkeypatch):
    def transport(request):
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, text=sse_events("response.incomplete"))
    response = invoke_stream(tmp_path, monkeypatch, transport)
    assert response.choices[0].finish_reason == "length"
    assert response.choices[0].message.content == "Checking the build."
    assert response.choices[0].message.tool_calls is None
    assert response.choices[0].message.function_call is None
    assert response.usage.prompt_tokens == 100
    assert response._hidden_params["response_cost"] > 0


def test_disconnected_stream_does_not_return_partial_tools(tmp_path, monkeypatch):
    class Disconnected(httpx.SyncByteStream):
        def __iter__(self):
            yield sse_events(None).encode()
            raise httpx.ReadError("offline simulated stream disconnect")
    def transport(request):
        return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, stream=Disconnected())
    with pytest.raises(Exception, match="disconnect"):
        invoke_stream(tmp_path, monkeypatch, transport)


def test_openai_stream_setting_is_optional_and_validated(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_STREAM", "true")
    assert Settings.from_env().openai_stream is True
    monkeypatch.setenv("OPENAI_STREAM", "false")
    assert Settings.from_env().openai_stream is False
    monkeypatch.setenv("OPENAI_STREAM", "invalid")
    with pytest.raises(ValueError, match="OPENAI_STREAM"):
        Settings.from_env()
    assert Settings(records_dir=tmp_path).openai_stream is False
