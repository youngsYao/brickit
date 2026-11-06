"""Load a validated interrupted-run snapshot without making any model calls."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import shutil
import sys
import uuid
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.config import Settings
from server.exporter import export_project
from server.storage import JOB_ID, JobStore, atomic_json, now


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id")
    args = parser.parse_args()
    if not JOB_ID.fullmatch(args.job_id):
        parser.error("Invalid job ID")
    settings = Settings.from_env()
    original = settings.records_dir / args.job_id
    candidates = []
    for report in original.glob("sdk/*/snapshots/*/report.json"):
        data = json.loads(report.read_text())
        source = report.parent / "build.py"
        if data.get("ok") and source.is_file() and hashlib.sha256(source.read_bytes()).hexdigest() == data.get("source_sha256"):
            candidates.append(report.parent)
    if not candidates:
        raise ValueError("No validated source snapshot exists")
    snapshot = max(candidates, key=lambda path: (path.parent.parent.name, path.name))

    from bricklayer.io import from_ldraw
    from bricklayer.io.reader import LDrawImportWarning
    from brickwright.config import Config
    from brickwright.record import Record
    from brickwright.tools.finalization import finalize

    job_id = uuid.uuid4().hex
    destination = settings.records_dir / job_id
    destination.mkdir(mode=0o700)
    shutil.copyfile(original / "input.png", destination / "input.png")
    request = json.loads((original / "request.json").read_text())
    atomic_json(destination / "request.json", request)
    config = Config(records_dir=destination / "sdk", model=settings.model,
                    max_cost=float("inf"), request_timeout=None,
                    build_timeout=float("inf"), render_timeout=float("inf"), max_critique_rounds=1)
    record = Record.create(config, prompt="Load the latest validated snapshot for local viewing.", image=destination / "input.png")
    record.write_build((snapshot / "build.py").read_text())
    print("Validating and rendering the saved snapshot; no model requests.", flush=True)
    finalized = finalize(record, config)
    if not finalized.ok:
        raise ValueError(finalized.error)
    with warnings.catch_warnings():
        warnings.simplefilter("error", LDrawImportWarning)
        build = from_ldraw(record.ldr_path.read_text())
    expected = json.loads((snapshot / "report.json").read_text())
    if build.brick_count != expected["brick_count"]:
        raise ValueError("Snapshot export changed the part count")
    provenance = {"model": settings.model, "importMode": "validated_snapshot",
                  "sourceJobId": args.job_id, "sourceSnapshot": snapshot.name,
                  "visualReviewCompleted": False, "sourceSha256": record.source_sha256}
    project = export_project(build, job_id=job_id, prompt="颂恩楼 · 最近有效版本",
                             size=request["size"], style=request["style"], max_parts=600,
                             source_image="data:image/png;base64," + base64.b64encode((destination / "input.png").read_bytes()).decode(),
                             generation=provenance)
    project["generation"]["previewNote"] += " 载入自中断任务的有效版本，外观评审尚未最终通过。"
    artifacts = destination / "artifacts"
    artifacts.mkdir()
    for name, source in (("build.py", record.build_path), ("build.ldr", record.ldr_path), ("build.png", record.render_path)):
        shutil.copyfile(source, artifacts / name)
    atomic_json(artifacts / "report.json", {
        **provenance, "jobId": job_id, "kind": "validated_snapshot_import",
        "validation": project["generation"]["validation"],
        "agentValidation": finalized.validation.model_dump(mode="json"),
        "note": "重新编译并独立校验已存版本；原中断生成任务的失败状态保持不变。",
        "artifactSha256": {name: hashlib.sha256((artifacts / name).read_bytes()).hexdigest()
                           for name in ("build.py", "build.ldr", "build.png")},
    })
    atomic_json(destination / "project.json", project)
    atomic_json(destination / "outcome.json", {"ok": True, "kind": "validated_snapshot_import"})
    stamp = now()
    message = "已载入最近一次通过结构检查的版本，可查看和编辑。"
    JobStore(settings.records_dir).save(job_id, {
        "id": job_id, "kind": "validated_snapshot_import", "sourceJobId": args.job_id,
        "status": "completed", "stage": "completed", "message": message,
        "createdAt": stamp, "updatedAt": stamp,
        "events": [{"stage": "completed", "message": message, "at": stamp}],
    })
    print(json.dumps({"jobId": job_id, "sourceJobId": args.job_id,
                      "snapshot": snapshot.name, "parts": build.brick_count}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
