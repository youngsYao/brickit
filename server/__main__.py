"""Run the local editor and generation API with ``uv run python -m server``."""

from __future__ import annotations

import uvicorn

from .config import Settings


def main() -> None:
    Settings.from_env()  # Load .env and fail early for malformed numeric limits.
    uvicorn.run("server.app:app", host="127.0.0.1", port=4173, access_log=False)


if __name__ == "__main__":
    main()
