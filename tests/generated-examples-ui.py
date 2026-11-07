#!/usr/bin/env python3
"""Verify the saved genuine models with only the static site available."""

import importlib.util
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("generation_ui", ROOT / "tests/generation-ui.py")
generation_ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generation_ui)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT / "dist")))
    Thread(target=server.serve_forever, daemon=True).start()
    output = ROOT / "output/playwright/generated-examples"
    output.mkdir(parents=True, exist_ok=True)
    checks = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(**generation_ui.browser_options(playwright, False))
            page = browser.new_page(viewport={"width": 1440, "height": 1050}, accept_downloads=True)
            page.set_default_timeout(20000)
            errors, mutations, api_artifacts, model_fetches = [], [], [], []
            page.on("pageerror", lambda error: errors.append(str(error)))

            def api(route):
                request = route.request
                if request.method != "GET":
                    mutations.append(request.url)
                if "/api/generations/" in request.url:
                    api_artifacts.append(request.url)
                route.fulfill(status=503, json={"error": {"message": "离线示例测试：生成服务不可用。"}})

            page.route("**/api/**", api)
            page.on("request", lambda request: model_fetches.append(request.url) if request.url.endswith("/project.json") else None)
            base = f"http://127.0.0.1:{server.server_port}"
            page.goto(base, wait_until="networkidle")
            expect(page.locator("body")).to_have_attribute("data-ready", "true")
            assert not model_fetches, "The home page must not eagerly fetch multi-megabyte meshes"
            expect(page.locator(".example-selector button")).to_have_count(3)
            for example_id, folder, count, job in [
                ("songen-generated-example", "generated-songen", 589, "7fc9523fa9c54d5f85517c69ffc0392e"),
                ("xmu-gate-example", "generated-gate", 246, "778fe03efefa48b29ee0eeb886284236"),
            ]:
                expected = json.loads((ROOT / "dist/examples" / folder / "project.json").read_text())
                page.locator(f".example-selector [data-example-id='{example_id}']").click()
                expect(page.locator("#builtin-example-preview")).to_have_attribute("data-example-id", example_id)
                expect(page.locator("#open-builtin-example")).to_have_attribute("data-example-id", example_id)
                page.wait_for_function("document.querySelector('#create-example-image').naturalWidth > 0")
                page.locator("#builtin-example-preview").screenshot(path=str(output / f"{folder}-home.png"))
                page.locator("#open-builtin-example").click()
                page.wait_for_function("id => window.brickit.project?.id === id", arg=example_id)
                expect(page.locator("#stage[data-ready='true']")).to_be_visible()
                page.wait_for_function("count => window.brickit.state.engine?.count === count", arg=count)
                actual = page.evaluate("window.brickit.project")
                assert actual["source"] == "example" and actual["draft"] is False
                assert actual["generation"]["jobId"] == job
                assert sorted(actual["pieces"], key=lambda p: p["id"]) == sorted(expected["pieces"], key=lambda p: p["id"])
                assert actual["parts"] == expected["parts"]
                assert actual["originalPieces"] == expected["pieces"]
                expect(page.locator("#model-validation")).to_have_attribute("data-valid", "true")
                page.locator("#build-controls [data-action='restart']").click()
                assert page.evaluate("window.brickit.state.progress") == 0
                page.locator("#build-controls [data-action='next']").click()
                assert page.evaluate("window.brickit.state.progress") == 1
                page.locator("#build-controls [data-action='previous']").click()
                assert page.evaluate("window.brickit.state.progress") == 0
                page.locator(f".stage-jump[data-count='{count}']").click()
                page.wait_for_function("n=>window.brickit.state.progress===n&&window.brickit.state.engine.pieces===n", arg=count)
                page.locator("#stage").screenshot(path=str(output / f"{folder}-model.png"))
                page.locator("[data-action='source']").click()
                expect(page.locator("#source-dialog")).to_be_visible()
                page.wait_for_function("document.querySelector('#source-dialog-image').naturalWidth>0")
                page.locator("#source-dialog [data-action='close-dialog']").click()
                for action, filename in [("export-ldraw", "build.ldr"), ("export-report", "report.json")]:
                    with page.expect_download() as event:
                        page.locator(f"[data-action='{action}']").click()
                    assert Path(event.value.path()).read_bytes() == (ROOT / "dist/examples" / folder / filename).read_bytes()
                before_save = page.evaluate("window.brickit.state.works")
                page.locator("[data-action='save']").first.click()
                page.wait_for_function("n=>window.brickit.state.works>n", arg=before_save)
                page.reload(wait_until="networkidle")
                page.wait_for_function("id=>window.brickit.project?.id===id", arg=example_id)
                assert page.evaluate("window.brickit.project.originalPieces.length") == count
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
                page.screenshot(path=str(output / f"{folder}-mobile.png"), full_page=True)
                page.set_viewport_size({"width": 1440, "height": 1050})
                page.locator("[data-action='new']").first.click()
                checks.append({"id": example_id, "pieces": count, "offline": True, "playback": True, "downloads": True, "reload": True})
            assert not errors, errors
            assert not mutations and not api_artifacts, (mutations, api_artifacts)
            browser.close()
        result = {"passed": True, "examples": checks, "browserErrors": errors, "apiMutations": mutations, "apiArtifacts": api_artifacts}
        (output / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(json.dumps(result, ensure_ascii=False))
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
