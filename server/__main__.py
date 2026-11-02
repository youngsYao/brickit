"""Run the local editor and generation API with ``uv run python -m server``."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn

from .config import Settings
from .ldraw_library import LibrarySetupError, ensure_ldraw_library


def prepare_library(settings: Settings) -> None:
    """Bootstrap once before serving; preserve the offline editor on failure."""
    try:
        ensure_ldraw_library(
            library_path=Path(os.environ["LDRAW_LIBRARY_PATH"]),
            download=settings.auto_download_ldraw,
            log=lambda message: print(f"LDraw: {message}", flush=True),
        )
    except LibrarySetupError as exc:
        print(f"LDraw 零件库暂未就绪：{exc}\n编辑器仍会启动。可运行 npm run setup:ldraw 重试，成功后重新启动服务。", file=sys.stderr, flush=True)


def main() -> None:
    settings = Settings.from_env()  # Fail early for malformed numeric limits.
    prepare_library(settings)
    uvicorn.run("server.app:app", host="127.0.0.1", port=4173, access_log=False)


if __name__ == "__main__":
    main()
