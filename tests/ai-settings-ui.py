#!/usr/bin/env python3
"""Exercise local model-settings interactions with deterministic provider fixtures."""
import importlib.util
import json
from pathlib import Path
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('generation_ui', ROOT / 'tests/generation-ui.py')
generation_ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generation_ui)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def main():
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(ROOT / 'dist')))
    Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    output = ROOT / 'output/playwright'
    output.mkdir(parents=True, exist_ok=True)
    calls = []
    fail_models = False
    capabilities = []

    def api(route):
        nonlocal fail_models
        request = route.request
        path = request.url.split('/api/', 1)[1]
        if path == 'ai/settings':
            if request.method == 'GET':
                body = {'baseUrl': 'https://models.example/v1', 'model': 'gpt-6-astra', 'hasApiKey': True}
            else:
                value = request.post_data_json
                calls.append(('settings', value))
                body = {'baseUrl': value['baseUrl'].rstrip('/') + ('/v1' if not value['baseUrl'].rstrip('/').endswith('/v1') else ''), 'model': value['model'], 'hasApiKey': True}
            route.fulfill(json=body)
        elif path == 'ai/models':
            calls.append(('models', request.post_data_json))
            if fail_models:
                route.fulfill(status=401, json={'error': {'code':'MODEL_AUTH_FAILED', 'message':'密钥无效，请检查后重试。'}})
            else:
                route.fulfill(json={'models': [{'id':'gpt-6-astra'}, {'id':'gpt-6-sol'}]})
        elif path == 'generation/capabilities':
            capabilities.append(True)
            route.fulfill(json={'available': False, 'reason':'测试环境不调用生成模型。', 'limits':{'parts':{'small':80,'medium':250,'large':600},'costUsd':None,'timeoutSeconds':None}})
        else:
            route.fulfill(status=404, json={'error':{'message':'测试环境中未使用的接口。'}})

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(**generation_ui.browser_options(p, False))
            page = browser.new_page(viewport={'width':1440,'height':1050}, device_scale_factor=1)
            errors=[]
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/api/**', api)
            page.goto(base)
            page.wait_for_load_state('networkidle')
            expect(page.locator('body')).to_have_attribute('data-ready','true')
            expect(page.locator('[data-example-id="apple-example"]')).to_have_count(0)
            expect(page.locator('#example-name')).to_contain_text('颂恩楼')
            trigger=page.get_by_role('button', name='用户与 AI 模型设置')
            panel=page.locator('#ai-settings-panel')
            trigger.hover()
            expect(panel).to_be_visible()
            expect(page.locator('#ai-model')).to_have_value('gpt-6-astra')
            expect(page.locator('#ai-api-key')).to_have_value('')
            expect(page.locator('#ai-settings-save')).to_be_enabled()
            expect(panel).to_have_css('opacity','1')
            page.screenshot(path=str(output/'ai-settings-desktop.png'), full_page=True)
            # Merely leaving the icon closes the hover panel; keyboard editing keeps it open.
            page.mouse.move(500,500)
            expect(panel).to_be_hidden()
            trigger.focus()
            trigger.press('ArrowDown')
            expect(panel).to_be_visible()
            page.locator('#ai-base-url').focus()
            page.mouse.move(500,500)
            expect(panel).to_be_visible()
            page.locator('#ai-base-url').fill('https://new-models.example')
            expect(page.locator('#ai-settings-save')).to_be_disabled()
            page.locator('#ai-api-key').fill('fixture-key-not-a-real-secret')
            page.get_by_role('button',name='获取模型',exact=True).click()
            expect(page.locator('#ai-settings-status')).to_contain_text('已获取 2 个模型')
            page.locator('#ai-model').select_option('gpt-6-sol')
            page.get_by_role('button',name='保存设置',exact=True).click()
            expect(page.locator('#ai-settings-status')).to_contain_text('设置已保存')
            assert calls[-1] == ('settings', {'baseUrl':'https://new-models.example','apiKey':'fixture-key-not-a-real-secret','model':'gpt-6-sol'})
            expect(page.locator('#ai-api-key')).to_have_value('')
            expect(page.locator('#ai-base-url')).to_have_value('https://new-models.example/v1')
            assert len(capabilities) >= 2, 'Saving must refresh generation availability'
            page.locator('#ai-model').press('Escape')
            expect(panel).to_be_hidden()
            expect(trigger).to_be_focused()
            # A failed discovery cannot save stale models, and users can retry.
            trigger.press('ArrowDown')
            fail_models=True
            page.locator('#ai-base-url').fill('https://failed.example/v1')
            page.get_by_role('button',name='获取模型',exact=True).click()
            expect(page.locator('#ai-settings-status')).to_contain_text('密钥无效')
            expect(page.locator('#ai-settings-save')).to_be_disabled()
            fail_models=False
            page.get_by_role('button',name='获取模型',exact=True).click()
            expect(page.locator('#ai-model')).to_have_value('gpt-6-sol')
            expect(page.locator('#ai-settings-save')).to_be_enabled()
            page.get_by_role('button',name='关闭模型设置').click()
            # The same controls work on touch-sized screens, with no clipped fields.
            page.set_viewport_size({'width':390,'height':844})
            trigger.click()
            expect(panel).to_be_visible()
            box=panel.bounding_box()
            assert box['x'] >= 0 and box['x'] + box['width'] <= 390
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            expect(panel).to_have_css('opacity','1')
            page.screenshot(path=str(output/'ai-settings-mobile.png'),full_page=True)
            expect(page.locator('#ai-base-url')).to_be_editable()
            assert not errors, errors
            browser.close()
        print(json.dumps({'passed':['苹果示例已移除','悬停与键盘打开','移出关闭与编辑时保持打开','地址变更使旧模型失效','模型发现与切换','保存设置并清空密钥输入','刷新生成可用性','失败可重试','移动端无横向溢出'],'screenshots':[str(output/'ai-settings-desktop.png'),str(output/'ai-settings-mobile.png')]}, ensure_ascii=False))
    finally:
        server.shutdown()


if __name__=='__main__':
    main()
