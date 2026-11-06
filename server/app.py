"""Serve the editor and local-only generation API on one origin."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import ROOT, Settings
from .jobs import JobManager, ServiceError
from .schemas import GenerationRequest
from .parts import router as parts_router
from .ai_settings import AISettings, ModelsRequest, SaveSettingsRequest

LOGGER = logging.getLogger("brick_it")
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def create_app(settings: Settings | None = None, *, manager: JobManager | None = None, env_path: Path | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    manager = manager or JobManager(settings)
    ai_settings = AISettings(manager, env_path)

    @asynccontextmanager
    async def lifespan(app):
        await manager.start()
        yield
        await manager.close()

    app = FastAPI(title="brick.it local generation", lifespan=lifespan)
    app.state.jobs = manager
    app.state.ai_settings = ai_settings
    app.include_router(parts_router)

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        request_id = uuid.uuid4().hex
        if request.url.hostname not in LOCAL_HOSTS:
            return JSONResponse({"error": {"code": "LOCAL_ONLY", "message": "此服务仅允许本机访问。"}}, status_code=403)
        if request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin:
                try:
                    parsed = urlsplit(origin)
                    expected_port = request.url.port or (443 if request.url.scheme == "https" else 80)
                    origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
                    valid_origin = parsed.hostname == request.url.hostname and origin_port == expected_port and parsed.scheme == request.url.scheme
                except ValueError:
                    valid_origin = False
                if not valid_origin:
                    return JSONResponse({"error": {"code": "ORIGIN_REJECTED", "message": "请从本地网站页面发起操作。"}}, status_code=403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                return JSONResponse({"error": {"code": "ORIGIN_REJECTED", "message": "不允许跨站请求。"}}, status_code=403)
            if request.method == "POST":
                if not request.headers.get("content-type", "").lower().startswith("application/json"):
                    return JSONResponse({"error": {"code": "JSON_REQUIRED", "message": "请使用 JSON 请求。"}}, status_code=415)
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > settings.max_body_bytes:
                        return JSONResponse({"error": {"code": "PAYLOAD_TOO_LARGE", "message": "照片数据太大。"}}, status_code=413)
                request._body = bytes(body)
        try:
            response = await call_next(request)
        except Exception:
            LOGGER.error(json.dumps({"event": "request_failed", "requestId": request_id}))
            response = JSONResponse({"error": {"code": "INTERNAL_ERROR", "message": "本地服务发生错误，请重试。"}}, status_code=500)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Request-ID"] = request_id
        if request.url.path.startswith("/api/") and not request.url.path.startswith("/api/ldraw/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        message = "API 地址、密钥或模型无效，请检查后重试。" if request.url.path.startswith("/api/ai/") else "照片、提示词或生成选项无效，请检查后重试。"
        return JSONResponse({"error": {"code": "INVALID_REQUEST", "message": message}}, status_code=422)

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/ready")
    async def ready():
        result = await asyncio.to_thread(manager.settings.capabilities)
        return JSONResponse(result, status_code=200 if result["available"] else 503)

    @app.get("/api/generation/capabilities")
    async def capabilities():
        return await asyncio.to_thread(manager.settings.capabilities)

    @app.get("/api/ai/settings")
    async def get_ai_settings():
        return ai_settings.public()

    @app.post("/api/ai/models")
    async def list_ai_models(request: ModelsRequest):
        return await ai_settings.models(request)

    @app.post("/api/ai/settings")
    async def save_ai_settings(request: SaveSettingsRequest):
        return await ai_settings.save(request)

    @app.post("/api/generations", status_code=202)
    async def create_generation(request: GenerationRequest):
        async with ai_settings.lock:
            return await manager.create(request)

    @app.get("/api/generations/{job_id}")
    async def generation(job_id: str):
        return manager.get(job_id)

    @app.delete("/api/generations/{job_id}")
    async def cancel_generation(job_id: str):
        return await manager.cancel(job_id)

    @app.get("/api/generations/{job_id}/artifacts/{name}")
    async def artifact(job_id: str, name: str):
        path = manager.artifact(job_id, name)
        media = {"build.png": "image/png", "report.json": "application/json", "build.py": "text/plain; charset=utf-8", "build.ldr": "text/plain; charset=utf-8"}[name]
        return FileResponse(path, filename=name, media_type=media)

    # Keep unknown API routes out of the static-file fallback.
    @app.api_route("/api/{path:path}", methods=["GET", "POST", "DELETE", "PUT", "PATCH"])
    async def missing_api(path: str):
        raise ServiceError("NOT_FOUND", "接口不存在。", 404)

    app.mount("/", StaticFiles(directory=ROOT / "dist", html=True), name="editor")
    return app


app = create_app()
