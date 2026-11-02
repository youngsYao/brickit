#!/usr/bin/env python3
"""Download/check the pinned LDraw snapshot, before installing Python packages."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.ldraw_library import LibrarySetupError, default_library_path, ensure_ldraw_library


def configured_destination() -> Path:
    if os.environ.get("LDRAW_LIBRARY_PATH", "").strip():
        return default_library_path()
    # Bootstrap stays stdlib-only. Read only the one relevant .env setting;
    # never interpret shell commands, print secret values or import dotenv.
    try:
        with (ROOT / ".env").open(encoding="utf-8") as source:
            for line in source:
                match = re.match(r"^\s*(?:export\s+)?LDRAW_LIBRARY_PATH\s*=\s*(.*)$", line)
                if not match:
                    continue
                value = match.group(1).strip()
                if value.startswith(("'", '"')):
                    quote = value[0]
                    end = value.find(quote, 1)
                    value = value[1:end] if end >= 1 else ""
                else:
                    value = value.split(" #", 1)[0].strip()
                if value:
                    destination = Path(os.path.expandvars(value)).expanduser()
                    return destination if destination.is_absolute() else ROOT / destination
                break
    except OSError:
        pass
    return default_library_path()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Install the manifest-pinned official LDraw library; no AI key or uv is required.")
    parser.add_argument("--destination", "--library-path", dest="destination", type=Path, help="New/empty library directory (defaults to LDRAW_LIBRARY_PATH or the local cache).")
    parser.add_argument("--archive", type=Path, help="Use an existing ZIP, still checking its pinned SHA-256 and byte size.")
    parser.add_argument("--manifest", type=Path, help="Use an explicit snapshot manifest.")
    parser.add_argument("--check", action="store_true", help="Check the existing library only; never download, extract or change it.")
    args = parser.parse_args(argv)
    try:
        library = ensure_ldraw_library(
            library_path=args.destination or configured_destination(),
            manifest_path=args.manifest,
            archive_path=None if args.check else args.archive,
            download=not args.check,
            log=print,
        )
    except LibrarySetupError as exc:
        print("LDraw setup: " + str(exc), file=sys.stderr)
        return 1
    print("LDraw library: " + str(library))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
