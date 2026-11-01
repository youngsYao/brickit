#!/usr/bin/env python3
"""Exercise the official LDraw catalog against a running, real local backend.

Run `npm run dev`, then `python3 tests/parts-catalog-ui.py`.
Uses no catalog fixtures, network interception, AI calls, or paid services.
"""

import argparse
import json
import os
from pathlib import Path
import time

from playwright.sync_api import sync_playwright, expect


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


def card_ids(page):
    return page.locator("#catalog-grid [data-action='use-ldraw-part']").evaluate_all(
        "buttons => buttons.map(button => `ldraw-${button.dataset.ldrawId}`)"
    )


def wait_images(page, minimum=3):
    page.wait_for_function("""minimum => {
        const cards = [...document.querySelectorAll('#catalog-grid .part-card')];
        const ready = cards.filter(card => [...card.querySelectorAll('img')].some(image =>
            image.src.startsWith('data:image/') && image.complete && image.naturalWidth > 20
        ));
        return ready.length >= Math.min(minimum, cards.length) && cards.length > 0;
    }""", arg=minimum, timeout=60000)


def assert_nonblank_thumbnail(page):
    pixels = page.locator("#catalog-grid .part-card").first.evaluate("""card => {
        const image = [...card.querySelectorAll('img')].find(img => img.src.startsWith('data:image/'));
        const canvas = document.createElement('canvas'); canvas.width = 80; canvas.height = 80;
        const context = canvas.getContext('2d', {willReadFrequently:true});
        context.drawImage(image, 0, 0, 80, 80);
        const values = context.getImageData(0, 0, 80, 80).data;
        const colors = new Set(); let visible = 0;
        for (let i = 0; i < values.length; i += 4) {
            if (values[i+3] > 15) { visible++; colors.add(`${values[i] >> 3},${values[i+1] >> 3},${values[i+2] >> 3}`); }
        }
        return {visible, colors:colors.size, width:image.naturalWidth, height:image.naturalHeight};
    }""")
    assert pixels["visible"] > 80 and pixels["colors"] > 8, f"The official thumbnail contains rendered geometry: {pixels}"
    return pixels


def complete_desktop_previews(page):
    """Scroll each row into view so the review screenshot has no lazy placeholders."""
    count = page.locator("#catalog-grid .ldraw-part-card").count()
    for index in range(0, count, 6):
        page.locator("#catalog-grid .ldraw-part-card").nth(index).scroll_into_view_if_needed()
        page.wait_for_function("""index => [...document.querySelectorAll('#catalog-grid .ldraw-part-preview')]
            .slice(index, index + 6).every(node => node.dataset.previewState === 'ready')""", arg=index, timeout=60000)
    page.evaluate("scrollTo(0,0)")


def open_catalog(page):
    page.locator(".primary-nav [data-page='catalog']").click()
    expect(page.locator("#page-catalog")).to_be_visible()
    page.wait_for_function("document.querySelectorAll('#catalog-grid [data-action=\"use-ldraw-part\"]').length > 0", timeout=30000)


def next_button(page):
    return page.locator("[data-catalog-page='next']")


def previous_button(page):
    return page.locator("[data-catalog-page='previous']")


def place_part(page):
    canvas = page.locator("#scene canvas")
    bounds = canvas.bounding_box()
    before = page.evaluate("window.brickit.state.total")
    # Prefer free baseplate positions, outside the existing model silhouette.
    positions = [(0.84, 0.78), (0.16, 0.78), (0.84, 0.64), (0.16, 0.64),
                 (0.74, 0.85), (0.26, 0.85), (0.5, 0.9), (0.9, 0.52), (0.1, 0.52)]
    for x, y in positions:
        position = {"x": bounds["width"] * x, "y": bounds["height"] * y}
        canvas.hover(position=position)
        page.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
        if page.evaluate("window.brickit.state.engine.placementValid"):
            canvas.click(position=position)
            if page.evaluate("window.brickit.state.total") == before + 1:
                return page.evaluate("window.brickit.state.selectedId")
    raise AssertionError("An official catalog part can be placed on the real workbench through the canvas")


def run(page, base_url, artifacts, checks):
    responses = []
    page.on("response", lambda response: responses.append({"url": response.url, "status": response.status})
            if "/api/ldraw/" in response.url or "/api/parts" in response.url else None)
    page.goto(base_url, wait_until="networkidle")
    page.wait_for_function("window.brickit?.state && document.body.dataset.ready === 'true'")

    first_page = page.request.get("/api/parts?offset=0&limit=24")
    assert first_page.ok, f"The real parts API responds (HTTP {first_page.status})"
    catalog = first_page.json()
    assert catalog["available"] and catalog["libraryTotal"] > 100 and catalog["items"], "The actual LDraw catalog is installed"
    checks.append(f"真实零件目录可读取，共 {catalog['total']} 种可浏览零件（官方库 {catalog['libraryTotal']} 个文件）")

    open_catalog(page)
    wait_images(page)
    first_ids = card_ids(page)
    assert first_ids and all(part_id.startswith("ldraw-") for part_id in first_ids), "Default cards reference official LDraw identities"
    assert any("/api/ldraw/" in response["url"] and response["status"] == 200 for response in responses), "Thumbnail rendering reads actual LDraw .dat dependencies"
    pixels = assert_nonblank_thumbnail(page)
    complete_desktop_previews(page)
    page.screenshot(path=str(artifacts / "parts-catalog-desktop.png"), full_page=True)
    checks.append(f"默认卡片由真实 LDraw 渲染为图片（{pixels['width']}×{pixels['height']}）")

    expect(next_button(page)).to_be_enabled()
    next_button(page).click()
    page.wait_for_function("""previous => {
        const ids = [...document.querySelectorAll('#catalog-grid [data-action=\"use-ldraw-part\"]')].map(button => `ldraw-${button.dataset.ldrawId}`);
        return ids.length > 0 && ids.join(',') !== previous.join(',');
    }""", arg=first_ids, timeout=30000)
    expect(previous_button(page)).to_be_enabled()
    previous_button(page).click()
    page.wait_for_function("previous => [...document.querySelectorAll('#catalog-grid [data-action=\"use-ldraw-part\"]')].map(button => `ldraw-${button.dataset.ldrawId}`).join(',') === previous.join(',')", arg=first_ids, timeout=30000)
    checks.append("下一页及上一页请求真实分页数据，卡片内容正确切换")

    categories = catalog["categories"]
    category = next((item for item in categories if item["id"] == "plate"), categories[0])
    with page.expect_response(lambda response: "/api/parts?" in response.url and f"category={category['id']}" in response.url and response.status == 200, timeout=30000) as category_response:
        page.locator("#catalog-category").select_option(category["id"])
    filtered = category_response.value.json()
    assert filtered["items"] and all(part["category"] == category["id"] for part in filtered["items"]), "Category filtering is performed by the actual catalog API"
    checks.append(f"分类筛选读取真实 {category['label']} 零件")

    page.locator("#catalog-category").select_option("all")
    with page.expect_response(lambda response: "/api/parts?" in response.url and "q=3001" in response.url and response.status == 200, timeout=30000) as search_response:
        page.locator("#catalog-search").fill("3001")
    assert str(search_response.value.json()["items"][0]["ldrawId"]) == "3001", "The actual API ranks an exact number before substring matches"
    exact = page.locator("#catalog-grid [data-action='use-ldraw-part'][data-ldraw-id='3001']")
    expect(exact).to_be_visible(timeout=30000)
    wait_images(page, 1)
    assert card_ids(page)[0] == "ldraw-3001", "An exact part-number match is ranked first"
    exact_card = page.locator("#catalog-grid .part-card[data-ldraw-part='3001']")
    expect(exact_card).to_contain_text("3001")
    exact.click()
    expect(page.locator("#stage[data-ready='true']")).to_be_visible(timeout=30000)
    page.wait_for_function("window.brickit.state.placement === 'ldraw-3001'", timeout=30000)
    checks.append("搜索3001精确命中真实零件，并能进入工作台放置")

    added_id = place_part(page)
    page.keyboard.press("Escape")
    project = page.evaluate("window.brickit.project")
    added = next(piece for piece in project["pieces"] if piece["id"] == added_id)
    assert added["partId"] == "ldraw-3001" and str(added["ldrawId"]) == "3001", "Placement preserves the official part number"
    official = next(part for part in project["parts"] if part["id"] == "ldraw-3001")
    assert len(official["mesh"]["positions"]) >= 9 and official["mesh"]["indices"], "The project embeds real descriptor geometry for persistence"
    page.locator("[data-action='save']").first.click()
    expect(page.locator("#toast")).to_contain_text("保存", timeout=30000)
    project_id = page.evaluate("window.brickit.project.id")
    page.reload(wait_until="networkidle")
    page.wait_for_function("projectId => window.brickit?.project?.id === projectId", arg=project_id, timeout=30000)
    restored = page.evaluate("window.brickit.project")
    assert next(piece for piece in restored["pieces"] if piece["id"] == added_id) == added, "Placed official pieces survive reload without geometry or pose changes"
    assert next(part for part in restored["parts"] if part["id"] == "ldraw-3001") == official, "The descriptor survives IndexedDB storage and reload"
    checks.append("真实零件通过画布放置，官方编号/网格/位置保存后刷新完整恢复")

    page.set_viewport_size({"width": 390, "height": 844})
    open_catalog(page)
    with page.expect_response(lambda response: "/api/parts?" in response.url and "q=3001" in response.url and response.status == 200, timeout=30000):
        page.locator("#catalog-search").fill("3001")
    expect(page.locator("#catalog-grid [data-action='use-ldraw-part'][data-ldraw-id='3001']")).to_be_visible(timeout=30000)
    wait_images(page, 1)
    layout = page.evaluate("({width:innerWidth,scrollWidth:document.documentElement.scrollWidth})")
    assert layout["scrollWidth"] <= layout["width"], f"The mobile catalog fits the viewport: {layout}"
    page.screenshot(path=str(artifacts / "parts-catalog-mobile.png"), full_page=True)
    checks.append("390px手机布局保留真实预览与操作入口，无横向溢出")

    # LDrawLoader probes several library roots. A replaced page can still have
    # old queued probes, so HTTP diagnostics must not reject those intermediate
    # 404s. Actual missing geometry is represented by a final preview error.
    failed = [response for response in responses if response["status"] >= 400 and "/api/parts" in response["url"]]
    assert not failed, f"The actual catalog metadata and descriptor requests succeed: {failed}"
    preview_errors = page.locator("#catalog-grid .ldraw-part-preview[data-preview-state='error']").evaluate_all("nodes => nodes.map(node => node.dataset.ldrawId)")
    assert not preview_errors, f"Current catalog cards have no final LDraw loading errors: {preview_errors}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:4173")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    artifacts = Path(__file__).resolve().parent.parent / "output/playwright"
    artifacts.mkdir(parents=True, exist_ok=True)
    checks, errors = [], []
    started = time.monotonic()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(**browser_options(playwright, args.headed))
        context = browser.new_context(base_url=args.base_url, viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        page = context.new_page()
        page.set_default_timeout(15000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            run(page, args.base_url, artifacts, checks)
            assert not errors, f"No uncaught browser errors: {errors}"
            result = {"passed": True, "checks": checks, "seconds": round(time.monotonic() - started, 2)}
        except Exception as error:
            page.screenshot(path=str(artifacts / "parts-catalog-failure.png"), full_page=True)
            (artifacts / "parts-catalog-failure.html").write_text(page.content(), encoding="utf-8")
            result = {"passed": False, "error": str(error), "checks": checks, "browserErrors": errors, "seconds": round(time.monotonic() - started, 2)}
        finally:
            context.close()
            browser.close()
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
