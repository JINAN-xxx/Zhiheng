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
    async def asyncSetUp(self):
        self.point_rates = []
        self.currency_rate_requests = []
        await super().asyncSetUp()

    async def open_model_details(self, name='日常助手'):
        await self.page.get_by_role('button', name='查看模型详情：' + name, exact=True).click()
        dialog = self.page.locator('.model-detail-modal[role="dialog"], .model-detail-modal [role="dialog"]')
        await expect(dialog).to_be_visible()
        return dialog

    async def open_model_more(self, name='日常助手'):
        detail = self.page.locator('.model-detail-modal[role="dialog"], .model-detail-modal [role="dialog"]')
        if not await detail.is_visible():
            detail = await self.open_model_details(name)
        await detail.get_by_role('button', name='更多模型操作', exact=True).click()
        return self.page.get_by_role('menu')

    async def open_rate_dialog(self):
        await self.page.goto(self.origin + '/models')
        menu = await self.open_model_more()
        await menu.get_by_role('menuitem', name='费率配置', exact=True).click()
        return self.page.get_by_role('dialog', name='费率配置', exact=True)


    async def test_chat_model_keeps_an_output_limit_above_8192(self):
        await self.page.goto(self.origin + '/models')
        await self.page.get_by_role('button', name='接入模型', exact=True).click()
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await drawer.get_by_placeholder('例如：日常助手').fill('长输出模型')
        await drawer.get_by_placeholder('填写服务商提供的模型 ID').fill('large-output')
        await drawer.locator('.ant-form-item').filter(has_text='最大输出 tokens').get_by_role('spinbutton').fill('65536')
        await drawer.get_by_role('button', name='保存模型', exact=True).click()
        await expect(drawer).not_to_be_visible()
        self.assertEqual(self.posts[-1]['max_output_tokens'], 65536)
        self.assertNotIn('published', self.posts[-1])
        await expect(self.page.locator('.model-item').filter(has=self.page.get_by_role('button', name='查看模型详情：长输出模型', exact=True))).not_to_contain_text('未发布')
        self.assertIsNone(self.posts[-1]['context_window_tokens'])


    async def test_8192_output_limit_is_saved_as_an_integer(self):
        await self.page.goto(self.origin + '/models')
        await self.page.get_by_role('button', name='接入模型', exact=True).click()
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await drawer.get_by_placeholder('例如：日常助手').fill('8192 对话模型')
        await drawer.get_by_placeholder('填写服务商提供的模型 ID').fill('provider/chat-8192')
        await drawer.locator('.ant-form-item').filter(has_text='最大输出 tokens').get_by_role('spinbutton').fill('8192')
        await drawer.get_by_role('button', name='保存模型', exact=True).click()
        await expect(drawer).not_to_be_visible()
        self.assertEqual(self.posts[-1]['max_output_tokens'], 8192)
        self.assertIsInstance(self.posts[-1]['max_output_tokens'], int)

    async def test_missing_model_fields_do_not_blame_a_valid_output_limit(self):
        connections = [
            {'id': f'connection-{i}', 'name': f'服务连接 {i}', 'provider': 'openai_compatible',
             'base_url': 'https://provider.example/v1', 'has_api_key': True, 'enabled': True}
            for i in range(1, 4)
        ]
        await self.page.route('**/api/models/connections', lambda route: route.fulfill(json=connections))
        await self.page.goto(self.origin + '/models')
        await self.page.get_by_role('button', name='接入模型', exact=True).click()
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await drawer.locator('.ant-form-item').filter(has_text='最大输出 tokens').get_by_role('spinbutton').fill('8192')
        save = drawer.get_by_role('button', name='保存模型', exact=True)
        await save.click()
        await expect(drawer.get_by_role('alert')).to_contain_text('请填写上游模型标识')
        await drawer.get_by_placeholder('填写服务商提供的模型 ID').fill('provider/chat-8192')
        await save.click()
        await expect(drawer.get_by_role('alert')).to_contain_text('请填写模型显示名称')
        await drawer.get_by_placeholder('例如：日常助手').fill('8192 多连接模型')
        await save.click()
        await expect(drawer.get_by_role('alert')).to_contain_text('请选择模型连接')
        await expect(drawer).not_to_contain_text('输出上限须为')
        self.assertEqual(self.posts, [])
        await drawer.locator('.ant-form-item').filter(has_text='模型连接').locator('.ant-select-selector').click()
        await self.page.locator('.ant-select-item-option-content').filter(has_text=re.compile(r'^服务连接 2$')).click()
        await save.click()
        await expect(drawer).not_to_be_visible()
        self.assertEqual(self.posts[-1]['connection_id'], 'connection-2')
        self.assertEqual(self.posts[-1]['max_output_tokens'], 8192)

    async def test_failed_save_validation_keeps_the_form_and_all_values(self):
        await self.page.route('**/api/models', lambda route: route.fulfill(status=422, json={'error': {'code': 'model_validation_not_found', 'message': '服务商未提供该模型或当前密钥无权使用，请核对上游模型标识'}}) if route.request.method == 'POST' else self.respond(route))
        await self.page.goto(self.origin + '/models')
        await self.page.get_by_role('button', name='接入模型', exact=True).click()
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await drawer.get_by_placeholder('例如：日常助手').fill('待核对模型')
        await drawer.get_by_placeholder('填写服务商提供的模型 ID').fill('wrong-provider-model')
        output = drawer.locator('.ant-form-item').filter(has_text='最大输出 tokens').get_by_role('spinbutton')
        await output.fill('262144')
        await drawer.get_by_role('button', name='保存模型', exact=True).click()
        await expect(drawer.get_by_role('alert')).to_contain_text('请核对上游模型标识')
        await expect(drawer.get_by_placeholder('例如：日常助手')).to_have_value('待核对模型')
        await expect(drawer.get_by_placeholder('填写服务商提供的模型 ID')).to_have_value('wrong-provider-model')
        await expect(output).to_have_value('262144')
        await expect(self.page.locator('.model-item')).to_have_count(1)

    async def test_output_limit_above_131072_is_saved_without_clamping(self):
        await self.page.goto(self.origin + '/models')
        await self.page.get_by_role('button', name='接入模型', exact=True).click()
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await drawer.get_by_placeholder('例如：日常助手').fill('大窗口模型')
        await drawer.get_by_placeholder('填写服务商提供的模型 ID').fill('provider/large-output')
        await drawer.locator('.ant-form-item').filter(has_text='最大输出 tokens').get_by_role('spinbutton').fill('262144')
        await drawer.get_by_role('button', name='保存模型', exact=True).click()
        await expect(drawer).not_to_be_visible()
        self.assertEqual(self.posts[-1]['max_output_tokens'], 262144)

    async def test_typed_connection_test_only_shows_success_or_failure(self):
        snapshot = {'id': 'smoke-run', 'resource_type': 'connection_test', 'resource_id': 'model-1', 'status': 'completed', 'attempt_id': 'attempt-1', 'attempt_no': 1, 'revision': 2, 'projection_seq': 1, 'text': '', 'error': None, 'created_at': '2026-10-23T00:00:00Z', 'usage': {'input_tokens': 6, 'output_tokens': 0, 'status': 'known'}, 'result': {'data': [{'index': 0, 'embedding': [0.1, 0.2]}]}}
        await self.page.route('**/api/models/model-1/test', lambda route: route.fulfill(status=201, json=snapshot))
        for status, message in [('completed', '测试成功'), ('failed', '测试失败')]:
            with self.subTest(status=status):
                snapshot['status'] = status
                await self.page.goto(self.origin + '/models')
                detail = await self.open_model_details()
                await detail.get_by_role('button', name='测试连接', exact=True).click()
                await expect(self.page.locator('.ant-message-notice-content')).to_have_text(message)
                await expect(self.page.locator('.test-result')).to_have_count(0)
                await expect(self.page.get_by_text('embedding', exact=True)).to_have_count(0)

    async def test_connection_admission_error_remains_visible_and_clears_after_success(self):
        rejected = True
        async def test_response(route):
            if rejected:
                return await route.fulfill(status=409, json={'error': {
                    'code': 'credit_rate_missing', 'message': '模型尚未配置积分费率'}})
            await route.fulfill(status=202, json={
                'id': 'test-success', 'resource_type': 'connection_test', 'resource_id': 'model-1',
                'status': 'completed', 'attempt_id': 'attempt-1', 'attempt_no': 1,
                'revision': 2, 'projection_seq': 1, 'text': 'OK', 'error': None,
                'created_at': '2026-11-10T00:00:00Z',
                'usage': {'input_tokens': 8, 'output_tokens': 2, 'status': 'known'}})
        await self.page.route('**/api/models/model-1/test', test_response)
        await self.page.goto(self.origin + '/models')
        detail = await self.open_model_details()
        await detail.get_by_role('button', name='测试连接', exact=True).click()
        await expect(detail.get_by_role('alert')).to_contain_text('模型尚未配置积分费率')
        await expect(self.page.locator('.ant-message-error')).to_have_text('模型尚未配置积分费率')
        rejected = False
        await detail.get_by_role('button', name='测试连接', exact=True).click()
        await expect(self.page.locator('.ant-message-success')).to_have_text('测试成功')
        await expect(detail.get_by_role('alert')).to_have_count(0)

    async def test_failed_connection_run_displays_the_actual_failure(self):
        await self.page.route('**/api/models/model-1/test', lambda route: route.fulfill(status=202, json={
            'id': 'test-failed', 'resource_type': 'connection_test', 'resource_id': 'model-1',
            'status': 'failed', 'attempt_id': 'attempt-1', 'attempt_no': 1,
            'revision': 2, 'projection_seq': 1, 'text': '',
            'error': {'code': 'provider_rejected', 'message': '供应商拒绝了本次请求，请检查模型配置'},
            'created_at': '2026-11-10T00:00:00Z',
            'usage': {'input_tokens': 0, 'output_tokens': 0, 'status': 'known'}}))
        await self.page.goto(self.origin + '/models')
        detail = await self.open_model_details()
        await detail.get_by_role('button', name='测试连接', exact=True).click()
        await expect(detail.get_by_role('alert')).to_contain_text('供应商拒绝了本次请求，请检查模型配置')
        await expect(self.page.locator('.ant-message-error')).to_have_text('供应商拒绝了本次请求，请检查模型配置')

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
            body = {**payload, 'id': 'new-model', 'enabled': True, 'capabilities': [payload['model_type']]}
        elif path == '/api/billing/models/model-1/rates':
            if method == 'POST':
                payload = route.request.post_data_json
                self.posts.append(payload)
                version = len(self.point_rates) + 1
                body = {**payload, 'id': f'point-rate-{version}', 'model_id': 'model-1', 'version': version,
                        'created_at': '2026-10-23T01:00:00Z'}
                self.point_rates.append(body)
            else:
                body = self.point_rates
        elif path == '/api/models/model-1/rate-cards':
            self.currency_rate_requests.append((method, path))
            await route.fulfill(status=500, json={'error': {'code': 'unexpected_currency_rate', 'message': path}})
            return
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
        self.assertNotIn('published', self.posts[-1])

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






    async def test_rate_dialog_is_points_only(self):
        self.point_rates = [dict(id='point-1', model_id='model-1', version=1,
            created_at='2026-10-23T01:00:00Z', billing_mode='token', is_free=False,
            input_points='1', output_points='2', cache_points='0', search_points='0')]
        dialog = await self.open_rate_dialog()
        await expect(dialog.get_by_role('heading', name='当前积分价格', exact=True)).to_be_visible()
        await expect(dialog.get_by_label('输入积分 / 百万 Token', exact=True)).to_have_value('1')
        await expect(dialog.get_by_role('tab')).to_have_count(0)
        await expect(dialog.get_by_text('金额费率', exact=True)).to_have_count(0)
        await expect(dialog.get_by_label('币种', exact=True)).to_have_count(0)
        await expect(dialog.get_by_text('USD', exact=True)).to_have_count(0)
        await expect(dialog.get_by_role('button', name='保存价格', exact=True)).to_be_disabled()
        self.assertEqual(self.currency_rate_requests, [])
        self.assertEqual(self.posts, [])

    async def test_point_rate_keeps_exact_decimal_string(self):
        dialog = await self.open_rate_dialog()
        await dialog.get_by_label('输入积分 / 百万 Token', exact=True).fill('0.000000000000000001')
        await dialog.get_by_label('输出积分 / 百万 Token', exact=True).fill('0.000000000000000002')
        await dialog.get_by_role('button', name='保存价格', exact=True).click()
        await expect(dialog.get_by_text('价格已保存', exact=True)).to_be_visible()
        self.assertEqual(self.posts[-1]['input_points'], '0.000000000000000001')
        self.assertEqual(self.posts[-1]['output_points'], '0.000000000000000002')
        self.assertNotIn('currency', self.posts[-1])
        self.assertNotIn('input_price', self.posts[-1])
        self.assertEqual(self.currency_rate_requests, [])

    async def test_late_point_price_history_cannot_remove_confirmed_price(self):
        requested, release = asyncio.Event(), asyncio.Event()
        reads = 0
        created = None
        async def rates(route):
            nonlocal reads, created
            if route.request.method == 'GET':
                reads += 1
                if reads == 2:
                    requested.set()
                    await release.wait()
                    with contextlib.suppress(Exception):
                        await route.fulfill(json=[])
                else:
                    await route.fulfill(json=[created] if created else [])
            else:
                created = {**route.request.post_data_json, 'id': 'new-point-rate', 'model_id': 'model-1',
                           'version': 2, 'created_at': '2026-10-23T01:00:00Z'}
                await route.fulfill(status=201, json=created)
        await self.page.route(self.origin + '/api/billing/models/model-1/rates', rates)
        try:
            dialog = await self.open_rate_dialog()
            await dialog.get_by_label('输入积分 / 百万 Token', exact=True).fill('1')
            await dialog.get_by_role('button', name='保存价格', exact=True).click()
            await asyncio.wait_for(requested.wait(), timeout=5)
            await expect(dialog.get_by_text('价格已保存', exact=True)).to_be_visible()
            await dialog.locator('summary').filter(has_text='价格调整记录').click()
            await expect(dialog.get_by_role('cell', name='2', exact=True)).to_be_visible()
            release.set()
            await expect(dialog.locator('.ant-spin')).to_have_count(0)
            await expect(dialog.get_by_role('cell', name='2', exact=True)).to_be_visible()
            await expect(dialog.get_by_role('button', name='保存价格', exact=True)).to_be_disabled()
            self.assertEqual(self.currency_rate_requests, [])
        finally:
            release.set()


if __name__ == '__main__':
    unittest.main(verbosity=2)
