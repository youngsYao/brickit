"""The reference stage gates modelling and remains reviewable after failure."""

import asyncio
import base64
import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from server.app import create_app
from server.config import Settings
from server.jobs import JobManager, REFERENCE_ARTIFACTS, ServiceError
from server.reference_images import ReferenceImageError
from server.schemas import GenerationRequest
from server.storage import atomic_json
from server import worker


def photo_data():
    stream = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(stream, "PNG")
    return "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()


def put_references(path):
    for name in REFERENCE_ARTIFACTS - {"reference.json"}:
        Image.new("RGB", (32, 32), "green").save(path / name, "PNG")
    metadata = {"model": "unit-test-image-model", "synthetic": True}
    atomic_json(path / "reference.json", metadata)
    return metadata


@pytest.mark.parametrize("image", [None, "photo"])
def test_accepts_text_or_photo_but_rejects_empty_input(image):
    kwargs = {"prompt": "厦门大学校门"} if image is None else {"image": photo_data()}
    assert GenerationRequest(**kwargs)
    for empty in ({}, {"prompt": "  \n "}, {"image": None, "prompt": ""}):
        with pytest.raises(ValidationError):
            GenerationRequest(**empty)


def test_text_only_cannot_enter_legacy_photo_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(Settings, "capabilities", lambda self: {"available": True})
    manager = JobManager(Settings(records_dir=tmp_path))

    async def scenario():
        with pytest.raises(ServiceError) as failure:
            await manager.create(GenerationRequest(prompt="厦大校门"))
        assert failure.value.code == "REFERENCE_MODEL_REQUIRED"
        assert not manager.tasks
        assert list(tmp_path.iterdir()) == []

    asyncio.run(scenario())


@pytest.mark.parametrize("with_photo", [False, True])
def test_submission_preserves_source_and_schedules_reference_worker(tmp_path, monkeypatch, with_photo):
    monkeypatch.setattr(Settings, "capabilities", lambda self: {"available": True})
    settings = Settings(records_dir=tmp_path, image_model="unit-test-image-model")
    manager = JobManager(settings)

    async def suspended_worker(job_id):
        await asyncio.Future()

    monkeypatch.setattr(manager, "_run", suspended_worker)

    async def scenario():
        request = GenerationRequest(prompt="厦大校门", image=photo_data() if with_photo else None)
        job = await manager.create(request)
        path = manager.store.path(job["id"])
        assert job["status"] == "queued" and "参考图" in job["message"]
        persisted = json.loads((path / "request.json").read_text())
        assert persisted["prompt"] == "厦大校门" and "image" not in persisted
        assert (path / "input.png").exists() is with_photo
        if with_photo:
            assert (path / "input.png").read_bytes() == request.decode_image(settings.max_image_bytes)
        await manager.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["referencing", "generating", "failed", "cancelled"])
def test_reference_artifacts_are_reviewable_before_or_after_model_failure(tmp_path, status):
    manager = JobManager(Settings(records_dir=tmp_path))
    with TestClient(create_app(manager.settings, manager=manager), base_url="http://localhost:4173") as client:
        path = manager.store.path("a" * 32)
        path.mkdir()
        manager.store.save(path.name, {"id": path.name, "status": status, "events": []})
        put_references(path)
        response = client.get(f"/api/generations/{path.name}")
        assert response.status_code == 200
        images = response.json()["referenceImages"]
        assert images["ready"] and images["synthetic"]
        assert {view["id"] for view in images["views"]} == {"front", "side", "rear", "perspective"}
        sheet = client.get(images["sheetUrl"])
        assert sheet.status_code == 200 and sheet.headers["content-type"] == "image/png"
        assert Image.open(io.BytesIO(sheet.content)).size == (32, 32)
        for view in images["views"]:
            assert client.get(view["url"]).status_code == 200
        # Builder artifacts and private user images retain their existing boundary.
        assert client.get(f"/api/generations/{path.name}/artifacts/build.py").status_code == 404
        assert client.get(f"/api/generations/{path.name}/artifacts/input.png").status_code == 404


def test_partial_reference_files_are_not_published(tmp_path):
    manager = JobManager(Settings(records_dir=tmp_path))
    path = manager.store.path("b" * 32)
    path.mkdir()
    manager.store.save(path.name, {"id": path.name, "status": "referencing", "events": []})
    (path / "reference.png").write_bytes(b"incomplete")
    assert "referenceImages" not in manager.get(path.name)
    with pytest.raises(ServiceError):
        manager.artifact(path.name, "reference.png")


def test_image_generation_finishes_before_builder_sees_reference(tmp_path, monkeypatch):
    settings = Settings(records_dir=tmp_path, image_model="unit-test-image-model")
    request = {"prompt": "厦大校门", "size": "small", "style": "classic"}
    atomic_json(tmp_path / "request.json", request)
    (tmp_path / "input.png").write_bytes(b"original uploaded photo")
    calls = []

    def generate(path, supplied, config):
        calls.append("reference")
        assert supplied == request and config is settings
        return put_references(path)

    class StopBeforePaidBuilder(Exception):
        pass

    def create_record(config, *, prompt, image):
        calls.append("builder")
        assert image == tmp_path / "reference.png"
        assert "ONE connected" in prompt and "2x2" in prompt
        assert "厦大校门" in prompt
        assert image.is_file()
        raise StopBeforePaidBuilder

    monkeypatch.setattr(worker, "generate_references", generate)
    monkeypatch.setattr("brickwright.record.Record.create", create_record)
    with pytest.raises(StopBeforePaidBuilder):
        worker.run(tmp_path, settings)
    assert calls == ["reference", "builder"]
    assert (tmp_path / "input.png").read_bytes() == b"original uploaded photo"


def test_failed_image_stage_never_starts_builder(tmp_path, monkeypatch):
    settings = Settings(records_dir=tmp_path, image_model="unit-test-image-model")
    atomic_json(tmp_path / "request.json", {"prompt": "厦大校门", "size": "small", "style": "classic"})

    def fail(*args, **kwargs):
        raise ReferenceImageError("REFERENCE_TIMEOUT", "生图请求超时")

    def forbidden_builder(*args, **kwargs):
        pytest.fail("A failed reference request must not start paid modelling")

    monkeypatch.setattr(worker, "generate_references", fail)
    monkeypatch.setattr("brickwright.record.Record.create", forbidden_builder)
    with pytest.raises(ReferenceImageError, match="生图请求超时"):
        worker.run(tmp_path, settings)
    assert not (tmp_path / "sdk").exists()


def test_resume_reuses_reference_without_second_image_charge(tmp_path, monkeypatch):
    settings = Settings(records_dir=tmp_path, image_model="new-model-after-original-run")
    metadata = put_references(tmp_path)
    original = (tmp_path / "reference.png").read_bytes()
    atomic_json(tmp_path / "resume.json", {"jobId": "a" * 32})

    def forbidden_generation(*args, **kwargs):
        pytest.fail("Resuming a builder must not regenerate its reference")

    monkeypatch.setattr(worker, "generate_references", forbidden_generation)
    image, restored = worker.prepare_reference(tmp_path, {"prompt": "厦大校门"}, settings)
    assert restored == metadata
    assert image.read_bytes() == original


def test_text_resume_restores_reference_as_original_builder_image(tmp_path):
    from brickwright.config import Config
    from brickwright.driver.transcript import Transcript
    from brickwright.record import Record
    import brickwright.driver.loop as loop
    from server.resume import resume_conversation

    root = tmp_path / "records"
    old, new = root / ("c" * 32), root / ("d" * 32)
    old.mkdir(parents=True)
    new.mkdir()
    request = {"prompt": "厦大校门", "size": "small", "style": "classic"}
    for path in (old, new):
        put_references(path)
        atomic_json(path / "request.json", request)
    atomic_json(old / "job.json", {"status": "failed"})
    atomic_json(new / "resume.json", {"jobId": old.name, "freshContext": True})
    prior = Record.create(Config(records_dir=old / "sdk"), prompt=request["prompt"], image=old / "reference.png")
    prior.write_build("def create_build():\n    return None\n")
    initial_url = "data:image/png;base64," + base64.b64encode((old / "reference.png").read_bytes()).decode()
    with Transcript(prior.messages_path) as transcript:
        transcript.append({"role": "system", "content": "Builder"})
        transcript.append({"role": "user", "content": [{"type": "image_url", "image_url": {"url": initial_url}}]})
    current = Record.create(Config(records_dir=new / "sdk"), prompt=request["prompt"], image=new / "reference.png")
    with resume_conversation(current, new, root):
        with loop.Transcript(current.messages_path) as transcript:
            actual_url = transcript.messages[1]["content"][0]["image_url"]["url"]
            assert actual_url == initial_url
    assert not (old / "input.png").exists() and not (new / "input.png").exists()
