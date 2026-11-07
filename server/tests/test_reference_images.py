"""Image-provider input preservation, bounded output and public error handling."""

import base64
import io
import json
import socket
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from server.reference_images import ReferenceImageError, generate_references


@pytest.fixture
def image_settings(monkeypatch):
    monkeypatch.setenv("BRICK_IT_IMAGE_API_KEY", "private-image-key")
    return SimpleNamespace(image_model="gpt-image-2.6-sunburst", image_api_base="https://images.example/v1", image_timeout=300)


def image_bytes(size=(1024, 1024)):
    image = Image.new("RGB", size)
    for box, color in [((0, 0, 512, 512), "red"), ((512, 0, 1024, 512), "green"),
                       ((0, 512, 512, 1024), "blue"), ((512, 512, 1024, 1024), "yellow")]:
        image.paste(color, box)
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def image_payload(**kwargs):
    return {"data": [{"b64_json": base64.b64encode(image_bytes()).decode()}], **kwargs}


def mock_client(monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr("server.reference_images.httpx.Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))


def test_text_generation_persists_sheet_and_deterministic_crops(tmp_path, image_settings, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://images.example/v1/images/generations"
        assert request.headers["authorization"] == "Bearer private-image-key"
        data = json.loads(request.content)
        assert data["model"] == "gpt-image-2.6-sunburst"
        assert data["n"] == 1
        assert "厦大校门" in data["prompt"]
        assert "Top left: straight-on front elevation" in data["prompt"]
        return httpx.Response(200, json=image_payload(usage={"total_tokens": 99, "secret": "private-image-key", "input_tokens_details": {"image_tokens": 3}}))
    mock_client(monkeypatch, handler)
    progress = []
    result = generate_references(tmp_path, {"prompt": "厦大校门"}, image_settings, progress.append)
    assert len(calls) == 1 and len(progress) == 2
    assert result["sourceType"] == "text" and result["ready"] is True
    assert result["synthetic"] is True and result["costUsd"] is None
    assert result["usage"] == {"total_tokens": 99, "input_tokens_details": {"image_tokens": 3}}
    assert [view["id"] for view in result["views"]] == ["front", "side", "rear", "perspective"]
    assert json.loads((tmp_path / "reference.json").read_text()) == result
    expected = {"front": (255, 0, 0), "side": (0, 128, 0), "rear": (0, 0, 255), "perspective": (255, 255, 0)}
    for view in result["views"]:
        with Image.open(tmp_path / view["file"]) as image:
            assert image.size == (512, 512)
            assert image.getpixel((20, 20)) == expected[view["id"]]
    assert "private-image-key" not in (tmp_path / "reference.json").read_text()


def test_uploaded_image_uses_multipart_edits_without_dropping_original(tmp_path, image_settings, monkeypatch):
    raw = image_bytes()
    (tmp_path / "input.png").write_bytes(raw)
    def handler(request):
        assert str(request.url).endswith("/images/edits")
        assert request.headers["content-type"].startswith("multipart/form-data")
        assert b'name="image"; filename="input.png"' in request.content
        assert raw in request.content
        assert b'name="model"' in request.content
        assert b"authoritative identity and geometry reference" in request.content
        return httpx.Response(200, json=image_payload())
    mock_client(monkeypatch, handler)
    result = generate_references(tmp_path, {"prompt": ""}, image_settings)
    assert result["sourceType"] == "image"
    assert (tmp_path / "input.png").read_bytes() == raw


@pytest.mark.parametrize(("status", "code"), [(401, "REFERENCE_AUTH_FAILED"), (403, "REFERENCE_AUTH_FAILED"),
    (429, "REFERENCE_RATE_LIMITED"), (400, "REFERENCE_REQUEST_REJECTED"), (404, "REFERENCE_REQUEST_REJECTED"),
    (500, "REFERENCE_PROVIDER_FAILED"), (302, "REFERENCE_PROVIDER_FAILED")])
def test_provider_failures_are_redacted_and_never_retried(tmp_path, image_settings, monkeypatch, status, code):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": "private-image-key"}, headers={"location": "https://other.example"})
    mock_client(monkeypatch, handler)
    with pytest.raises(ReferenceImageError) as caught:
        generate_references(tmp_path, {"prompt": "gate"}, image_settings)
    assert caught.value.code == code
    assert "private-image-key" not in str(caught.value)
    assert len(calls) == 1
    assert not (tmp_path / "reference.json").exists()
    assert not (tmp_path / "reference.png").exists()


@pytest.mark.parametrize("payload", [{}, {"data": []}, {"data": [{}]}, {"data": [{"b64_json": "invalid"}]},
    {"data": [{"b64_json": base64.b64encode(b"not an image").decode()}]}, []])
def test_invalid_provider_outputs_do_not_publish_references(tmp_path, image_settings, monkeypatch, payload):
    mock_client(monkeypatch, lambda request: httpx.Response(200, json=payload))
    with pytest.raises(ReferenceImageError) as caught:
        generate_references(tmp_path, {"prompt": "gate"}, image_settings)
    assert caught.value.code == "REFERENCE_INVALID_OUTPUT"
    assert not (tmp_path / "reference.json").exists()
    assert not (tmp_path / "reference.png").exists()


@pytest.mark.parametrize(("error", "code"), [(httpx.ReadTimeout("private-image-key"), "REFERENCE_TIMEOUT"),
                        (httpx.ConnectError("private-image-key"), "REFERENCE_CONNECTION_FAILED")])
def test_timeout_and_connection_errors_are_redacted(tmp_path, image_settings, monkeypatch, error, code):
    def handler(request):
        raise error
    mock_client(monkeypatch, handler)
    with pytest.raises(ReferenceImageError) as caught:
        generate_references(tmp_path, {"prompt": "gate"}, image_settings)
    assert caught.value.code == code
    assert "private-image-key" not in str(caught.value)


def test_public_url_download_never_receives_provider_authorization(tmp_path, image_settings, monkeypatch):
    monkeypatch.setattr("server.reference_images.socket.getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))])
    requests = []
    def handler(request):
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"data": [{"url": "https://cdn.example/image.png?signature=token"}]})
        assert request.headers.get("authorization") is None
        return httpx.Response(200, content=image_bytes())
    mock_client(monkeypatch, handler)
    result = generate_references(tmp_path, {"prompt": "gate"}, image_settings)
    assert result["ready"] is True and len(requests) == 2
    assert "signature" not in (tmp_path / "reference.json").read_text()


@pytest.mark.parametrize("url", ["http://127.0.0.1/private", "http://169.254.169.254/metadata", "file:///etc/passwd", "https://secret:password@cdn.example/test"])
def test_unsafe_output_urls_are_rejected(tmp_path, image_settings, monkeypatch, url):
    mock_client(monkeypatch, lambda request: httpx.Response(200, json={"data": [{"url": url}]}))
    with pytest.raises(ReferenceImageError) as caught:
        generate_references(tmp_path, {"prompt": "gate"}, image_settings)
    assert caught.value.code == "REFERENCE_INVALID_OUTPUT"


def test_missing_image_credentials_do_not_reuse_modelling_key(tmp_path, image_settings, monkeypatch):
    monkeypatch.delenv("BRICK_IT_IMAGE_API_KEY")
    monkeypatch.setenv("OPENAI_API_KEY", "modelling-only-key")
    with pytest.raises(ReferenceImageError) as caught:
        generate_references(tmp_path, {"prompt": "gate"}, image_settings)
    assert caught.value.code == "REFERENCE_CONFIG_REQUIRED"
