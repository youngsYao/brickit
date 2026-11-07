#!/usr/bin/env python3
"""Verify a real local generation without creating another paid job.

Run after a job completes, for example:
    python3 tests/live-generation-ui.py --job-id <32-character ID>

Uses the real local API and an isolated browser context, with no intercepted or
substituted responses. It imports the existing result, verifies official meshes
and placements, downloads the original LDraw, and captures desktop/mobile views.
"""

import argparse
import csv
import hashlib
import json
import os
import re
from collections import Counter
from io import StringIO
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright


def browser_options(playwright):
    options = {"headless": True, "args": ["--enable-unsafe-swiftshader"]}
    configured = os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE")
    if configured:
        options["executable_path"] = configured
    elif not Path(playwright.chromium.executable_path).exists():
        candidates = sorted((Path.home() / "Library/Caches/ms-playwright").glob(
            "chromium-*/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
        ))
        if candidates:
            options["executable_path"] = str(candidates[-1])
    return options


def by_piece_id(pieces):
    values = {piece["id"]: piece for piece in pieces}
    assert len(values) == len(pieces), "Piece IDs must remain unique"
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:4173")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--progress", action="store_true", help="Inspect and capture the existing running job, then exit")
    args = parser.parse_args()
    assert re.fullmatch(r"[a-f0-9]{32}", args.job_id), "Use an existing local job ID"
    base = args.base_url.rstrip("/")
    parsed = urlsplit(base)
    assert parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}, "Use the local API"
    destination = args.output_dir or Path("output/live-generation") / args.job_id
    destination.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**browser_options(playwright))
        context = browser.new_context(base_url=base, viewport={"width": 1440, "height": 1100}, accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(20000)
        mutations, errors = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda request: mutations.append({"method": request.method, "path": urlsplit(request.url).path})
                if request.method not in {"GET", "HEAD", "OPTIONS"} and "/api/" in request.url else None)
        summary = {"jobId": args.job_id, "baseUrl": base, "passed": False}
        try:
            response = context.request.get(f"/api/generations/{args.job_id}", timeout=30000)
            assert response.ok, f"Existing job API returned HTTP {response.status}"
            job = response.json()
            if job.get("status") != "completed":
                if args.progress and job.get("status") in {"queued", "referencing", "generating", "validating", "rendering"}:
                    page.goto(f"{base}/?generation={args.job_id}", wait_until="networkidle")
                    page.wait_for_function("id => window.brickit?.state.generation?.id === id", arg=args.job_id)
                    page.locator("[data-action='show-generation']").click()
                    expect(page.locator("#generation-dialog")).to_be_visible()
                    if job.get("referenceImages", {}).get("ready"):
                        expect(page.locator("#generation-references")).to_be_visible()
                        page.wait_for_function("document.querySelector('#generation-references img')?.naturalWidth > 0")
                        expect(page.locator("#generation-references")).to_contain_text("未见部分为 AI 推测")
                    assert page.evaluate("window.brickit.project") is None, "Running jobs must not import unfinished results"
                    page.screenshot(path=str(destination / "progress-desktop.png"), full_page=True)
                    page.set_viewport_size({"width": 390, "height": 844})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    page.screenshot(path=str(destination / "progress-mobile.png"), full_page=True)
                    assert not mutations and not errors
                    summary.update(passed=True, status=job["status"], progressOnly=True,
                                   referenceReady=job.get("referenceImages", {}).get("ready", False),
                                   apiMutations=mutations, browserErrors=errors)
                    (destination / "progress-verification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(json.dumps(summary, ensure_ascii=False))
                    return 0
                print(json.dumps({"jobId": args.job_id, "status": job.get("status"),
                                  "message": job.get("message"), "notCompleted": True}, ensure_ascii=False))
                return 2
            expected = job["result"]
            validation = expected["generation"]["validation"]
            assert validation["ok"] is True and validation["collisions"] == 0 and validation["floating"] == 0
            assert validation["components"] == 1 and validation["partCount"] == len(expected["pieces"])
            assert expected["generation"]["jobId"] == args.job_id
            assert expected["pieces"] and expected["parts"]
            assert all(part["mesh"]["positions"] and part["mesh"]["indices"] and
                       part["provenance"]["provider"] == "BrickLayer" for part in expected["parts"])

            # This is the app's real resume link. It reads and imports the existing
            # task; no generation button or paid endpoint is invoked.
            page.goto(f"{base}/?generation={args.job_id}", wait_until="networkidle")
            page.wait_for_function("id => window.brickit?.project?.id === id", arg=expected["id"], timeout=60000)
            expect(page.locator("#stage[data-ready='true']")).to_be_visible(timeout=60000)
            page.wait_for_function("window.brickit.state.engine?.count === window.brickit.project.pieces.length", timeout=60000)
            actual = page.evaluate("window.brickit.project")
            assert by_piece_id(actual["pieces"]) == by_piece_id(expected["pieces"]), "Official poses survive actual import"
            assert by_piece_id(actual["originalPieces"]) == by_piece_id(expected["originalPieces"])
            assert actual["parts"] == expected["parts"], "Actual SDK mesh data survives import"
            assert actual["generation"] == expected["generation"], "Actual generation report survives import"
            assert actual["sourceImage"] == expected["sourceImage"]
            expect(page.locator("#model-validation")).to_have_attribute("data-valid", "true")
            assert page.evaluate("localStorage.getItem('brickit-generation-job')") is None

            # Exercise the actual controls and renderer, including timed playback.
            # Playback changes visibility only; official placements stay intact.
            total = len(expected["pieces"])
            page.locator("#build-controls [data-action='restart']").click()
            page.wait_for_function("window.brickit.state.progress === 0 && window.brickit.state.engine.pieces === 0")
            page.locator("#build-controls [data-action='next']").click()
            page.wait_for_function("window.brickit.state.progress === 1 && window.brickit.state.engine.pieces === 1")
            page.locator("#build-controls [data-action='previous']").click()
            page.wait_for_function("window.brickit.state.progress === 0 && window.brickit.state.engine.pieces === 0")
            page.locator("#play-speed").select_option("120")
            page.locator("#build-controls [data-action='play']").click()
            page.wait_for_function("window.brickit.state.playing && window.brickit.state.progress >= 3 && window.brickit.state.engine.pieces === window.brickit.state.progress")
            page.locator("#build-controls [data-action='play']").click()
            playback_state = page.evaluate("window.brickit.state")
            assert playback_state["playing"] is False and playback_state["progress"] >= 3
            assert playback_state["engine"]["pieces"] == playback_state["progress"]
            assert playback_state["engine"]["count"] == total, "Playback retains every official piece"
            page.locator("#progress-range").evaluate(
                "(control, total) => { control.value = total; control.dispatchEvent(new Event('input', {bubbles: true})); }", total)
            page.wait_for_function("total => window.brickit.state.progress === total && window.brickit.state.engine.pieces === total && !window.brickit.state.playing", arg=total)
            after_playback = page.evaluate("window.brickit.project")
            assert by_piece_id(after_playback["pieces"]) == by_piece_id(expected["pieces"]), "Playback must not change official coordinates or poses"
            assert by_piece_id(after_playback["originalPieces"]) == by_piece_id(expected["originalPieces"])
            assert after_playback["parts"] == expected["parts"]
            expect(page.locator("#model-validation")).to_have_attribute("data-valid", "true")
            playback = {"restartVisible": 0, "nextVisible": 1, "previousVisible": 0,
                        "timedPlaybackVisible": playback_state["progress"], "paused": True,
                        "restoredVisible": total, "sourcePlacementsUnchanged": True}
            page.screenshot(path=str(destination / "studio-desktop.png"), full_page=True)
            page.locator("#stage").screenshot(path=str(destination / "model-canvas.png"))

            with page.expect_download() as ldraw_download:
                page.locator("[data-action='export-ldraw']").click()
            downloaded_ldraw = Path(ldraw_download.value.path()).read_bytes()
            original = context.request.get(f"/api/generations/{args.job_id}/artifacts/build.ldr")
            assert original.ok and original.body() == downloaded_ldraw, "Downloaded LDraw is the exact actual artifact"
            (destination / "build.ldr").write_bytes(downloaded_ldraw)

            with page.expect_download() as csv_download:
                page.locator("[data-action='export-parts']").click()
            csv_bytes = Path(csv_download.value.path()).read_bytes()
            rows = list(csv.reader(StringIO(csv_bytes.decode("utf-8-sig"))))
            header = next(index for index, row in enumerate(rows) if row and row[0] == "零件编号")
            counts = Counter()
            for row in rows[header + 1:]:
                if row and row[0] != "总计":
                    counts[row[0]] += int(row[4])
            assert counts == Counter(str(piece["ldrawId"]) for piece in expected["pieces"])
            (destination / "parts.csv").write_bytes(csv_bytes)

            artifacts = {}
            for filename in ("build.png", "report.json", "reference.png"):
                artifact = context.request.get(f"/api/generations/{args.job_id}/artifacts/{filename}")
                assert artifact.ok, f"Actual artifact {filename} must be available"
                content = artifact.body()
                assert content
                (destination / filename).write_bytes(content)
                artifacts[filename] = {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}

            page.reload(wait_until="networkidle")
            page.wait_for_function("id => window.brickit?.project?.id === id", arg=expected["id"], timeout=60000)
            expect(page.locator("#stage[data-ready='true']")).to_be_visible(timeout=60000)
            restored = page.evaluate("window.brickit.project")
            assert by_piece_id(restored["pieces"]) == by_piece_id(expected["pieces"])
            assert restored["parts"] == expected["parts"]
            assert restored["generation"] == expected["generation"]
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile layout fits the viewport"
            page.screenshot(path=str(destination / "studio-mobile.png"), full_page=True)
            assert not mutations, f"Validation must not create, cancel, or change API jobs: {mutations}"
            assert not errors, f"No uncaught browser errors: {errors}"
            summary.update(passed=True, projectId=expected["id"], title=expected["title"],
                           pieces=len(expected["pieces"]), meshTypes=len(expected["parts"]),
                           validation=validation, playback=playback, artifacts=artifacts, apiMutations=mutations, browserErrors=errors)
            (destination / "verification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(summary, ensure_ascii=False))
            return 0
        except Exception as error:
            summary.update(error=str(error), apiMutations=mutations, browserErrors=errors)
            page.screenshot(path=str(destination / "failure.png"), full_page=True)
            (destination / "verification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(summary, ensure_ascii=False))
            return 1
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    raise SystemExit(main())
