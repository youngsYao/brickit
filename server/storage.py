"""Small atomic JSON records; never expose official transcripts or credentials."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from .mesh_assets import MESH_REFERENCE, MeshStorageError, PostgresMeshStore

LOGGER = logging.getLogger(__name__)
JOB_ID = re.compile(r"^[a-f0-9]{32}$")
TERMINAL = {"completed", "failed", "cancelled"}


def now() -> str:
    return datetime.now(UTC).isoformat()


def atomic_json(path: Path, value: dict) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temp.replace(path)


class JobStore:
    def __init__(self, root: Path, *, mesh_store: PostgresMeshStore | None = None):
        self.root = root
        self.mesh_store = mesh_store
        root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, job_id: str) -> Path:
        if not JOB_ID.fullmatch(job_id):
            raise KeyError(job_id)
        return self.root / job_id

    def read(self, job_id: str, *, include_result: bool = True) -> dict:
        try:
            record = json.loads((self.path(job_id) / "job.json").read_text())
            if include_result and record["status"] == "completed":
                record["result"] = self.read_project(job_id)
            return record
        except FileNotFoundError as exc:
            raise KeyError(job_id) from exc

    def save(self, job_id: str, value: dict) -> None:
        atomic_json(self.path(job_id) / "job.json", value)

    def read_project(self, job_id: str) -> dict:
        project = json.loads((self.path(job_id) / "project.json").read_text(encoding="utf-8"))
        if self.mesh_store is not None:
            return self.mesh_store.restore_project(project)
        if any(MESH_REFERENCE in part for part in project.get("parts", [])):
            raise MeshStorageError("PostgreSQL connection is required to restore this project")
        return project

    def compact_project(self, job_id: str) -> None:
        if self.mesh_store is None:
            return
        path = self.path(job_id) / "project.json"
        try:
            original = json.loads(path.read_text(encoding="utf-8"))
            compact = self.mesh_store.reference_project(original)
            if compact is not original:
                atomic_json(path, compact)
        except (MeshStorageError, OSError, ValueError) as exc:
            # The worker already published a complete inline snapshot. Keep it
            # usable when asset storage is temporarily unavailable.
            LOGGER.warning("Keeping generation project inline (%s)", type(exc).__name__)

    def update(self, job_id: str, status: str, message: str, *, error: dict | None = None) -> dict:
        record = self.read(job_id, include_result=False)
        if record["status"] in TERMINAL:
            return record
        record.update(status=status, stage=status, message=message, updatedAt=now())
        record["events"].append({"stage": status, "message": message, "at": now()})
        record["events"] = record["events"][-100:]
        if error:
            record["error"] = error
        self.save(job_id, record)
        return record

    def recover(self) -> None:
        for path in self.root.iterdir():
            if path.is_dir() and JOB_ID.fullmatch(path.name):
                try:
                    record = self.read(path.name, include_result=False)
                except (KeyError, ValueError):
                    continue
                if record["status"] not in TERMINAL:
                    self.update(path.name, "failed", "服务已重新启动，请重新生成。", error={"code": "INTERRUPTED", "message": "上次生成未完成。"})
