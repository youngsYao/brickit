"""Bundle the approved Songen and gate results for offline example playback."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = (
    {"id": "songen-generated-example", "folder": "generated-songen", "jobId": "7fc9523fa9c54d5f85517c69ffc0392e",
     "title": "颂恩楼 · AI 生成", "selectorLabel": "颂恩楼", "selectorHint": "AI 生成",
     "description": "589 颗真实零件搭出的颂恩楼，保留塔身、玻璃窗与红瓦屋顶。", "pieceCount": 589},
    {"id": "xmu-gate-example", "folder": "generated-gate", "jobId": "778fe03efefa48b29ee0eeb886284236",
     "title": "厦大校门 · AI 生成", "selectorLabel": "厦大校门", "selectorHint": "AI 生成",
     "description": "246 颗真实零件搭出的厦大校门，包含三开间入口与高低错落的屋顶。", "pieceCount": 246},
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records-dir", type=Path, default=ROOT / ".brick-it-records")
    args = parser.parse_args()
    manifest = []
    for definition in EXAMPLES:
        source = args.records_dir / definition["jobId"]
        project = json.loads((source / "project.json").read_text())
        job = json.loads((source / "job.json").read_text())
        report = json.loads((source / "artifacts/report.json").read_text())
        validation = project["generation"]["validation"]
        assert validation["ok"] and validation["collisions"] == validation["floating"] == 0 and validation["components"] == 1
        assert len(project["pieces"]) == validation["partCount"] == definition["pieceCount"]
        assert hashlib.sha256((source / "artifacts/build.py").read_bytes()).hexdigest() == report["sourceSha256"]
        destination = ROOT / "dist/examples" / definition["folder"]
        destination.mkdir(parents=True, exist_ok=True)
        prefix = "./examples/" + definition["folder"] + "/"
        artifacts = {key: prefix + filename for key, filename in
                     (("ldraw", "build.ldr"), ("source", "build.py"), ("report", "report.json"), ("preview", "build.png"))}
        for filename in ("build.ldr", "build.py", "report.json", "build.png"):
            shutil.copyfile(source / "artifacts" / filename, destination / filename)
        image = project["sourceImage"]
        assert image.startswith("data:image/png;base64,")
        (destination / "source.png").write_bytes(base64.b64decode(image.split(",", 1)[1], validate=True))
        public_generation = {key: project["generation"][key] for key in (
            "engine", "model", "sdkCommit", "jobId", "sourceSha256", "validation", "previewGeometry", "previewNote",
            "importMode", "sourceJobId", "sourceSnapshot", "visualReviewCompleted", "critiqueRounds", "critiqueMatched",
        ) if key in project["generation"]}
        public_generation["artifacts"] = artifacts
        bundled = {"id": definition["id"], "pieces": project["pieces"], "parts": project["parts"],
                   "settings": project["settings"], "generation": public_generation}
        (destination / "project.json").write_text(json.dumps(bundled, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
        manifest.append({key: value for key, value in definition.items() if key not in {"folder", "jobId"}} | {
            "createdAt": project.get("createdAt") or job["createdAt"],
            "updatedAt": project.get("updatedAt") or job["updatedAt"],
            "projectUrl": prefix + "project.json", "sourceImage": prefix + "source.png", "thumbnail": prefix + "thumbnail.jpg",
        })
        print(f"Bundled {definition['title']}: {definition['pieceCount']} pieces")
    (ROOT / "dist/examples/generated.js").write_text(
        "/** Saved AI results; model meshes are loaded only when an example is opened. */\n"
        + "export const GENERATED_EXAMPLES = Object.freeze(" + json.dumps(manifest, ensure_ascii=False, indent=2) + ".map(Object.freeze));\n")


if __name__ == "__main__":
    main()
