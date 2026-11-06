"""Completion guards use genuine SDK records and report models without paid calls."""

import bricklayer as bl
import pytest
from brickwright.config import Config
from brickwright.driver.report import RunReport
from brickwright.record import Record, Status, StopReason, Usage
from brickwright.tools.validation import ValidationReport

from server.exporter import ExportRejected
from server.worker import verify_completed_run


@pytest.fixture
def finished(tmp_path):
    record = Record.create(Config(records_dir=tmp_path))
    build = bl.LegoBuild()
    build.add(bl.Element("3001", bl.Color.RED))
    bl.write_ldraw(build, record.ldr_path)
    record.render_path.write_bytes(b"test-render")
    digest = record.source_sha256
    record.update_meta(status=Status.VALID, finalized=True, validated_source_sha256=digest,
                       exported_source_sha256=digest, stop_reason=StopReason.FINALIZED)
    report = RunReport(done=True, source_sha256=digest, stop_reason=StopReason.FINALIZED,
                       report=ValidationReport(ok=True, source_sha256=digest, brick_count=1),
                       validations=1, turns=2, critique_rounds=1, usage=Usage(cost_usd=.01))
    return record, report


def test_accepts_only_complete_latest_source(finished):
    verify_completed_run(*finished)


@pytest.mark.parametrize("changes,code", [
    ({"over_budget": True}, "BUDGET_EXCEEDED"),
    ({"done": False}, "GENERATION_INCOMPLETE"),
    ({"stop_reason": StopReason.MAX_TURNS}, "GENERATION_INCOMPLETE"),
    ({"stop_reason": StopReason.UNKNOWN_COST}, "GENERATION_INCOMPLETE"),
    ({"critique_rounds": 0}, "INCOMPLETE_FEEDBACK"),
])
def test_refuses_partial_or_budget_output(finished, changes, code):
    record, report = finished
    with pytest.raises(ExportRejected) as error:
        verify_completed_run(record, report.model_copy(update=changes))
    assert error.value.code == code


def test_critique_edit_without_refinalization_is_rejected(finished):
    record, report = finished
    record.build_path.write_text(record.read_build() + "\n# changed after finalization\n")
    assert record.read_meta().finalized is False
    with pytest.raises(ExportRejected):
        verify_completed_run(record, report)


def test_failed_custom_validation_is_rejected(finished):
    record, report = finished
    report = report.model_copy(update={"report": report.report.model_copy(update={"ok": False})})
    with pytest.raises(ExportRejected):
        verify_completed_run(record, report)


def test_unlimited_agent_controls_survive_real_sdk_record(tmp_path):
    from math import isinf
    from brickwright.config import Effort
    from server.config import Settings
    from server.worker import agent_config, generation_settings

    settings = Settings(
        records_dir=tmp_path, model="moonshot/kimi-k3.1-pro",
        max_cost=None, timeout=None, request_timeout=None,
        max_turns=0, max_validations=0, moonshot_reasoning_effort="max",
        max_critique_rounds=10, max_viewpoint_iterations=6,
    )
    config = agent_config(tmp_path, settings)
    assert config.effort is Effort.MAX
    assert all(isinf(value) for value in (config.max_cost, config.build_timeout, config.render_timeout))
    assert config.request_timeout is None
    assert config.max_turns == config.max_validations == 0
    assert config.max_critique_rounds == 1
    record = Record.create(config)
    assert record.read_meta().generation.effort is Effort.MAX
    controls = generation_settings(settings)
    assert controls["maxCritiqueRounds"] == 1
    assert controls["budgetUsd"] is None
    assert all(controls[name] is None for name in (
        "jobTimeoutSeconds", "requestTimeoutSeconds", "buildTimeoutSeconds", "renderTimeoutSeconds",
    ))


def test_default_agent_limits_remain_bounded(tmp_path):
    from server.config import Settings
    from server.worker import agent_config

    config = agent_config(tmp_path, Settings(records_dir=tmp_path))
    assert config.max_cost == 1
    assert config.request_timeout == 120
    assert config.build_timeout == 30 and config.render_timeout == 90
    assert config.max_critique_rounds == 1 and config.max_viewpoint_iterations == 3


@pytest.mark.parametrize("legacy_rounds", [2, 10, 20])
@pytest.mark.parametrize("critique_done", [False, True])
def test_sdk_accepts_only_one_visual_revision_then_renders_final_source(tmp_path, monkeypatch, legacy_rounds, critique_done):
    from types import SimpleNamespace

    import brickwright.driver.loop as loop
    from brickwright.driver.critique import CritiqueResult
    from brickwright.driver.narration import Narrator
    from brickwright.driver.tracking import RunTracker
    from rich.console import Console

    from server.config import Settings
    from server.worker import agent_config, generation_settings

    settings = Settings(records_dir=tmp_path, max_cost=None, timeout=None, request_timeout=None,
                        max_turns=0, max_validations=0, max_critique_rounds=legacy_rounds)
    config = agent_config(tmp_path, settings)
    photo = tmp_path / "input.png"
    photo.write_bytes(b"reference-image")
    record = Record.create(config, image=photo)
    record.write_build("original source")
    history = []
    run = SimpleNamespace(config=config, record=record, tracker=RunTracker(),
                          narrator=Narrator(Console(quiet=True)), transcript=SimpleNamespace(append=history.append))
    events = []

    def render(current):
        events.append(("render", current.record.read_build()))
        return True

    def critique(*_args, **_kwargs):
        events.append(("critique", record.read_build()))
        return CritiqueResult(done=critique_done, differences_description="Improve the roof proportions.")

    def revise(current):
        events.append(("revise", current.record.read_build()))
        current.record.write_build("revised source")
        # Multiple physical checks remain available inside this one revision.
        current.tracker.validations += 3
        assert current.tracker.validation_budget_left(current.config.max_validations)

    monkeypatch.setattr(loop, "_render_and_snapshot", render)
    monkeypatch.setattr(loop, "critique", critique)
    monkeypatch.setattr(loop, "_run_builder_pass", revise)
    result = loop._run_critique_loop(run)

    assert result.done is critique_done
    assert run.tracker.critique_rounds == 1
    assert generation_settings(settings)["maxCritiqueRounds"] == config.max_critique_rounds == 1
    expected = [("render", "original source"), ("critique", "original source")]
    if not critique_done:
        expected += [("revise", "original source"), ("render", "revised source")]
        assert len(history) == 1
        assert "Improve the roof proportions." in history[0]["content"]
    else:
        assert history == []
    assert events == expected
