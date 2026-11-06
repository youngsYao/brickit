"""Real HTTPX SSE parsing; all requests use an offline transport."""

import json
import stat

import httpx
import litellm
import pytest

from server.moonshot_stream import IncompleteMoonshotStream, stream_completion


def event(delta=None, *, finish=None, usage=None, empty=False):
    result = {"id": "stream-test", "object": "chat.completion.chunk", "created": 1,
              "model": "kimi-k3.1-pro", "choices": [] if empty else [
                  {"index": 0, "delta": delta or {}, "finish_reason": finish},
              ]}
    if usage is not None:
        result["usage"] = usage
    return result


USAGE = {"prompt_tokens": 1200, "completion_tokens": 35, "total_tokens": 1235,
         "prompt_tokens_details": {"cached_tokens": 1024, "cache_write_tokens": 128},
         "completion_tokens_details": {"reasoning_tokens": 22}}


def wire(events, *, done=True):
    text = ": keepalive\n\n" + "".join("data: " + json.dumps(e, ensure_ascii=False) + "\n\n" for e in events)
    return (text + ("data: [DONE]\n\n" if done else "")).encode()


def call(events, *, done=True, progress=None, capture=None, timeout=None):
    def handler(request):
        if capture is not None:
            capture.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream; charset=utf-8"},
                              content=wire(events, done=done))

    return stream_completion(
        model="openai/kimi-k3.1-pro", api_key="unit-test-secret", api_base="https://api.moonshot.cn/v1",
        timeout=timeout, messages=[{"role": "user", "content": "苹果"}],
        reasoning_effort="max", max_completion_tokens=131072,
        transport=httpx.MockTransport(handler), progress_path=progress,
    )


def test_stream_preserves_reasoning_text_and_real_cached_usage(tmp_path):
    captured = []
    progress = tmp_path / "progress.json"
    response = call([
        event({"role": "assistant", "reasoning_content": "synthetic-"}),
        event({"reasoning_content": "test-context"}),
        event({"content": "红"}), event({"content": "苹果"}, finish="stop"),
        event(usage=USAGE, empty=True),
    ], capture=captured, progress=progress)
    message = response.choices[0].message
    assert message.content == "红苹果" and message.reasoning_content == "synthetic-test-context"
    assert response.choices[0].finish_reason == "stop"
    assert response.usage.prompt_tokens == 1200 and response.usage.completion_tokens == 35
    assert response.usage.prompt_tokens_details.cached_tokens == 1024
    assert response.usage.completion_tokens_details.reasoning_tokens == 22
    request = captured[0]
    assert str(request.url) == "https://api.moonshot.cn/v1/chat/completions"
    assert request.extensions["timeout"] == {"connect": None, "read": None, "write": None, "pool": None}
    body = json.loads(request.content)
    assert body["model"] == "kimi-k3.1-pro" and body["reasoning_effort"] == "max"
    assert body["max_completion_tokens"] == 131072
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert request.headers["authorization"] == "Bearer unit-test-secret"
    summary = json.loads(progress.read_text())
    assert summary["stage"] == "complete" and summary["chunks"] == 5
    assert summary["contentCharacters"] == 3
    assert summary["reasoningCharacters"] == len("synthetic-test-context")
    assert "synthetic" not in progress.read_text() and "unit-test-secret" not in progress.read_text()
    assert stat.S_IMODE(progress.stat().st_mode) == 0o600


def test_stream_reassembles_interleaved_tool_indexes_without_losing_fragments():
    result = call([
        event({"tool_calls": [{"index": 0, "id": "call-write", "type": "function", "function": {"name": "write_", "arguments": "{\"source\":"}}]}),
        event({"tool_calls": [{"index": 1, "id": "call-check", "type": "function", "function": {"name": "validate", "arguments": "{"}},
                              {"index": 0, "function": {"name": "build", "arguments": "\"苹果\"}"}}]}),
        event({"tool_calls": [{"index": 1, "function": {"arguments": "}"}}]}, finish="tool_calls"),
        event(usage=USAGE, empty=True),
    ])
    calls = result.choices[0].message.tool_calls
    assert [c.id for c in calls] == ["call-write", "call-check"]
    assert calls[0].function.name == "write_build"
    assert json.loads(calls[0].function.arguments) == {"source": "苹果"}
    assert json.loads(calls[1].function.arguments) == {}


def test_wire_preserves_images_history_tools_and_json_format():
    captured = []
    messages = [
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,dGVzdA=="}}]},
        {"role": "assistant", "content": "", "reasoning_content": "synthetic-test-context",
         "tool_calls": [{"id": "old-call", "type": "function", "function": {"name": "render", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "old-call", "content": "rendered"},
    ]
    tools = [{"type": "function", "function": {"name": "validate", "parameters": {"type": "object"}}}]

    def handler(request):
        captured.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=wire([
            event({"content": "{}"}, finish="stop"), event(usage=USAGE, empty=True),
        ]))

    stream_completion(model="moonshot/kimi-k3.1-pro", api_key="unit-test-secret", api_base="https://api.moonshot.cn/v1",
        messages=messages, tools=tools, tool_choice="auto", response_format={"type": "json_object"},
        timeout=httpx.Timeout(None), transport=httpx.MockTransport(handler))
    body = json.loads(captured[0].content)
    assert body["messages"] == messages and body["tools"] == tools
    assert body["tool_choice"] == "auto" and body["response_format"] == {"type": "json_object"}
    assert captured[0].extensions["timeout"] == {"connect": None, "read": None, "write": None, "pool": None}


@pytest.mark.parametrize("case", ["no_done", "no_usage", "no_finish", "zero_usage", "inconsistent_usage", "partial_tool", "tool_without_id"])
def test_incomplete_stream_never_returns_executable_calls(case):
    usage = dict(USAGE)
    events = [event({"content": "apple"}, finish="stop"), event(usage=usage, empty=True)]
    done = True
    if case == "no_done": done = False
    if case == "no_usage": events = events[:1]
    if case == "no_finish": events[0]["choices"][0]["finish_reason"] = None
    if case == "zero_usage": events[-1]["usage"] = {"prompt_tokens": 0, "completion_tokens": 0}
    if case == "inconsistent_usage": events[-1]["usage"]["total_tokens"] = 0
    if case in {"partial_tool", "tool_without_id"}:
        fragment = {"index": 0, "id": "call-1", "function": {"name": "write_build", "arguments": "{" if case == "partial_tool" else "{}"}}
        if case == "tool_without_id": del fragment["id"]
        events[0] = event({"tool_calls": [fragment]}, finish="tool_calls")
    with pytest.raises(IncompleteMoonshotStream):
        call(events, done=done)


@pytest.mark.parametrize("status,expected", [(429, litellm.RateLimitError), (500, litellm.InternalServerError),
                                            (502, litellm.InternalServerError), (503, litellm.InternalServerError),
                                            (504, litellm.InternalServerError), (400, litellm.BadRequestError)])
def test_http_errors_keep_retry_status_without_exposing_credentials(status, expected):
    def handler(request):
        return httpx.Response(status, headers={"retry-after": "2"}, json={"error": {
            "type": "rate_limit_reached_error", "message": "upstream unit-test-secret"}})
    with pytest.raises(expected) as raised:
        stream_completion(model="openai/kimi-k3.1-pro", api_key="unit-test-secret", api_base="https://api.moonshot.cn/v1",
                          messages=[], transport=httpx.MockTransport(handler), timeout=None)
    assert raised.value.status_code == status
    assert "unit-test-secret" not in str(raised.value)
    assert raised.value.response.headers["retry-after"] == "2"


def test_read_timeout_maps_to_existing_provider_timeout_type():
    def handler(request):
        raise httpx.ReadTimeout("private transport details", request=request)
    with pytest.raises(litellm.Timeout) as raised:
        stream_completion(model="openai/kimi-k3.1-pro", api_key="unit-test-secret", api_base="https://api.moonshot.cn/v1",
                          messages=[], transport=httpx.MockTransport(handler), timeout=None)
    assert "private transport details" not in str(raised.value)


def test_completed_usage_does_not_replace_a_missing_done_marker():
    with pytest.raises(IncompleteMoonshotStream):
        call([event({"content": ""}, finish="length"), event(usage=USAGE, empty=True)], done=False)


def test_legacy_usage_on_finished_choice_is_preserved():
    chunk = event({"content": "apple"}, finish="stop")
    chunk["choices"][0]["usage"] = USAGE
    result = call([chunk])
    assert result.usage.prompt_tokens == 1200
