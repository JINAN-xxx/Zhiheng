"""Model management controls must send the corresponding real core contracts."""
import json
import asyncio
import contextlib
import re
import unittest
from urllib.parse import urlparse

from playwright.async_api import expect
from developer_regressions import DeveloperBoundary


class ModelManagementRegressions(DeveloperBoundary):
    async def test_chat_model_keeps_an_output_limit_above_8192(self):
        await self.page.goto(self.origin + '/models')
        await self.page.get_by_role('button', name='接入模型', exact=True).click()
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await drawer.get_by_placeholder('例如：日常助手').fill('长输出模型')
        await drawer.get_by_placeholder('填写服务商提供的模型 ID').fill('large-output')
        await drawer.get_by_role('spinbutton').fill('65536')
        await drawer.get_by_role('button', name='保存模型', exact=True).click()
        await expect(drawer).not_to_be_visible()
        self.assertEqual(self.posts[-1]['max_output_tokens'], 65536)

    async def test_late_price_history_cannot_remove_confirmed_version(self):
        requested, release = asyncio.Event(), asyncio.Event()
        reads = 0
        created = None
        async def rates(route):
            nonlocal reads, created
            if route.request.method == 'GET':
                reads += 1
                if reads == 1:
                    requested.set()
                    await release.wait()
                    with contextlib.suppress(Exception):
                        await route.fulfill(json=[])
                else:
                    await route.fulfill(json=[created] if created else [])
            else:
                created = {**route.request.post_data_json, 'id': 'new-rate', 'model_id': 'model-1', 'version': 2, 'created_at': '2026-10-23T01:00:00Z'}
                await route.fulfill(status=201, json=created)
        await self.page.route(self.origin + '/api/models/model-1/rate-cards', rates)
        try:
            await self.page.goto(self.origin + '/models')
            await self.page.get_by_role('button', name='更多操作', exact=True).click()
            await self.page.get_by_role('menuitem', name='费率配置', exact=True).click()
            await requested.wait()
            dialog = self.page.get_by_role('dialog')
            await dialog.get_by_role('tab', name='历史金额费率', exact=True).click()
            await dialog.get_by_label('输入令牌单价', exact=True).fill('0.000000012345')
            await dialog.get_by_role('button', name='创建价格版本', exact=True).click()
            await expect(dialog.get_by_text('2', exact=True)).to_be_visible()
            release.set()
            await self.page.wait_for_timeout(400)
            await expect(dialog.get_by_text('2', exact=True)).to_be_visible()
        finally:
            release.set()

    async def test_typed_connection_test_displays_structured_result(self):
        snapshot = {'id': 'smoke-run', 'resource_type': 'connection_test', 'resource_id': 'model-1', 'status': 'completed', 'attempt_id': 'attempt-1', 'attempt_no': 1, 'revision': 2, 'projection_seq': 1, 'text': '', 'error': None, 'created_at': '2026-10-23T00:00:00Z', 'usage': {'input_tokens': 6, 'output_tokens': 0, 'status': 'known'}, 'result': {'data': [{'index': 0, 'embedding': [0.1, 0.2]}]}}
        await self.page.route('**/api/models/model-1/test', lambda route: route.fulfill(status=201, json=snapshot))
        await self.page.goto(self.origin + '/models')
        await self.page.get_by_role('button', name='测试连接', exact=True).click()
        await expect(self.page.locator('.test-result pre')).to_contain_text('embedding')
        await expect(self.page.locator('.test-result pre')).not_to_contain_text('等待模型响应')

    async def respond(self, route):
        path = urlparse(route.request.url).path
        method = route.request.method
        connection = {'id': 'connection-1', 'name': '团队服务', 'provider': 'openai_compatible', 'base_url': 'http://provider.example/v1', 'has_api_key': True, 'enabled': True}
        if path == '/api/models/connections':
            body = [connection]
        elif path == '/api/models/connections/connection-1' and method == 'PATCH':
            payload = route.request.post_data_json
            self.posts.append(payload)
            body = {**connection, **payload}
        elif path == '/api/models' and method == 'POST':
            payload = route.request.post_data_json
            self.posts.append(payload)
            body = {**payload, 'id': 'new-model', 'enabled': True, 'published': True, 'capabilities': [payload['model_type']]}
        elif path == '/api/billing/models/model-1/rates' and method == 'GET':
            body = []
        elif path == '/api/models/model-1/rate-cards':
            if method == 'POST':
                payload = route.request.post_data_json
                self.posts.append(payload)
                body = {**payload, 'id': 'rate-1', 'model_id': 'model-1', 'version': 1, 'created_at': '2026-10-23T00:00:00Z'}
            else:
                body = []
        else:
            return await super().respond(route)
        await route.fulfill(status=201 if method == 'POST' else 200, content_type='application/json', body=json.dumps(body))

    async def test_embedding_model_can_be_configured(self):
        await self.page.goto(self.origin + '/models')
        await self.page.get_by_role('button', name='接入模型', exact=True).click()
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        embedding = drawer.get_by_role('radio', name='向量', exact=True)
        await expect(embedding).to_be_enabled()
        await drawer.get_by_text('向量', exact=True).click()
        await expect(embedding).to_be_checked()
        await drawer.get_by_placeholder('例如：日常助手').fill('向量模型')
        await drawer.get_by_placeholder('填写服务商提供的模型 ID').fill('embedding-private')
        await drawer.locator('.ant-select-selector').click()
        await self.page.locator('.ant-select-item-option-content').filter(has_text='团队服务').click()
        await drawer.get_by_role('button', name='保存模型', exact=True).click()
        await expect(drawer).not_to_be_visible()
        self.assertEqual(self.posts[-1]['model_type'], 'embedding')

    async def test_edit_connection_does_not_send_an_empty_replacement_key(self):
        await self.page.goto(self.origin + '/models')
        await self.page.get_by_role('tab', name='凭据管理', exact=True).click()
        await self.page.get_by_role('button', name='编辑凭据', exact=True).click()
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await drawer.get_by_placeholder('例如：团队模型服务').fill('已改名称')
        await drawer.get_by_role('button', name='保存连接', exact=True).click()
        await expect(drawer).not_to_be_visible()
        self.assertEqual(self.posts[-1]['name'], '已改名称')
        self.assertNotIn('api_key', self.posts[-1])

    async def test_manual_rate_keeps_exact_decimal_string(self):
        await self.page.goto(self.origin + '/models')
        await self.page.get_by_role('button', name='更多操作', exact=True).click()
        await self.page.get_by_role('menuitem', name='费率配置', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        await dialog.get_by_role('tab', name='历史金额费率', exact=True).click()
        await dialog.get_by_label('输入令牌单价', exact=True).fill('0.00000001')
        await dialog.get_by_label('输出令牌单价', exact=True).fill('0.00000002')
        await dialog.get_by_role('button', name='创建价格版本', exact=True).click()
        await expect(dialog.get_by_text('1', exact=True)).not_to_have_count(0)
        self.assertEqual(self.posts[-1]['input_price'], '0.00000001')
        self.assertEqual(self.posts[-1]['output_price'], '0.00000002')


if __name__ == '__main__':
    unittest.main(verbosity=2)
