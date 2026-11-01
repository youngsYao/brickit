"""One process per paid run; the server can terminate its entire process group.

Only explicit, bounded progress events cross stdout. SDK transcripts stay private.
An SDK-finalized artifact is imported back into BrickLayer and independently
verified before any project or public artifact is published.
"""

from __future__ import annotations

import hashlib
import json
import sys
import warnings
from pathlib import Path

from .config import PART_LIMITS, Settings
from .exporter import ExportRejected, export_project
from .storage import atomic_json


def emit(stage: str, message: str) -> None:
    print(json.dumps({"brickItEvent": True, "stage": stage, "message": message}, ensure_ascii=False), flush=True)


def build_prompt(request: dict) -> str:
    cap = PART_LIMITS[request["size"]]
    style = (
        "Use a classic sculptural LEGO style, with varied catalogued bricks, plates, slopes and appropriate specialty parts."
        if request["style"] == "classic" else
        "Use a deliberately blocky, pixel-inspired three-dimensional LEGO sculpture with a limited color palette; do not make a flat photo mosaic."
    )
    return (
        "Create a LEGO model of the main subject in the attached photograph. "
        "Preserve its distinctive silhouette, proportions, dominant colors and recognizable details. "
        "Invent a plausible back when it is not visible; the result is a stylized interpretation. "
        f"Use at least 1 and no more than {cap} real catalogued parts. {style} "
        "The model must be one connected assembly, with no collisions and no floating parts. "
        "Place the build near the origin with its lowest physical point on the ground plane. "
        "Keep all physical checks, add tests for the part-count cap and one connected component, "
        "and inspect multi-view renders before finalizing. Use a deterministic create_build(). "
        "Do not weaken the checks to finish. Do not create external files except through the provided tools.\n\n"
        f"Additional user request: {request['prompt'].strip() or 'Match the photograph as closely as possible.'}"
    )


def verify_completed_run(record, report) -> None:
    """Only the latest finalized, rendered and costed SDK snapshot is eligible."""
    from brickwright.record import StopReason

    meta = record.read_meta()
    if report.over_budget or meta.over_budget:
        raise ExportRejected("BUDGET_EXCEEDED", "已达到生成预算，未把未完成结果作为模型导入。")
    if report.stop_reason is not StopReason.FINALIZED or not report.succeeded or not meta.finalized:
        raise ExportRejected("GENERATION_INCOMPLETE", "AI 未能在限制内完成并通过校验，请换用更清晰的照片或更小尺寸。")
    if report.usage.cost_usd is None or report.critique_rounds < 1 or not record.render_path.is_file():
        raise ExportRejected("INCOMPLETE_FEEDBACK", "生成结果缺少完整的渲染反馈或用量记录，未作为完成模型导入。")
    if record.source_sha256 != report.source_sha256:
        raise ExportRejected("SOURCE_CHANGED", "模型源文件在校验后发生变化，请重新生成。")


def run(job_dir: Path, settings: Settings) -> None:
    from bricklayer.io import from_ldraw
    from bricklayer.io.reader import LDrawImportWarning
    from brickwright.config import Config, Effort
    from brickwright.driver.loop import run_agent
    from brickwright.driver.narration import Narrator
    from brickwright.record import Record
    from rich.console import Console

    class ProgressNarrator(Narrator):
        def __init__(self):
            super().__init__(Console(quiet=True))

        def run_started(self, record, config):
            emit("generating", "正在分析照片并编写积木结构。")

        def tool_called(self, name, arguments):
            if name in {"validate", "finalize"}:
                emit("validating", "正在用 BrickLayer 检查碰撞、悬空和连接关系。")
            elif name == "render":
                emit("rendering", "正在渲染模型并核对照片特征。")
            elif name in {"write_build", "edit_build", "insert_build", "multi_edit_build"}:
                emit("generating", "正在搭建模型并根据反馈调整零件。")

        def tool_finished(self, name, outcome):
            if name == "validate" and not outcome.ok:
                emit("generating", "校验发现问题，AI 正在修正结构。")

        def model_message(self, text):
            pass

        def warn(self, message):
            pass

        def critique_started(self, round_num, total):
            emit("rendering", "正在比较参考照片与模型预览。")

        def critique_result(self, result):
            if not result.done:
                emit("generating", "正在根据视觉反馈改善模型。")

    request = json.loads((job_dir / "request.json").read_text())
    config = Config(
        records_dir=job_dir / "sdk", model=settings.model, effort=Effort.HIGH,
        max_cost=settings.max_cost, max_turns=settings.max_turns,
        max_validations=settings.max_validations, request_timeout=settings.request_timeout,
        build_timeout=30, render_timeout=90, subagents_enabled=False,
        max_critique_rounds=2, max_viewpoint_iterations=3,
    )
    record = Record.create(config, prompt=build_prompt(request), image=job_dir / "input.png")
    report = run_agent(record, config, narrator=ProgressNarrator())
    verify_completed_run(record, report)

    emit("validating", "正在独立复查最终导出的完整模型。")
    source_bytes = record.build_path.read_bytes()
    ldraw_bytes = record.ldr_path.read_bytes()
    preview_bytes = record.render_path.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != report.source_sha256:
        raise ExportRejected("SOURCE_CHANGED", "模型源文件在导出前发生变化，请重新生成。")
    # Import the exported bytes, rather than calling arbitrary create_build() a
    # second time. Import warnings indicate skipped geometry: never accept it.
    with warnings.catch_warnings():
        warnings.simplefilter("error", LDrawImportWarning)
        build = from_ldraw(ldraw_bytes.decode("utf-8"))
    if build.brick_count != report.report.brick_count:
        raise ExportRejected("INCOMPLETE_EXPORT", "导出的零件数与已校验模型不一致。")
    source_image = "data:image/png;base64," + __import__("base64").b64encode((job_dir / "input.png").read_bytes()).decode()
    project = export_project(
        build, job_id=job_dir.name, prompt=request["prompt"], size=request["size"],
        style=request["style"], source_image=source_image, max_parts=PART_LIMITS[request["size"]],
        generation={"model": settings.model, "usage": report.usage.model_dump(mode="json"),
                    "critiqueRounds": report.critique_rounds, "critiqueMatched": report.critique_done,
                    "sourceSha256": report.source_sha256},
    )
    artifacts = job_dir / "artifacts"
    artifacts.mkdir(exist_ok=True)
    # Publish precisely the bytes checked above; a later record edit cannot
    # silently replace the public artifacts with an unverified source/assembly.
    for name, content in (("build.py", source_bytes), ("build.ldr", ldraw_bytes), ("build.png", preview_bytes)):
        (artifacts / name).write_bytes(content)
    hashes = {name: hashlib.sha256((artifacts / name).read_bytes()).hexdigest() for name in ("build.py", "build.ldr", "build.png")}
    safe_report = {
        "jobId": job_dir.name, "engine": "BrickWright", "model": settings.model,
        "sourceSha256": report.source_sha256, "artifactSha256": hashes,
        "validation": project["generation"]["validation"],
        "agentValidation": {"ok": report.report.ok, "sourceSha256": report.report.source_sha256,
                            "checkCount": len(report.report.checks),
                            "allChecksPassed": all(check.passed for check in report.report.checks)},
        "usage": report.usage.model_dump(mode="json"),
        "critiqueRounds": report.critique_rounds, "critiqueMatched": report.critique_done,
        "previewGeometry": project["generation"]["previewGeometry"],
        "previewNote": project["generation"]["previewNote"],
    }
    atomic_json(artifacts / "report.json", safe_report)
    atomic_json(job_dir / "project.json", project)
    atomic_json(job_dir / "outcome.json", {"ok": True})


def main() -> int:
    job_dir = Path(sys.argv[1]).resolve()
    try:
        run(job_dir, Settings.from_env())
        return 0
    except ExportRejected as exc:
        atomic_json(job_dir / "outcome.json", {"ok": False, "code": exc.code, "message": str(exc)})
    except Exception:
        # Provider errors can contain credentials, HTTP bodies or generated
        # source. Keep only a fixed safe message in public records.
        atomic_json(job_dir / "outcome.json", {"ok": False, "code": "GENERATION_FAILED", "message": "生成服务未能完成任务。请检查模型配置、网络和 LDraw 零件库后重试。"})
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
