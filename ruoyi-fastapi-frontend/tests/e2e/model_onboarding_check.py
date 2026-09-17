"""Single-page model onboarding through real Vue components and isolated API fixtures."""
import asyncio
import copy
import json
import os
import re
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect
from platform_shell_check import response_for
from knowledge_check import button

BASE_URL = os.environ.get('MODEL_ONBOARDING_BASE_URL', 'http://127.0.0.1:12580')


class Fixture:
    def __init__(self, *, saved=True):
        self.calls = []
        self.credentials = [{'credentialName': 'openai-saved', 'provider': 'openai'}] if saved else []
        self.deployments = []
        self.models = []
        self.fail_publish = False
        self.test_delay = 0
        self.lose_responses = set()
        self.fail_confirmation_for = None
        self.pending_read_failure = None

    def respond(self, path, method, body):
        if path == '/getInfo':
            return {**response_for(path, method, 'all'), 'permissions': ['*:*:*']}
        if not path.startswith('/ai/model-engine') and not path.startswith('/ai/models'):
            return response_for(path, method, 'all')
        self.calls.append((path, method, copy.deepcopy(body)))
        if path.endswith('/providers'):
            return {'code': 200, 'data': [{'provider': 'OpenAI', 'displayName': 'OpenAI', 'litellmProvider': 'openai',
                'credentialFields': [{'fieldKey': 'api_key', 'label': 'API Key', 'fieldType': 'password', 'required': True},
                                     {'fieldKey': 'api_base', 'label': 'API 地址', 'fieldType': 'text', 'required': False}]}]}
        if path.endswith('/credentials'):
            if method == 'GET' and self.pending_read_failure == 'credential':
                self.pending_read_failure = None
                return {'code': 500, 'msg': '模拟核对失败'}
            if method == 'POST':
                self.credentials.append({'credentialName': body['credentialName'], 'provider': 'openai'})
                if 'credential' in self.lose_responses:
                    self.lose_responses.remove('credential')
                    if self.fail_confirmation_for == 'credential':
                        self.pending_read_failure = 'credential'
                    return {'code': 500, 'msg': '模拟响应丢失'}
                return {'code': 200, 'data': {}}
            return {'code': 200, 'data': self.credentials}
        if path.endswith('/test-connection'):
            assert body['modelType'] in ('chat', 'embedding', 'rerank')
            return {'code': 200, 'data': {'success': True, 'latencyMs': 18, 'message': '连接成功'}}
        if path.endswith('/deployments'):
            if method == 'POST':
                deployment = {**body, 'deploymentId': 'deployment-created', 'provider': 'openai', 'blocked': False}
                self.deployments.append(deployment)
                if 'deployment' in self.lose_responses:
                    self.lose_responses.remove('deployment')
                    return {'code': 500, 'msg': '模拟响应丢失'}
                return {'code': 200, 'data': deployment}
            return {'code': 200, 'data': self.deployments}
        if path == '/ai/models':
            if method == 'GET' and self.pending_read_failure == 'model':
                self.pending_read_failure = None
                return {'code': 500, 'msg': '模拟核对失败'}
            if method == 'POST':
                model = {**body, 'modelId': 'mdl_33333333333343338333333333333333', 'lifecycleStatus': 'draft'}
                self.models.append(model)
                if 'model' in self.lose_responses:
                    self.lose_responses.remove('model')
                    if self.fail_confirmation_for == 'model':
                        self.pending_read_failure = 'model'
                    return {'code': 500, 'msg': '模拟响应丢失'}
                return {'code': 200, 'data': model}
            return {'code': 200, 'rows': self.models, 'total': len(self.models)}
        if path.endswith('/binding'):
            binding = {**body, 'version': 1}
            self.models[0]['binding'] = binding
            if 'binding' in self.lose_responses:
                self.lose_responses.remove('binding')
                return {'code': 500, 'msg': '模拟响应丢失'}
            return {'code': 200, 'data': binding}
        if path.endswith('/publish'):
            if self.fail_publish:
                self.fail_publish = False
                return {'code': 500, 'msg': '模拟发布失败，请重试'}
            self.models[0]['lifecycleStatus'] = 'active'
            return {'code': 200, 'data': self.models[0]}
        return {'code': 200, 'data': self.models[0] if self.models else {}}

    async def install(self, context):
        async def handler(route):
            request = route.request
            path = re.sub(r'^/(dev-api|docker-api)', '', urlparse(request.url).path)
            if path.endswith('/test-connection'):
                await asyncio.sleep(self.test_delay)
            body = request.post_data_json if request.post_data else {}
            await route.fulfill(status=200, content_type='application/json',
                                body=json.dumps(self.respond(path, request.method, body), ensure_ascii=False))
        for pattern in ('**/dev-api/**', '**/docker-api/**', '**/transport/crypto/frontend-config', '**/captchaImage'):
            await context.route(pattern, handler)
        await context.add_cookies([{'name': 'Admin-Token', 'value': 'model-fixture-token', 'url': BASE_URL}])

    def writes(self, suffix):
        return [body for path, method, body in self.calls if path.endswith(suffix) and method in ('POST', 'PUT')]


async def open_form(browser, fixture, *, dark=False):
    context = await browser.new_context(viewport={'width': 1440, 'height': 960}, color_scheme='dark' if dark else 'light')
    await fixture.install(context)
    page = await context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    await page.goto(BASE_URL + '/ai/model', wait_until='networkidle')
    await button(page.locator('main.ai-model-center'), '接入模型').click()
    drawer = page.locator('.model-onboarding-drawer .ant-drawer-content')
    await expect(drawer.get_by_text('模型信息', exact=True)).to_be_visible()
    await expect(drawer.get_by_text('连接配置', exact=True)).to_be_visible()
    assert await drawer.locator('.ant-steps').count() == 0
    assert await drawer.get_by_label('模型代码', exact=True).count() == 0
    assert await drawer.get_by_label('路由别名', exact=True).count() == 0
    return context, page, drawer, errors


async def typed_existing(browser):
    for kind, label in [('chat', '对话'), ('embedding', '向量'), ('rerank', '重排序')]:
        fixture = Fixture()
        context, page, drawer, errors = await open_form(browser, fixture)
        try:
            await drawer.get_by_text(label, exact=True).click()
            await drawer.get_by_label('模型名称', exact=True).fill('provider/test-model')
            await expect(button(drawer, '接入模型')).to_be_disabled()
            await button(drawer, '测试连接').click()
            await expect(drawer.get_by_text('连接测试通过', exact=False)).to_be_visible()
            assert fixture.writes('/test-connection')[-1]['modelType'] == kind
            assert not fixture.writes('/credentials') and not fixture.writes('/deployments')
            await drawer.get_by_label('模型名称', exact=True).fill('provider/changed-model')
            await expect(button(drawer, '接入模型')).to_be_disabled()
            await button(drawer, '测试连接').click()
            await expect(button(drawer, '接入模型')).to_be_enabled()
            await button(drawer, '接入模型').click()
            await expect(drawer.get_by_text('接入成功', exact=True)).to_be_visible()
            assert len(fixture.writes('/ai/models')) == 1
            assert fixture.writes('/ai/models')[0]['modelType'] == kind
            assert fixture.writes('/binding')[0]['engineResourceId'] == fixture.deployments[0]['modelAlias']
            assert not errors, errors
        finally:
            await context.close()
    print('PASS single page, three model types, existing credentials, invalidation, auto binding')


async def new_credential_retry(browser):
    fixture = Fixture(saved=False)
    fixture.fail_publish = True
    context, page, drawer, errors = await open_form(browser, fixture)
    try:
        await drawer.get_by_label('模型名称', exact=True).fill('new-model')
        await drawer.get_by_label('API Key', exact=True).fill('fixture-secret')
        await button(drawer, '测试连接').click()
        await expect(button(drawer, '接入模型')).to_be_enabled()
        assert fixture.writes('/test-connection')[-1]['values']['api_key'] == 'fixture-secret'
        assert not fixture.writes('/credentials')
        await page.screenshot(path='/tmp/model-onboarding-single-page.png', full_page=True)
        await button(drawer, '接入模型').click()
        await expect(button(drawer, '重试接入')).to_be_enabled()
        await expect(drawer.get_by_label('模型名称', exact=True)).to_be_disabled()
        await button(drawer, '重试接入').click()
        await expect(drawer.get_by_text('接入成功', exact=True)).to_be_visible()
        assert len(fixture.writes('/credentials')) == len(fixture.writes('/deployments')) == len(fixture.writes('/ai/models')) == 1
        assert len(fixture.writes('/publish')) == 2
        assert 'fixture-secret' not in await page.evaluate('JSON.stringify({...localStorage,...sessionStorage})')
        assert not errors, errors
    finally:
        await context.close()
    print('PASS new credentials tested before saving; publish retry reuses resources; no credential persistence')


async def stale_test_and_layout(browser):
    fixture = Fixture()
    fixture.test_delay = 0.8
    context, page, drawer, errors = await open_form(browser, fixture, dark=True)
    try:
        await drawer.get_by_label('模型名称', exact=True).fill('old-model')
        await button(drawer, '测试连接').click()
        await drawer.locator('.ant-drawer-close').click()
        await button(page.locator('main.ai-model-center'), '接入模型').click()
        await expect(drawer.get_by_label('模型名称', exact=True)).to_have_value('')
        await page.wait_for_timeout(1000)
        await expect(button(drawer, '接入模型')).to_be_disabled()
        assert await drawer.get_by_text('连接测试通过', exact=False).count() == 0
        await page.screenshot(path='/tmp/model-onboarding-single-page-dark.png', full_page=True)
        assert await drawer.evaluate('(node) => node.scrollWidth <= node.clientWidth')
        assert not errors, errors
    finally:
        await context.close()
    print('PASS close/reopen ignores stale test; desktop layout fits')


async def uncertain_response_retry(browser):
    fixture = Fixture(saved=False)
    fixture.lose_responses = {'credential', 'deployment', 'model', 'binding'}
    context, page, drawer, errors = await open_form(browser, fixture)
    try:
        await drawer.get_by_label('模型名称', exact=True).fill('uncertain-model')
        await drawer.get_by_label('API Key', exact=True).fill('fixture-secret')
        await button(drawer, '测试连接').click()
        await expect(button(drawer, '接入模型')).to_be_enabled()
        await button(drawer, '接入模型').click()
        await expect(button(drawer, '重试接入')).to_be_enabled()
        await button(drawer, '重试接入').click()
        await expect(drawer.get_by_text('接入成功', exact=True)).to_be_visible()
        for suffix in ('/credentials', '/deployments', '/ai/models', '/binding', '/publish'):
            assert len(fixture.writes(suffix)) == 1, (suffix, fixture.calls)
        assert not errors, errors
    finally:
        await context.close()
    print('PASS successful writes with lost responses reconcile without duplicate credentials, deployment, model or binding')


async def uncertain_write_and_failed_lookup(browser):
    for stage in ('credential', 'model'):
        fixture = Fixture(saved=False)
        fixture.lose_responses = {stage}
        fixture.fail_confirmation_for = stage
        context, page, drawer, errors = await open_form(browser, fixture)
        try:
            await page.set_viewport_size({'width': 1280, 'height': 800})
            await drawer.get_by_label('模型名称', exact=True).fill('uncertain-after-query-error')
            await drawer.get_by_label('API Key', exact=True).fill('fixture-secret')
            await button(drawer, '测试连接').click()
            await expect(button(drawer, '接入模型')).to_be_enabled()
            await button(drawer, '接入模型').click()
            await expect(button(drawer, '重试接入')).to_be_enabled()
            await expect(drawer.get_by_label('模型名称', exact=True)).to_be_disabled()
            await button(drawer, '重试接入').click()
            await expect(drawer.get_by_text('接入成功', exact=True)).to_be_visible()
            assert len(fixture.writes('/credentials')) == len(fixture.writes('/ai/models')) == 1
            assert await drawer.evaluate('(node) => node.scrollWidth <= node.clientWidth')
            assert not errors, errors
        finally:
            await context.close()
    print('PASS retry queries before resubmitting unknown writes even after confirmation failed; 1280px desktop fits')


async def main():
    expect.set_options(timeout=15000)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            await typed_existing(browser)
            await new_credential_retry(browser)
            await stale_test_and_layout(browser)
            await uncertain_response_retry(browser)
            await uncertain_write_and_failed_lookup(browser)
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
