"""The public request boundary, including actual image decoding."""

from __future__ import annotations

import base64
import binascii
import io
import re
import warnings
from typing import Literal

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, model_validator


class GenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image: str | None = Field(default=None, min_length=24, max_length=12 * 1024 * 1024)
    prompt: str = Field(default="", max_length=2000)
    size: Literal["small", "medium", "large"] = "medium"
    style: Literal["classic", "pixel"] = "classic"

    @model_validator(mode="after")
    def require_input(self):
        if not self.image and not self.prompt.strip():
            raise ValueError("请上传图片或填写文字描述。")
        return self

    def decode_image(self, max_bytes: int) -> bytes:
        if self.image is None:
            raise ValueError("请上传图片。")
        match = re.fullmatch(r"data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=\r\n]+)", self.image)
        if match is None:
            raise ValueError("请上传 PNG、JPEG 或 WebP 照片。")
        try:
            raw = base64.b64decode(match.group(2), validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("照片数据无效，请重新上传。") from exc
        if len(raw) > max_bytes:
            raise ValueError("照片超过 8 MB，请压缩后上传。")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as im:
                    if im.format not in {"PNG", "JPEG", "WEBP"} or im.width * im.height > 24_000_000:
                        raise ValueError("请使用不超过 2400 万像素的 PNG、JPEG 或 WebP 照片。")
                    if min(im.size) < 8:
                        raise ValueError("照片尺寸太小。")
                    im.load()
                    # A canonical RGB PNG prevents a filename/content mismatch and
                    # makes provider image input independent of browser MIME labels.
                    converted = im.convert("RGB")
                    converted.thumbnail((2048, 2048))
                    out = io.BytesIO()
                    converted.save(out, format="PNG")
                    return out.getvalue()
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ValueError("照片无法读取，请换一张照片。") from exc
