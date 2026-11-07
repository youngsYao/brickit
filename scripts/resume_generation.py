#!/usr/bin/env python3
"""Continue a failed local generation with its existing source and conversation.

Uses the same worker, job supervision, checks, artifacts and local API records as
the website. Original records are read-only; the continuation receives a new ID.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import shutil
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.config import Settings
from server.jobs import JobManager, REFERENCE_ARTIFACTS
from server.storage import TERMINAL, atomic_json, now


async def resume(job_id: str, feedback_file: Path | None = None, feedback_image: Path | None = None, fresh_context: bool = False) -> int:
    settings = Settings.from_env()
    manager = JobManager(settings)
    original = manager.store.read(job_id, include_result=False)
    if original["status"] not in {"failed", "cancelled"}:
        raise ValueError("Only failed or cancelled jobs can be continued")
    source = manager.store.path(job_id)
    request = json.loads((source / "request.json").read_text())
    new_id = uuid.uuid4().hex
    destination = manager.store.path(new_id)
    destination.mkdir(mode=0o700)
    for filename in ("input.png", *REFERENCE_ARTIFACTS):
        if (source / filename).is_file():
            shutil.copyfile(source / filename, destination / filename)
    atomic_json(destination / "request.json", request)
    atomic_json(destination / "resume.json", {"jobId": job_id, "freshContext": fresh_context})
    if feedback_file is not None:
        shutil.copyfile(feedback_file, destination / "resume-feedback.txt")
    if feedback_image is not None:
        shutil.copyfile(feedback_image, destination / "resume-reference.png")
    message = "正在从上次中断处继续生成，保留已有零件查询和装配程序。"
    stamp = now()
    manager.store.save(new_id, {
        "id": new_id, "status": "queued", "stage": "queued", "message": message,
        "createdAt": stamp, "updatedAt": stamp, "resumedFrom": job_id,
        "events": [{"stage": "queued", "message": message, "at": stamp}],
    })
    print(json.dumps({"jobId": new_id, "resumedFrom": job_id}, ensure_ascii=False), flush=True)
    task = asyncio.create_task(manager._run(new_id))
    manager.tasks[new_id] = task
    last = None
    try:
        while not task.done():
            state = manager.store.read(new_id, include_result=False)
            signature = state["status"], state["updatedAt"]
            if signature != last:
                print(json.dumps({key: state[key] for key in ("id", "status", "message", "updatedAt")}, ensure_ascii=False), flush=True)
                last = signature
            if state["status"] == "cancelled":
                task.cancel()  # Also honor cancellation through the website API.
                break
            await asyncio.sleep(1)
        try:
            await task
        except asyncio.CancelledError:
            pass
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        manager.tasks.pop(new_id, None)
    state = manager.store.read(new_id, include_result=False)
    assert state["status"] in TERMINAL
    print(json.dumps({"jobId": new_id, "status": state["status"], "message": state["message"]}, ensure_ascii=False), flush=True)
    return 0 if state["status"] == "completed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job_id", help="ID of a failed or cancelled local generation")
    parser.add_argument("--feedback-file", type=Path, help="Optional local diagnostic feedback for the continuation")
    parser.add_argument("--feedback-image", type=Path, help="Optional PNG diagnostic render, in addition to the original photo")
    parser.add_argument("--fresh-context", action="store_true", help="Keep the original task and latest source; omit completed tool history from model input")
    args = parser.parse_args()
    return asyncio.run(resume(args.job_id, args.feedback_file, args.feedback_image, args.fresh_context))


if __name__ == "__main__":
    raise SystemExit(main())
