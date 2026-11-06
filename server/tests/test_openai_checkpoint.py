"""Builder recovery checkpoints and worker failure details exclude credentials."""

import importlib
import json
import sys
from types import SimpleNamespace

import pytest

from server.config import Settings
from server.providers import ProviderIncompleteResponse, safe_failure_details, worker_completion_provider
from server import worker


def test_openai_checkpoint_keeps_only_full_builder_history(tmp_path, monkeypatch):
    key = "sk-offline-private-key-never-record"
    settings = Settings(records_dir=tmp_path, model="openai/gpt-6-astra", request_timeout=None)
    checkpoint = tmp_path / "conversation-checkpoint.json"
    modules = {name: importlib.import_module(f"brickwright.driver.{name}") for name in ("loop", "critique", "viewpoint")}
    result = SimpleNamespace(_hidden_params={"response_cost": .01})
    for module in modules.values():
        monkeypatch.setattr(module, "litellm_complete", lambda: lambda **kwargs: result)
    messages = [{"role": "system", "content": "builder system"},
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,cGhvdG8="}}]},
                {"role": "assistant", "content": "", "reasoning_content": "prior builder context"},
                {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,cmVuZGVy"}}]}]
    with worker_completion_provider(settings, checkpoint_path=checkpoint):
        assert modules["loop"].litellm_complete()(model=settings.model, messages=messages, api_key=key, headers={"Authorization": key}) is result
        expected = {"model": settings.model, "agent": "builder", "messages": messages}
        assert json.loads(checkpoint.read_text()) == expected
        previous = checkpoint.read_bytes()
        for name in ("critique", "viewpoint"):
            modules[name].litellm_complete()(model=settings.model, messages=[{"role": "user", "content": "helper context"}])
            assert checkpoint.read_bytes() == previous
    assert checkpoint.stat().st_mode & 0o777 == 0o600
    assert key not in checkpoint.read_text()
    assert "headers" not in checkpoint.read_text() and "api_key" not in checkpoint.read_text()


def test_checkpoint_is_written_before_failed_builder_request(tmp_path, monkeypatch):
    module = importlib.import_module("brickwright.driver.loop")
    def fail(**kwargs):
        raise RuntimeError("offline simulated interruption")
    monkeypatch.setattr(module, "litellm_complete", lambda: fail)
    checkpoint = tmp_path / "conversation-checkpoint.json"
    settings = Settings(records_dir=tmp_path, model="openai/gpt-6-astra")
    messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "latest builder turn"}]
    with pytest.raises(RuntimeError, match="offline simulated interruption"):
        with worker_completion_provider(settings, checkpoint_path=checkpoint):
            module.litellm_complete()(model=settings.model, messages=messages)
    assert json.loads(checkpoint.read_text())["messages"] == messages


def test_worker_failure_diagnostic_has_no_raw_exception_or_credentials(tmp_path, monkeypatch):
    class UnsafeProviderError(Exception):
        status_code = 401
        body = {"api_key": "sk-offline-credential", "request": "private source and image"}
    def fail(*args):
        raise UnsafeProviderError("Bearer sk-offline-credential private source and image")
    monkeypatch.setattr(worker, "run", fail)
    monkeypatch.setattr(worker.Settings, "from_env", lambda: Settings(records_dir=tmp_path))
    monkeypatch.setattr(sys, "argv", ["worker", str(tmp_path)])
    assert worker.main() == 1
    diagnostic = tmp_path / "failure-diagnostic.json"
    assert json.loads(diagnostic.read_text()) == {
        "category": "authentication", "exceptionType": "UnsafeProviderError", "statusCode": 401}
    assert diagnostic.stat().st_mode & 0o777 == 0o600
    for path in [diagnostic, tmp_path / "outcome.json"]:
        assert "sk-offline-credential" not in path.read_text()
        assert "private source and image" not in path.read_text()


def test_stream_diagnostic_reason_is_an_allowlisted_value():
    assert safe_failure_details(ProviderIncompleteResponse("missing_usage")) == {
        "category": "incomplete_response", "exceptionType": "ProviderIncompleteResponse",
        "statusCode": None, "reason": "missing_usage"}
    assert safe_failure_details(ProviderIncompleteResponse("sk-offline-credential"))["reason"] == "unknown"
