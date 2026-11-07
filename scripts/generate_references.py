#!/usr/bin/env python3
"""Exercise the app's image stage without starting paid brick modelling."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server.config import Settings
from server.reference_images import ReferenceImageError, generate_references, reference_prompt
from server.schemas import GenerationRequest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prompt", default="")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    settings = Settings.from_env()
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("Choose a new or empty output directory to preserve existing references.")
    if not args.prompt.strip() and args.image is None:
        parser.error("Provide --prompt or --image.")
    output.mkdir(parents=True, exist_ok=True)
    if args.image:
        import base64
        raw = args.image.read_bytes()
        if len(raw) > settings.max_image_bytes:
            parser.error("Source image exceeds the upload limit.")
        encoded = "data:image/png;base64," + base64.b64encode(raw).decode()
        request = GenerationRequest(image=encoded, prompt=args.prompt)
        (output / "input.png").write_bytes(request.decode_image(settings.max_image_bytes))
    (output / "prompt.txt").write_text(reference_prompt({"prompt": args.prompt}, has_image=bool(args.image)), encoding="utf-8")
    try:
        result = generate_references(output, {"prompt": args.prompt}, settings,
                                     on_progress=lambda message: print(message, flush=True))
    except ReferenceImageError as exc:
        print(json.dumps({"code": exc.code, "message": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"model": result["model"], "sheet": str(output / "reference.png"),
                      "views": result["views"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
