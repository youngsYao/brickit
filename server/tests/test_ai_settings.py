"""Model discovery, durable private settings and live generation handoff."""

import asyncio
import base64
import io
import json
import os
import stat
from dataclasses import replace

import httpx
import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from PIL import Image

from server.ai_settings import normalize_base_url
from server.app import create_app
from server.config import Settings
from server.jobs import JobManager

NAMES = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE", "BRICKWRIGHT_GENERATION_MODEL")


@pytest.fixture
def configured(tmp_path, monkeypatch):
    values = {"OPENAI_API_KEY": "private-old-token", "OPENAI_BASE_URL": "https://old.example/v1",
              "OPENAI_API_BASE": "https://old.example/v1", "BRICKWRIGHT_GENERATION_MODEL": "openai/old-model"}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    env_path = tmp_path / ".env"
    env_path.write_text("# Keep generation controls\nBRICK_IT_MAX_COST=0\nBRICK_IT_JOB_TIMEOUT=0\nBRICK_IT_REQUEST_TIMEOUT=0\nBRICK_IT_MAX_CRITIQUE_ROUNDS=1\nOPENAI_STREAM=true\n" + "".join(f"{key}={value}\n" for key, value in values.items()))
    settings = Settings(records_dir=tmp_path / "records", model="openai/old-model", max_cost=None,
                        timeout=None, request_timeout=None, openai_stream=True)
    app = create_app(settings, env_path=env_path)
    with TestClient(app, base_url="http://localhost:4173") as client:
        yield client, app, env_path


def mock_models(monkeypatch, handler):
    real_client = httpx.AsyncClient
    monkeypatch.setattr("server.ai_settings.httpx.AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))


def test_get_settings_never_returns_secret(configured):
    client, _, _ = configured
    response = client.get("/api/ai/settings")
    assert response.json() == {"baseUrl": "https://old.example/v1", "model": "old-model", "hasApiKey": True}
    assert "private-old-token" not in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(("given", "expected"), [
    ("https://cf.api.fan/", "https://cf.api.fan/v1"),
    ("https://proxy.example/api/v1/", "https://proxy.example/api/v1"),
    ("https://proxy.example/api", "https://proxy.example/api/v1"),
    ("http://127.0.0.1:8000/v1", "http://127.0.0.1:8000/v1"),
    ("http://[::1]:8000", "http://[::1]:8000/v1"),
])
def test_normalize_proxy_urls(given, expected):
    assert normalize_base_url(given) == expected


def test_models_fetches_ids_from_url_and_reuses_stored_key(configured, monkeypatch):
    client, _, _ = configured
    def handler(request):
        assert str(request.url) == "https://proxy.example/api/v1/models"
        assert request.headers["authorization"] == "Bearer private-old-token"
        return httpx.Response(200, json={"data": [{"id": "gpt-6-astra"}, {"id": "alpha"}, {"id": "gpt-6-astra"}, {"id": "bad id"}, {}]})
    mock_models(monkeypatch, handler)
    response = client.post("/api/ai/models", json={"baseUrl": "https://proxy.example/api/", "apiKey": ""})
    assert response.json() == {"baseUrl": "https://proxy.example/api/v1", "models": [{"id": "alpha"}, {"id": "gpt-6-astra"}]}
    assert "private-old-token" not in response.text


def test_models_uses_unsaved_credentials_without_saving_them(configured, monkeypatch):
    client, _, env_path = configured
    before = env_path.read_text()
    def handler(request):
        assert request.headers["authorization"] == "Bearer private-new-token"
        return httpx.Response(200, json={"data": [{"id": "available"}]})
    mock_models(monkeypatch, handler)
    assert client.post("/api/ai/models", json={"baseUrl": "https://new.example", "apiKey": "private-new-token"}).status_code == 200
    assert env_path.read_text() == before
    assert os.environ["OPENAI_API_KEY"] == "private-old-token"


@pytest.mark.parametrize(("status", "body", "code"), [
    (401, {"error": "private-old-token"}, "MODEL_AUTH_FAILED"),
    (500, {"error": "private-old-token"}, "MODEL_LIST_FAILED"),
    (302, {}, "MODEL_LIST_FAILED"),
    (200, {"wrong": "private-old-token"}, "INVALID_MODEL_LIST"),
    (200, {"data": []}, "EMPTY_MODEL_LIST"),
])
def test_model_failures_are_safe(configured, monkeypatch, status, body, code):
    client, _, _ = configured
    mock_models(monkeypatch, lambda request: httpx.Response(status, json=body, headers={"location": "https://elsewhere.example"}))
    response = client.post("/api/ai/models", json={"baseUrl": "https://new.example"})
    assert response.json()["error"]["code"] == code
    assert "private-old-token" not in response.text


def test_connection_failure_and_missing_key_are_safe(configured, monkeypatch):
    client, _, _ = configured
    def failed(request):
        raise httpx.ConnectError("private-old-token")
    mock_models(monkeypatch, failed)
    response = client.post("/api/ai/models", json={"baseUrl": "https://new.example"})
    assert response.json()["error"]["code"] == "MODEL_CONNECTION_FAILED"
    assert "private-old-token" not in response.text
    monkeypatch.delenv("OPENAI_API_KEY")
    assert client.post("/api/ai/models", json={"baseUrl": "https://new.example"}).json()["error"]["code"] == "API_KEY_REQUIRED"


def test_save_is_private_durable_and_immediate_without_changing_limits(configured, monkeypatch):
    client, app, env_path = configured
    response = client.post("/api/ai/settings", json={"baseUrl": "https://new.example/api/", "apiKey": "private-new-token", "model": "gpt-6-astra"})
    assert response.status_code == 200
    assert response.json() == {"baseUrl": "https://new.example/api/v1", "model": "gpt-6-astra", "hasApiKey": True}
    assert "private-new-token" not in response.text
    saved = dotenv_values(env_path)
    assert saved["OPENAI_API_KEY"] == "private-new-token"
    assert saved["OPENAI_BASE_URL"] == saved["OPENAI_API_BASE"] == "https://new.example/api/v1"
    assert saved["BRICKWRIGHT_GENERATION_MODEL"] == "openai/gpt-6-astra"
    assert saved["BRICK_IT_MAX_COST"] == saved["BRICK_IT_JOB_TIMEOUT"] == saved["BRICK_IT_REQUEST_TIMEOUT"] == "0"
    assert saved["BRICK_IT_MAX_CRITIQUE_ROUNDS"] == "1"
    assert saved["OPENAI_STREAM"] == "true"
    assert env_path.read_text().startswith("# Keep generation controls")
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert app.state.jobs.settings == replace(app.state.jobs.settings, model="openai/gpt-6-astra", max_cost=None, timeout=None, request_timeout=None)
    assert os.environ["OPENAI_API_KEY"] == "private-new-token"
    monkeypatch.setattr(Settings, "capabilities", lambda self: {"available": self.model == "openai/gpt-6-astra", "reason": ""})
    assert client.get("/ready").status_code == 200
    assert client.get("/api/generation/capabilities").json()["available"] is True


def test_saved_settings_are_read_after_restart(configured, monkeypatch):
    client, _, env_path = configured
    client.post("/api/ai/settings", json={"baseUrl": "https://new.example", "model": "new-model"})
    for name in NAMES:
        monkeypatch.delenv(name, raising=False)
    import server.config as config
    monkeypatch.setattr(config, "ROOT", env_path.parent)
    restarted = Settings.from_env()
    assert restarted.model == "openai/new-model"
    assert os.environ["OPENAI_BASE_URL"] == "https://new.example/v1"
    assert os.environ["OPENAI_API_KEY"] == "private-old-token"
    assert restarted.timeout is None and restarted.request_timeout is None and restarted.max_cost is None


def test_blank_key_preserves_saved_key(configured):
    client, _, env_path = configured
    response = client.post("/api/ai/settings", json={"baseUrl": "https://new.example", "apiKey": "", "model": "new-model"})
    assert response.status_code == 200
    assert dotenv_values(env_path)["OPENAI_API_KEY"] == "private-old-token"


def test_save_refuses_active_generation_then_next_job_uses_new_model(configured, monkeypatch):
    client, app, env_path = configured
    before = env_path.read_text()
    monkeypatch.setattr(Settings, "capabilities", lambda self: {"available": True, "reason": ""})
    used_models = []
    async def wait_for_cancel(self, job_id):
        used_models.append((self.settings.model, os.environ["OPENAI_BASE_URL"], os.environ["OPENAI_API_KEY"]))
        await asyncio.Event().wait()
    monkeypatch.setattr(JobManager, "_run", wait_for_cancel)
    image = io.BytesIO()
    Image.new("RGB", (16, 16)).save(image, "PNG")
    payload = {"image": "data:image/png;base64," + base64.b64encode(image.getvalue()).decode()}
    first = client.post("/api/generations", json=payload).json()["id"]
    response = client.post("/api/ai/settings", json={"baseUrl": "https://new.example", "apiKey": "private-new-token", "model": "new-model"})
    assert response.status_code == 409
    assert env_path.read_text() == before
    client.delete(f"/api/generations/{first}")
    assert client.post("/api/ai/settings", json={"baseUrl": "https://new.example", "apiKey": "private-new-token", "model": "new-model"}).status_code == 200
    second = client.post("/api/generations", json=payload).json()["id"]
    assert used_models[-1] == ("openai/new-model", "https://new.example/v1", "private-new-token")
    client.delete(f"/api/generations/{second}")


@pytest.mark.parametrize("bad", ["ftp://proxy.example", "not-a-url", "https://user:secret@proxy.example", "https://proxy.example/?key=secret", "https://proxy.example/#fragment", "https://proxy.example:bad", "https://proxy.example/a\nb", "https://☃.example", "https://\uffff.example", "https://[v1.foo]"])
def test_invalid_urls_cannot_write_or_send_requests(configured, bad):
    client, _, env_path = configured
    before = env_path.read_text()
    response = client.post("/api/ai/settings", json={"baseUrl": bad, "apiKey": "private-new-token", "model": "model"})
    assert response.status_code == 422
    assert "private-new-token" not in response.text
    assert env_path.read_text() == before


def test_cross_site_cannot_discover_or_save_models(configured):
    client, _, env_path = configured
    before = env_path.read_text()
    for path in ("/api/ai/models", "/api/ai/settings"):
        response = client.post(path, json={"baseUrl": "https://new.example", "apiKey": "private-new-token", "model": "model"}, headers={"origin": "https://elsewhere.example"})
        assert response.status_code == 403
    assert env_path.read_text() == before


def test_failed_persistence_does_not_change_live_configuration(configured, monkeypatch):
    client, app, _ = configured
    def fail(values):
        raise PermissionError("private-new-token")
    monkeypatch.setattr(app.state.ai_settings, "_persist", fail)
    response = client.post("/api/ai/settings", json={"baseUrl": "https://new.example", "apiKey": "private-new-token", "model": "new-model"})
    assert response.status_code == 503
    assert "private-new-token" not in response.text
    assert os.environ["OPENAI_API_KEY"] == "private-old-token"
    assert app.state.jobs.settings.model == "openai/old-model"
