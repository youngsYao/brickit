"""Moonshot SSE transport that returns only a complete, metered response.

The local worker still executes tools through BrickWright. This module only
assembles provider deltas; it never executes partial calls or publishes model
reasoning. Progress files contain counters, never message contents.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any

import httpx
import litellm


class IncompleteMoonshotStream(RuntimeError):
    """An unverified stream must not reach the tool executor."""


def _integer(value: Any) -> bool:
    return type(value) is int and value >= 0


def _usage(value: Any) -> dict:
    if not isinstance(value, dict) or not _integer(value.get("prompt_tokens")) or value["prompt_tokens"] <= 0 or not _integer(value.get("completion_tokens")):
        raise IncompleteMoonshotStream("Moonshot stream is missing complete provider usage")
    result = copy.deepcopy(value)
    total = result["prompt_tokens"] + result["completion_tokens"]
    if "total_tokens" in result and (not _integer(result["total_tokens"]) or result["total_tokens"] != total):
        raise IncompleteMoonshotStream("Moonshot stream contains inconsistent provider usage")
    result.setdefault("total_tokens", total)
    return result


def _http_error(response: httpx.Response, model: str, api_key: str) -> Exception:
    try:
        body = response.json()
    except (ValueError, UnicodeError):
        body = {"error": {"message": "Moonshot rejected the streaming request"}}
    # The vendor's fixed error types are needed for quota-versus-rate decisions.
    # Strip the request credential even if an upstream error unexpectedly echoes it.
    body = json.loads(json.dumps(body, ensure_ascii=False).replace(api_key, "[redacted]")) if api_key else body
    detail = body.get("error", body) if isinstance(body, dict) else {}
    message = str(detail.get("message", "Moonshot rejected the streaming request"))[:1000] if isinstance(detail, dict) else "Moonshot rejected the streaming request"
    safe_response = httpx.Response(
        response.status_code,
        headers={key: value for key, value in response.headers.items() if key.lower() == "retry-after"},
        json=body,
        request=httpx.Request("POST", str(response.request.url)),
    )
    kwargs = {"message": message, "model": model, "llm_provider": "moonshot", "body": body}
    if response.status_code == 429:
        return litellm.RateLimitError(response=safe_response, **kwargs)
    if response.status_code >= 500:
        error = litellm.InternalServerError(response=safe_response, **kwargs)
        # LiteLLM's constructor labels all upstream 5xx errors as 500. Keep the
        # actual status for the caller's retry policy and private diagnostics.
        error.status_code = response.status_code
        return error
    if response.status_code == 400:
        return litellm.BadRequestError(response=safe_response, **kwargs)
    return litellm.APIError(status_code=response.status_code, request=safe_response.request, **kwargs)


def stream_completion(**routed: Any) -> litellm.ModelResponse:
    """Use the routed CN endpoint, preserving a genuinely unlimited HTTP timeout.

    Optional ``transport`` is an HTTPX transport for offline tests. Optional
    ``progress_path`` stores private counters. Neither is transmitted to the API.
    """
    model = str(routed["model"]).split("/", 1)[-1]
    api_key = str(routed["api_key"])
    api_base = str(routed["api_base"]).rstrip("/")
    body = {key: copy.deepcopy(routed[key]) for key in (
        "messages", "tools", "tool_choice", "reasoning_effort", "max_completion_tokens", "response_format",
    ) if key in routed and routed[key] is not None}
    body.update(model=model, stream=True, stream_options={"include_usage": True})
    timeout = routed["timeout"] if "timeout" in routed else None
    progress = Path(routed["progress_path"]) if routed.get("progress_path") is not None else None
    chunks = content_chars = reasoning_chars = argument_chars = 0
    last_progress = -float("inf")

    def update(stage: str, *, force: bool = False) -> None:
        nonlocal last_progress
        if progress is None:
            return
        now = time.monotonic()
        if not force and now - last_progress < 5:
            return
        last_progress = now
        state = {"stage": stage, "chunks": chunks, "contentCharacters": content_chars,
                 "reasoningCharacters": reasoning_chars, "toolArgumentCharacters": argument_chars,
                 "lastReceivedAt": datetime.now(timezone.utc).isoformat()}
        progress.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = progress.with_suffix(progress.suffix + ".tmp")
        temp.touch(mode=0o600, exist_ok=True)
        temp.chmod(0o600)
        temp.write_text(json.dumps(state), encoding="utf-8")
        temp.replace(progress)

    text: list[str] = []
    reasoning: list[str] = []
    calls: dict[int, dict] = {}
    finish = None
    usage = None
    response_id = None
    created = 0
    done = False
    events: list[str] = []

    def consume(payload: str) -> None:
        nonlocal chunks, content_chars, reasoning_chars, argument_chars, finish, usage, response_id, created, done
        if payload.strip() == "[DONE]":
            done = True
            return
        try:
            chunk = json.loads(payload)
        except (ValueError, UnicodeError):
            raise IncompleteMoonshotStream("Moonshot returned a malformed streaming event") from None
        if not isinstance(chunk, dict) or "error" in chunk:
            raise IncompleteMoonshotStream("Moonshot returned an unsuccessful streaming event")
        chunks += 1
        response_id = chunk.get("id") or response_id
        created = chunk.get("created") or created
        choices = chunk.get("choices")
        if not isinstance(choices, list):
            raise IncompleteMoonshotStream("Moonshot stream is missing choices")
        if chunk.get("usage") is not None:
            usage = chunk["usage"]
        if not choices:
            update("receiving")
            return
        if len(choices) != 1 or choices[0].get("index", 0) != 0:
            raise IncompleteMoonshotStream("Moonshot returned unexpected streaming choices")
        choice = choices[0]
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            raise IncompleteMoonshotStream("Moonshot stream is missing a delta")
        if choice.get("finish_reason") is not None:
            finish = choice["finish_reason"]
            if choice.get("usage") is not None:
                usage = choice["usage"]
        for key, target in (("content", text), ("reasoning_content", reasoning)):
            fragment = delta.get(key)
            if fragment is not None:
                if not isinstance(fragment, str):
                    raise IncompleteMoonshotStream("Moonshot returned an invalid text delta")
                target.append(fragment)
                if key == "content":
                    content_chars += len(fragment)
                else:
                    reasoning_chars += len(fragment)
        fragments = delta.get("tool_calls") or []
        if not isinstance(fragments, list):
            raise IncompleteMoonshotStream("Moonshot returned invalid tool deltas")
        for fragment in fragments:
            if not isinstance(fragment, dict) or not _integer(fragment.get("index")):
                raise IncompleteMoonshotStream("Moonshot returned an invalid tool index")
            call = calls.setdefault(fragment["index"], {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
            if fragment.get("id"):
                if call["id"] and call["id"] != fragment["id"]:
                    raise IncompleteMoonshotStream("Moonshot changed a streaming tool identity")
                call["id"] = fragment["id"]
            if fragment.get("type"):
                call["type"] = fragment["type"]
            function = fragment.get("function") or {}
            if not isinstance(function, dict):
                raise IncompleteMoonshotStream("Moonshot returned invalid function deltas")
            for key in ("name", "arguments"):
                part = function.get(key)
                if part is not None:
                    if not isinstance(part, str):
                        raise IncompleteMoonshotStream("Moonshot returned invalid function deltas")
                    call["function"][key] += part
                    if key == "arguments":
                        argument_chars += len(part)
        update("receiving")

    try:
        with httpx.Client(timeout=timeout, transport=routed.get("transport")) as client:
            with client.stream("POST", api_base + "/chat/completions", headers={"Authorization": "Bearer " + api_key}, json=body) as response:
                if response.is_error:
                    response.read()
                    raise _http_error(response, model, api_key)
                if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "text/event-stream":
                    raise IncompleteMoonshotStream("Moonshot did not return an SSE response")
                update("connected", force=True)
                for line in response.iter_lines():
                    if line == "":
                        if events:
                            consume("\n".join(events))
                            events.clear()
                            if done:
                                break
                    elif line.startswith("data:"):
                        events.append(line[5:].removeprefix(" "))
                    elif line.startswith(":"):
                        update("receiving")
                if events and not done:
                    consume("\n".join(events))
        if not done or not isinstance(finish, str) or finish not in {"stop", "tool_calls", "length", "content_filter"}:
            raise IncompleteMoonshotStream("Moonshot stream ended before completion")
        real_usage = _usage(usage)
        ordered_calls = [calls[index] for index in sorted(calls)]
        if calls and (sorted(calls) != list(range(len(calls))) or finish != "tool_calls"):
            raise IncompleteMoonshotStream("Moonshot stream contains incomplete tool calls")
        if finish == "tool_calls" and not calls:
            raise IncompleteMoonshotStream("Moonshot stream is missing its tool calls")
        for call in ordered_calls:
            if not isinstance(call["id"], str) or not call["id"] or call["type"] != "function" or not call["function"]["name"]:
                raise IncompleteMoonshotStream("Moonshot stream contains incomplete tool calls")
            try:
                arguments = json.loads(call["function"]["arguments"])
            except ValueError:
                raise IncompleteMoonshotStream("Moonshot stream contains incomplete tool arguments") from None
            if not isinstance(arguments, dict):
                raise IncompleteMoonshotStream("Moonshot stream contains invalid tool arguments")
        message: dict[str, Any] = {"role": "assistant", "content": "".join(text)}
        if reasoning:
            message["reasoning_content"] = "".join(reasoning)
        if calls:
            message["tool_calls"] = ordered_calls
        result = litellm.ModelResponse(id=response_id, model=model, created=created,
            choices=[{"index": 0, "finish_reason": finish, "message": message}], usage=real_usage)
        update("complete", force=True)
        return result
    except httpx.TimeoutException:
        update("error", force=True)
        raise litellm.Timeout(message="Moonshot streaming request timed out", model=model, llm_provider="moonshot") from None
    except httpx.RequestError:
        update("error", force=True)
        raise litellm.APIConnectionError(message="Moonshot streaming connection failed", model=model, llm_provider="moonshot") from None
    except Exception:
        update("error", force=True)
        raise
