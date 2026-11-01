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
