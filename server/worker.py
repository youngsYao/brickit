"""One process per paid run; the server can terminate its entire process group.

Only explicit, bounded progress events cross stdout. SDK transcripts stay private.
An SDK-finalized artifact is imported back into BrickLayer and independently
verified before any project or public artifact is published.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
import warnings
from pathlib import Path

from .config import MAX_CRITIQUE_ROUNDS, PART_LIMITS, Settings
from .exporter import ExportRejected, export_project
from .providers import ProviderBudgetExceeded, ProviderRateLimited, ProviderRequestTimeout, safe_failure_details, worker_completion_provider
from .resume import ResumeRejected, resume_conversation
from .storage import atomic_json
from .reference_images import ReferenceImageError, generate_references
from .reference_context import reference_critique


def emit(stage: str, message: str) -> None:
    print(json.dumps({"brickItEvent": True, "stage": stage, "message": message}, ensure_ascii=False), flush=True)


def build_prompt(request: dict) -> str:
    cap = PART_LIMITS[request["size"]]
    style = (
        "Use a classic sculptural LEGO style, with varied catalogued bricks, plates, slopes and appropriate specialty parts."
        if request["style"] == "classic" else
        "Use a deliberately blocky, pixel-inspired three-dimensional LEGO sculpture with a limited color palette; do not make a flat photo mosaic."
    )
    reference = (
        "The attached image is an AI-generated 2x2 multi-view reference sheet of ONE subject, "
        "not four separate objects: top-left FRONT, top-right RIGHT SIDE, bottom-left REAR, "
        "bottom-right FRONT THREE-QUARTER PERSPECTIVE. Build ONE connected three-dimensional subject. "
        "Use all four views to understand its volume, roof, depth and silhouette. "
        "Do not build the contact-sheet grid, labels, or duplicated copies. "
        "These are synthetic design references; unseen surfaces are inferred, not verified observations. "
        if request.get("referenceImages") else
        "Create a LEGO model of the main subject in the attached photograph. "
    )
    return (
        reference +
        "Preserve its distinctive silhouette, proportions, dominant colors and recognizable details. "
        "Use the supplied views for the back when available; otherwise infer a plausible back. The result is a stylized interpretation. "
        f"Use at least 1 and no more than {cap} real catalogued parts. {style} "
        "The model must be one connected assembly, with no collisions and no floating parts. "
        "Place the build near the origin with its lowest physical point on the ground plane. "
        "Keep all physical checks, add tests for the part-count cap and one connected component, "
        "and inspect multi-view renders before finalizing. Use a deterministic create_build(). "
        "Do not weaken the checks to finish. Do not create external files except through the provided tools.\n\n"
        f"Additional user request: {request['prompt'].strip() or 'Match the reference subject as closely as possible.'}"
    )


def prepare_reference(job_dir: Path, request: dict, settings: Settings) -> tuple[Path, dict | None]:
    """Complete image generation before any paid builder call; reuse on resume."""
    marker = job_dir / "reference.json"
    if marker.is_file():
        metadata = json.loads(marker.read_text())
        if not (job_dir / "reference.png").is_file():
            raise ReferenceImageError("REFERENCE_MISSING", "多视角参考图不完整，请重新生成。")
        return job_dir / "reference.png", metadata
    if settings.image_model and not (job_dir / "resume.json").exists():
        emit("referencing", "正在生成正面、侧面、背面和透视参考图。")
        metadata = generate_references(job_dir, request, settings)
        emit("referencing", "四视角参考图已完成，即将开始积木建模。")
        return job_dir / "reference.png", metadata
    if not (job_dir / "input.png").is_file():
        raise ReferenceImageError("REFERENCE_MISSING", "文字输入需要先生成多视角参考图。")
    return job_dir / "input.png", None


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


def agent_config(job_dir: Path, settings: Settings):
    """Translate explicit unlimited settings without inventing a larger cap."""
    from brickwright.config import Config, Effort

    return Config(
        records_dir=job_dir / "sdk", model=settings.model,
        effort=Effort(settings.moonshot_reasoning_effort) if settings.provider == "moonshot" else Effort.HIGH,
        max_cost=settings.max_cost if settings.max_cost is not None else float("inf"),
        max_turns=settings.max_turns, max_validations=settings.max_validations,
        request_timeout=settings.request_timeout,
        build_timeout=30 if settings.timeout is not None else float("inf"),
        render_timeout=90 if settings.timeout is not None else float("inf"),
        # One visual verdict may trigger one builder revision, then the SDK
        # renders the final source without starting another critique round.
        subagents_enabled=False, max_critique_rounds=MAX_CRITIQUE_ROUNDS,
        max_viewpoint_iterations=settings.max_viewpoint_iterations,
    )


def generation_settings(settings: Settings) -> dict:
    """Keep the actual controls reviewable with JSON null for unlimited limits."""
    return {
        "reasoningEffort": settings.moonshot_reasoning_effort if settings.provider == "moonshot" else "high",
        "maxOutputTokens": settings.moonshot_max_output_tokens if settings.provider == "moonshot" else None,
        "streaming": settings.moonshot_stream if settings.provider == "moonshot" else settings.openai_stream if settings.provider == "openai" else False,
        "budgetUsd": settings.max_cost,
        "jobTimeoutSeconds": settings.timeout,
        "requestTimeoutSeconds": settings.request_timeout,
        "buildTimeoutSeconds": 30 if settings.timeout is not None else None,
        "renderTimeoutSeconds": 90 if settings.timeout is not None else None,
        "maxTurns": settings.max_turns or None,
        "maxValidations": settings.max_validations or None,
        "maxCritiqueRounds": MAX_CRITIQUE_ROUNDS,
        "maxViewpointIterations": settings.max_viewpoint_iterations,
        "referenceImageModel": settings.image_model or None,
        "referenceImageTimeoutSeconds": settings.image_timeout if settings.image_model else None,
    }


def run(job_dir: Path, settings: Settings) -> None:
    from bricklayer.io import from_ldraw
    from bricklayer.io.reader import LDrawImportWarning
    from brickwright.driver.loop import run_agent
    from brickwright.driver.narration import Narrator
    from brickwright.record import Record
    from rich.console import Console

    class ProgressNarrator(Narrator):
        def __init__(self):
            super().__init__(Console(quiet=True))
            self.review_finished = False

        def run_started(self, record, config):
            emit("generating", "正在分析照片并编写积木结构。")

        def tool_called(self, name, arguments):
            if name in {"validate", "finalize"}:
                emit("validating", "正在检查评审后修改的碰撞、悬空和连接关系。" if self.review_finished else "正在用 BrickLayer 检查碰撞、悬空和连接关系。")
            elif name == "render":
                emit("rendering", "正在渲染评审后的修改结果。" if self.review_finished else "正在渲染模型，检查外观与观察角度。")
            elif name in {"write_build", "edit_build", "insert_build", "multi_edit_build"}:
                emit("generating", "视觉评审已完成（1/1 轮），正在修改模型。" if self.review_finished else "正在搭建模型并根据反馈调整零件。")

        def tool_finished(self, name, outcome):
            if name == "validate" and not outcome.ok:
                emit("generating", "评审后的修改尚未通过结构检查，正在修正。" if self.review_finished else "校验发现问题，AI 正在修正结构。")

        def model_message(self, text):
            pass

        def warn(self, message):
            pass

        def critique_started(self, round_num, total):
            emit("rendering", f"正在进行视觉评审（第 {round_num}/{total} 轮），匹配观察角度并对照参考图。")

        def critique_result(self, result):
            self.review_finished = True
            if not result.done:
                emit("generating", "视觉评审已完成（1/1 轮），正在按反馈修改模型。")

    request = json.loads((job_dir / "request.json").read_text())
    reference_path, reference_metadata = prepare_reference(job_dir, request, settings)
    if reference_metadata:
        request["referenceImages"] = True
    config = agent_config(job_dir, settings)
    record = Record.create(config, prompt=build_prompt(request), image=reference_path)
    with reference_critique(job_dir), resume_conversation(record, job_dir, settings.records_dir) as resumed, worker_completion_provider(
        settings, checkpoint_path=job_dir / "conversation-checkpoint.json",
        on_wait=lambda seconds: emit("generating", f"模型服务要求等待约 {max(1, math.ceil(seconds / 60))} 分钟，恢复后将自动继续。"),
    ) as accounting:
        try:
            report = run_agent(record, config, narrator=ProgressNarrator())
        except ProviderBudgetExceeded as exc:
            raise ExportRejected("BUDGET_EXCEEDED", "已达到费用上限或无法继续确认费用，未继续调用生成服务。") from exc
        except ProviderRequestTimeout as exc:
            raise ExportRejected("PROVIDER_TIMEOUT", "模型单次请求超时，已停止生成。请稍后重试或降低推理强度。") from exc
        except ProviderRateLimited as exc:
            raise ExportRejected("PROVIDER_RATE_LIMITED", "模型服务暂时达到调用限额，已停止生成。请稍后重试或检查服务商账户限额。") from exc
        finally:
            if accounting is not None:
                atomic_json(job_dir / "provider-billing.json", accounting.snapshot())
    billing = {"providerBilling": accounting.snapshot()} if accounting is not None else {}
    billing["generationSettings"] = generation_settings(settings)
    if reference_metadata:
        billing["referenceImages"] = reference_metadata
        billing["generationSettings"]["referenceImageModel"] = reference_metadata.get("model")
    if resumed is not None:
        billing["resumedFrom"] = resumed
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
    source_path = job_dir / "input.png" if (job_dir / "input.png").is_file() else reference_path
    source_image = "data:image/png;base64," + __import__("base64").b64encode(source_path.read_bytes()).decode()
    project = export_project(
        build, job_id=job_dir.name, prompt=request["prompt"], size=request["size"],
        style=request["style"], source_image=source_image, max_parts=PART_LIMITS[request["size"]],
        generation={**billing, "model": settings.model, "usage": report.usage.model_dump(mode="json"),
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
        **billing,
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
    except ReferenceImageError as exc:
        atomic_json(job_dir / "outcome.json", {"ok": False, "code": exc.code, "message": str(exc)})
    except ResumeRejected:
        atomic_json(job_dir / "outcome.json", {"ok": False, "code": "RESUME_REJECTED", "message": "无法恢复该任务，请使用同一照片与生成设置重试。"})
    except Exception as exc:
        # Provider errors can contain credentials, HTTP bodies or generated
        # source. Keep only a fixed safe message in public records.
        diagnostic = job_dir / "failure-diagnostic.json"
        atomic_json(diagnostic, safe_failure_details(exc))
        diagnostic.chmod(0o600)
        atomic_json(job_dir / "outcome.json", {"ok": False, "code": "GENERATION_FAILED", "message": "生成服务未能完成任务。请检查模型配置、网络和 LDraw 零件库后重试。"})
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
