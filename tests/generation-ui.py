#!/usr/bin/env python3
"""Browser regression checks for generation UI, using explicit API test fixtures.

Run python3 tests/generation-ui.py; a temporary static server is started automatically.
Pass --base-url to test an already running development server instead.
Requires the already installed Python Playwright and a local Chromium/Chrome.
No AI credentials, paid model calls, external services, or browser downloads.
"""

import argparse
import copy
import csv
import json
import os
from collections import Counter
from io import StringIO
from pathlib import Path
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from playwright.sync_api import sync_playwright, expect

PROJECT_FIXTURE_PATH = None


def fixture_project(job_id="ui-fixture-job"):
    """A known test mesh, with official dimensions/IDs; never a model output."""
    if PROJECT_FIXTURE_PATH:
        project = json.loads(Path(PROJECT_FIXTURE_PATH).read_text(encoding="utf-8"))
        project["id"] = "generated-ui-fixture"
        project["title"] = "UI 测试 · 真实零件导入"
        project["generation"]["jobId"] = job_id
        project["generation"]["artifacts"] = {
            key: f"/api/generations/{job_id}/artifacts/{key}"
            for key in ("ldraw", "source", "report")
        }
        return project
    width, height, depth = 1.16, 0.56, 2.32
    positions = [coordinate for x, y, z in [
        (-1, -1, -1), (1, -1, -1), (1, 1, -1), (-1, 1, -1),
        (-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1),
    ] for coordinate in (x * width / 2, y * height / 2, z * depth / 2)]
    part = {
        "id": "ldraw-3001", "ldrawId": "3001", "name": "Brick 2 x 4 · 测试夹具",
        "w": 2, "d": 4, "h": height, "shape": "mesh", "studs": False,
        "category": "brick", "colors": ["#b91620"],
        "mesh": {"positions": positions, "indices": [
            0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7,
            0, 1, 5, 0, 5, 4, 3, 7, 6, 3, 6, 2,
            0, 4, 7, 0, 7, 3, 1, 2, 6, 1, 6, 5,
        ]},
    }
    piece = {
        "id": "official-test-piece-1", "partId": part["id"], "ldrawId": "3001",
        "colorCode": 4, "color": "#b91620", "w": 2, "d": 4, "h": height,
        "shape": "mesh", "x": 0.34, "y": 0.28, "z": -0.58,
        "rx": 0, "ry": 1.5707963267948966, "rz": 0, "layer": 1,
        "metadata": {"fixture": True, "originalLDrawPosition": [20, -24, 0]},
    }
    now = "2026-11-01T10:00:00.000Z"
    return {
        "id": "generated-ui-fixture", "title": "UI 测试 · 真实零件导入", "source": "ai",
        "sourceImage": None, "createdAt": now, "updatedAt": now,
        "version": 1, "favorite": False, "parts": [part], "pieces": [piece],
        "originalPieces": [copy.deepcopy(piece)],
        "settings": {"size": "small", "style": "classic"},
        "generation": {
            "jobId": job_id,
            "validation": {"ok": True, "checks": {
                "collisions": {"ok": True, "count": 0},
                "floating": {"ok": True, "count": 0},
                "connected": {"ok": True, "count": 1},
            }},
            "artifacts": {
                key: f"/api/generations/{job_id}/artifacts/{key}"
                for key in ("ldraw", "source", "report")
            },
        },
    }


class ApiFixture:
    def __init__(self, availability="available"):
        self.availability = availability
        self.current = "generating"
        self.message = "TEST 后端正在创建真实积木"
        self.posts = []
        self.gets = 0
        self.cancelled = False
        self.history = []
        self.failed_gets_remaining = 0
        self.capability_delay = 0

    def advance(self, stage, message):
        self.current, self.message = stage, message

    def install(self, page):
        def respond(route):
            request = route.request
            pathname = request.url.split("?", 1)[0].split("/api/", 1)[-1]
            if pathname == "generation/capabilities":
                if self.availability == "network-error":
                    route.abort("connectionrefused")
                    return
                if self.capability_delay:
                    time.sleep(self.capability_delay)
                available = self.availability == "available"
                route.fulfill(json={"available": available, "reason": None if available else "TEST 生成服务尚未配置"})
            elif pathname == "generations" and request.method == "POST":
                self.posts.append(request.post_data_json)
                route.fulfill(status=202, json={"id": "ui-fixture-job", "status": "queued"})
            elif pathname == "generations/ui-fixture-job" and request.method == "DELETE":
                self.cancelled = True
                self.advance("cancelled", "TEST 后端确认已取消")
                route.fulfill(json=self.job())
            elif pathname == "generations/ui-fixture-job":
                self.gets += 1
                if self.failed_gets_remaining:
                    self.failed_gets_remaining -= 1
                    route.abort("connectionrefused")
                    return
                route.fulfill(json=self.job())
            elif pathname.startswith("generations/ui-fixture-job/artifacts/"):
                route.fulfill(content_type="text/plain", body="TEST artifact fixture\n")
            else:
                route.fulfill(status=404, json={"error": {"code": "TEST_NOT_FOUND", "message": pathname}})

        page.route("**/api/**", respond)

    def job(self):
        event = {"stage": self.current, "message": self.message, "at": "2026-11-01T10:00:00.000Z"}
        if not self.history or self.history[-1]["message"] != self.message:
            self.history.append(event)
        job = {"id": "ui-fixture-job", "status": self.current, "stage": self.current,
               "message": self.message, "events": copy.deepcopy(self.history)}
        if self.current == "completed":
            job["result"] = fixture_project()
        if self.current == "failed":
            job["error"] = {"code": "VALIDATION_FAILED", "message": "TEST 无法通过碰撞检查"}
        return job


def upload(page):
    image = page.request.get("/assets/songen-preview.png")
    assert image.ok, "The reference photo fixture is available"
    page.locator("#image-input").set_input_files({
        "name": "generation-test.png", "mimeType": "image/png", "buffer": image.body(),
    })
    expect(page.locator("#source-preview.has-image .source-image")).to_be_visible()


def ready(page, base_url):
    page.goto(base_url, wait_until="networkidle")
    page.wait_for_function("window.brickit?.state && document.body.dataset.ready === 'true'")


def assert_no_import(page):
    assert page.evaluate("window.brickit.project") is None, "An unfinished/failed job must not import a project"
    assert page.locator("#page-create").is_visible(), "An unfinished job keeps the photo workflow available"


def assert_same_pieces(actual, expected, message):
    """Display order can change; every field of each stable piece must survive."""
    actual_by_id = {piece["id"]: piece for piece in actual}
    expected_by_id = {piece["id"]: piece for piece in expected}
    assert len(actual_by_id) == len(actual), "Imported piece IDs remain unique"
    assert len(expected_by_id) == len(expected), "Fixture piece IDs are unique"
    assert actual_by_id == expected_by_id, message


def test_success(page, fixture, base_url, checks):
    ready(page, base_url)
    upload(page)
    page.locator("#generation-size").select_option("small")
    page.locator("#generation-style").select_option("classic")
    prompt = page.locator("#generation-prompt")
    if prompt.count():
        prompt.fill("保留红色外观与主要轮廓")
    expect(page.locator("#generate-button")).to_be_enabled()
    page.locator("#generate-button").click()
    expect(page.locator("#generation-message")).to_contain_text(fixture.message)
    assert len(fixture.posts) == 1, "One click submits exactly one generation request"
    request = fixture.posts[0]
    assert request["image"].startswith("data:image/"), "Generation sends the selected photo"
    assert request["size"] == "small" and request["style"] == "classic", "Generation sends the selected settings"
    if prompt.count():
        assert request["prompt"] == "保留红色外观与主要轮廓", "Generation sends the user's instructions"
    assert_no_import(page)
    checks.append("上传照片及设置通过真实请求契约提交，未提前导入示例")

    for stage, message in [
        ("validating", "TEST 检查碰撞、悬空与连接"),
        ("rendering", "TEST 根据真实模型准备预览"),
    ]:
        fixture.advance(stage, message)
        expect(page.locator("#generation-message")).to_contain_text(message, timeout=20000)
        assert_no_import(page)
    checks.append("生成、校验、渲染均显示服务端返回的阶段信息")

    fixture.advance("completed", "TEST 模型已完成")
    page.wait_for_function("window.brickit.project?.id === 'generated-ui-fixture'", timeout=20000)
    expect(page.locator("#stage[data-ready='true']")).to_be_visible(timeout=20000)
    imported = page.evaluate("window.brickit.project")
    expected = fixture_project()
    assert_same_pieces(imported["pieces"], expected["pieces"], "Official poses and piece metadata survive import")
    assert imported["parts"] == expected["parts"], "Official part meshes survive import"
    assert_same_pieces(imported["originalPieces"], expected["originalPieces"], "The generated baseline survives import")
    assert imported["generation"]["validation"]["ok"] is True, "Backend validation report survives import"
    assert imported["generation"]["artifacts"]["ldraw"].endswith("/ldraw"), "The original LDraw remains downloadable"
    assert page.evaluate("window.brickit.state.engine.count") == len(imported["pieces"]), "The renderer loads the generated piece count"
    expect(page.locator("#right-panel")).to_contain_text(imported["parts"][0]["name"])
    checks.append("真实零件编号、网格、姿态、校验报告、原始快照完整导入工作台")

    with page.expect_download() as download:
        page.locator("[data-action='export-parts']").click()
    csv_text = Path(download.value.path()).read_text(encoding="utf-8-sig")
    rows = list(csv.reader(StringIO(csv_text)))
    header = next(index for index, row in enumerate(rows) if row and row[0] == "零件编号")
    actual_counts = Counter()
    for row in rows[header + 1:]:
        if row and row[0] != "总计":
            actual_counts[row[0]] += int(row[4])
    expected_counts = Counter(str(piece["ldrawId"]) for piece in expected["pieces"])
    assert actual_counts == expected_counts, "Parts export preserves every official ID and quantity"
    checks.append("零件CSV保留官方编号，不改写成示例零件编号")

    page.locator("[data-action='save']").first.click()
    expect(page.locator("#toast")).to_contain_text("保存", timeout=20000)
    page.wait_for_function("window.brickit.state.works >= 1")
    page.reload(wait_until="networkidle")
    page.wait_for_function("window.brickit.project?.id === 'generated-ui-fixture'", timeout=20000)
    restored = page.evaluate("window.brickit.project")
    for key in ("parts", "pieces", "originalPieces", "generation"):
        message = f"{key} survives persistence and a browser reload"
        if key in ("pieces", "originalPieces"):
            assert_same_pieces(restored[key], imported[key], message)
        else:
            assert restored[key] == imported[key], message
    checks.append("生成作品保存及页面重载保留完整网格与生成记录")

    expect(page.locator("[data-action='export-ldraw']")).to_be_enabled()
    with page.expect_download() as ldraw_download:
        page.locator("[data-action='export-ldraw']").click()
    assert ldraw_download.value.suggested_filename.endswith(".ldr"), "The original model downloads as LDraw"

    page.locator("button[data-mode='edit']").first.click()
    canvas = page.locator("#scene canvas")
    bounds = canvas.bounding_box()
    selected = False
    for x, y in [(0.5, 0.62), (0.5, 0.7), (0.5, 0.77), (0.43, 0.7), (0.57, 0.7), (0.5, 0.55)]:
        canvas.click(position={"x": bounds["width"] * x, "y": bounds["height"] * y})
        if page.evaluate("Boolean(window.brickit.state.selectedId)"):
            selected = True
            break
    assert selected, "The actual imported mesh can be selected through the canvas"
    page.locator("#right-panel [data-action='piece-color'][data-color='#648a35']").click()
    expect(page.locator("#model-validation")).to_have_attribute("data-valid", "false")
    expect(page.locator("[data-action='export-ldraw']")).to_be_disabled()
    expect(page.locator("#model-validation-label")).to_contain_text("尚未重新校验")
    page.locator("#editor-recovery [data-action='restore-original']").click()
    page.locator("#restore-dialog [data-action='confirm-restore-original']").click()
    expect(page.locator("#model-validation")).to_have_attribute("data-valid", "true")
    expect(page.locator("[data-action='export-ldraw']")).to_be_enabled()
    checks.append("真实网格可点击编辑；修改后校验与原始下载失效，还原后恢复")

    page.locator("[data-action='return-create']").click()
    assert page.locator("#page-create").is_visible(), "The creator remains reachable after generation"


def test_cancel(page, fixture, base_url, checks):
    ready(page, base_url)
    upload(page)
    expect(page.locator("#generate-button")).to_be_enabled()
    page.locator("#generate-button").click()
    expect(page.locator("#generation-message")).to_contain_text(fixture.message)
    page.locator("[data-action='cancel-generation']").click()
    page.wait_for_function("!document.querySelector('#generation-dialog')?.open || /取消/.test(document.querySelector('#generation-message')?.textContent || '')")
    assert fixture.cancelled, "Cancel sends DELETE to the active backend job"
    assert_no_import(page)
    fixture.advance("completed", "TEST 取消后的迟到响应")
    old_gets = fixture.gets
    page.wait_for_timeout(1800)
    assert_no_import(page)
    assert fixture.gets <= old_gets + 1, "Cancel stops further polling"
    checks.append("取消通知后端、停止轮询，迟到结果不会覆盖工作台")


def test_failure(page, fixture, base_url, checks):
    ready(page, base_url)
    upload(page)
    expect(page.locator("#generate-button")).to_be_enabled()
    page.locator("#generate-button").click()
    expect(page.locator("#generation-message")).to_contain_text(fixture.message)
    fixture.advance("failed", "TEST 无法通过碰撞检查")
    expect(page.locator("#generation-message")).to_contain_text("TEST 无法通过碰撞检查", timeout=20000)
    assert_no_import(page)
    assert page.locator("#source-preview.has-image").is_visible(), "A failed job retains the photo for retry"
    checks.append("后端失败显示具体原因、保留图片，不导入伪结果")


def test_unavailable(page, fixture, base_url, checks):
    ready(page, base_url)
    upload(page)
    status = page.locator("#generation-status")
    expect(status).to_contain_text("TEST 生成服务尚未配置")
    if page.locator("#generate-button").is_enabled():
        page.locator("#generate-button").click()
    assert len(fixture.posts) == 0, "An unavailable backend does not start a job"
    assert_no_import(page)
    page.locator("[data-action='open-example']").first.click()
    page.wait_for_function("window.brickit.project?.id === 'songen-example'")
    expect(page.locator("#stage[data-ready='true']")).to_be_visible(timeout=20000)
    checks.append("服务未配置时明确说明原因、不会假生成，颂恩楼示例仍可使用")


def test_network_error(page, fixture, base_url, checks):
    ready(page, base_url)
    upload(page)
    expect(page.locator("#generation-status")).to_contain_text("服务", timeout=20000)
    if page.locator("#generate-button").is_enabled():
        page.locator("#generate-button").click()
    assert len(fixture.posts) == 0, "A capability network error does not submit or fabricate a generation"
    assert_no_import(page)
    checks.append("服务不可连接时不提交任务、不产生模型")


def test_resume(page, fixture, base_url, checks):
    ready(page, base_url)
    upload(page)
    page.locator("#generate-button").click()
    expect(page.locator("#generation-message")).to_contain_text(fixture.message)
    fixture.failed_gets_remaining = 3
    expect(page.locator("#generation-title")).to_contain_text("中断", timeout=20000)
    assert len(fixture.posts) == 1, "Poll failures never resubmit a paid generation"
    page.locator("[data-action='resume-generation']").click()
    expect(page.locator("#generation-message")).to_contain_text(fixture.message)
    assert len(fixture.posts) == 1, "Manual reconnection reuses the existing job"
    page.reload(wait_until="networkidle")
    page.wait_for_function("window.brickit.state.generation?.id === 'ui-fixture-job'")
    expect(page.locator("#generation-status")).to_contain_text(fixture.message, timeout=20000)
    assert len(fixture.posts) == 1, "Reload resumes the existing job without a second submission"
    fixture.advance("completed", "TEST 续传任务已完成")
    page.wait_for_function("window.brickit.project?.id === 'generated-ui-fixture'", timeout=20000)
    assert page.evaluate("localStorage.getItem('brickit-generation-job')") is None, "Completion clears the pending job marker"
    checks.append("轮询断线可重连、刷新可续传，均不重复提交生成任务")


def test_double_click(page, fixture, base_url, checks):
    ready(page, base_url)
    upload(page)
    fixture.capability_delay = 0.25
    page.locator("#generate-button").dblclick()
    expect(page.locator("#generation-message")).to_contain_text(fixture.message)
    page.wait_for_timeout(800)
    assert len(fixture.posts) == 1, f"A rapid double click must not create multiple paid jobs (got {len(fixture.posts)})"
    checks.append("快速双击也只提交一个生成任务")


def browser_options(playwright, headed):
    options = {"headless": not headed, "args": ["--enable-unsafe-swiftshader"]}
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


def main():
    global PROJECT_FIXTURE_PATH
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", help="Existing server URL; omit to start an isolated static server")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--project-fixture", help="Optional real BrickLayer-generated project JSON to test instead of the built-in mesh fixture")
    parser.add_argument("--only", choices=["success", "cancel", "failure", "unavailable", "network-error", "resume", "double-click"])
    args = parser.parse_args()
    temporary_server = None
    if args.base_url is None:
        class QuietHandler(SimpleHTTPRequestHandler):
            def log_message(self, *_args):
                pass
        handler = partial(QuietHandler, directory=str(Path(__file__).resolve().parent.parent / "dist"))
        temporary_server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        Thread(target=temporary_server.serve_forever, daemon=True).start()
        args.base_url = f"http://127.0.0.1:{temporary_server.server_port}"
    PROJECT_FIXTURE_PATH = args.project_fixture
    cases = [
        ("success", test_success, "available"),
        ("cancel", test_cancel, "available"),
        ("failure", test_failure, "available"),
        ("unavailable", test_unavailable, "unavailable"),
        ("network-error", test_network_error, "network-error"),
        ("resume", test_resume, "available"),
        ("double-click", test_double_click, "available"),
    ]
    results = []
    started = time.monotonic()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**browser_options(playwright, args.headed))
        try:
            for name, test, availability in cases:
                if args.only and args.only != name:
                    continue
                context = browser.new_context(base_url=args.base_url, viewport={"width": 1440, "height": 1000}, accept_downloads=True)
                page = context.new_page()
                page.set_default_timeout(12000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                fixture = ApiFixture(availability)
                fixture.install(page)
                checks = []
                try:
                    test(page, fixture, args.base_url, checks)
                    assert not errors, f"No uncaught browser errors: {errors}"
                    result = {"case": name, "passed": True, "checks": checks}
                except Exception as error:
                    destination = Path("output/playwright")
                    destination.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(destination / f"generation-{name}-failure.png"), full_page=True)
                    (destination / f"generation-{name}-failure.html").write_text(page.content(), encoding="utf-8")
                    result = {"case": name, "passed": False, "error": str(error), "checks": checks, "browserErrors": errors}
                finally:
                    context.close()
                print(json.dumps(result, ensure_ascii=False), flush=True)
                results.append(result)
        finally:
            browser.close()
    passed = sum(result["passed"] for result in results)
    print(json.dumps({"passed": passed, "total": len(results), "seconds": round(time.monotonic() - started, 2)}, ensure_ascii=False))
    if temporary_server is not None:
        temporary_server.shutdown()
        temporary_server.server_close()
    raise SystemExit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
