"""Browse real SDK parts and serve a bounded, read-only official LDraw library.

The catalogue is the intersection of BrickLayer's metadata and downloaded
official part files. It works without an AI model or API key. Listing dimensions
come from measured SDK bounds; editor meshes retain their inset-geometry label.
"""

from __future__ import annotations

import copy
import os
import re
from collections import Counter
from functools import cached_property, lru_cache
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse, JSONResponse

from .exporter import ExportRejected, LDU_SCALE, export_project
from .jobs import ServiceError

DEFAULT_LIBRARY = Path.home() / ".cache" / "brick-it" / "ldraw"
SAFE_PART_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.+\-]{0,99}$")
QUERY_TRANSLATIONS = {
    "砖": "brick", "砖块": "brick", "标准砖": "brick", "薄板": "plate",
    "板": "plate", "光面砖": "tile", "斜坡": "slope", "斜坡件": "slope",
    "轮胎": "tyre", "轮子": "wheel", "人仔": "minifig", "窗": "window",
    "门": "door", "基础板": "baseplate", "齿轮": "gear", "技术梁": "technic beam",
}


def resolve_library_root(explicit: Path | None = None) -> Path | None:
    # Do not search arbitrary system directories or accept paths from a request.
    # The caller's configured library (or this application's downloaded cache)
    # is the sole root of the read-only file surface.
    configured = explicit if explicit is not None else Path(os.environ.get("LDRAW_LIBRARY_PATH") or DEFAULT_LIBRARY)
    root = Path(configured).expanduser().resolve()
    if not (root / "parts").is_dir() or not (root / "p").is_dir() or not (root / "LDConfig.ldr").is_file():
        return None
    return root


class PartsService:
    def __init__(self, library_root: Path | None = None):
        self.library_root = resolve_library_root(library_root)

    @cached_property
    def library_ids(self) -> frozenset[str]:
        if not self.library_root:
            return frozenset()
        return frozenset(path.stem.lower() for path in (self.library_root / "parts").glob("*.dat") if path.is_file())

    @cached_property
    def catalogue(self) -> tuple:
        from bricklayer.parts.catalog import all_parts
        from bricklayer.ldraw import partdata

        return tuple(sorted(
            (spec for spec in all_parts() if spec.ldraw_id.lower() in self.library_ids),
            # Keep decorated variants searchable, while the initial page gives
            # builders a varied set of common undecorated shapes to start with.
            key=lambda spec: (any(word in spec.name.lower() for word in ("pattern", "sticker", "print")),
                              -partdata.popularity(spec.ldraw_id), spec.ldraw_id),
        ))

    @cached_property
    def by_id(self) -> dict:
        return {spec.ldraw_id.lower(): spec for spec in self.catalogue}

    @cached_property
    def categories(self) -> list[dict]:
        counts = Counter(spec.category for spec in self.catalogue)
        return [{"id": name.lower(), "label": name, "count": count}
                for name, count in sorted(counts.items(), key=lambda row: (-row[1], row[0]))]

    @staticmethod
    @lru_cache(maxsize=4096)
    def metadata(spec) -> dict:
        from bricklayer.ldraw import partdata

        extent = partdata.measured_extents(spec.ldraw_id)
        return {
            "id": f"ldraw-{spec.ldraw_id}", "ldrawId": spec.ldraw_id,
            "name": spec.name, "category": spec.category.lower(), "categoryName": spec.category,
            "w": float(extent.x), "d": float(extent.y), "h": float(extent.z) * 24 * LDU_SCALE,
            "description": f"LDraw {spec.ldraw_id} · {spec.name}",
        }

    @lru_cache(maxsize=128)
    def _matching(self, q: str, category: str) -> tuple:
        from bricklayer import search_parts

        category = category.strip().lower()
        accepted = lambda spec: category in {"", "all"} or spec.category.lower() == category
        if not q:
            return tuple(spec for spec in self.catalogue if accepted(spec))
        query = q.strip().lower()
        part_id = query.removeprefix("ldraw-").removesuffix(".dat")
        exact = self.by_id.get(part_id)
        normalized = QUERY_TRANSLATIONS.get(query, query)
        normalized = re.sub(r"(?<=\d)[x×](?=\d)", " x ", normalized)
        if not re.search(r"[a-z0-9]", normalized):
            return ()
        # Ask the official SDK for all matches so total and pagination describe
        # the complete filtered set, rather than a truncated search top-k.
        ranked = search_parts(normalized, limit=len(self.by_id))
        output = []
        seen = set()
        if exact and accepted(exact):
            output.append(exact)
            seen.add(exact.ldraw_id)
        if re.fullmatch(r"[a-z]*\d+[a-z0-9]*", part_id):
            for spec in self.catalogue:
                if spec.ldraw_id.lower().startswith(part_id) and accepted(spec) and spec.ldraw_id not in seen:
                    output.append(spec)
                    seen.add(spec.ldraw_id)
        for spec in ranked:
            if spec.ldraw_id.lower() in self.by_id and spec.ldraw_id not in seen and accepted(spec):
                output.append(spec)
                seen.add(spec.ldraw_id)
        return tuple(output)

    def list(self, q: str = "", category: str = "", offset: int = 0, limit: int = 24) -> dict:
        if offset < 0 or not 1 <= limit <= 60:
            raise ServiceError("INVALID_PAGINATION", "零件分页范围无效。", 422)
        base = {"available": False, "total": 0, "libraryTotal": len(self.library_ids),
                "categories": [], "items": [], "offset": offset, "limit": limit, "hasMore": False}
        if not self.library_root:
            return {**base, "reason": "尚未安装 LDraw 零件库。请下载官方零件库并设置 LDRAW_LIBRARY_PATH。"}
        try:
            matches = self._matching(q.strip(), category)
            return {**base, "available": True, "total": len(matches),
                    "categories": self.categories,
                    "items": [copy.deepcopy(self.metadata(spec)) for spec in matches[offset:offset + limit]],
                    "hasMore": offset + limit < len(matches),
                    "catalogueTotal": len(self.catalogue), "metadataSource": "BrickLayer + official LDraw library"}
        except ImportError as exc:
            raise ServiceError("PARTS_UNAVAILABLE", "零件 SDK 尚未安装，请运行 uv sync。", 503) from exc

    def file(self, requested: str) -> Path:
        if not self.library_root:
            raise ServiceError("PARTS_UNAVAILABLE", "LDraw 零件库尚未安装。", 503)
        # PurePosixPath collapses dot segments, so inspect the raw segments first.
        segments = requested.split("/")
        if not requested or requested.startswith("/") or "\\" in requested or any(part in {"..", ".", ""} for part in segments) or "%" in requested or ":" in requested or "\x00" in requested:
            raise ServiceError("INVALID_PART_PATH", "零件文件路径无效。", 404)
        path = PurePosixPath(requested)
        is_config = requested.lower() == "ldconfig.ldr"
        if not is_config and (path.parts[0].lower() not in {"parts", "p"} or path.suffix.lower() not in {".dat", ".ldr"}):
            raise ServiceError("INVALID_PART_PATH", "只允许读取官方零件或颜色配置。", 404)
        resolved = (self.library_root / path).resolve()
        if not resolved.is_relative_to(self.library_root) or not resolved.is_file():
            raise ServiceError("PART_NOT_FOUND", "找不到这个 LDraw 零件文件。", 404)
        return resolved

    @lru_cache(maxsize=96)
    def _descriptor(self, ldraw_id: str) -> dict:
        import bricklayer as bl

        build = bl.LegoBuild()
        build.add(bl.Element(ldraw_id, bl.Color.RED))
        # A standalone Technic/side-connector part can have its alignment origin
        # away from its physical bottom. Ground it for the independent gate;
        # the returned mesh remains in the same centered local LDraw frame.
        build = bl.translate_to_ground(build)
        project = export_project(build, job_id="0" * 32, prompt="part", size="small", style="classic", source_image="", max_parts=1)
        descriptor = project["parts"][0]
        descriptor["colors"] = ["#" + color.rgb.lower() for color in (
            bl.Color.RED, bl.Color.BLUE, bl.Color.YELLOW, bl.Color.GREEN,
            bl.Color.WHITE, bl.Color.BLACK, bl.Color.TAN, bl.Color.REDDISH_BROWN,
            bl.Color.LIGHT_BLUISH_GRAY, bl.Color.DARK_BLUISH_GRAY,
        )]
        descriptor["description"] += " 颜色是设计调色板，不代表该零件的实际可购颜色。"
        return descriptor

    def descriptor(self, ldraw_id: str) -> dict:
        if not self.library_root:
            raise ServiceError("PARTS_UNAVAILABLE", "LDraw 零件库尚未安装。", 503)
        part_id = ldraw_id.removeprefix("ldraw-").removesuffix(".dat")
        if not SAFE_PART_ID.fullmatch(part_id) or part_id.lower() not in self.by_id:
            raise ServiceError("PART_NOT_FOUND", "此零件没有可用的 SDK 网格。", 404)
        spec = self.by_id[part_id.lower()]
        try:
            return copy.deepcopy(self._descriptor(spec.ldraw_id))
        except ExportRejected as exc:
            raise ServiceError("UNSUPPORTED_PART", "此零件超出当前工作台支持范围，可在仓库查看完整 LDraw 预览。", 422) from exc
        except (LookupError, ValueError) as exc:
            raise ServiceError("UNSUPPORTED_PART", "此零件没有可用的工作台网格，可在仓库查看 LDraw 预览。", 422) from exc


def create_router(service: PartsService | None = None) -> APIRouter:
    router = APIRouter()

    @lru_cache(maxsize=1)
    def get_service() -> PartsService:
        return service or PartsService()

    def error(exc: ServiceError):
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)

    @router.get("/api/parts")
    def parts(q: str = Query(default="", max_length=200), category: str = Query(default="", max_length=100),
              offset: int = Query(default=0, ge=0, le=100000), limit: int = Query(default=24, ge=1, le=60)):
        try:
            return get_service().list(q, category, offset, limit)
        except ServiceError as exc:
            return error(exc)

    @router.get("/api/parts/{ldraw_id}")
    def part(ldraw_id: str):
        try:
            return get_service().descriptor(ldraw_id)
        except ServiceError as exc:
            return error(exc)

    @router.get("/api/ldraw/{path:path}")
    def ldraw(path: str):
        try:
            file = get_service().file(path)
            return FileResponse(file, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "public, max-age=3600"})
        except ServiceError as exc:
            return error(exc)

    return router


router = create_router()
