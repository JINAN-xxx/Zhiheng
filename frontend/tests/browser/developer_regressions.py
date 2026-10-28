"""Original developer UI with controlled new-core HTTP boundaries."""
import asyncio
import contextlib
import json
import os
import re
import unittest
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright, expect


class DeveloperBoundary(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.context = await self.browser.new_context(viewport={'width': 1440, 'height': 900}, permissions=['clipboard-read', 'clipboard-write'])
        self.page = await self.context.new_page()
        self.page.set_default_timeout(10000)
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.origin = os.environ.get('PLATFORM_BROWSER_URL', 'http://127.0.0.1:15194')
        self.app = {'id': 'app-1', 'code': 'internal-tools', 'name': '内部工具', 'status': 'active', 'version': 1, 'owner_id': 'user-1', 'created_at': '2026-10-23T00:00:00Z', 'updated_at': '2026-10-23T00:00:00Z', 'deleted_at': None}
        self.keys = []
        self.key_pages = []
        self.posts = []
        self.secret = 'sk-zh_fixture-only-one-time-secret'
        self.hold_keys = False
        self.keys_requested = asyncio.Event()
        self.release_keys = asyncio.Event()
        await self.page.route(self.origin + '/api/**', self.respond)

    async def asyncTearDown(self):
        self.release_keys.set()
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()
        self.assertEqual(self.errors, [])

    async def respond(self, route):
        path = urlparse(route.request.url).path
        method = route.request.method
        status = 200
        if path == '/api/auth/me':
            body = {'id': 'user-1', 'username': 'admin', 'is_admin': True}
        elif path == '/api/models':
            body = [{'id': 'model-1', 'name': '日常助手', 'connection_id': 'connection-1', 'upstream_model': 'upstream-private', 'max_output_tokens': 512, 'model_type': 'chat', 'published': True, 'enabled': True, 'capabilities': ['chat', 'stream']}]
        elif path == '/api/chat/conversations':
            body = []
        elif path == '/api/knowledge/available':
            body = []
        elif path == '/api/developer/applications':
            if method == 'POST':
                payload = route.request.post_data_json
                self.posts.append(payload)
                self.app.update(payload)
                status = 201
                body = self.app
            else:
                items = [self.app] if self.app['deleted_at'] is None else []
                body = {'items': items, 'total': len(items), 'page': 1, 'page_size': 10}
        elif path == '/api/developer/applications/app-1':
            if method == 'PATCH':
                payload = route.request.post_data_json
                self.posts.append(payload)
                self.app['name'] = payload['name']
                self.app['version'] += 1
            elif method == 'DELETE':
                self.app['deleted_at'] = '2026-10-23T01:00:00Z'
            body = self.app
        elif path.endswith('/app-1/disable') or path.endswith('/app-1/enable'):
            self.app['status'] = 'disabled' if path.endswith('/disable') else 'active'
            self.app['version'] += 1
            body = self.app
        elif path == '/api/developer/applications/app-1/keys':
            if method == 'POST':
                payload = route.request.post_data_json
                self.posts.append(payload)
                key = {'id': 'key-1', 'application_id': 'app-1', 'name': payload['name'], 'prefix': 'sk-zh_fixture…', 'status': 'active', 'model_ids': payload['model_ids'], 'rpm_limit': payload.get('rpm_limit'), 'tpm_limit': payload.get('tpm_limit'), 'concurrency_limit': payload.get('concurrency_limit'), 'monthly_budget': None, 'currency': 'USD', 'created_at': '2026-10-23T00:00:00Z', 'expires_at': None, 'retire_at': None, 'last_used_at': None}
                self.keys.append(key)
                body = {**key, 'api_key': self.secret}
                status = 201
            else:
                if self.hold_keys:
                    self.keys_requested.set()
                    await self.release_keys.wait()
                query = parse_qs(urlparse(route.request.url).query)
                page = int(query.get('page', ['1'])[0])
                page_size = int(query.get('page_size', ['20'])[0])
                self.key_pages.append(page)
                offset = (page - 1) * page_size
                body = {'items': self.keys[offset:offset + page_size], 'total': len(self.keys), 'page': page, 'page_size': page_size}
        elif path == '/api/developer/applications/app-1/usage':
            body = {'items': [{'id': 'usage-1', 'run_id': 'run-1', 'application_id': 'app-1', 'key_id': 'key-1', 'model_id': 'model-1', 'operation': 'chat', 'status': 'unknown', 'run_status': 'completed', 'error': None, 'input_tokens': None, 'output_tokens': None, 'search_units': None, 'billable_cost': None, 'currency': 'USD', 'rate_card_id': None, 'rate_version': None, 'created_at': '2026-10-23T00:00:00Z'}], 'total': 1, 'page': 1, 'page_size': 10}
        else:
            body = {'error': {'code': 'fixture_missing', 'message': path}}
            status = 404
        await route.fulfill(status=status, content_type='application/json', body=json.dumps(body))

    async def open_details(self):
        await self.page.goto(self.origin + '/developer?applicationId=app-1&tab=overview')
        await expect(self.page.get_by_role('heading', name='开发接入', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('heading', name='内部工具', exact=True)).to_be_visible()



class DeveloperUIRegressions(DeveloperBoundary):
    async def test_key_pages_are_loaded_for_list_and_model_grants(self):
        self.keys = [{'id': f'key-{index}', 'application_id': 'app-1', 'name': f'客户端{index}', 'prefix': f'sk-zh_fixture-{index}', 'status': 'active', 'model_ids': ['last-page-model'] if index == 120 else ['model-1'], 'rpm_limit': None, 'tpm_limit': None, 'concurrency_limit': None, 'expires_at': None, 'last_used_at': None} for index in range(121)]
        await self.open_details()
        await self.page.get_by_role('tab', name='访问密钥', exact=True).click()
        await expect(self.page.locator('.key-row')).to_have_count(121)
        self.assertEqual(self.key_pages, [1, 2])
        await self.page.get_by_role('tab', name='模型授权', exact=True).click()
        await expect(self.page.get_by_text('last-page-model', exact=True)).to_be_visible()

    async def delayed_edit_navigation(self, destination):
        hold = False
        requested, release = asyncio.Event(), asyncio.Event()
        original = self.respond
        async def fixtures(route):
            nonlocal hold
            path = urlparse(route.request.url).path
            if path == '/api/developer/applications/app-1' and route.request.method == 'PATCH':
                self.app['name'] = route.request.post_data_json['name']
                self.app['version'] += 1
                hold = True
                await route.fulfill(json=self.app)
            elif path == '/api/developer/applications' and hold:
                requested.set()
                await release.wait()
                with contextlib.suppress(Exception):
                    await route.fulfill(json={'items': [self.app], 'total': 1})
            else:
                await original(route)
        await self.page.unroute('**/api/**')
        await self.page.route('**/api/**', fixtures)
        try:
            await self.open_details()
            await self.page.get_by_role('button', name=re.compile('编辑')).click()
            dialog = self.page.get_by_role('dialog')
            await dialog.get_by_placeholder('例如内部智能工具').fill('已修改')
            await dialog.get_by_role('button', name=re.compile(r'^保\s*存$')).click()
            await requested.wait()
            await expect(dialog).not_to_be_visible()
            if destination == 'models':
                await self.page.get_by_role('link', name='模型目录', exact=True).click()
                await expect(self.page.get_by_role('heading', name='模型目录', exact=True)).to_be_visible()
            else:
                await self.page.get_by_role('button', name=re.compile('应用列表')).click()
            release.set()
            await self.page.wait_for_timeout(400)
            self.assertNotIn('applicationId=', self.page.url)
        finally:
            release.set()

    async def test_late_save_does_not_modify_another_module_route(self):
        await self.delayed_edit_navigation('models')

    async def test_late_save_does_not_reopen_application_details(self):
        await self.delayed_edit_navigation('list')

    async def test_rotation_response_cannot_reopen_secret_after_tab_change(self):
        key = {'id': 'key-1', 'application_id': 'app-1', 'name': '客户端', 'prefix': 'sk-zh_fixture…', 'status': 'active', 'model_ids': ['model-1'], 'rpm_limit': None, 'tpm_limit': None, 'concurrency_limit': None, 'expires_at': None, 'last_used_at': None}
        self.keys = [key]
        requested, release = asyncio.Event(), asyncio.Event()
        async def rotate(route):
            requested.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await route.fulfill(json={**key, 'id': 'key-2', 'api_key': self.secret})
        await self.page.route('**/api/developer/applications/app-1/keys/key-1/rotate', rotate)
        try:
            await self.open_details()
            await self.page.get_by_role('tab', name='访问密钥', exact=True).click()
            await self.page.get_by_role('button', name='更多操作', exact=True).click()
            await self.page.get_by_role('menuitem', name='轮换密钥', exact=True).click()
            await self.confirm_action('轮换后旧密钥')
            await requested.wait()
            await self.page.get_by_role('tab', name='调用记录', exact=True).click()
            release.set()
            await self.page.wait_for_timeout(400)
            await expect(self.page.get_by_text(self.secret, exact=True)).to_have_count(0)
        finally:
            release.set()

    async def test_failed_detail_is_visible_and_can_retry(self):
        attempts = 0
        async def detail(route):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '应用服务暂时不可用'}})
            else:
                await route.fulfill(status=200, json=self.app)
        await self.page.route('**/api/developer/applications/app-1', detail)
        await self.page.goto(self.origin + '/developer?applicationId=app-1')
        await expect(self.page.get_by_text('应用服务暂时不可用', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name=re.compile(r'^重\s*试$')).click()
        await expect(self.page.get_by_role('heading', name='内部工具', exact=True)).to_be_visible()

    async def test_missing_detail_returns_to_first_available_application(self):
        await self.page.goto(self.origin + '/developer?applicationId=missing')
        await expect(self.page.get_by_role('heading', name='内部工具', exact=True)).to_be_visible()
        await expect(self.page).to_have_url(re.compile(r'applicationId=app-1'))

    async def test_failed_lifecycle_action_preserves_record_and_shows_error(self):
        await self.open_details()
        await self.page.route('**/api/developer/applications/app-1/disable', lambda route: route.fulfill(status=409, json={'error': {'code': 'conflict', 'message': '应用状态已变化，请刷新后重试'}}))
        await self.page.get_by_role('button', name=re.compile('停用')).click()
        await self.confirm_action('停用后，该应用下所有访问密钥将无法调用')
        await expect(self.page.get_by_text('应用状态已变化，请刷新后重试', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('heading', name='内部工具', exact=True)).to_be_visible()

    async def test_unavailable_granted_model_remains_visible(self):
        self.keys = [{'id': 'key-1', 'application_id': 'app-1', 'name': '已授权客户端', 'prefix': 'sk-zh_fixture…', 'status': 'active', 'model_ids': ['unavailable-model'], 'rpm_limit': None, 'tpm_limit': None, 'concurrency_limit': None, 'monthly_budget': None, 'currency': 'USD', 'created_at': '2026-10-23T00:00:00Z', 'expires_at': None, 'retire_at': None, 'last_used_at': None}]
        await self.open_details()
        await self.page.get_by_role('tab', name='模型授权', exact=True).click()
        await expect(self.page.get_by_text('unavailable-model', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('不可用', exact=True)).to_be_visible()

    async def confirm_action(self, text):
        dialog = self.page.get_by_role('dialog').filter(has_text=text)
        await dialog.get_by_role('button', name=re.compile(r'^确\s*定$')).click()

    async def test_application_edit_lifecycle_and_soft_delete(self):
        await self.open_details()
        await self.page.get_by_role('button', name=re.compile('编辑')).click()
        dialog = self.page.get_by_role('dialog')
        await dialog.get_by_placeholder('例如内部智能工具').fill('更新后的应用')
        await dialog.get_by_role('button', name=re.compile(r'^保\s*存$')).click()
        await expect(dialog).not_to_be_visible()
        await expect(self.page.get_by_role('heading', name='更新后的应用', exact=True)).to_be_visible()
        self.assertIn({'name': '更新后的应用', 'expected_version': 1}, self.posts)
        await self.page.get_by_role('button', name=re.compile('停用')).click()
        await self.confirm_action('停用后，该应用下所有访问密钥将无法调用')
        await expect(self.page.get_by_role('button', name=re.compile('重新启用'))).to_be_visible()
        await self.page.get_by_role('button', name=re.compile('重新启用')).click()
        await self.confirm_action('重新启用后')
        await expect(self.page.get_by_role('button', name=re.compile('停用'))).to_be_visible()
        await self.page.get_by_role('button', name=re.compile('停用')).click()
        await self.confirm_action('停用后，该应用下所有访问密钥将无法调用')
        await self.page.get_by_role('button', name=re.compile('删除应用')).click()
        await self.confirm_action('仅停用应用可以删除')
        await expect(self.page.get_by_text('暂无开发者应用', exact=True)).to_be_visible()
        self.assertIsNotNone(self.app['deleted_at'])

    async def test_original_application_create_uses_new_contract(self):
        await self.page.goto(self.origin + '/developer')
        await expect(self.page.get_by_role('heading', name='开发接入', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='新建应用', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        await dialog.get_by_placeholder('例如 internal-tools').fill('team-tools')
        await dialog.get_by_placeholder('例如内部智能工具').fill('团队工具')
        await dialog.get_by_role('button', name=re.compile(r'^保\s*存$')).click()
        await expect(dialog).not_to_be_visible()
        self.assertIn({'code': 'team-tools', 'name': '团队工具'}, self.posts)

    async def test_key_is_shown_once_copied_and_never_persisted(self):
        await self.open_details()
        await self.page.get_by_role('tab', name='访问密钥', exact=True).click()
        await self.page.get_by_role('button', name='创建密钥', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        await dialog.get_by_label('密钥名称', exact=True).fill('客户端')
        await dialog.locator('.ant-select-selector').click()
        await self.page.locator('.ant-select-item-option-content').filter(has_text='日常助手').click()
        await dialog.get_by_role('button', name=re.compile(r'^创\s*建$')).click()
        issued = self.page.get_by_role('dialog').filter(has_text='访问密钥已生成')
        await expect(issued.get_by_text(self.secret, exact=True)).to_be_visible()
        await issued.get_by_role('button', name='复制访问密钥', exact=True).click()
        self.assertEqual(await self.page.evaluate('navigator.clipboard.readText()'), self.secret)
        await issued.get_by_role('button', name='我已保存', exact=True).click()
        await expect(self.page.get_by_text(self.secret, exact=True)).to_have_count(0)
        storage = await self.page.evaluate('JSON.stringify({local: {...localStorage}, session: {...sessionStorage}, cookie: document.cookie})')
        self.assertNotIn(self.secret, storage)
        await self.page.reload()
        await expect(self.page.get_by_text('客户端', exact=True)).to_be_visible()
        await expect(self.page.get_by_text(self.secret, exact=True)).to_have_count(0)

    async def test_unknown_application_usage_is_not_displayed_as_zero_cost(self):
        await self.open_details()
        await self.page.get_by_role('tab', name='调用记录', exact=True).click()
        await expect(self.page.get_by_text('待核对', exact=True)).not_to_have_count(0)
        await expect(self.page.get_by_text('0.000000', exact=True)).to_have_count(0)
        await self.page.set_viewport_size({'width': 375, 'height': 812})
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))

    async def test_api_docs_use_platform_ids_and_current_public_url(self):
        await self.page.goto(self.origin + '/developer?view=docs')
        await expect(self.page.get_by_role('heading', name='API 说明', exact=True)).to_be_visible()
        await expect(self.page.get_by_text(self.origin + '/v1', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('upstream-private', exact=True)).to_have_count(0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
