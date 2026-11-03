"""Model resource cards preserve identity, capability and permission boundaries."""
import os
import unittest
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import expect
from developer_regressions import DeveloperBoundary


MODEL_ID = '87c1558e-7b8b-46a3-8e1b-d06eea1c53c5'
LONG_NAME = '团队知识检索与多语言对话专用模型名称需要完整保留在详情中'


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
            elif action in ['publish', 'unpublish']:
                row['published'] = action == 'publish'
            return await route.fulfill(json=row)
        return await DeveloperBoundary.respond(self, route)

    async def open_models(self):
        await self.page.goto(self.origin + '/models')
        await expect(self.page.locator('.model-card')).to_have_count(3)

    async def open_more(self, card):
        await card.get_by_role('button', name='更多操作', exact=True).click()
        return self.page.get_by_role('menu')

    async def save_evidence(self, filename):
        directory = os.environ.get('MODEL_CARD_EVIDENCE')
        if directory:
            Path(directory).mkdir(parents=True, exist_ok=True)
            await self.page.screenshot(path=str(Path(directory) / filename), full_page=True, animations='disabled')

    async def test_identity_is_compact_copyable_and_available_in_readonly_details(self):
        await self.open_models()
        card = self.page.locator('.model-card').first
        await expect(card).not_to_contain_text(MODEL_ID)
        await card.get_by_role('button', name='复制模型代码', exact=True).click()
        self.assertEqual(await self.page.evaluate('navigator.clipboard.readText()'), MODEL_ID)
        await card.get_by_role('button', name='查看模型详情：' + LONG_NAME, exact=True).click()
        detail = self.page.get_by_role('dialog', name='模型详情', exact=True)
        await expect(detail).to_contain_text(MODEL_ID)
        await expect(detail).to_contain_text(LONG_NAME)
        await expect(detail.get_by_role('textbox')).to_have_count(0)
        self.assertFalse(any(path == '/api/models/' + MODEL_ID for _, path, _ in self.requests))
        await self.save_evidence('model-identity-details.png')

    async def test_late_copy_failure_does_not_replace_a_new_details_context(self):
        await self.open_models()
        await self.page.evaluate("""() => {
            Object.defineProperty(navigator.clipboard, 'writeText', {
                configurable: true,
                value: () => new Promise((_resolve, reject) => { window.rejectModelCopy = reject })
            })
        }""")
        await self.page.locator('.model-card').first.get_by_role('button', name='复制模型代码', exact=True).click()
        await self.page.locator('.model-card').nth(1).get_by_role('button', name='查看模型详情：知识向量', exact=True).click()
        await self.page.evaluate("async () => { window.rejectModelCopy(new Error('clipboard unavailable')); await new Promise(requestAnimationFrame) }")
        await expect(self.page.get_by_role('dialog', name='模型详情', exact=True)).to_contain_text('知识向量')
        await expect(self.page.locator('.ai-model-center .ant-alert-error')).to_have_count(0)

    async def test_type_specific_content_has_no_inapplicable_or_invented_metrics(self):
        await self.open_models()
        cards = self.page.locator('.model-card')
        await expect(cards.nth(0)).to_contain_text('最大输出')
        await expect(cards.nth(0)).to_contain_text('65,536 Token')
        for index, purpose in [(1, '用于文本向量化与知识检索'), (2, '用于检索结果相关性排序')]:
            await expect(cards.nth(index)).not_to_contain_text('最大输出')
            await expect(cards.nth(index)).to_contain_text(purpose)
        for card in [cards.nth(index) for index in range(3)]:
            await expect(card).not_to_contain_text('维度')
            await expect(card).not_to_contain_text('更新于')
            self.assertLessEqual((await card.inner_text()).count('已启用'), 1)
        await self.save_evidence('model-cards-desktop.png')

    async def test_card_has_one_primary_action_and_preserves_every_management_menu_entry(self):
        await self.open_models()
        card = self.page.locator('.model-card').first
        await expect(card.get_by_role('button', name='测试连接', exact=True)).to_be_enabled()
        for label in ['编辑模型', '费率配置']:
            await expect(card.get_by_role('button', name=label, exact=True)).to_have_count(0)
        menu = await self.open_more(card)
        for label in ['编辑模型', '费率配置', '停用', '取消发布', '删除模型']:
            await expect(menu.get_by_role('menuitem', name=label, exact=True)).to_be_visible()
        await expect(menu.get_by_role('menuitem', name='测试连接', exact=True)).to_have_count(0)
        await self.save_evidence('model-more-actions.png')
        await menu.get_by_role('menuitem', name='编辑模型', exact=True).click()
        await expect(self.page.locator('.ant-drawer-open .ant-drawer-content')).to_contain_text('编辑模型')

    async def test_disabled_model_can_be_enabled_and_published_from_more(self):
        await self.open_models()
        card = self.page.locator('.model-card').nth(2)
        await expect(card.get_by_role('button', name='测试连接', exact=True)).to_be_disabled()
        menu = await self.open_more(card)
        await expect(menu.get_by_role('menuitem', name='发布', exact=True)).to_have_count(0)
        await menu.get_by_role('menuitem', name='启用', exact=True).click()
        await expect(card.get_by_role('button', name='测试连接', exact=True)).to_be_enabled()
        menu = await self.open_more(card)
        await menu.get_by_role('menuitem', name='发布', exact=True).click()
        await expect(card).to_contain_text('已启用')
        self.assertTrue(any(path == '/api/models/rerank-id/enable' for _, path, _ in self.requests))
        self.assertTrue(any(path == '/api/models/rerank-id/publish' for _, path, _ in self.requests))

    async def test_known_disabled_connection_explains_why_test_is_unavailable(self):
        self.connection_enabled = False
        await self.open_models()
        card = self.page.locator('.model-card').first
        await expect(card).to_contain_text('连接已停用，暂不可测试')
        await expect(card.get_by_role('button', name='测试连接', exact=True)).to_be_disabled()
        await self.save_evidence('model-disabled-connection.png')
        await self.page.get_by_role('button', name='列表视图', exact=True).click()
        await expect(self.page.locator('.model-table').get_by_role('button', name='测试连接', exact=True).first).to_be_disabled()

    async def test_tester_without_connection_read_permission_keeps_legitimate_test_access(self):
        self.permissions = ['ai:models:test']
        await self.open_models()
        card = self.page.locator('.model-card').first
        await expect(card).not_to_contain_text('团队服务')
        await expect(card.get_by_role('button', name='更多操作', exact=True)).to_have_count(0)
        await card.get_by_role('button', name='测试连接', exact=True).click()
        await expect(self.page.locator('.ant-message-notice-content')).to_have_text('测试成功')
        await expect(self.page.locator('.test-result')).to_have_count(0)
        self.assertFalse(any(path == '/api/models/connections' for _, path, _ in self.requests))

    async def test_rate_reader_has_only_rate_menu_and_readonly_rate_dialog(self):
        self.permissions = ['ai:rates:read']
        await self.open_models()
        card = self.page.locator('.model-card').first
        await expect(card.get_by_role('button', name='测试连接', exact=True)).to_have_count(0)
        menu = await self.open_more(card)
        await expect(menu.get_by_role('menuitem')).to_have_count(1)
        await menu.get_by_role('menuitem', name='费率配置', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='费率配置', exact=True)
        await expect(dialog.get_by_role('button', name='创建积分价格版本', exact=True)).to_have_count(0)
        self.assertFalse(any(method != 'GET' for method, _, _ in self.requests))

    async def test_writer_keeps_management_without_test_or_rate_access(self):
        self.permissions = ['ai:models:write']
        await self.open_models()
        card = self.page.locator('.model-card').first
        await expect(card.get_by_role('button', name='测试连接', exact=True)).to_have_count(0)
        menu = await self.open_more(card)
        await expect(menu.get_by_role('menuitem', name='编辑模型', exact=True)).to_be_visible()
        await expect(menu.get_by_role('menuitem', name='费率配置', exact=True)).to_have_count(0)

    async def test_basic_user_has_details_without_management_requests_or_actions(self):
        self.permissions = []
        await self.open_models()
        card = self.page.locator('.model-card').first
        await expect(card.get_by_role('button', name='测试连接', exact=True)).to_have_count(0)
        await expect(card.get_by_role('button', name='更多操作', exact=True)).to_have_count(0)
        await expect(card).not_to_contain_text('团队服务')
        await card.get_by_role('button', name='查看模型详情：' + LONG_NAME, exact=True).click()
        await expect(self.page.get_by_role('dialog', name='模型详情', exact=True)).to_contain_text(MODEL_ID)
        self.assertFalse(any(path == '/api/models/connections' or '/rate' in path for _, path, _ in self.requests))

    async def test_long_names_fit_three_and_two_columns_and_mobile_keeps_table_actions(self):
        await self.open_models()
        cards = self.page.locator('.model-card')
        first, second, third = [await cards.nth(i).bounding_box() for i in range(3)]
        self.assertAlmostEqual(first['y'], third['y'], delta=1)
        self.assertLess(first['x'], second['x'])
        await self.page.set_viewport_size({'width': 1150, 'height': 900})
        first, second, third = [await cards.nth(i).bounding_box() for i in range(3)]
        self.assertAlmostEqual(first['y'], second['y'], delta=1)
        self.assertGreater(third['y'], first['y'])
        await self.save_evidence('model-cards-two-columns.png')
        await self.page.get_by_role('button', name='列表视图', exact=True).click()
        await expect(cards).to_have_count(0)
        await expect(self.page.get_by_role('button', name='编辑模型', exact=True)).to_have_count(3)
        await expect(self.page.get_by_role('button', name='费率配置', exact=True)).to_have_count(3)
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        await expect(self.page.locator('.model-table')).to_be_visible()
        await self.page.locator('.model-table').get_by_role('button', name='更多操作', exact=True).first.click()
        await expect(self.page.get_by_role('menuitem', name='编辑模型', exact=True)).to_be_visible()
        self.assertFalse(await self.page.evaluate('document.documentElement.scrollWidth > window.innerWidth'))
        await self.save_evidence('model-mobile-table.png')


if __name__ == '__main__':
    unittest.main(verbosity=2)
