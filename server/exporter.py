"""Verify exact LDraw placements and adapt official collision meshes to the editor.

The editor previews the SDK's inset collision meshes, not photorealistic LDraw
surfaces. The .ldr artifact remains the authoritative assembly. No substitute
boxes or fabricated part IDs are used when mesh conversion fails.
"""

from __future__ import annotations

import copy
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bricklayer import LegoBuild

UNIT = .58
LDU_SCALE = UNIT / 20
SDK_COMMIT = "f8a90d1a416582e945505398311ffa397aa84959"
PREVIEW_NOTE = "预览使用 BrickLayer 的内缩碰撞网格，外观与完整 LDraw 零件略有差异。"
VALIDATION_NOTE = "仅验证零件、碰撞、悬空和连接关系；未验证受力稳定性、采购颜色库存或可执行装配顺序。"
MAX_SCENE_SPAN = 48.0  # Keep the whole assembly within the editor's camera range.
MAX_MESH_VALUES = 9_000_000


class ExportRejected(ValueError):
    """A completed assembly cannot be safely represented as an approved project."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def verify_build(build: "LegoBuild", max_parts: int) -> dict:
    """Hard acceptance gate, independent of the agent's own test program."""
    from bricklayer import Element

    placements = build.flattened_placements
    if not 1 <= len(placements) <= max_parts:
        raise ExportRejected("PART_LIMIT", f"模型零件数须为 1–{max_parts}，当前结果不符合所选尺寸。")
    if not all(isinstance(p.content, Element) for p in placements):
        raise ExportRejected("INVALID_PART", "生成结果包含无法识别的零件。")
    collisions = build.colliding_placements
    floating = build.floating_placements
    components = build.connected_components
    if collisions:
        raise ExportRejected("COLLISION", "生成结果仍有零件碰撞，未作为完成模型导入。")
    if floating:
        raise ExportRejected("FLOATING", "生成结果仍有悬空零件，未作为完成模型导入。")
    if len(components) != 1:
        raise ExportRejected("DISCONNECTED", "生成结果没有形成一个连接完整的模型。")
    return {
        "ok": True, "status": "passed", "engine": "BrickLayer", "sdkCommit": SDK_COMMIT,
        "partCount": len(placements), "collisions": 0, "floating": 0,
        "components": 1, "scope": ["catalog_parts", "no_collisions", "no_floaters", "one_component"],
        "source": "original_generation", "note": VALIDATION_NOTE,
    }


def _euler_xyz(matrix) -> tuple[float, float, float]:
    """Three.js Euler.setFromRotationMatrix(..., 'XYZ'), including gimbal lock."""
    y = math.asin(max(-1.0, min(1.0, float(matrix[0, 2]))))
    if abs(matrix[0, 2]) < .9999999:
        return math.atan2(-matrix[1, 2], matrix[2, 2]), y, math.atan2(-matrix[0, 1], matrix[0, 0])
    return math.atan2(matrix[2, 1], matrix[1, 1]), y, 0.0


def export_project(build: "LegoBuild", *, job_id: str, prompt: str, size: str,
                   style: str, source_image: str, max_parts: int,
                   generation: dict | None = None) -> dict:
    import numpy as np

    validation = verify_build(build, max_parts)
    conversion = np.diag([1.0, -1.0, -1.0])
    parts: dict[str, dict] = {}
    pieces = []
    scene_lo = np.full(3, np.inf)
    scene_hi = np.full(3, -np.inf)
    for index, placement in enumerate(build.flattened_placements):
        element = placement.content
        body = placement.collision_body
        vertices = np.asarray(body.collider.mesh.vertices, dtype=float)
        faces = np.asarray(body.collider.mesh.faces, dtype=int)
        if not len(vertices) or not len(faces) or vertices.size > MAX_MESH_VALUES or faces.size > MAX_MESH_VALUES or not np.isfinite(vertices).all():
            raise ExportRejected("MESH_UNAVAILABLE", "零件网格不可用，无法准确导入此模型。")
        if faces.min() < 0 or faces.max() >= len(vertices):
            raise ExportRejected("MESH_UNAVAILABLE", "零件网格数据不完整。")
        lo, hi = vertices.min(axis=0), vertices.max(axis=0)
        if np.any(hi - lo <= 0):
            raise ExportRejected("MESH_UNAVAILABLE", "零件网格没有有效的三维尺寸。")
        center = (lo + hi) / 2
        local_ui = (vertices - center) @ conversion.T * LDU_SCALE
        rotation = conversion @ body.rotation @ conversion
        position = conversion @ (body.rotation @ center + body.translation) * LDU_SCALE
        if not np.isfinite(position).all() or not np.isfinite(rotation).all():
            raise ExportRejected("INVALID_POSE", "模型包含无效零件位置。")
        world_vertices = vertices @ body.rotation.T + body.translation
        world_ui = world_vertices @ conversion.T * LDU_SCALE
        scene_lo = np.minimum(scene_lo, world_ui.min(axis=0))
        scene_hi = np.maximum(scene_hi, world_ui.max(axis=0))
        if np.max(np.abs(local_ui)) > 10000 or np.max(np.abs(position)) > 10000:
            raise ExportRejected("SCENE_TOO_LARGE", "模型尺寸或坐标超出当前编辑器支持范围。")
        rx, ry, rz = _euler_xyz(rotation)
        part_id = f"ldraw-{element.ldraw_id}"
        hex_color = "#" + element.color.rgb.lstrip("#").lower()
        extent = hi - lo
        w, d, h = float(extent[0] / 20), float(extent[2] / 20), float(extent[1] * LDU_SCALE)
        if part_id not in parts:
            category = element.spec.category.lower()
            parts[part_id] = {
                "id": part_id, "ldrawId": element.ldraw_id, "name": element.spec.name,
                "w": w, "d": d, "h": h, "shape": "mesh", "studs": False,
                "category": category, "categoryName": element.spec.category,
                "description": f"LDraw {element.ldraw_id} · {PREVIEW_NOTE}",
                "colors": [], "mesh": {"positions": local_ui.reshape(-1).tolist(), "indices": faces.reshape(-1).tolist()},
                "provenance": {"provider": "BrickLayer", "sdkCommit": SDK_COMMIT, "geometry": "inset_collision_mesh"},
            }
        if hex_color not in parts[part_id]["colors"]:
            parts[part_id]["colors"].append(hex_color)
        pieces.append({
            "id": f"{job_id}-p{index + 1}", "partId": part_id,
            "ldrawId": element.ldraw_id, "colorCode": element.color.ldraw_id,
            "w": w, "d": d, "h": h, "shape": "mesh", "studs": False,
            "color": hex_color, "x": float(position[0]), "y": float(position[1]), "z": float(position[2]),
            "rx": rx, "ry": ry, "rz": rz,
            "layer": max(0, round(float(position[1]) / (8 * LDU_SCALE))),
        })
    if np.max(scene_hi - scene_lo) > MAX_SCENE_SPAN or np.max(np.abs((scene_lo + scene_hi)[[0, 2]] / 2)) > MAX_SCENE_SPAN:
        raise ExportRejected("SCENE_TOO_LARGE", "模型尺寸或位置超出当前编辑器的显示范围，请尝试更小尺寸。")
    if not -.08 <= scene_lo[1] <= .24:
        raise ExportRejected("UNSUPPORTED_ORIGIN", "模型未放置在编辑器地面附近，未导入偏移的预览。")
    base = f"/api/generations/{job_id}/artifacts"
    return {
        "id": f"ai-{job_id}", "title": (prompt.strip()[:32] or "照片生成的积木模型"),
        "source": "ai", "draft": True, "version": 1,
        "pieces": pieces, "originalPieces": copy.deepcopy(pieces), "parts": list(parts.values()),
        "settings": {"size": size, "style": style}, "sourceImage": source_image,
        "generation": {
            **(generation or {}), "jobId": job_id, "engine": "BrickWright", "sdkCommit": SDK_COMMIT,
            "validation": validation, "previewGeometry": "inset_collision_mesh", "previewNote": PREVIEW_NOTE,
            "artifacts": {"ldraw": f"{base}/build.ldr", "source": f"{base}/build.py", "report": f"{base}/report.json", "preview": f"{base}/build.png"},
        },
    }
