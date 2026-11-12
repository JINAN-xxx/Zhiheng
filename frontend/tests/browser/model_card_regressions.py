"""Compact model entries preserve identity, capability and permission boundaries."""
from browser_clipboard import read_clipboard
import os
import unittest
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import expect
from developer_regressions import DeveloperBoundary


MODEL_ID = '87c1558e-7b8b-46a3-8e1b-d06eea1c53c5'
LONG_NAME = '团队知识检索与多语言对话专用模型名称需要完整保留在详情中'
DETAIL_SELECTOR = '.model-detail-modal[role="dialog"], .model-detail-modal [role="dialog"]'


class ModelCardRegressions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.permissions = ['ai:models:write', 'ai:models:test', 'ai:rates:write']
        self.requests = []
        self.connection_enabled = True
        self.rows = [
            {'id': MODEL_ID, 'name': LONG_NAME, 'connection_id': 'connection-1',
             'upstream_model': 'provider/chat-with-a-very-long-upstream-model-code',
             'max_output_tokens': 65536, 'model_type': 'chat', 'published': True,
             'enabled': True, 'capabilities': ['chat', 'stream']},
            {'id': 'embedding-id', 'name': '知识向量', 'connection_id': 'connection-1',
             'upstream_model': 'provider/embedding', 'max_output_tokens': 512,
             'model_type': 'embedding', 'published': True, 'enabled': True,
             'capabilities': ['embedding']},
            {'id': 'rerank-id', 'name': '结果重排序', 'connection_id': 'connection-1',
             'upstream_model': 'provider/rerank', 'max_output_tokens': 512,
             'model_type': 'rerank', 'published': False, 'enabled': False,
             'capabilities': ['rerank']}
        ]
        await DeveloperBoundary.asyncSetUp(self)

    async def asyncTearDown(self):
        await DeveloperBoundary.asyncTearDown(self)

    async def respond(self, route):
        request = route.request
        path = urlparse(request.url).path
        self.requests.append((request.method, path, request.post_data_json if request.post_data else None))
        if path == '/api/auth/me':
            return await route.fulfill(json={'id': 'user-1', 'username': 'member',
                                            'is_admin': False, 'permissions': self.permissions})
        if path == '/api/models':
            return await route.fulfill(json=self.rows)
        if path == '/api/models/connections':
            return await route.fulfill(json=[{'id': 'connection-1', 'name': '团队服务',
                'provider': 'openai_compatible', 'base_url': 'https://provider.example/v1',
                'has_api_key': True, 'enabled': self.connection_enabled}])
        if path.startswith('/api/models/') and path.endswith('/test'):
            return await route.fulfill(status=201, json={'id': 'test-run',
                'resource_type': 'connection_test', 'resource_id': path.split('/')[3],
                'status': 'completed', 'attempt_id': 'attempt-1', 'attempt_no': 1,
                'revision': 1, 'projection_seq': 1, 'text': '连接成功', 'error': None,
                'created_at': '2026-10-30T00:00:00Z',
                'usage': {'input_tokens': 1, 'output_tokens': 1, 'status': 'known'}})
        if '/rate-cards' in path or path.startswith('/api/billing/models/'):
            return await route.fulfill(json=[])
        if path.startswith('/api/models/') and request.method == 'POST':
            model_id, action = path.split('/')[-2:]
            row = next(row for row in self.rows if row['id'] == model_id)
            if action in ['enable', 'disable']:
                row['enabled'] = action == 'enable'
            return await route.fulfill(json=row)
        return await DeveloperBoundary.respond(self, route)

    async def open_models(self):
        await self.page.goto(self.origin + '/models')
        await expect(self.page.locator('.model-item')).to_have_count(len(self.rows))

    async def open_details(self, item):
        await item.locator('button[aria-label^="查看模型详情："]').click()
        detail = self.page.locator(DETAIL_SELECTOR)
        await expect(detail).to_be_visible()
        return detail

    async def close_details(self):
        detail = self.page.locator(DETAIL_SELECTOR)
        await detail.get_by_role('button', name='Close', exact=True).click()
        await expect(detail).to_be_hidden()

    async def open_more(self, detail):
        await detail.get_by_role('button', name='更多模型操作', exact=True).click()
        return self.page.get_by_role('menu')

    async def save_evidence(self, filename):
        directory = os.environ.get('MODEL_CARD_EVIDENCE')
        if directory:
            Path(directory).mkdir(parents=True, exist_ok=True)
            await self.page.screenshot(path=str(Path(directory) / filename), full_page=True, animations='disabled')

    async def test_identity_is_compact_copyable_and_available_in_readonly_details(self):
        await self.open_models()
        item = self.page.locator('.model-item').first
        await expect(item).not_to_contain_text(MODEL_ID)
        await expect(item.get_by_role('button', name='复制模型代码', exact=True)).to_have_count(0)
        detail = await self.open_details(item)
        await detail.get_by_role('button', name='复制模型代码', exact=True).click()
        self.assertEqual(await read_clipboard(self.page), MODEL_ID)
        await expect(detail).to_contain_text(MODEL_ID)
        await expect(detail).to_contain_text(LONG_NAME)
        await expect(detail.get_by_role('textbox')).to_have_count(0)
        self.assertFalse(any(path == '/api/models/' + MODEL_ID for _, path, _ in self.requests))
        await self.save_evidence('model-identity-details.png')

    async def test_late_copy_failure_does_not_replace_a_new_details_context(self):
        await self.open_models()
        await self.page.evaluate("""() => {
            Object.defineProperty(navigator, 'clipboard', {
                configurable: true,
                value: {
                    writeText: () => new Promise((_resolve, reject) => { window.rejectModelCopy = reject })
                }
            })
        }""")
        detail = await self.open_details(self.page.locator('.model-item').first)
        await detail.get_by_role('button', name='复制模型代码', exact=True).click()
        await self.close_details()
        detail = await self.open_details(self.page.locator('.model-item').nth(1))
        await self.page.evaluate("async () => { window.rejectModelCopy(new Error('clipboard unavailable')); await new Promise(requestAnimationFrame) }")
        await expect(detail).to_contain_text('知识向量')
        await expect(detail).not_to_contain_text(MODEL_ID)
        await expect(self.page.locator('.ai-model-center .ant-alert-error')).to_have_count(0)
        await expect(detail.locator('.ant-alert-error')).to_have_count(0)

    async def test_type_specific_content_has_no_inapplicable_or_invented_metrics(self):
        await self.open_models()
        items = self.page.locator('.model-item')
        for index in range(3):
            item = items.nth(index)
            await expect(item).not_to_contain_text('最大输出')
            await expect(item).not_to_contain_text('provider/')
            await expect(item).not_to_contain_text('维度')
            await expect(item).not_to_contain_text('更新于')
            self.assertLessEqual((await item.inner_text()).count('已启用'), 1)
        await self.save_evidence('model-cards-desktop.png')
        for index, purpose in [(0, None), (1, '用于文本向量化与知识检索'), (2, '用于检索结果相关性排序')]:
            detail = await self.open_details(items.nth(index))
            if index == 0:
                await expect(detail).to_contain_text('最大输出')
                await expect(detail).to_contain_text('65,536 Token')
            else:
                await expect(detail).not_to_contain_text('最大输出')
                await expect(detail).to_contain_text(purpose)
            await expect(detail).not_to_contain_text('维度')
            await expect(detail).not_to_contain_text('更新于')
            await self.close_details()

    async def test_card_has_one_primary_action_and_preserves_every_management_menu_entry(self):
        await self.open_models()
        item = self.page.locator('.model-item').first
        for label in ['测试连接', '编辑模型', '费率配置', '复制模型代码', '更多模型操作']:
            await expect(item.get_by_role('button', name=label, exact=True)).to_have_count(0)
        await expect(item.get_by_role('switch', name='启用模型“' + LONG_NAME + '”', exact=True)).to_be_checked()
        detail = await self.open_details(item)
        await expect(detail.get_by_role('button', name='测试连接', exact=True)).to_be_enabled()
        menu = await self.open_more(detail)
        for label in ['编辑模型', '费率配置', '删除模型']:
            await expect(menu.get_by_role('menuitem', name=label, exact=True)).to_be_visible()
        for label in ['启用', '停用', '测试连接', '发布', '取消发布']:
            await expect(menu.get_by_role('menuitem', name=label, exact=True)).to_have_count(0)
        await self.save_evidence('model-more-actions.png')
        await menu.get_by_role('menuitem', name='编辑模型', exact=True).click()
        await expect(self.page.locator('.ant-drawer-open .ant-drawer-content')).to_contain_text('编辑模型')

    async def test_disabled_model_only_needs_enablement(self):
        await self.open_models()
        item = self.page.locator('.model-item').nth(2)
        detail = await self.open_details(item)
        await expect(detail.get_by_role('button', name='测试连接', exact=True)).to_be_disabled()
        toggle = detail.get_by_role('switch', name='启用模型“结果重排序”', exact=True)
        await expect(toggle).not_to_be_checked()
        await toggle.click()
        await expect(toggle).to_be_checked()
        await expect(detail).to_contain_text('已启用')
        await expect(detail.get_by_role('button', name='测试连接', exact=True)).to_be_enabled()
        await expect(detail).not_to_contain_text('发布状态')
        await expect(item).not_to_contain_text('未发布')
        menu = await self.open_more(detail)
        for label in ['发布', '取消发布']:
            await expect(menu.get_by_role('menuitem', name=label, exact=True)).to_have_count(0)
        self.assertTrue(any(path == '/api/models/rerank-id/enable' for _, path, _ in self.requests))
        self.assertFalse(any(path.endswith(('/publish', '/unpublish')) for _, path, _ in self.requests))

    async def test_known_disabled_connection_explains_why_test_is_unavailable(self):
        self.connection_enabled = False
        await self.open_models()
        item = self.page.locator('.model-item').first
        await expect(item).to_contain_text('连接已停用')
        detail = await self.open_details(item)
        await expect(detail).to_contain_text('连接已停用')
        await expect(detail.get_by_role('button', name='测试连接', exact=True)).to_be_disabled()
        await self.save_evidence('model-disabled-connection.png')
        await self.close_details()
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        await self.page.locator('.model-table').get_by_role('button', name='更多操作', exact=True).first.click()
        await expect(self.page.get_by_role('menuitem', name='测试连接', exact=True)).to_be_disabled()

    async def test_tester_without_connection_read_permission_keeps_legitimate_test_access(self):
        self.permissions = ['ai:models:test']
        await self.open_models()
        item = self.page.locator('.model-item').first
        await expect(item).not_to_contain_text('团队服务')
        detail = await self.open_details(item)
        await expect(detail).not_to_contain_text('团队服务')
        await expect(detail.get_by_role('button', name='更多模型操作', exact=True)).to_have_count(0)
        await expect(detail.get_by_role('switch')).to_have_count(0)
        await detail.get_by_role('button', name='测试连接', exact=True).click()
        await expect(self.page.locator('.ant-message-notice-content')).to_have_text('测试成功')
        await expect(self.page.locator('.test-result')).to_have_count(0)
        self.assertFalse(any(path == '/api/models/connections' for _, path, _ in self.requests))

    async def test_rate_reader_has_only_rate_menu_and_readonly_rate_dialog(self):
        self.permissions = ['ai:rates:read']
        await self.open_models()
        detail = await self.open_details(self.page.locator('.model-item').first)
        await expect(detail.get_by_role('button', name='测试连接', exact=True)).to_have_count(0)
        await expect(detail.get_by_role('switch')).to_have_count(0)
        menu = await self.open_more(detail)
        await expect(menu.get_by_role('menuitem')).to_have_count(1)
        await menu.get_by_role('menuitem', name='费率配置', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='费率配置', exact=True)
        await expect(dialog.get_by_role('button', name='保存价格', exact=True)).to_have_count(0)
        self.assertFalse(any(method != 'GET' for method, _, _ in self.requests))

    async def test_writer_keeps_management_without_test_or_rate_access(self):
        self.permissions = ['ai:models:write']
        await self.open_models()
        detail = await self.open_details(self.page.locator('.model-item').first)
        await expect(detail.get_by_role('button', name='测试连接', exact=True)).to_have_count(0)
        menu = await self.open_more(detail)
        await expect(menu.get_by_role('menuitem', name='编辑模型', exact=True)).to_be_visible()
        await expect(menu.get_by_role('menuitem', name='费率配置', exact=True)).to_have_count(0)

    async def test_basic_user_has_details_without_management_requests_or_actions(self):
        self.permissions = []
        await self.open_models()
        item = self.page.locator('.model-item').first
        await expect(item).not_to_contain_text('团队服务')
        await expect(item.get_by_role('switch')).to_have_count(0)
        detail = await self.open_details(item)
        for label in ['测试连接', '更多模型操作']:
            await expect(detail.get_by_role('button', name=label, exact=True)).to_have_count(0)
        await expect(detail.get_by_role('switch')).to_have_count(0)
        await expect(detail).not_to_contain_text('团队服务')
        await expect(detail).to_contain_text(MODEL_ID)
        self.assertFalse(any(path == '/api/models/connections' or '/rate' in path for _, path, _ in self.requests))

    async def test_long_names_fit_three_and_two_columns_and_mobile_keeps_table_actions(self):
        self.rows.append({**self.rows[0], 'id': 'fourth-model', 'name': '第四个模型'})
        await self.open_models()
        items = self.page.locator('.model-item')
        first, second, third, fourth = [await items.nth(i).bounding_box() for i in range(4)]
        self.assertAlmostEqual(first['y'], third['y'], delta=1)
        self.assertLess(first['x'], second['x'])
        self.assertGreater(fourth['y'], first['y'])
        await self.page.set_viewport_size({'width': 1920, 'height': 1080})
        first, second, third, fourth = [await items.nth(i).bounding_box() for i in range(4)]
        self.assertAlmostEqual(first['y'], fourth['y'], delta=1)
        self.assertLess(third['x'], fourth['x'])
        self.assertFalse(await self.page.evaluate('document.documentElement.scrollWidth > window.innerWidth'))
        detail = await self.open_details(items.first)
        await expect(detail).to_contain_text(LONG_NAME)
        await self.close_details()
        await self.save_evidence('model-cards-four-columns.png')
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        await expect(items).to_have_count(0)
        await expect(self.page.locator('.model-table')).to_be_visible()
        await self.page.locator('.model-table').get_by_role('button', name='更多操作', exact=True).first.click()
        await expect(self.page.get_by_role('menuitem', name='编辑模型', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('menuitem', name='费率配置', exact=True)).to_be_visible()
        self.assertFalse(await self.page.evaluate('document.documentElement.scrollWidth > window.innerWidth'))
        await self.save_evidence('model-mobile-table.png')


if __name__ == '__main__':
    unittest.main(verbosity=2)
