"""The builder retains its sheet and critic receives one camera-matchable crop."""

import pickle

import pytest
from PIL import Image

from brickwright.config import Config
from brickwright.record import Record
import brickwright.driver.loop as loop
from server.reference_context import reference_critique
from server.reference_images import ReferenceImageError


@pytest.fixture
def reference_record(tmp_path):
    Image.new("RGB", (1024, 1024), "red").save(tmp_path / "reference.png")
    Image.new("RGB", (512, 512), "blue").save(tmp_path / "reference-perspective.png")
    (tmp_path / "reference.json").write_text('{"ready":true}')
    config = Config(records_dir=tmp_path / "sdk")
    record = Record.create(config, prompt="Build one object using this four-view sheet", image=tmp_path / "reference.png")
    return tmp_path, record, config


def test_only_critic_receives_crop_and_nonpersistent_prompt(reference_record, monkeypatch):
    job_dir, record, config = reference_record
    prompt_image = record.prompt_image_path
    before = record.meta_path.read_bytes()
    seen = []
    callback = lambda response: None
    def original(review_record, review_config, **kwargs):
        assert review_record.prompt_image_path == job_dir / "reference-perspective.png"
        assert "ONLY the bottom-right" in review_record.read_meta().prompt
        assert review_record.build_path == record.build_path
        assert review_record.read_build() == record.read_build()
        assert kwargs["on_response"] is callback
        assert review_config is config
        seen.append(review_record)
        return "verdict"
    monkeypatch.setattr(loop, "critique", original)
    with reference_critique(job_dir):
        assert record.prompt_image_path == prompt_image
        assert loop.critique(record, config, on_response=callback) == "verdict"
        assert record.meta_path.read_bytes() == before
    assert loop.critique is original
    assert len(seen) == 1 and record.meta_path.read_bytes() == before


def test_critic_proxy_survives_spawn_pickling_and_delegates_writes(reference_record, monkeypatch):
    job_dir, record, config = reference_record
    def original(review_record, review_config, **kwargs):
        restored = pickle.loads(pickle.dumps(review_record))
        assert restored.prompt_image_path == job_dir / "reference-perspective.png"
        assert restored.build_path == record.build_path
        restored.update_meta(turns=42)
        assert "Visual review context" in restored.read_meta().prompt
    monkeypatch.setattr(loop, "critique", original)
    with reference_critique(job_dir):
        loop.critique(record, config)
    assert record.read_meta().turns == 42
    assert "Visual review context" not in record.read_meta().prompt


def test_restores_sdk_entry_point_after_critic_failure(reference_record, monkeypatch):
    job_dir, record, config = reference_record
    def original(*args, **kwargs):
        raise RuntimeError("critic failed")
    monkeypatch.setattr(loop, "critique", original)
    with pytest.raises(RuntimeError, match="critic failed"):
        with reference_critique(job_dir):
            loop.critique(record, config)
    assert loop.critique is original


def test_no_reference_job_keeps_sdk_unchanged(tmp_path):
    original = loop.critique
    with reference_critique(tmp_path):
        assert loop.critique is original
    assert loop.critique is original


def test_missing_crop_fails_before_critic_call(tmp_path):
    original = loop.critique
    (tmp_path / "reference.json").write_text('{}')
    with pytest.raises(ReferenceImageError) as caught:
        with reference_critique(tmp_path):
            pytest.fail("missing crop must not enter worker context")
    assert caught.value.code == "REFERENCE_MISSING"
    assert loop.critique is original
