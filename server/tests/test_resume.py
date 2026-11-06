"""Private recovery uses real SDK source and transcript files, never a model call."""

import base64
import copy
import json

import pytest
from brickwright.config import Config
from brickwright.driver.transcript import Transcript
from brickwright.record import Record

from server.resume import CONTINUE_MESSAGE, ResumeRejected, resume_conversation
from server.storage import atomic_json


@pytest.fixture
def recovery(tmp_path):
    root = tmp_path / "records"
    old = root / ("a" * 32)
    new = root / ("b" * 32)
    old.mkdir(parents=True)
    new.mkdir()
    request = {"prompt": "Faithfully rebuild this apple", "size": "large", "style": "classic"}
    for job in (old, new):
        (job / "input.png").write_bytes(b"canonical-photo")
        atomic_json(job / "request.json", request)
    atomic_json(old / "job.json", {"id": old.name, "status": "failed"})
    atomic_json(new / "resume.json", {"jobId": old.name})
    prior = Record.create(Config(records_dir=old / "sdk"), prompt=request["prompt"], image=old / "input.png")
    prior.write_build("def create_build():\n    return 'prior-source'\n")
    image_url = "data:image/png;base64," + base64.b64encode((old / "input.png").read_bytes()).decode()
    messages = [
        {"role": "system", "content": "The genuine builder system prompt"},
        {"role": "user", "content": [{"type": "text", "text": request["prompt"]}, {"type": "image_url", "image_url": {"url": image_url}}]},
        {"role": "assistant", "content": "", "reasoning_content": "private-test-reasoning", "tool_calls": [{"id": "lookup-1", "type": "function", "function": {"name": "explore", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "lookup-1", "name": "explore", "content": "Previously gathered connector metadata"},
    ]
    with Transcript(prior.messages_path) as transcript:
        for message in messages:
            transcript.append(copy.deepcopy(message))
    current = Record.create(Config(records_dir=new / "sdk"), prompt=request["prompt"], image=new / "input.png")
    return root, old, new, prior, current, messages


def test_restores_completed_queries_source_and_original_photo_once(recovery):
    import brickwright.driver.loop as loop

    root, old, new, prior, current, messages = recovery
    old_bytes = {str(path.relative_to(old)): path.read_bytes() for path in old.rglob("*") if path.is_file()}
    original = loop.Transcript
    with resume_conversation(current, new, root) as info:
        assert info == {"jobId": old.name, "recordId": prior.id}
        assert current.read_build() == prior.read_build()
        with loop.Transcript(current.messages_path) as transcript:
            # These are run_agent's two automatic initial appends.
            transcript.append({"role": "system", "content": "fresh duplicate"})
            transcript.append({"role": "user", "content": "fresh duplicate"})
            assert transcript.messages[:-1] == messages
            assert transcript.messages[-1]["role"] == "user"
            assert transcript.messages[-1]["content"].startswith(CONTINUE_MESSAGE)
            assert prior.read_build() in transcript.messages[-1]["content"]
            transcript.append({"role": "assistant", "content": "next builder answer"})
            assert transcript.messages[-1]["content"] == "next builder answer"
    assert loop.Transcript is original
    assert old_bytes == {str(path.relative_to(old)): path.read_bytes() for path in old.rglob("*") if path.is_file()}
    assert "canonical-photo" not in current.messages_path.read_text()
    assert "<image: image/png elided>" in current.messages_path.read_text()


def test_restores_factory_when_builder_raises(recovery):
    import brickwright.driver.loop as loop

    root, _, new, _, current, _ = recovery
    original = loop.Transcript
    with pytest.raises(RuntimeError):
        with resume_conversation(current, new, root):
            raise RuntimeError("test interruption")
    assert loop.Transcript is original


def test_resume_adds_explicit_diagnostic_feedback_without_changing_original(recovery):
    import brickwright.driver.loop as loop
    from PIL import Image

    root, old, new, prior, current, messages = recovery
    (new / "resume-feedback.txt").write_text("Diagnostic copy has collisions; rebuild the supports.")
    Image.new("RGB", (2, 2), "red").save(new / "resume-reference.png")
    original_source = prior.read_build()
    with resume_conversation(current, new, root):
        with loop.Transcript(current.messages_path) as transcript:
            transcript.append({"role": "system", "content": "fresh"})
            transcript.append({"role": "user", "content": "fresh"})
            assert transcript.messages[:-1] == messages
            followup = transcript.messages[-1]["content"]
            assert "Diagnostic copy has collisions" in followup[0]["text"]
            assert followup[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert prior.read_build() == original_source


def test_fresh_resume_keeps_original_task_photo_and_latest_source_without_old_tools(recovery):
    import brickwright.driver.loop as loop

    root, old, new, prior, current, messages = recovery
    atomic_json(new / "resume.json", {"jobId": old.name, "freshContext": True})
    with resume_conversation(current, new, root) as info:
        assert info["contextMode"] == "fresh"
        with loop.Transcript(current.messages_path) as transcript:
            transcript.append({"role": "system", "content": "fresh duplicate"})
            transcript.append({"role": "user", "content": "fresh duplicate"})
            assert transcript.messages[:2] == messages[:2]
            assert prior.read_build() in transcript.messages[2]["content"]
            assert all(m["role"] in {"system", "user"} for m in transcript.messages)
            assert len(transcript.messages) == 4
    assert "Previously gathered connector metadata" in prior.messages_path.read_text()


def test_fresh_resume_restores_original_photo_after_render_without_checkpoint(recovery):
    import brickwright.driver.loop as loop

    root, old, new, prior, current, messages = recovery
    atomic_json(new / "resume.json", {"jobId": old.name, "freshContext": True})
    prior.render_path.write_bytes(b"archived-render")
    with prior.messages_path.open("a") as handle:
        # Neither a rendered image placeholder nor an unfinished tool request
        # can safely be replayed; both belong only to the archived history.
        handle.write(json.dumps({"role": "user", "content": [{"type": "image_url", "image_url": {"url": "<image: image/png elided>"}}]}) + "\n")
        handle.write(json.dumps(messages[2]) + "\n")
    original_files = {str(path.relative_to(old)): path.read_bytes() for path in old.rglob("*") if path.is_file()}
    with resume_conversation(current, new, root) as info:
        assert info["contextMode"] == "fresh"
        assert current.read_build() == prior.read_build()
        with loop.Transcript(current.messages_path) as transcript:
            assert transcript.messages[:2] == messages[:2]
            restored = transcript.messages[1]["content"][1]["image_url"]["url"]
            assert base64.b64decode(restored.split(",", 1)[1]) == (old / "input.png").read_bytes()
            assert prior.read_build() in transcript.messages[2]["content"]
            assert all(message["role"] in {"system", "user"} for message in transcript.messages)
            assert "Previously gathered connector metadata" not in json.dumps(transcript.messages)
    assert original_files == {str(path.relative_to(old)): path.read_bytes() for path in old.rglob("*") if path.is_file()}


def test_render_without_checkpoint_still_requires_explicit_fresh_context(recovery):
    root, _, new, prior, current, _ = recovery
    prior.render_path.write_bytes(b"archived-render")
    with pytest.raises(ResumeRejected, match="完整渲染对话"):
        with resume_conversation(current, new, root):
            pytest.fail("Full-history recovery must not discard missing renders")


@pytest.mark.parametrize("invalid_role", ["assistant", "tool"])
def test_fresh_resume_rejects_invalid_initial_user(recovery, invalid_role):
    root, old, new, prior, current, _ = recovery
    atomic_json(new / "resume.json", {"jobId": old.name, "freshContext": True})
    logged = [json.loads(line) for line in prior.messages_path.read_text().splitlines()]
    logged[1]["role"] = invalid_role
    prior.messages_path.write_text("\n".join(json.dumps(message) for message in logged) + "\n")
    with pytest.raises(ResumeRejected, match="对话结构"):
        with resume_conversation(current, new, root):
            pytest.fail("Fresh recovery still requires the real original user turn")


def test_no_resume_marker_keeps_normal_builder(recovery):
    import brickwright.driver.loop as loop

    root, _, new, _, current, _ = recovery
    (new / "resume.json").unlink()
    original = loop.Transcript
    source = current.read_build()
    with resume_conversation(current, new, root) as info:
        assert info is None
        assert loop.Transcript is original
    assert current.read_build() == source


@pytest.mark.parametrize("change", ["photo", "prompt", "size", "style", "running"])
def test_rejects_changed_or_active_request(recovery, change):
    root, old, new, _, current, _ = recovery
    if change == "photo":
        (new / "input.png").write_bytes(b"different-photo")
    elif change == "running":
        atomic_json(old / "job.json", {"status": "generating"})
    else:
        request = json.loads((new / "request.json").read_text())
        request[change] = "different"
        atomic_json(new / "request.json", request)
    with pytest.raises(ResumeRejected):
        with resume_conversation(current, new, root):
            pytest.fail("Invalid recovery must not reach the builder")


@pytest.mark.parametrize("job_id", ["../outside", "a" * 31, "b" * 32])
def test_rejects_invalid_or_self_source(recovery, job_id):
    root, _, new, _, current, _ = recovery
    atomic_json(new / "resume.json", {"jobId": job_id})
    with pytest.raises(ResumeRejected):
        with resume_conversation(current, new, root):
            pass


def test_rejects_job_symlink_outside_records(recovery, tmp_path):
    root, _, new, _, current, _ = recovery
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / ("c" * 32)).symlink_to(outside, target_is_directory=True)
    atomic_json(new / "resume.json", {"jobId": "c" * 32})
    with pytest.raises(ResumeRejected):
        with resume_conversation(current, new, root):
            pass


def test_full_checkpoint_preserves_render_and_reasoning(recovery):
    import brickwright.driver.loop as loop

    root, old, new, prior, current, messages = recovery
    messages = copy.deepcopy(messages)
    messages.append({"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,cmVuZGVy"}}]})
    prior.render_path.write_bytes(b"render")
    atomic_json(old / "conversation-checkpoint.json", {"agent": "builder", "model": "moonshot/kimi-k3.1-pro", "messages": messages})
    with resume_conversation(current, new, root):
        with loop.Transcript(current.messages_path) as transcript:
            assert transcript.messages[:-1] == messages
            assert transcript.messages[2]["reasoning_content"] == "private-test-reasoning"


def test_elided_render_without_checkpoint_is_rejected(recovery):
    root, _, new, prior, current, _ = recovery
    with prior.messages_path.open("a") as handle:
        handle.write(json.dumps({"role": "user", "content": [{"type": "image_url", "image_url": {"url": "<image: image/png elided>"}}]}) + "\n")
    with pytest.raises(ResumeRejected):
        with resume_conversation(current, new, root):
            pass


def test_helper_checkpoint_cannot_become_builder_history(recovery):
    root, old, new, _, current, messages = recovery
    messages = copy.deepcopy(messages)
    messages[0]["content"] = "Independent camera matching helper"
    atomic_json(old / "conversation-checkpoint.json", {"agent": "viewpoint", "messages": messages})
    with pytest.raises(ResumeRejected):
        with resume_conversation(current, new, root):
            pass


def test_pending_tool_without_result_is_rejected(recovery):
    root, _, new, prior, current, messages = recovery
    with Transcript(prior.messages_path) as transcript:
        for message in messages[:-1]:
            transcript.append(message)
    with pytest.raises(ResumeRejected):
        with resume_conversation(current, new, root):
            pass
