"""Generate one coherent multiview sheet before the BrickWright modelling stage.

The image provider is independent of the modelling provider. Paid requests are
never retried automatically, and provider bodies/credentials never cross the
public error boundary.
"""

from __future__ import annotations

import base64
import binascii
import io
import ipaddress
import json
import math
import os
import re
import socket
import tempfile
import warnings
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit, urlunsplit

import httpx
from PIL import Image, UnidentifiedImageError

from .storage import atomic_json

MAX_OUTPUT_BYTES = 32 * 1024 * 1024
MAX_RESPONSE_BYTES = 48 * 1024 * 1024
VIEWS = (
    ("front", "正面", "reference-front.png"),
    ("side", "右侧面", "reference-side.png"),
    ("rear", "背面", "reference-rear.png"),
    ("perspective", "右前方俯视", "reference-perspective.png"),
)
REFERENCE_FILES = {"reference.png", "reference.json", *(view[2] for view in VIEWS)}


class ReferenceImageError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def reference_prompt(request: dict, *, has_image: bool) -> str:
    subject = request.get("prompt", "").strip()
    input_instruction = (
        "The uploaded photograph is the authoritative identity and geometry reference. "
        "Preserve its main subject, distinctive silhouette, proportions, materials, colors, "
        "and visible architectural details. Infer unseen surfaces conservatively; "
        "do not replace it with a different subject. "
        if has_image else
        "Create the subject described by the user. Preserve its recognizable silhouette, "
        "proportions, colors, materials and architectural details. "
    )
    return (
        "Create a consistent four-view reference sheet for subsequent 3D LEGO modelling. "
        + input_instruction
        + "Render the SAME single object in all four panels, with identical geometry, "
        "materials and scale. Use a precise 2 by 2 grid of equally sized panels, "
        "with boundaries exactly at the image's horizontal and vertical midpoints. "
        "Top left: straight-on front elevation. Top right: right-side elevation. "
        "Bottom left: rear elevation. Bottom right: elevated front-right three-quarter perspective. "
        "Show the complete object in every panel with generous margins and no cropping. "
        "Use a simple light neutral background and soft neutral daylight; remove unrelated "
        "people, cars, trees and visual clutter. Render the subject with clear natural surfaces "
        "rather than adding LEGO studs; the next stage will design the actual bricks. "
        "No panel borders, captions, viewpoint labels, dimensions, watermarks or decorative text. "
        "Keep text only where it is part of the subject itself. "
        "Unseen details are a plausible interpretation, not surveyed architectural evidence.\n\n"
        "Additional user description: " + (subject or "Reconstruct the main subject in the uploaded photograph.")
    )


def _api_base(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        _ = parsed.port
        httpx.URL(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment
                or any(char.isspace() or ord(char) < 32 for char in value)):
            raise ValueError
        path = parsed.path.rstrip("/")
        if not re.search(r"/v\d+(?:[a-z0-9.-]*)?$", path, re.I):
            path += "/v1"
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    except (ValueError, httpx.InvalidURL):
        raise ReferenceImageError("REFERENCE_CONFIG_INVALID", "生图服务地址无效，请检查本地生图配置。") from None


def _read_response(response: httpx.Response, limit: int) -> bytes:
    if not response.is_success:
        if response.status_code in {401, 403}:
            raise ReferenceImageError("REFERENCE_AUTH_FAILED", "生图 API Key 无效或没有此模型的使用权限。")
        if response.status_code == 429:
            raise ReferenceImageError("REFERENCE_RATE_LIMITED", "生图服务达到调用限额，请稍后重试或检查账户额度。")
        if response.status_code in {400, 404, 405, 422}:
            raise ReferenceImageError("REFERENCE_REQUEST_REJECTED", "生图服务拒绝了请求，请检查模型名称及图像生成或编辑接口支持。")
        raise ReferenceImageError("REFERENCE_PROVIDER_FAILED", "生图服务暂时无法完成请求，请稍后重试。")
    chunks = bytearray()
    for chunk in response.iter_bytes():
        chunks.extend(chunk)
        if len(chunks) > limit:
            raise ReferenceImageError("REFERENCE_OUTPUT_TOO_LARGE", "生图服务返回的数据过大，已停止读取。")
    return bytes(chunks)


def _public_image_url(value: str) -> str:
    """Image download URLs never receive API authorization or redirects."""
    try:
        parsed = urlsplit(value)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.fragment or len(value) > 8192):
            raise ValueError
        addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(entry[4][0]).is_global for entry in addresses):
            raise ValueError
    except (ValueError, OSError):
        raise ReferenceImageError("REFERENCE_INVALID_OUTPUT", "生图服务没有返回可安全读取的图片地址。") from None
    return value


def _output_image(payload: dict, client: httpx.Client) -> Image.Image:
    try:
        entries = payload.get("data")
        if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict):
            raise ValueError
        entry = entries[0]
        encoded = entry.get("b64_json")
        if isinstance(encoded, str) and encoded:
            if len(encoded) > (MAX_OUTPUT_BYTES + 2) // 3 * 4:
                raise ReferenceImageError("REFERENCE_OUTPUT_TOO_LARGE", "生图服务返回的图片过大。")
            raw = base64.b64decode(encoded, validate=True)
        elif isinstance(entry.get("url"), str):
            image_url = _public_image_url(entry["url"])
            with client.stream("GET", image_url, headers={"Accept": "image/*"}) as response:
                raw = _read_response(response, MAX_OUTPUT_BYTES)
        else:
            raise ValueError
        if len(raw) > MAX_OUTPUT_BYTES:
            raise ReferenceImageError("REFERENCE_OUTPUT_TOO_LARGE", "生图服务返回的图片过大。")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if (image.format not in {"PNG", "JPEG", "WEBP"} or min(image.size) < 512
                        or image.width * image.height > 24_000_000):
                    raise ValueError
                image.load()
                return image.convert("RGB")
    except (ValueError, TypeError, binascii.Error, OSError, UnidentifiedImageError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ReferenceImageError("REFERENCE_INVALID_OUTPUT", "生图服务没有返回有效的多视角参考图片。") from None


def _usage(value: object) -> dict | None:
    """Retain numeric token measurements only, never arbitrary provider strings."""
    if not isinstance(value, dict):
        return None
    result = {}
    for key in ("input_tokens", "output_tokens", "total_tokens", "input_tokens_details", "output_tokens_details",
                "text_tokens", "image_tokens", "cached_tokens", "reasoning_tokens"):
        item = value.get(key)
        if isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item) and item >= 0:
            result[key] = item
        elif isinstance(item, dict):
            nested = _usage(item)
            if nested:
                result[key] = nested
    return result or None


def generate_references(
    job_dir: Path, request: dict, settings, on_progress: Callable[[str], None] | None = None,
) -> dict:
    """Generate and persist the sheet and four deterministic crops.

    ``reference.json`` is written last as the commit marker. Only the explicit
    dedicated image-provider key is used, keeping modelling credentials separate.
    """
    key = os.environ.get("BRICK_IT_IMAGE_API_KEY", "").strip()
    if not settings.image_model or not key:
        raise ReferenceImageError("REFERENCE_CONFIG_REQUIRED", "请先配置生图模型、服务地址和 API Key。")
    base = _api_base(settings.image_api_base)
    source = job_dir / "input.png"
    has_image = source.is_file()
    if not has_image and not request.get("prompt", "").strip():
        raise ReferenceImageError("REFERENCE_INPUT_REQUIRED", "请上传图片或填写主体描述。")
    prompt = reference_prompt(request, has_image=has_image)
    if on_progress:
        on_progress("正在根据上传图片生成四视角参考图。" if has_image else "正在根据文字描述生成四视角参考图。")
    fields = {"model": settings.image_model, "prompt": prompt, "n": 1, "size": "1536x1024"}
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json"}
    try:
        # Credentials are per-request so generated image URLs never inherit them.
        with httpx.Client(timeout=settings.image_timeout, follow_redirects=False) as client:
            if has_image:
                with source.open("rb") as image_file:
                    with client.stream("POST", base + "/images/edits", headers=headers,
                                       data={name: str(value) for name, value in fields.items()},
                                       files={"image": ("input.png", image_file, "image/png")}) as response:
                        body = _read_response(response, MAX_RESPONSE_BYTES)
            else:
                with client.stream("POST", base + "/images/generations", headers=headers, json=fields) as response:
                    body = _read_response(response, MAX_RESPONSE_BYTES)
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError):
                raise ReferenceImageError("REFERENCE_INVALID_OUTPUT", "生图服务没有返回有效的图片数据。") from None
            if not isinstance(payload, dict):
                raise ReferenceImageError("REFERENCE_INVALID_OUTPUT", "生图服务没有返回有效的图片数据。")
            image = _output_image(payload, client)
    except httpx.TimeoutException:
        raise ReferenceImageError("REFERENCE_TIMEOUT", "生图请求超时，已停止后续建模；请稍后重试。") from None
    except (httpx.HTTPError, httpx.InvalidURL):
        raise ReferenceImageError("REFERENCE_CONNECTION_FAILED", "无法连接生图服务，请检查地址和网络后重试。") from None
    width, height = image.size
    boxes = ((0, 0, width // 2, height // 2), (width // 2, 0, width, height // 2),
             (0, height // 2, width // 2, height), (width // 2, height // 2, width, height))
    metadata = {
        "model": settings.image_model,
        "ready": True,
        "sourceType": "image" if has_image else "text",
        "sheet": "reference.png",
        "views": [{"id": view[0], "label": view[1], "file": view[2]} for view in VIEWS],
        "width": width, "height": height,
        "synthetic": True,
        "notice": "AI 生成的多视角参考图；未见角度属于合理推测，不代表实测建筑资料。",
        "usage": _usage(payload.get("usage")),
        "costUsd": None,
    }
    with tempfile.TemporaryDirectory(prefix=".reference-", dir=job_dir) as temporary:
        temporary = Path(temporary)
        image.save(temporary / "reference.png", format="PNG")
        for view, box in zip(VIEWS, boxes):
            image.crop(box).save(temporary / view[2], format="PNG")
        for name in REFERENCE_FILES - {"reference.json"}:
            (temporary / name).replace(job_dir / name)
    atomic_json(job_dir / "reference.json", metadata)
    if on_progress:
        on_progress("四视角参考图已生成，正在准备积木建模。")
    return metadata
