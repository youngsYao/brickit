"""HTTP contract, origin boundary, image decoding, and actual child termination."""

import asyncio
import base64
import io
import json
import os
import sys
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from server.app import create_app
from server.config import Settings
from server.jobs import JobManager
from server.schemas import GenerationRequest
from server.storage import atomic_json


def image_data():
    image = io.BytesIO()
    Image.new("RGB", (16, 16), "red").save(image, "PNG")
    return "data:image/png;base64," + base64.b64encode(image.getvalue()).decode()


def payload():
    return {"image": image_data(), "prompt": "a red cube", "size": "small", "style": "classic"}


@pytest.fixture
def settings(tmp_path):
    return Settings(records_dir=tmp_path / "records", model="")


def test_missing_config_keeps_editor_available_and_generation_honest(settings):
    with TestClient(create_app(settings), base_url="http://localhost:4173") as client:
        capability = client.get("/api/generation/capabilities").json()
        assert capability["available"] is False
        assert "右上角的 AI 模型设置" in capability["reason"]
        assert client.get("/").status_code == 200
        assert client.get("/assets/vendor/three.module.js").status_code == 200
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/ready").status_code == 503
        response = client.post("/api/generations", json=payload())
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "GENERATION_UNAVAILABLE"


def test_cross_origin_and_invalid_request_do_not_start_paid_job(settings):
    with TestClient(create_app(settings), base_url="http://localhost:4173") as client:
        for origin in ("https://evil.example", "http://localhost:4174", "null", "http://localhost:bad"):
            assert client.post("/api/generations", json=payload(), headers={"origin": origin}).status_code == 403
        assert client.post("/api/generations", content="{}", headers={"content-type": "text/plain"}).status_code == 415
        response = client.post("/api/generations", json={**payload(), "size": "x"})
        assert response.status_code == 422 and "image" not in json.dumps(response.json())
        assert client.get("/api/generations/../secrets").status_code == 404
        assert client.get("/api/generations/not-a-job").status_code == 404
        assert client.get("/api/unknown").status_code == 404
        assert client.get("/", headers={"host": "evil.example"}).status_code == 403
        assert list(settings.records_dir.iterdir()) == []


def test_body_limit_and_invalid_image_are_bounded(settings, monkeypatch):
    monkeypatch.setattr(Settings, "capabilities", lambda self: {"available": True, "reason": "ready"})
    with TestClient(create_app(replace(settings, max_body_bytes=1024)), base_url="http://localhost:4173") as client:
        assert client.post("/api/generations", json={**payload(), "prompt": "x" * 3000}).status_code == 413
        response = client.post("/api/generations", json={**payload(), "image": "data:image/png;base64,SGVsbG8gd29ybGQ="})
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "INVALID_IMAGE"


def test_png_is_actually_decoded_and_bad_formats_rejected():
    raw = GenerationRequest(**payload()).decode_image(1024)
    assert Image.open(io.BytesIO(raw)).format == "PNG"
    with pytest.raises(ValueError):
        GenerationRequest(**{**payload(), "image": "data:image/png;base64," + base64.b64encode(b"not image").decode()}).decode_image(1024)
    with pytest.raises(ValueError):
        GenerationRequest(**payload()).decode_image(8)


def mock_local_child(monkeypatch, code):
    """Replace only the paid-worker command with a real local child process."""
    real_create = asyncio.create_subprocess_exec
    children = []

    async def create(*args, **kwargs):
        child = await real_create(sys.executable, "-c", code, str(args[-1]), **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr("server.jobs.asyncio.create_subprocess_exec", create)
    return children


def test_cancel_stops_running_child_and_does_not_publish_artifacts(settings, monkeypatch):
    monkeypatch.setattr(Settings, "capabilities", lambda self: {"available": True, "reason": "ready"})
    children = mock_local_child(monkeypatch, "import time;time.sleep(120)")

    async def scenario():
        manager = JobManager(settings)
        await manager.start()
        record = await manager.create(GenerationRequest(**payload()))
        for _ in range(100):
            if children:
                break
            await asyncio.sleep(.01)
        assert children
        cancelled = await manager.cancel(record["id"])
        assert cancelled["status"] == "cancelled"
        assert children[0].returncode is not None
        assert record["id"] not in manager.processes
        with pytest.raises(Exception) as error:
            manager.artifact(record["id"], "build.py")
        assert error.value.status == 404
        await manager.close()

    asyncio.run(scenario())


def test_timeout_terminates_child_and_records_failure(settings, monkeypatch):
    monkeypatch.setattr(Settings, "capabilities", lambda self: {"available": True, "reason": "ready"})
    children = mock_local_child(monkeypatch, "import time;time.sleep(120)")

    async def scenario():
        manager = JobManager(replace(settings, timeout=.1))
        await manager.start()
        record = await manager.create(GenerationRequest(**payload()))
        task = manager.tasks[record["id"]]
        await task
        final = manager.get(record["id"])
        assert final["status"] == "failed" and final["error"]["code"] == "TIMEOUT"
        assert children[0].returncode is not None
        await manager.close()

    asyncio.run(scenario())


def test_cancel_during_spawn_does_not_orphan_worker(settings, monkeypatch):
    monkeypatch.setattr(Settings, "capabilities", lambda self: {"available": True, "reason": "ready"})
    real_create = asyncio.create_subprocess_exec
    children = []

    async def delayed_create(*args, **kwargs):
        process = await real_create(sys.executable, "-c", "import time;time.sleep(120)", **kwargs)
        children.append(process)
        await asyncio.sleep(.1)  # Simulate a spawned process awaiting its handle.
        return process

    monkeypatch.setattr("server.jobs.asyncio.create_subprocess_exec", delayed_create)

    async def scenario():
        manager = JobManager(settings)
        await manager.start()
        record = await manager.create(GenerationRequest(**payload()))
        while not children:
            await asyncio.sleep(.005)
        result = await manager.cancel(record["id"])
        assert result["status"] == "cancelled"
        assert children[0].returncode is not None
        await manager.close()

    asyncio.run(scenario())


def test_restart_does_not_resume_or_charge_interrupted_work(settings):
    manager = JobManager(settings)
    job_id = "a" * 32
    path = manager.store.path(job_id)
    path.mkdir()
    atomic_json(path / "job.json", {"id": job_id, "status": "generating", "events": []})
    asyncio.run(manager.start())
    assert manager.get(job_id)["error"]["code"] == "INTERRUPTED"
    assert manager.tasks == {}


def test_completed_job_delivers_real_sdk_mesh_and_allowlisted_artifacts(settings, monkeypatch):
    # This local child replaces the paid model call, not the physics/geometry.
    # It really creates and exports two attached catalog bricks with BrickLayer.
    monkeypatch.setattr(Settings, "capabilities", lambda self: {"available": True, "reason": "ready"})
    code = '''
import json,sys
from pathlib import Path
import bricklayer as bl
from server.exporter import export_project
from server.storage import atomic_json
path=Path(sys.argv[1])
build=bl.LegoBuild()
build.add(bl.Element("3001",bl.Color.RED))
build.add(bl.Element("3001",bl.Color.BLUE),(0,0,1))
project=export_project(build,job_id=path.name,prompt="local SDK test",size="small",style="classic",source_image="",max_parts=80)
out=path/"artifacts"
out.mkdir()
bl.write_ldraw(build,out/"build.ldr")
(out/"build.py").write_text("# local SDK fixture, no model call")
(out/"build.png").write_bytes(b"local preview fixture")
atomic_json(out/"report.json",project["generation"]["validation"])
atomic_json(path/"project.json",project)
atomic_json(path/"outcome.json",{"ok":True})
'''
    children = mock_local_child(monkeypatch, code)
    with TestClient(create_app(settings), base_url="http://localhost:4173") as client:
        response = client.post("/api/generations", json=payload(), headers={"origin": "http://localhost:4173"})
        assert response.status_code == 202
        job_id = response.json()["id"]
        for _ in range(200):
            response = client.get(f"/api/generations/{job_id}")
            if response.json()["status"] in {"completed", "failed"}:
                break
            time.sleep(.02)
        job = response.json()
        assert job["status"] == "completed", job
        assert job["result"]["generation"]["validation"]["ok"]
        assert len(job["result"]["pieces"]) == 2
        assert len(job["result"]["parts"][0]["mesh"]["positions"]) > 100
        ldraw = client.get(f"/api/generations/{job_id}/artifacts/build.ldr")
        assert ldraw.status_code == 200 and ldraw.text.count("3001.dat") == 2
        assert "attachment" in ldraw.headers["content-disposition"]
        assert client.get(f"/api/generations/{job_id}/artifacts/meta.json").status_code == 404
        assert client.get(f"/api/generations/{job_id}/artifacts/report.json").json()["components"] == 1
    assert children[0].returncode == 0
