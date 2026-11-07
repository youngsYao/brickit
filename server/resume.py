"""Resume a private builder conversation without repeating its completed tools.

Only the dedicated worker temporarily replaces the SDK transcript class. The
source job is read-only; all new logs, source changes and exports belong to the
new job and still pass the normal independent completion checks.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .storage import JOB_ID, atomic_json


class ResumeRejected(ValueError):
    """The requested private history cannot be restored safely."""


CONTINUE_MESSAGE = (
    "Continue the interrupted builder run using the catalogue information and tool results already above. "
    "The model service interrupted an unfinished run; any pending validation failures still need correcting. "
    "The authoritative current build.py is included in this request. Inspect that supplied source directly; "
    "do not call read_build merely to repeat it. Do not repeat completed catalogue exploration before "
    "attempting the known corrections. Do not mechanically replay an old edit. "
    "Apply a concrete source correction now, or write a first complete create_build() if it is still empty, "
    "then validate and improve it incrementally, one region at a time. Preserve all original fidelity "
    "requirements and physical checks. Keep maximum reasoning effort, but do not postpone every code "
    "change until the entire detailed design has been planned in one response."
)


def _read(path: Path, boundary: Path) -> bytes:
    resolved = path.resolve()
    if not resolved.is_relative_to(boundary) or not resolved.is_file():
        raise ResumeRejected("恢复记录不完整或路径无效，未开始生成。")
    return resolved.read_bytes()


def _json(path: Path, boundary: Path) -> Any:
    try:
        return json.loads(_read(path, boundary))
    except (ValueError, OSError) as exc:
        raise ResumeRejected("恢复记录不完整或无法读取，未开始生成。") from exc


def _load(job_dir: Path, records_dir: Path) -> tuple[dict, list[dict], str] | None:
    marker = job_dir / "resume.json"
    if not marker.exists():
        return None
    root = records_dir.resolve()
    current = job_dir.resolve()
    if current.parent != root:
        raise ResumeRejected("恢复任务目录无效，未开始生成。")
    data = _json(marker, current)
    job_id = data.get("jobId") if isinstance(data, dict) else None
    if not isinstance(job_id, str) or not JOB_ID.fullmatch(job_id) or job_id == job_dir.name:
        raise ResumeRejected("恢复来源任务无效，未开始生成。")
    old = (root / job_id).resolve()
    if old.parent != root or not old.is_dir():
        raise ResumeRejected("恢复来源任务无效，未开始生成。")
    status = _json(old / "job.json", old)
    if status.get("status") not in {"failed", "cancelled"}:
        raise ResumeRejected("只能继续已停止且未完成的生成任务。")
    for filename in ("input.png", "reference.png", "reference.json"):
        before, after = old / filename, current / filename
        if before.exists() != after.exists():
            raise ResumeRejected("恢复任务与原始图片或参考图不一致，未开始生成。")
        if before.exists() and hashlib.sha256(_read(before, old)).digest() != hashlib.sha256(_read(after, current)).digest():
            raise ResumeRejected("恢复任务与原始图片或参考图不一致，未开始生成。")
    image_name = "reference.png" if (old / "reference.png").is_file() else "input.png"
    image = _read(current / image_name, current)
    previous_request = _json(old / "request.json", old)
    request = _json(current / "request.json", current)
    if any(previous_request.get(key) != request.get(key) for key in ("prompt", "size", "style")):
        raise ResumeRejected("恢复任务的提示词或生成选项已变化，未开始生成。")
    sdk = (old / "sdk").resolve()
    if sdk.parent != old or not sdk.is_dir():
        raise ResumeRejected("恢复任务缺少装配记录，未开始生成。")
    candidates = [p for p in sdk.iterdir() if p.is_dir() and p.resolve().parent == sdk
                  and (p / "build.py").is_file() and (p / "messages.jsonl").is_file()]
    if not candidates:
        raise ResumeRejected("恢复任务缺少装配记录，未开始生成。")
    record_dir = max(candidates, key=lambda p: p.name).resolve()
    try:
        logged = [json.loads(line) for line in _read(record_dir / "messages.jsonl", record_dir).decode().splitlines() if line]
        source = _read(record_dir / "build.py", record_dir).decode()
    except (ValueError, UnicodeError) as exc:
        raise ResumeRejected("恢复对话或装配程序无法读取，未开始生成。") from exc
    fresh_context = data.get("freshContext") is True
    checkpoint = old / "conversation-checkpoint.json"
    if fresh_context:
        # A fresh continuation needs only the genuine initial task and photo.
        # Render placeholders and incomplete tool turns in the archived history
        # are deliberately discarded, rather than restored as builder context.
        messages = copy.deepcopy(logged[:2])
        _restore_original_image(messages, image)
    elif checkpoint.exists():
        payload = _json(checkpoint, old)
        messages = payload.get("messages") if isinstance(payload, dict) else None
        # A helper's camera/critic conversation cannot replace the builder.
        if not logged or not isinstance(messages, list) or not messages or messages[0] != logged[0]:
            raise ResumeRejected("中断阶段不是可继续的建模对话，未开始生成。")
        messages = copy.deepcopy(messages)
    else:
        if any((record_dir / "out").glob("*.png")):
            raise ResumeRejected("旧记录缺少完整渲染对话，无法安全恢复。")
        messages = copy.deepcopy(logged)
        _restore_original_image(messages, image)
    _validate_messages(messages)
    info = {"jobId": job_id, "recordId": record_dir.name}
    if fresh_context:
        # This is a new builder conversation, as in the SDK's fresh critique
        # mode: no orphaned assistant/tool turns or partial reasoning remain.
        messages = copy.deepcopy(messages[:2])
        messages.append({"role": "user", "content": (
            "Continue improving this previously generated source. It is unfinished and must be validated; "
            "do not treat it as an approved model. Completed catalogue exploration is archived locally.\n\n"
            "Current build.py:\n```python\n" + source + "\n```"
        )})
        info["contextMode"] = "fresh"
    return info, messages, source


def _restore_original_image(messages: list[dict], image: bytes) -> None:
    images = [block for message in messages if isinstance(message, dict) and isinstance(message.get("content"), list)
              for block in message["content"] if isinstance(block, dict) and block.get("type") == "image_url"]
    if len(images) != 1 or not isinstance(images[0].get("image_url"), dict) or not str(images[0]["image_url"].get("url", "")).startswith("<image:"):
        raise ResumeRejected("旧记录缺少可恢复的原图对话，未开始生成。")
    images[0]["image_url"]["url"] = "data:image/png;base64," + base64.b64encode(image).decode()


def _validate_messages(messages: Any) -> None:
    if not isinstance(messages, list) or len(messages) < 2 or not all(isinstance(message, dict) for message in messages) or messages[0].get("role") != "system" or messages[1].get("role") != "user":
        raise ResumeRejected("恢复对话结构不完整，未开始生成。")
    pending: set[str] = set()
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant", "tool"}:
            raise ResumeRejected("恢复对话结构不完整，未开始生成。")
        if message["role"] == "tool":
            tool_id = message.get("tool_call_id")
            if tool_id not in pending:
                raise ResumeRejected("恢复对话的工具结果不完整，未开始生成。")
            pending.remove(tool_id)
        else:
            if pending:
                raise ResumeRejected("恢复对话的工具结果不完整，未开始生成。")
            if message["role"] == "assistant":
                calls = message.get("tool_calls") or []
                ids = [call.get("id") for call in calls if isinstance(call, dict)]
                if len(ids) != len(calls) or any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
                    raise ResumeRejected("恢复对话的工具调用无效，未开始生成。")
                pending.update(ids)
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image_url":
                    url = block.get("image_url", {}).get("url", "")
                    if not isinstance(url, str) or not url.startswith(("data:image/png;base64,", "data:image/jpeg;base64,", "data:image/webp;base64,")):
                        raise ResumeRejected("恢复对话的图片数据不完整，未开始生成。")
    if pending:
        raise ResumeRejected("恢复对话的工具结果不完整，未开始生成。")


@contextmanager
def resume_conversation(record, job_dir: Path, records_dir: Path) -> Iterator[dict | None]:
    """Seed this worker's SDK transcript and source, restoring its factory on exit."""
    loaded = _load(job_dir, records_dir)
    if loaded is None:
        yield None
        return
    info, messages, source = loaded
    import brickwright.driver.loop as loop

    record.write_build(source)
    atomic_json(job_dir / "resume-info.json", {**info, "sourceSha256": hashlib.sha256(source.encode()).hexdigest()})
    original = loop.Transcript
    followup: str | list[dict] = CONTINUE_MESSAGE
    if info.get("contextMode") != "fresh":
        followup += "\n\nAuthoritative current build.py:\n```python\n" + source + "\n```"
    feedback_path = job_dir / "resume-feedback.txt"
    if feedback_path.exists():
        feedback = _read(feedback_path, job_dir.resolve()).decode("utf-8")
        if len(feedback) > 16000:
            raise ResumeRejected("本地诊断反馈过长，未继续生成。")
        followup += "\n\nLocal diagnostic feedback (not a finalized model):\n" + feedback
    diagnostic_path = job_dir / "resume-reference.png"
    if diagnostic_path.exists():
        diagnostic = _read(diagnostic_path, job_dir.resolve())
        if not diagnostic.startswith(b"\x89PNG\r\n\x1a\n") or len(diagnostic) > 8 * 1024 * 1024:
            raise ResumeRejected("本地诊断预览无效，未继续生成。")
        followup = [{"type": "text", "text": followup}, {
            "type": "image_url", "image_url": {
                "url": "data:image/png;base64," + base64.b64encode(diagnostic).decode()
            },
        }]

    class ResumedTranscript(original):
        def __init__(self, path):
            super().__init__(path)
            self._initial_roles = ["system", "user"]

        def __enter__(self):
            result = super().__enter__()
            for message in messages:
                original.append(self, copy.deepcopy(message))
            original.append(self, {"role": "user", "content": copy.deepcopy(followup)})
            return result

        def append(self, message):
            if self._initial_roles:
                if message.get("role") != self._initial_roles.pop(0):
                    raise ResumeRejected("建模对话初始化不一致，未继续生成。")
                return
            super().append(message)

    loop.Transcript = ResumedTranscript
    try:
        yield info
    finally:
        loop.Transcript = original
