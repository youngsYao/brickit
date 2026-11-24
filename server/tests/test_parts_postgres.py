"""Exercise the catalogue switch against a dedicated PostgreSQL test database."""

import os
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from scripts.import_parts_catalog import import_catalogue
from server.app import create_app
from server.config import Settings
from server.parts import PartsService


def test_catalogue_settings(tmp_path, monkeypatch):
    monkeypatch.setattr("server.config.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("BRICK_IT_RECORDS_DIR", str(tmp_path))
    monkeypatch.setenv("LDRAW_LIBRARY_PATH", str(tmp_path / "ldraw"))
    monkeypatch.delenv("BRICK_IT_PARTS_BACKEND", raising=False)
    monkeypatch.delenv("BRICK_IT_PARTS_DATABASE_URL", raising=False)
    assert Settings.from_env().parts_backend == "local"

    monkeypatch.setenv("BRICK_IT_PARTS_BACKEND", "invalid")
    with pytest.raises(ValueError, match="BRICK_IT_PARTS_BACKEND"):
        Settings.from_env()

    monkeypatch.setenv("BRICK_IT_PARTS_BACKEND", "postgres")
    with pytest.raises(ValueError, match="BRICK_IT_PARTS_DATABASE_URL"):
        Settings.from_env()


@pytest.fixture(scope="module")
def imported_catalogue():
    database_url = os.environ.get("BRICK_IT_TEST_PARTS_DATABASE_URL")
    if not database_url:
        pytest.skip("Set BRICK_IT_TEST_PARTS_DATABASE_URL to a dedicated PostgreSQL test database")
    library = PartsService()
    if not library.library_root:
        pytest.skip("Install the official LDraw library for the catalogue import test")
    first = import_catalogue(database_url, library)
    assert import_catalogue(database_url, library) == first
    assert first[1:] == (len(library.catalogue), len(library.library_ids))
    return database_url, library


def test_postgres_api_and_switch_back(imported_catalogue, tmp_path):
    database_url, library = imported_catalogue
    settings = Settings(tmp_path / "records", parts_backend="postgres", parts_database_url=database_url)
    browse_queries = (
        {}, {"offset": 24}, {"category": " Brick ", "limit": 10}, {"category": "no-such-category"},
        {"q": "3001"}, {"q": "ldraw-3001"}, {"q": "3001.dat"}, {"q": "3001", "offset": 99999},
        {"q": "不存在的零件"},
    )
    with TestClient(create_app(settings), base_url="http://localhost") as client:
        for params in browse_queries:
            response = client.get("/api/parts", params=params)
            assert response.status_code == 200
            assert response.json() == library.list(**params)

        for params in ({"q": "brick 2x4", "category": "brick"}, {"q": "标准砖"}):
            page = client.get("/api/parts", params=params).json()
            old = library.list(**params)
            assert page["total"] == old["total"]
            assert page["items"][0] == old["items"][0]
            assert page["categories"] == old["categories"]

        assert client.get("/api/parts", params={"limit": 61}).status_code == 422
        assert client.get("/api/parts/3001").json() == library.descriptor("3001")
        assert client.get("/api/ldraw/parts/3001.dat").content == library.file("parts/3001.dat").read_bytes()

    # A saved database URL must have no effect after switching back to local mode.
    local = replace(settings, parts_backend="local", parts_database_url="invalid connection string")
    with TestClient(create_app(local), base_url="http://localhost") as client:
        assert client.get("/api/parts").json() == library.list()
