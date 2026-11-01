"""Exercise downloaded official files and real SDK catalogue/mesh data, offline."""

from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.jobs import ServiceError
from server.parts import DEFAULT_LIBRARY, PartsService, create_router


@pytest.fixture(scope="module")
def service():
    # The SDK tests remain usable on machines that have not downloaded LDraw.
    if not (DEFAULT_LIBRARY / "parts" / "3001.dat").is_file():
        pytest.skip("Install the official LDraw library to run catalogue integration tests")
    return PartsService(DEFAULT_LIBRARY)


@pytest.fixture
def client(service):
    app = FastAPI()
    app.include_router(create_router(service))
    with TestClient(app) as client:
        yield client


def test_real_catalogue_paginates_without_ai_credentials(client, monkeypatch):
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "BRICKWRIGHT_GENERATION_MODEL"):
        monkeypatch.delenv(name, raising=False)
    first = client.get("/api/parts?limit=24").json()
    second = client.get("/api/parts?offset=24&limit=24").json()
    assert first["available"] is True
    assert first["libraryTotal"] > 20000
    assert 17000 < first["total"] <= first["libraryTotal"]
    assert first["hasMore"] is True and first["offset"] == 0
    assert len(first["items"]) == len(second["items"]) == 24
    assert not set(p["id"] for p in first["items"]) & set(p["id"] for p in second["items"])
    assert sum(c["count"] for c in first["categories"]) == first["total"]
    assert all(c["id"] == c["id"].lower() for c in first["categories"])
    assert client.get("/api/parts?limit=61").status_code == 422
    assert client.get("/api/parts?offset=-1").status_code == 422
    assert client.get("/api/parts?offset=99999").json()["items"] == []


def test_exact_part_id_keyword_and_category_search(client):
    for query in ("3001", "ldraw-3001", "3001.dat"):
        response = client.get("/api/parts", params={"q": query}).json()
        assert response["items"][0]["ldrawId"] == "3001"
    bricks = client.get("/api/parts", params={"q": "brick 2x4", "category": "brick"}).json()
    assert bricks["total"] > 1
    assert all(item["category"] == "brick" for item in bricks["items"])
    plates = client.get("/api/parts", params={"category": "plate", "limit": 60}).json()
    assert all(item["category"] == "plate" for item in plates["items"])
    assert client.get("/api/parts", params={"q": "标准砖"}).json()["total"] > 1
    assert client.get("/api/parts", params={"q": "不存在的零件"}).json()["total"] == 0
    assert client.get("/api/parts", params={"category": "no-such-category"}).json()["total"] == 0


def test_descriptor_uses_real_centered_mesh_with_matching_dimensions(client):
    part = client.get("/api/parts/3001").json()
    assert part["id"] == "ldraw-3001" and part["ldrawId"] == "3001"
    assert part["shape"] == "mesh" and part["provenance"]["geometry"] == "inset_collision_mesh"
    assert len(part["colors"]) >= 8
    points = np.array(part["mesh"]["positions"]).reshape(-1, 3)
    indices = np.array(part["mesh"]["indices"])
    np.testing.assert_allclose(points.min(axis=0) + points.max(axis=0), 0, atol=1e-10)
    np.testing.assert_allclose(np.ptp(points, axis=0), [part["w"] * .58, part["h"], part["d"] * .58], atol=1e-10)
    assert indices.max() < len(points) and indices.min() >= 0
    assert client.get("/api/parts/doesnotexist").status_code == 404


def test_descriptor_cache_does_not_share_mutable_client_state(service):
    first = service.descriptor("3001")
    first["mesh"]["positions"][0] = 999999
    first["colors"].clear()
    second = service.descriptor("3001")
    assert second["mesh"]["positions"][0] != 999999
    assert second["colors"]


@pytest.mark.parametrize("part_id", ["32062", "11203"])
def test_side_connector_parts_are_grounded_before_mesh_conversion(service, part_id):
    part = service.descriptor(part_id)
    assert part["ldrawId"] == part_id
    assert part["shape"] == "mesh" and part["mesh"]["positions"]


def test_official_part_color_and_recursive_library_files(client, service):
    part = client.get("/api/ldraw/parts/3001.dat")
    assert part.status_code == 200 and "Brick  2 x  4" in part.text
    assert part.content == (DEFAULT_LIBRARY / "parts" / "3001.dat").read_bytes()
    color = client.get("/api/ldraw/LDConfig.ldr")
    assert color.status_code == 200 and "!COLOUR" in color.text
    for folder in ("parts/s", "p/48"):
        path = next((DEFAULT_LIBRARY / folder).glob("*.dat"))
        response = client.get("/api/ldraw/" + path.relative_to(DEFAULT_LIBRARY).as_posix())
        assert response.status_code == 200 and response.content == path.read_bytes()


@pytest.mark.parametrize("requested", [
    "../.env", "parts/../../.env", "/etc/passwd", "parts\\..\\secret.dat",
    "parts/%2e%2e/private.dat", "parts//3001.dat", "parts/./3001.dat",
    "parts/3001.txt", "records/build.ldr", "LDConfig.ldr/other.dat", "file:/parts/3001.dat",
])
def test_path_traversal_and_non_library_files_are_rejected(service, requested):
    with pytest.raises(ServiceError) as error:
        service.file(requested)
    assert error.value.status == 404


def test_resolved_symlink_cannot_escape_library(tmp_path):
    library = tmp_path / "ldraw"
    (library / "parts").mkdir(parents=True)
    (library / "p").mkdir()
    (library / "LDConfig.ldr").write_text("0 color config")
    private = tmp_path / "private.dat"
    private.write_text("private")
    (library / "parts" / "escape.dat").symlink_to(private)
    service = PartsService(library)
    with pytest.raises(ServiceError):
        service.file("parts/escape.dat")


def test_missing_library_has_clear_state_and_never_returns_fake_parts(tmp_path):
    service = PartsService(tmp_path / "not-installed")
    response = service.list()
    assert response["available"] is False and response["items"] == [] and response["total"] == 0
    assert "LDRAW_LIBRARY_PATH" in response["reason"]
    with pytest.raises(ServiceError) as error:
        service.descriptor("3001")
    assert error.value.status == 503
