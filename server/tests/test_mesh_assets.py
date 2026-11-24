"""Mesh storage preserves geometry, legacy projects, and workbench responses."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import math
import os

import psycopg
import pytest
from fastapi.testclient import TestClient

from scripts.import_mesh_assets import import_bundled_meshes
from server.app import create_app
from server.config import Settings
from server.mesh_assets import MESH_REFERENCE, MeshStorageError, PostgresMeshStore, decode_mesh, encode_mesh
from server.parts import PartsService
from server.storage import JobStore, atomic_json

MESH = {"positions": [-0.0, 0.0, 0.0, 0.12345678912345678, 0.0, 0.0, 0.0, 1.0, 0.0], "indices": [0, 1, 2]}
PROJECT = {"parts": [{"id": "ldraw-test-a", "mesh": MESH}, {"id": "ldraw-test-b", "mesh": MESH}], "pieces": []}


def test_mesh_encoding_round_trips_double_precision_and_rejects_invalid_data():
    encoded = encode_mesh(MESH)
    decoded = decode_mesh(encoded)
    assert decoded == MESH
    assert math.copysign(1, decoded["positions"][0]) == -1
    with pytest.raises(MeshStorageError):
        decode_mesh(encoded[:-1])
    with pytest.raises(MeshStorageError):
        encode_mesh({**MESH, "indices": [0, 1, 3]})
    with pytest.raises(MeshStorageError):
        encode_mesh({**MESH, "positions": [float("nan"), *MESH["positions"][1:]]})


def test_inline_projects_survive_database_failure(tmp_path):
    def unavailable():
        raise psycopg.OperationalError("offline")

    store = JobStore(tmp_path, mesh_store=PostgresMeshStore(unavailable))
    job_id = "a" * 32
    job = store.path(job_id)
    job.mkdir()
    store.save(job_id, {"id": job_id, "status": "completed"})
    path = job / "project.json"
    atomic_json(path, PROJECT)
    original = path.read_bytes()
    store.compact_project(job_id)
    assert path.read_bytes() == original
    assert store.read(job_id)["result"] == PROJECT
    assert JobStore(tmp_path).read(job_id)["result"] == PROJECT


@pytest.fixture(scope="module")
def mesh_db():
    database_url = os.environ.get("BRICK_IT_TEST_PARTS_DATABASE_URL")
    if not database_url:
        pytest.skip("Set BRICK_IT_TEST_PARTS_DATABASE_URL to a dedicated PostgreSQL test database")
    assert import_bundled_meshes(database_url) == (90, 71)
    return database_url


def test_concurrent_projects_share_one_mesh(mesh_db):
    store = PostgresMeshStore.for_url(mesh_db)
    with ThreadPoolExecutor(max_workers=4) as pool:
        projects = list(pool.map(store.reference_project, [PROJECT] * 4))
    digest = hashlib.sha256(encode_mesh(MESH)).digest()
    for project in projects:
        assert [part[MESH_REFERENCE] for part in project["parts"]] == [digest.hex()] * 2
        assert store.restore_project(project) == PROJECT
    with psycopg.connect(mesh_db) as conn:
        assert conn.execute("SELECT count(*) FROM brick_it_catalog.mesh_assets WHERE sha256 = %s", (digest,)).fetchone() == (1,)


def test_workbench_reuses_postgres_descriptor(mesh_db, tmp_path, monkeypatch):
    service = PartsService()
    if not service.library_root:
        pytest.skip("Install the official LDraw library to test workbench mesh generation")
    expected = service.descriptor("3001")
    settings = Settings(tmp_path, parts_backend="postgres", parts_database_url=mesh_db)
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        assert client.get("/api/parts/3001").json() == expected

        def fail_if_rebuilt(*args):
            pytest.fail("Cached mesh must be read from PostgreSQL")

        monkeypatch.setattr(PartsService, "_descriptor", fail_if_rebuilt)
        assert client.get("/api/parts/ldraw-3001.dat").json() == expected
        assert client.get("/api/parts/doesnotexist").status_code == 404
