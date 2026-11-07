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
    fail_image_models = False
    hold_image_models = False
    pending_image_models = []
    image_settings = {'baseUrl': 'https://images.example/v1', 'model': 'gpt-image-2.6-sunburst', 'hasApiKey': True}
    capabilities = []

    def api(route):
        nonlocal fail_models, image_settings
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
        elif path == 'ai/image/settings':
            if request.method == 'POST':
                value = request.post_data_json
                calls.append(('image-settings', value))
                image_settings = {'baseUrl': value['baseUrl'].rstrip('/') + ('/v1' if not value['baseUrl'].rstrip('/').endswith('/v1') else ''), 'model': value['model'], 'hasApiKey': True}
            route.fulfill(json=image_settings)
        elif path == 'ai/image/models':
            calls.append(('image-models', request.post_data_json))
            if hold_image_models:
                pending_image_models.append(route)
            elif fail_image_models:
                route.fulfill(status=502,json={'error':{'message':'暂时无法读取生图模型列表。'}})
            else:
                route.fulfill(json={'models':[{'id':'gpt-image-2.6-sunburst'},{'id':'fixture-image-alternative'}]})
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
            expect(panel).to_have_css('position','fixed')
            expect(panel).to_have_css('background-color','rgb(252, 251, 247)')
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

            # Independent image settings keep modelling drafts and keys intact.
            builder_value=page.locator('#ai-model').input_value()
            page.get_by_role('tab',name='参考图生成').click()
            expect(page.locator('#ai-settings-form')).to_be_hidden()
            expect(page.locator('#ai-image-model')).to_have_value('gpt-image-2.6-sunburst')
            expect(page.locator('#ai-image-api-key')).to_have_value('')
            page.locator('#ai-image-base-url').fill('https://new-images.example')
            page.locator('#ai-image-api-key').fill('fixture-image-key-not-a-secret')
            expect(page.locator('#ai-image-settings-save')).to_be_disabled()
            page.locator('#ai-image-models-refresh').click()
            expect(page.locator('#ai-image-settings-status')).to_contain_text('已获取 2 个模型')
            page.locator('#ai-image-model').select_option('fixture-image-alternative')
            page.locator('#ai-image-settings-save').click()
            expect(page.locator('#ai-image-settings-status')).to_contain_text('生图设置已保存')
            assert calls[-1]==('image-settings',{'baseUrl':'https://new-images.example','apiKey':'fixture-image-key-not-a-secret','model':'fixture-image-alternative'})
            expect(page.locator('#ai-image-api-key')).to_have_value('')
            expect(page.locator('#ai-image-base-url')).to_have_value('https://new-images.example/v1')
            assert page.evaluate('!Object.keys(localStorage).some(k=>String(localStorage.getItem(k)).includes("fixture-image-key-not-a-secret"))')
            # Existing settings stay visible during a temporary discovery outage.
            fail_image_models=True
            page.locator('#ai-image-models-refresh').click()
            expect(page.locator('#ai-image-settings-status')).to_contain_text('暂时无法读取生图模型列表')
            expect(page.locator('#ai-image-model')).to_have_value('fixture-image-alternative')
            expect(page.locator('#ai-image-settings-save')).to_be_enabled()
            fail_image_models=False
            # An old response cannot re-enable models for a newly edited address.
            hold_image_models=True
            page.locator('#ai-image-models-refresh').click()
            expect(page.locator('#ai-image-models-refresh')).to_be_disabled()
            assert len(pending_image_models)==1
            page.locator('#ai-image-base-url').fill('https://final-images.example/v1')
            pending_image_models.pop().fulfill(json={'models':[{'id':'stale-image-result'}]})
            expect(page.locator('#ai-image-model')).to_be_disabled()
            expect(page.locator('#ai-image-settings-save')).to_be_disabled()
            hold_image_models=False
            page.locator('#ai-image-models-refresh').click()
            expect(page.locator('#ai-image-model')).to_have_value('fixture-image-alternative')
            page.locator('#ai-image-settings-save').click()
            expect(page.locator('#ai-image-settings-status')).to_contain_text('生图设置已保存')
            assert calls[-1]==('image-settings',{'baseUrl':'https://final-images.example/v1','apiKey':'','model':'fixture-image-alternative'})
            image_tab=page.get_by_role('tab',name='参考图生成')
            image_tab.focus();image_tab.press('ArrowLeft')
            expect(page.get_by_role('tab',name='积木建模')).to_be_focused()
            expect(page.locator('#ai-model')).to_have_value(builder_value)
            expect(page.locator('#ai-api-key')).to_have_value('')
            page.get_by_role('tab',name='积木建模').press('ArrowRight')
            expect(image_tab).to_be_focused()

            # The panel stays inside short/zoomed windows without moving the header.
            # Reproduce the reported "My works" screen for the viewport sweep.
            image_tab.press('Escape')
            page.get_by_role('button',name='我的作品',exact=True).click()
            trigger.press('ArrowDown')
            for width,height in [(1440,1050),(2360,1638),(1000,620),(820,420),(720,450),(390,844),(320,568)]:
                page.set_viewport_size({'width':width,'height':height})
                expect(panel).to_be_visible()
                box=panel.bounding_box()
                assert box['x']>=0 and box['y']>=0 and box['x']+box['width']<=width+1 and box['y']+box['height']<=height+1, (width,height,box)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), (width,height,page.evaluate('({scroll:document.documentElement.scrollWidth,inner:innerWidth})'))
                header_before=page.locator('#app-header').bounding_box()['height']
                for selector in ['#ai-image-base-url','#ai-image-api-key','#ai-image-model','#ai-image-settings-save']:
                    field=page.locator(selector);field.scroll_into_view_if_needed()
                    expect(field).to_be_visible()
                    field_box=field.bounding_box()
                    visible_box=panel.bounding_box()
                    assert field_box['y']>=visible_box['y']-1 and field_box['y']+field_box['height']<=visible_box['y']+visible_box['height']+1, (width,height,selector,visible_box,field_box)
                expect(page.locator('#ai-image-base-url')).to_be_editable()
                page.locator('#ai-image-settings-save').press('Escape')
                expect(panel).to_be_hidden()
                assert abs(page.locator('#app-header').bounding_box()['height']-header_before)<1
                trigger.press('ArrowDown')
            for size,label in [({'width':1440,'height':1050},'desktop'),({'width':390,'height':844},'mobile')]:
                page.set_viewport_size(size)
                panel.evaluate('e=>e.scrollTop=0')
                page.screenshot(path=str(output/f'ai-image-settings-{label}.png'),full_page=True)
            assert not errors, errors
            browser.close()
        print(json.dumps({'passed':['悬停、点击与键盘打开','面板固定定位与独立样式','建模与生图分别发现、选择、保存模型','密钥保存后清空且不写入浏览器存储','切换配置保留各自状态','发现暂不可用时仍展示当前模型','旧响应不会覆盖新地址','保存后刷新生成可用性','七种窗口尺寸均可访问全部字段','页面高度不被面板撑开','桌面与手机无溢出'],'screenshots':[str(output/'ai-settings-desktop.png'),str(output/'ai-image-settings-desktop.png'),str(output/'ai-image-settings-mobile.png')]}, ensure_ascii=False))
    finally:
        server.shutdown()


if __name__=='__main__':
    main()
