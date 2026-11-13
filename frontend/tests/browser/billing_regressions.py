"""Platform credit UI uses decimal strings and exact permission boundaries."""
import json
import os
from pathlib import Path
import re
import unittest
from urllib.parse import urlparse

from playwright.async_api import expect
from developer_regressions import DeveloperBoundary


class BillingRegressions(unittest.IsolatedAsyncioTestCase):
    # Reuse the controlled HTTP fixture without inheriting unrelated legacy cases.
    asyncTearDown = DeveloperBoundary.asyncTearDown
    open_details = DeveloperBoundary.open_details

    async def asyncSetUp(self):
        self.user = {'id': 'user-1', 'username': 'credit-manager', 'is_admin': False,
                     'permissions': ['ai:credits:read', 'ai:credits:write']}
        self.account = {'user_id': 'user-1', 'username': 'credit-manager', 'display_name': '积分管理员',
                        'is_active': True, 'enabled': True, 'version': 3,
                        'allocated_points': '100', 'reclaimed_points': '0', 'used_points': '0.2',
                        'reserved_points': '0.6', 'available_points': '99.2'}
        self.usage = {'run_id': 'run-unknown', 'user_id': 'user-1', 'model_id': 'model-1', 'source': 'chat', 'status': 'unknown', 'version': 2, 'reserved_points': '0.6', 'charged_points': None, 'rate_version': 1, 'created_at': '2026-10-29T01:00:00Z', 'billing_mode': 'token', 'input_tokens': None, 'output_tokens': None, 'cache_tokens': None, 'search_units': None, 'reasoning_tokens': None}
        self.fail_adjust_once = False
        self.adjustments = []
        self.billing_requests = []
        self.rate_posts = []
        self.point_rates = []
        self.fail_rate_once = False
        self.state = {'enabled': False, 'version': 0}
        await DeveloperBoundary.asyncSetUp(self)

    async def respond(self, route):
        path = urlparse(route.request.url).path
        method = route.request.method
        if path.startswith('/api/billing/'):
            self.billing_requests.append((method, path))
        if path == '/api/auth/me':
            body = self.user
        elif path == '/api/billing/state':
            if method == 'PATCH':
                self.posts.append(route.request.post_data_json)
                self.state = {'enabled': route.request.post_data_json['enabled'], 'version': 1}
            body = self.state
        elif path == '/api/billing/me':
            body = self.account
        elif path == '/api/billing/accounts':
            body = {'items': [self.account], 'total': 1, 'page': 1, 'page_size': 10}
        elif path == '/api/billing/accounts/user-1/state':
            payload = route.request.post_data_json
            self.posts.append(payload)
            self.account.update(enabled=payload['enabled'], version=4)
            body = self.account
        elif path == '/api/billing/accounts/user-1/adjust':
            payload = route.request.post_data_json
            self.adjustments.append(payload)
            if self.fail_adjust_once:
                self.fail_adjust_once = False
                return await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '服务暂不可用'}})
            body = self.account
        elif path.endswith('/ledger') and path.startswith('/api/billing/'):
            body = {'items': [], 'total': 0, 'page': 1, 'page_size': 10}
        elif path.endswith('/usage') and path.startswith('/api/billing/'):
            body = {'items': [self.usage], 'total': 1, 'page': 1, 'page_size': 10}
        elif path == '/api/billing/usage/run-unknown/reconcile':
            payload = route.request.post_data_json
            self.posts.append(payload)
            self.usage.update({key: value for key, value in payload.items() if key in self.usage and value is not None})
            self.usage.update(status='settled', version=3, charged_points='0.2')
            body = self.usage
        elif path == '/api/billing/models/model-1/rates':
            if method == 'POST':
                payload = route.request.post_data_json
                self.rate_posts.append(payload)
                if self.fail_rate_once:
                    self.fail_rate_once = False
                    return await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '定价确认暂不可用'}})
                version = len(self.point_rates) + 1
                body = {**payload, 'id': f'point-rate-{version}', 'model_id': 'model-1', 'version': version,
                        'created_at': '2026-10-29T01:00:00Z'}
                self.point_rates.append(body)
            else:
                body = self.point_rates
        elif path == '/api/developer/applications/app-1/keys' and method == 'POST':
            payload = route.request.post_data_json
            self.posts.append(payload)
            body = {**payload, 'id': 'key-1', 'application_id': 'app-1', 'prefix': 'sk-fixture...', 'status': 'active', 'created_at': '2026-10-29T01:00:00Z', 'expires_at': None, 'last_used_at': None, 'api_key': self.secret}
            self.keys.append({key: value for key, value in body.items() if key != 'api_key'})
        elif path == '/api/models/connections':
            body = []
        elif path == '/api/models/model-1/rate-cards':
            body = []
        elif path == '/api/usage':
            body = {'limit_tokens': 1000, 'used_tokens': 5, 'reserved_tokens': 0,
                    'points_enabled': self.state['enabled'], 'records': []}
        else:
            return await DeveloperBoundary.respond(self, route)
        await route.fulfill(status=200, content_type='application/json', body=json.dumps(body))

    async def test_personal_balance_and_unknown_usage_keep_original_units(self):
        await self.page.goto(self.origin + '/usage')
        await expect(self.page.get_by_text('积分计费已停用', exact=True)).to_be_visible()
        await expect(self.page.locator('.available-points')).to_have_text('99.2')
        await expect(self.page.get_by_role('button', name='计费设置', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('cell', name='待核对', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('cell', name='待核对，预占保留', exact=True)).to_be_visible()
        await self.save_evidence('my-credits-desktop.png')
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        await self.save_evidence('my-credits-mobile.png')
        await self.page.get_by_role('tab', name='Token 用量', exact=True).click()
        await expect(self.page.get_by_role('region', name='Token 用量统计')).to_contain_text('累计使用 Token')

    async def save_evidence(self, name):
        directory = os.environ.get('BILLING_EVIDENCE_DIR')
        if directory:
            await expect(self.page.locator('.ant-spin-blur')).to_have_count(0)
            Path(directory).mkdir(parents=True, exist_ok=True)
            await self.page.screenshot(path=str(Path(directory) / name), full_page=True, animations='disabled')

    async def test_grant_retry_keeps_idempotency_and_decimal_string(self):
        self.fail_adjust_once = True
        await self.page.goto(self.origin + '/usage')
        await self.page.get_by_role('tab', name='用户积分', exact=True).click()
        await self.page.get_by_role('button', name='发放', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='发放积分', exact=True)
        await dialog.get_by_label('积分数量', exact=True).fill('0.000000000000000001')
        await dialog.get_by_label('操作原因', exact=True).fill('测试精确发放')
        await dialog.get_by_role('button', name='确认发放', exact=True).click()
        await expect(dialog.get_by_text('服务暂不可用', exact=True)).to_be_visible()
        await dialog.get_by_role('button', name='确认发放', exact=True).click()
        await expect(dialog).to_be_hidden()
        self.assertEqual(self.adjustments[0], self.adjustments[1])
        self.assertEqual(self.adjustments[0]['points'], '0.000000000000000001')
        self.assertTrue(self.adjustments[0]['client_request_id'])

    async def test_account_pause_carries_current_version_and_reason(self):
        await self.page.goto(self.origin + '/usage')
        await self.page.get_by_role('tab', name='用户积分', exact=True).click()
        await self.save_evidence('credit-accounts-desktop.png')
        await self.page.get_by_role('button', name='暂停', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='暂停积分账户', exact=True)
        await dialog.get_by_label('操作原因', exact=True).fill('管理员暂停核实')
        await dialog.get_by_role('button', name='确认暂停', exact=True).click()
        await expect(dialog).to_be_hidden()
        self.assertEqual(self.posts[-1]['expected_version'], 3)
        self.assertFalse(self.posts[-1]['enabled'])

    async def test_unknown_usage_reconciliation_keeps_version_and_real_counts(self):
        await self.page.goto(self.origin + '/usage')
        await self.page.get_by_role('tab', name='用户积分', exact=True).click()
        await self.page.get_by_role('button', name='明细', exact=True).click()
        drawer = self.page.locator('.ant-drawer-open')
        await drawer.get_by_role('button', name='核对', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='核对积分消费', exact=True)
        await dialog.get_by_label('实际输入 Token', exact=True).fill('1000')
        await dialog.get_by_label('实际输出 Token', exact=True).fill('200')
        await dialog.get_by_label('核对依据', exact=True).fill('依据本地供应商已确认用量')
        await dialog.get_by_role('button', name='确认核对', exact=True).click()
        await expect(dialog).to_be_hidden()
        self.assertEqual(self.posts[-1]['expected_version'], 2)
        self.assertEqual(self.posts[-1]['input_tokens'], 1000)
        self.assertEqual(self.posts[-1]['output_tokens'], 200)
        self.assertFalse(self.posts[-1]['rejected_before_execution'])

    async def open_usage_reconciliation(self):
        await self.page.goto(self.origin + '/usage')
        await self.page.get_by_role('tab', name='用户积分', exact=True).click()
        await self.page.get_by_role('button', name='明细', exact=True).click()
        await self.page.locator('.ant-drawer-open').get_by_role('button', name='核对', exact=True).click()
        return self.page.get_by_role('dialog', name='核对积分消费', exact=True)

    async def test_grant_then_reconcile_uses_distinct_reason_labels_and_ids(self):
        self.usage['output_tokens'] = 200
        await self.page.goto(self.origin + '/usage')
        await self.page.get_by_role('tab', name='用户积分', exact=True).click()
        await self.page.get_by_role('button', name='发放', exact=True).click()
        grant = self.page.get_by_role('dialog', name='发放积分', exact=True)
        await grant.get_by_label('积分数量', exact=True).fill('100')
        grant_reason = grant.get_by_label('操作原因', exact=True)
        await grant_reason.fill('先发放账户积分')
        grant_reason_id = await grant_reason.get_attribute('id')
        await grant.get_by_role('button', name='确认发放', exact=True).click()
        await expect(grant).to_be_hidden()
        await self.page.get_by_role('button', name='明细', exact=True).click()
        await self.page.locator('.ant-drawer-open').get_by_role('button', name='核对', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='核对积分消费', exact=True)
        await dialog.get_by_label('实际输入 Token', exact=True).fill('1000')
        reason = dialog.get_by_label('核对依据', exact=True)
        await expect(reason).to_be_visible()
        await reason.fill('发放后的真实用量核对')
        reason_id = await reason.get_attribute('id')
        self.assertNotEqual(reason_id, grant_reason_id)
        reason_ids = await self.page.locator('textarea[id]').evaluate_all('(elements) => elements.map((element) => element.id)')
        self.assertEqual(len(reason_ids), len(set(reason_ids)), 'retained modal textareas must never duplicate IDs')
        await dialog.get_by_role('button', name='确认核对', exact=True).click()
        await expect(dialog).to_be_hidden()
        self.assertEqual(self.posts[-1]['reason'], '发放后的真实用量核对')
        self.assertEqual(self.posts[-1]['output_tokens'], 200)

    async def test_partial_known_output_is_prefilled_and_preserved(self):
        self.usage['output_tokens'] = 200
        dialog = await self.open_usage_reconciliation()
        await expect(dialog.get_by_label('实际输入 Token', exact=True)).to_have_value('')
        await expect(dialog.get_by_label('实际输出 Token', exact=True)).to_have_value('200')
        await expect(dialog.get_by_label('实际输出 Token', exact=True)).to_be_disabled()
        await dialog.get_by_label('核对依据', exact=True).fill('已确认缺失输入量')
        await dialog.get_by_role('button', name='确认核对', exact=True).click()
        await expect(dialog.get_by_text('请确认实际输入与输出 Token；未知值不能按零提交', exact=True)).to_be_visible()
        self.assertEqual(self.posts, [])
        await dialog.get_by_label('实际输入 Token', exact=True).fill('1000')
        await dialog.get_by_role('button', name='确认核对', exact=True).click()
        await expect(dialog).to_be_hidden()
        self.assertEqual(self.posts[-1]['input_tokens'], 1000)
        self.assertEqual(self.posts[-1]['output_tokens'], 200)
        self.assertIsNone(self.posts[-1]['cache_tokens'])
        self.assertIsNone(self.posts[-1]['search_units'])
        self.assertFalse(self.posts[-1]['rejected_before_execution'])
        await expect(self.page.locator('.ant-drawer-open').get_by_role('cell').filter(has_text='输入 1,000 / 输出 200')).to_be_visible()

    async def test_rerank_reconciliation_only_requires_search_and_keeps_tokens_unknown(self):
        self.usage.update(billing_mode='search_unit', source='rerank')
        dialog = await self.open_usage_reconciliation()
        await expect(dialog.get_by_label('实际检索单元', exact=True)).to_have_value('')
        await expect(dialog.get_by_label('实际输入 Token', exact=True)).to_have_count(0)
        await dialog.get_by_label('核对依据', exact=True).fill('供应商仅提供检索单元')
        await dialog.get_by_role('button', name='确认核对', exact=True).click()
        await expect(dialog.get_by_text('请确认实际检索单元；未知值不能按零提交', exact=True)).to_be_visible()
        self.assertEqual(self.posts, [])
        await dialog.get_by_label('实际检索单元', exact=True).fill('2')
        await dialog.get_by_role('button', name='确认核对', exact=True).click()
        await expect(dialog).to_be_hidden()
        self.assertEqual(self.posts[-1]['search_units'], 2)
        self.assertIsNone(self.posts[-1]['input_tokens'])
        self.assertIsNone(self.posts[-1]['output_tokens'])
        self.assertIsNone(self.posts[-1]['cache_tokens'])
        cell = self.page.locator('.ant-drawer-open').get_by_role('cell').filter(has_text='检索单元 2')
        await expect(cell).to_contain_text('输入 未提供 / 输出 未提供')
        await expect(cell).to_contain_text('缓存 未提供 / 推理 未提供')
        await self.save_evidence('search-only-consumption.png')

    async def test_key_monthly_point_limit_uses_string_and_does_not_add_balance(self):
        await self.open_details()
        await self.page.get_by_role('tab', name='访问密钥', exact=True).click()
        await self.page.get_by_role('button', name='创建密钥', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='创建访问密钥', exact=True)
        await dialog.get_by_label('密钥名称', exact=True).fill('积分月限密钥')
        await dialog.locator('.ant-select-selector').click()
        await self.page.locator('.ant-select-item-option-content').filter(has_text='日常助手').click()
        await dialog.get_by_label('月积分上限', exact=True).fill('0.000000000000000001')
        await dialog.get_by_role('button', name=re.compile(r'^创\s*建$')).click()
        await expect(self.page.get_by_text(self.secret, exact=True)).to_be_visible()
        self.assertEqual(self.posts[-1]['monthly_points_limit'], '0.000000000000000001')
        self.assertNotIn('monthly_budget', self.posts[-1])
        self.assertNotIn('allocated_points', self.posts[-1])

    async def test_basic_user_sees_own_credits_without_management_requests(self):
        self.user['permissions'] = []
        self.state['enabled'] = True
        await self.page.goto(self.origin + '/usage')
        await expect(self.page.locator('.available-points')).to_have_text('99.2')
        await expect(self.page.get_by_role('tab', name='用户积分', exact=True)).to_have_count(0)
        for action in ['计费设置', '发放', '回收', '暂停', '核对']:
            await expect(self.page.get_by_role('button', name=action, exact=True)).to_have_count(0)
        await self.page.get_by_role('tab', name='Token 用量', exact=True).click()
        await expect(self.page.get_by_role('region', name='Token 用量统计')).to_contain_text('累计使用 Token')
        await expect(self.page.locator('.token-total strong')).to_have_text('5')
        await expect(self.page.get_by_role('region', name='Token 额度')).to_have_count(0)
        self.assertTrue(any(path == '/api/billing/me' for _, path in self.billing_requests))
        self.assertFalse(any('/accounts' in path for _, path in self.billing_requests))
        self.assertTrue(all(method == 'GET' for method, _ in self.billing_requests))

    async def test_model_manager_cannot_grant_credits(self):
        self.user['permissions'] = ['ai:models:write', 'ai:rates:write']
        await self.page.goto(self.origin + '/usage')
        await expect(self.page.locator('.available-points')).to_be_visible()
        await expect(self.page.get_by_role('tab', name='用户积分', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button', name='计费设置', exact=True)).to_have_count(0)

    async def test_credit_reader_cannot_adjust_pause_or_reconcile(self):
        self.user['permissions'] = ['ai:credits:read']
        await self.page.goto(self.origin + '/usage')
        await self.page.get_by_role('tab', name='用户积分', exact=True).click()
        await expect(self.page.get_by_role('button', name='发放', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button', name='回收', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button', name='暂停', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='明细', exact=True).click()
        drawer = self.page.locator('.ant-drawer-open')
        await expect(drawer.get_by_role('cell', name='待核对', exact=True)).to_be_visible()
        await expect(drawer.get_by_role('button', name='核对', exact=True)).to_have_count(0)

    async def test_superadmin_switch_keeps_note_and_version(self):
        self.user['is_admin'] = True
        await self.page.goto(self.origin + '/usage')
        await self.page.get_by_role('button', name='计费设置', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='积分计费设置', exact=True)
        await dialog.get_by_role('switch', name='启用积分计费', exact=True).check()
        await expect(dialog.get_by_text('请先配置模型积分价格并准备用户余额。启用后，未配置价格或余额不足的调用将被拒绝。', exact=True)).to_be_visible()
        await dialog.get_by_label('备注', exact=True).fill('已完成价格与账户准备')
        await dialog.get_by_role('button', name='保存设置', exact=True).click()
        await expect(self.page.get_by_text('积分计费已启用', exact=True)).to_be_visible()
        self.assertEqual(self.posts[-1]['expected_version'], 0)
        self.assertEqual(self.posts[-1]['enabled'], True)
        self.assertTrue(self.posts[-1]['client_request_id'])

    async def rejected_mode_change(self, code, message):
        self.user['is_admin'] = True
        async def reject(route):
            if route.request.method == 'PATCH':
                return await route.fulfill(status=409, json={'error': {'code': code, 'message': message}})
            await self.respond(route)
        await self.page.route(self.origin + '/api/billing/state', reject)
        await self.page.goto(self.origin + '/usage')
        await self.page.get_by_role('button', name='计费设置', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='积分计费设置', exact=True)
        await dialog.get_by_role('switch', name='启用积分计费', exact=True).check()
        await dialog.get_by_label('备注', exact=True).fill('管理员确认启用积分')
        await dialog.get_by_role('button', name='保存设置', exact=True).click()
        return dialog

    async def test_enable_preserves_missing_rates_message_and_model_names(self):
        message = '以下已启用模型尚未配置积分费率：bge-m3、GLM-5.3-Flash'
        dialog = await self.rejected_mode_change('credit_rates_incomplete', message)
        await expect(dialog.get_by_text(message, exact=True)).to_be_visible()
        await expect(dialog.get_by_label('备注', exact=True)).to_have_value('管理员确认启用积分')
        self.assertFalse(self.state['enabled'])

    async def test_enable_preserves_active_work_message(self):
        message = '仍有进行中的运行或知识任务，请等待完成后再切换积分计费'
        dialog = await self.rejected_mode_change('billing_switch_busy', message)
        await expect(dialog.get_by_text(message, exact=True)).to_be_visible()
        self.assertFalse(self.state['enabled'])

    async def test_enable_version_conflict_keeps_refresh_guidance(self):
        dialog = await self.rejected_mode_change('version_conflict', '数据已更新，请刷新后重试')
        await expect(dialog.get_by_text('计费状态已变化，请关闭设置并刷新后重试。', exact=True)).to_be_visible()

    async def test_reclaim_preserves_insufficient_available_points_message(self):
        message = '仅能回收未使用且未预占的积分'
        await self.page.route(self.origin + '/api/billing/accounts/user-1/adjust', lambda route: route.fulfill(status=409, json={'error': {'code': 'credits_insufficient', 'message': message}}))
        await self.page.goto(self.origin + '/usage')
        await self.page.get_by_role('tab', name='用户积分', exact=True).click()
        await self.page.get_by_role('button', name='回收', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='回收积分', exact=True)
        await dialog.get_by_label('积分数量', exact=True).fill('100')
        await dialog.get_by_label('操作原因', exact=True).fill('回收剩余积分')
        await dialog.get_by_role('button', name='确认回收', exact=True).click()
        await expect(dialog.get_by_text(message, exact=True)).to_be_visible()

    async def test_reconciliation_preserves_confirmed_usage_conflict_message(self):
        message = '已有实际用量，不能确认模型调用未执行'
        await self.page.route(self.origin + '/api/billing/usage/run-unknown/reconcile', lambda route: route.fulfill(status=409, json={'error': {'code': 'usage_conflict', 'message': message}}))
        dialog = await self.open_usage_reconciliation()
        await dialog.get_by_label('实际输入 Token', exact=True).fill('1000')
        await dialog.get_by_label('实际输出 Token', exact=True).fill('200')
        await dialog.get_by_label('核对依据', exact=True).fill('供应商提供的实际计量')
        await dialog.get_by_role('button', name='确认核对', exact=True).click()
        await expect(dialog.get_by_text(message, exact=True)).to_be_visible()

    async def test_point_rate_requires_explicit_free_and_sends_per_million_strings(self):
        self.fail_rate_once = True
        self.user['permissions'] = ['ai:rates:read', 'ai:rates:write', 'ai:models:read']
        await self.page.goto(self.origin + '/models')
        await self.page.get_by_role('button', name='查看模型详情：日常助手', exact=True).click()
        await self.page.get_by_role('button', name='更多模型操作', exact=True).click()
        await self.page.get_by_role('menuitem', name='费率配置', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='费率配置', exact=True)
        await dialog.get_by_role('button', name='保存价格', exact=True).click()
        await expect(dialog.get_by_text('零价格必须明确标记为免费', exact=True)).to_be_visible()
        await dialog.get_by_label('输入积分 / 百万 Token', exact=True).fill('100')
        await dialog.get_by_label('输出积分 / 百万 Token', exact=True).fill('500')
        await dialog.get_by_label('备注', exact=True).fill('按平台积分方案设置新版本')
        await dialog.get_by_role('button', name='保存价格', exact=True).click()
        await expect(dialog.get_by_text('定价确认暂不可用', exact=True)).to_be_visible()
        await dialog.get_by_label('输入积分 / 百万 Token', exact=True).fill('100.000')
        await dialog.get_by_role('button', name='保存价格', exact=True).click()
        await expect(dialog.get_by_text('价格已保存', exact=True)).to_be_visible()
        await expect(dialog.get_by_role('button', name='保存价格', exact=True)).to_be_disabled()
        await expect(dialog.get_by_role('cell', name='1', exact=True)).to_have_count(0)
        self.assertEqual(self.rate_posts[0], self.rate_posts[1])
        self.assertTrue(self.rate_posts[-1]['client_request_id'])
        self.assertEqual(self.rate_posts[-1]['input_points'], '100')
        self.assertEqual(self.rate_posts[-1]['output_points'], '500')
        self.assertFalse(self.rate_posts[-1]['is_free'])
        await self.save_evidence('model-credit-rates.png')

    async def test_point_rate_can_save_without_note(self):
        self.user['permissions'] = ['ai:rates:read', 'ai:rates:write', 'ai:models:read']
        await self.page.goto(self.origin + '/models')
        await self.page.get_by_role('button', name='查看模型详情：日常助手', exact=True).click()
        await self.page.get_by_role('button', name='更多模型操作', exact=True).click()
        await self.page.get_by_role('menuitem', name='费率配置', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='费率配置', exact=True)
        await dialog.get_by_label('输入积分 / 百万 Token', exact=True).fill('1')
        await dialog.get_by_role('button', name='保存价格', exact=True).click()
        await expect(dialog.get_by_text('价格已保存', exact=True)).to_be_visible()
        await expect(dialog.get_by_label('备注', exact=True)).to_have_value('')
        self.assertEqual(self.rate_posts[-1]['reason'], '')
        self.assertTrue(self.rate_posts[-1]['client_request_id'])

    async def test_current_price_prefills_exact_values_and_keeps_history_folded(self):
        self.user['permissions'] = ['ai:rates:read', 'ai:rates:write', 'ai:models:read']
        original = dict(id='rate-1', model_id='model-1', version=1, created_at='2026-10-28T01:00:00Z',
                        billing_mode='token', is_free=False, input_points='1', output_points='1', cache_points='0', search_points='0')
        current = {**original, 'id': 'rate-2', 'version': 2, 'input_points': '0.000000000000000001', 'output_points': '2.000000000000000000'}
        self.point_rates = [original, current]
        await self.page.goto(self.origin + '/models')
        await self.page.get_by_role('button', name='查看模型详情：日常助手', exact=True).click()
        await self.page.get_by_role('button', name='更多模型操作', exact=True).click()
        await self.page.get_by_role('menuitem', name='费率配置', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='费率配置', exact=True)
        await expect(dialog.get_by_label('输入积分 / 百万 Token', exact=True)).to_have_value('0.000000000000000001')
        await expect(dialog.get_by_label('输出积分 / 百万 Token', exact=True)).to_have_value('2')
        save = dialog.get_by_role('button', name='保存价格', exact=True)
        await expect(save).to_be_disabled()
        await expect(dialog.get_by_role('table')).to_have_count(0)
        await dialog.get_by_label('输出积分 / 百万 Token', exact=True).fill('2.00')
        await expect(save).to_be_disabled()
        await dialog.get_by_label('输入积分 / 百万 Token', exact=True).fill('0.000000000000000002')
        await save.click()
        await expect(dialog.get_by_text('价格已保存', exact=True)).to_be_visible()
        self.assertEqual(len(self.rate_posts), 1)
        self.assertEqual(self.rate_posts[0]['input_points'], '0.000000000000000002')
        self.assertEqual(original['input_points'], '1')
        await dialog.locator('summary').filter(has_text='价格调整记录').click()
        await expect(dialog.get_by_role('table')).to_be_visible()
        self.assertEqual(len(self.point_rates), 3)
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))

    async def test_price_readers_cannot_edit_or_save_current_prices(self):
        self.user['permissions'] = ['ai:rates:read', 'ai:models:read']
        self.point_rates = [dict(id='rate-1', model_id='model-1', version=1, created_at='2026-10-28T01:00:00Z',
                                billing_mode='token', is_free=False, input_points='1', output_points='2', cache_points='0', search_points='0')]
        await self.page.goto(self.origin + '/models')
        await self.page.get_by_role('button', name='查看模型详情：日常助手', exact=True).click()
        await self.page.get_by_role('button', name='更多模型操作', exact=True).click()
        await self.page.get_by_role('menuitem', name='费率配置', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='费率配置', exact=True)
        await expect(dialog.get_by_label('输入积分 / 百万 Token', exact=True)).to_have_value('1')
        await expect(dialog.get_by_label('输入积分 / 百万 Token', exact=True)).to_be_disabled()
        await expect(dialog.get_by_role('checkbox', name='设为免费', exact=True)).to_be_disabled()
        await expect(dialog.get_by_role('button', name='保存价格', exact=True)).to_have_count(0)
        self.assertEqual(self.rate_posts, [])

    async def test_failed_price_loading_cannot_save_unknown_current_price(self):
        self.user['permissions'] = ['ai:rates:read', 'ai:rates:write', 'ai:models:read']
        await self.page.route(self.origin + '/api/billing/models/model-1/rates', lambda route: route.fulfill(status=503, json={
            'error': {'code': 'unavailable', 'message': '价格暂不可用'}}))
        await self.page.goto(self.origin + '/models')
        await self.page.get_by_role('button', name='查看模型详情：日常助手', exact=True).click()
        await self.page.get_by_role('button', name='更多模型操作', exact=True).click()
        await self.page.get_by_role('menuitem', name='费率配置', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='费率配置', exact=True)
        await expect(dialog.get_by_text('价格暂不可用', exact=True)).to_be_visible()
        await expect(dialog.get_by_role('button', name='保存价格', exact=True)).to_be_disabled()
        self.assertEqual(self.rate_posts, [])

    async def test_superadmin_can_enable_and_disable_without_note(self):
        self.user['is_admin'] = True
        await self.page.goto(self.origin + '/usage')
        await self.page.get_by_role('button', name='计费设置', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='积分计费设置', exact=True)
        await dialog.get_by_role('switch', name='启用积分计费', exact=True).check()
        await dialog.get_by_role('button', name='保存设置', exact=True).click()
        await expect(self.page.get_by_text('积分计费已启用', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='计费设置', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='积分计费设置', exact=True)
        await dialog.get_by_role('switch', name='启用积分计费', exact=True).uncheck()
        await expect(dialog.get_by_label('备注', exact=True)).to_have_value('')
        await dialog.get_by_role('button', name='保存设置', exact=True).click()
        await expect(self.page.get_by_text('积分计费已停用', exact=True)).to_be_visible()
        self.assertEqual([body['enabled'] for body in self.posts], [True, False])
        self.assertEqual([body['expected_version'] for body in self.posts], [0, 1])
        self.assertEqual([body['reason'] for body in self.posts], ['', ''])
        self.assertNotEqual(self.posts[0]['client_request_id'], self.posts[1]['client_request_id'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
