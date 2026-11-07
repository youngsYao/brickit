"""Bounded local jobs with real subprocess cancellation and disk recovery."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import uuid
from pathlib import Path

from .config import ROOT, Settings
from .schemas import GenerationRequest
from .storage import TERMINAL, JobStore, atomic_json, now

ARTIFACTS = {"build.ldr", "build.py", "report.json", "build.png"}
REFERENCE_ARTIFACTS = {"reference.png", "reference-front.png", "reference-side.png",
                       "reference-rear.png", "reference-perspective.png", "reference.json"}


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status


class JobManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.store = JobStore(settings.records_dir)
        self.tasks: dict[str, asyncio.Task] = {}
        self.processes: dict[str, asyncio.subprocess.Process] = {}

    async def start(self) -> None:
        self.store.recover()

    async def create(self, request: GenerationRequest) -> dict:
        if sum(not task.done() for task in self.tasks.values()) >= self.settings.max_active:
            raise ServiceError("BUSY", "本地已有生成任务，请等待完成或取消后重试。", 429)
        capability = await asyncio.to_thread(self.settings.capabilities)
        if not capability["available"]:
            raise ServiceError("GENERATION_UNAVAILABLE", capability["reason"], 503)
        if request.image is None and not self.settings.image_model:
            raise ServiceError("REFERENCE_MODEL_REQUIRED", "文字生成需要先配置多视角生图模型。", 422)
        try:
            image = await asyncio.to_thread(request.decode_image, self.settings.max_image_bytes) if request.image else None
        except ValueError as exc:
            raise ServiceError("INVALID_IMAGE", str(exc), 422) from exc
        # Image decoding yields to the event loop. Recheck before reserving a slot.
        if sum(not task.done() for task in self.tasks.values()) >= self.settings.max_active:
            raise ServiceError("BUSY", "本地已有生成任务，请稍后重试。", 429)
        job_id = uuid.uuid4().hex
        path = self.store.path(job_id)
        path.mkdir(mode=0o700)
        if image is not None:
            (path / "input.png").write_bytes(image)
        atomic_json(path / "request.json", request.model_dump(exclude={"image"}))
        message = "任务已提交，正在准备多视角参考图。" if self.settings.image_model else "任务已提交，正在准备 BrickWright。"
        record = {"id": job_id, "status": "queued", "stage": "queued", "message": message,
                  "createdAt": now(), "updatedAt": now(),
                  "events": [{"stage": "queued", "message": message, "at": now()}]}
        self.store.save(job_id, record)
        self.tasks[job_id] = asyncio.create_task(self._run(job_id))
        self.tasks[job_id].add_done_callback(lambda task: self.tasks.pop(job_id, None))
        return record

    async def _run(self, job_id: str) -> None:
        process = None
        try:
            creation = asyncio.create_task(asyncio.create_subprocess_exec(
                sys.executable, "-m", "server.worker", str(self.store.path(job_id)),
                cwd=str(ROOT), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True, limit=1024 * 1024,
            ))
            try:
                process = await asyncio.shield(creation)
            except asyncio.CancelledError:
                # Cancellation during OS process creation must still recover its
                # handle, otherwise an already-started paid run could be orphaned.
                process = await creation
                raise
            self.processes[job_id] = process
            stage = "referencing" if self.settings.image_model else "generating"
            self.store.update(job_id, stage, "正在启动多视角参考图生成。" if self.settings.image_model else "正在启动照片分析和积木生成。")
            await asyncio.wait_for(self._watch(job_id, process), timeout=self.settings.timeout)
            outcome_path = self.store.path(job_id) / "outcome.json"
            outcome = json.loads(outcome_path.read_text()) if outcome_path.is_file() else {}
            complete = outcome.get("ok") is True and process.returncode == 0
            if complete:
                path = self.store.path(job_id)
                complete = (path / "project.json").is_file() and all((path / "artifacts" / n).is_file() for n in ARTIFACTS)
            if complete:
                self.store.update(job_id, "completed", "模型已完成并通过 BrickLayer 校验。")
            else:
                code = outcome.get("code", "WORKER_FAILED")
                message = outcome.get("message", "生成进程未完成，请重试。")
                self.store.update(job_id, "failed", message, error={"code": code, "message": message})
        except asyncio.TimeoutError:
            self.store.update(job_id, "failed", "生成时间超过限制，任务已停止。", error={"code": "TIMEOUT", "message": "请尝试更小尺寸或更清晰的照片。"})
        except asyncio.CancelledError:
            self.store.update(job_id, "cancelled", "生成已取消。")
            raise
        except Exception:
            self.store.update(job_id, "failed", "生成服务暂时无法完成任务。", error={"code": "INTERNAL_ERROR", "message": "请检查本地服务配置后重试。"})
        finally:
            if process:
                await self._terminate(process)
            self.processes.pop(job_id, None)

    async def _watch(self, job_id: str, process: asyncio.subprocess.Process) -> None:
        assert process.stdout
        while line := await process.stdout.readline():
            try:
                value = json.loads(line)
                if value.get("brickItEvent") is True and value.get("stage") in {"referencing", "generating", "validating", "rendering"}:
                    self.store.update(job_id, value["stage"], str(value["message"])[:500])
            except (ValueError, AttributeError, KeyError):
                pass  # SDK logging is deliberately not part of the public API.
        await process.wait()

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        # Kill the whole session, including SDK validation/render subprocesses.
        # A parent may exit before its children: still signal the process group.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except asyncio.TimeoutError:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()

    def get(self, job_id: str) -> dict:
        try:
            record = self.store.read(job_id)
            path = self.store.path(job_id)
            if (path / "reference.json").is_file() and all((path / name).is_file() for name in REFERENCE_ARTIFACTS):
                # Construct URLs from an allowlist, never from provider metadata.
                metadata = json.loads((path / "reference.json").read_text())
                prefix = f"/api/generations/{job_id}/artifacts/"
                record["referenceImages"] = {"ready": True, "model": metadata.get("model", ""),
                    "sheetUrl": prefix + "reference.png", "synthetic": True,
                    "views": [{"id": key, "label": label, "url": prefix + f"reference-{key}.png"}
                              for key, label in (("front", "正面"), ("side", "侧面"), ("rear", "背面"), ("perspective", "透视"))]}
            return record
        except KeyError as exc:
            raise ServiceError("NOT_FOUND", "找不到这个生成任务。", 404) from exc

    async def cancel(self, job_id: str) -> dict:
        record = self.get(job_id)
        task = self.tasks.get(job_id)
        if record["status"] not in TERMINAL:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            self.store.update(job_id, "cancelled", "生成已取消。")
        return self.get(job_id)

    def artifact(self, job_id: str, name: str) -> Path:
        record = self.get(job_id)
        if name in REFERENCE_ARTIFACTS and record.get("referenceImages", {}).get("ready"):
            return self.store.path(job_id) / name
        if name not in ARTIFACTS or record["status"] != "completed":
            raise ServiceError("NOT_FOUND", "该下载文件不存在或模型尚未完成。", 404)
        path = self.store.path(job_id) / "artifacts" / name
        if not path.is_file():
            raise ServiceError("NOT_FOUND", "下载文件已不存在，请重新生成。", 404)
        return path

    async def close(self) -> None:
        tasks = list(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
