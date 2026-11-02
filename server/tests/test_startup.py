"""Startup reuses or bootstraps the library without breaking offline editing."""

from pathlib import Path

import pytest

from server import __main__ as startup
from server import config
from server.ldraw_library import LibrarySetupError


def isolate_environment(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *_args, **_kwargs: None)
    for name in ("LDRAW_LIBRARY_PATH", "BRICK_IT_AUTO_DOWNLOAD_LDRAW"):
        monkeypatch.delenv(name, raising=False)


def test_blank_library_setting_resolves_to_shared_cache(tmp_path, monkeypatch):
    isolate_environment(monkeypatch)
    expected = tmp_path / "cache" / "ldraw"
    monkeypatch.setattr(config, "DEFAULT_LDRAW_LIBRARY", expected)
    monkeypatch.setenv("LDRAW_LIBRARY_PATH", "")
    settings = config.Settings.from_env()
    assert config.os.environ["LDRAW_LIBRARY_PATH"] == str(expected)
    assert settings.auto_download_ldraw is True
    assert not expected.exists(), "Configuration must not perform an installation"


def test_explicit_relative_library_is_resolved_from_project_root(monkeypatch):
    isolate_environment(monkeypatch)
    monkeypatch.setenv("LDRAW_LIBRARY_PATH", "custom/ldraw")
    config.Settings.from_env()
    assert Path(config.os.environ["LDRAW_LIBRARY_PATH"]) == config.ROOT / "custom/ldraw"


def test_auto_download_false_is_forwarded_to_existing_only_check(tmp_path, monkeypatch):
    isolate_environment(monkeypatch)
    destination = tmp_path / "ldraw"
    monkeypatch.setenv("LDRAW_LIBRARY_PATH", str(destination))
    monkeypatch.setenv("BRICK_IT_AUTO_DOWNLOAD_LDRAW", "false")
    calls = []
    monkeypatch.setattr(startup, "ensure_ldraw_library", lambda **kwargs: calls.append(kwargs))
    startup.prepare_library(config.Settings.from_env())
    assert calls[0]["download"] is False
    assert calls[0]["library_path"] == destination


def test_failed_bootstrap_still_starts_editor_and_explains_retry(tmp_path, monkeypatch, capsys):
    isolate_environment(monkeypatch)
    monkeypatch.setenv("LDRAW_LIBRARY_PATH", str(tmp_path / "missing"))
    events = []

    def fail(**_kwargs):
        events.append("bootstrap")
        raise LibrarySetupError("Pinned download unavailable")

    monkeypatch.setattr(startup, "ensure_ldraw_library", fail)
    monkeypatch.setattr(startup.uvicorn, "run", lambda *_args, **_kwargs: events.append("serve"))
    startup.main()
    assert events == ["bootstrap", "serve"]
    assert "npm run setup:ldraw" in capsys.readouterr().err


def test_invalid_auto_download_setting_fails_before_serving(monkeypatch):
    isolate_environment(monkeypatch)
    monkeypatch.setenv("BRICK_IT_AUTO_DOWNLOAD_LDRAW", "perhaps")
    with pytest.raises(ValueError, match="BRICK_IT_AUTO_DOWNLOAD_LDRAW"):
        config.Settings.from_env()
