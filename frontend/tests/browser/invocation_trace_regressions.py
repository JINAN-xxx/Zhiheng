"""Financial details through the real UI with controlled HTTP facts."""
import asyncio
import unittest
from urllib.parse import urlparse

from playwright.async_api import expect
from billing_regressions import BillingRegressions
from invocation_trace_fixture import credit_trace


class InvocationTraceRegressions(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = BillingRegressions.asyncTearDown
    save_evidence = BillingRegressions.save_evidence
    open_details = BillingRegressions.open_details

    async def asyncSetUp(self):
        self.trace_overrides = {}
        await BillingRegressions.asyncSetUp(self)

    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path in self.trace_overrides:
            return await route.fulfill(json=self.trace_overrides[path])
        return await BillingRegressions.respond(self, route)

    async def open_trace(self):
        await self.page.goto(self.origin + '/usage')
        await self.page.get_by_role('button', name='调用详情', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='调用与费用详情', exact=True)
        await expect(dialog.get_by_role('region', name='调用追溯', exact=True)).to_be_visible()
        return dialog

    async def test_exact_money_distinct_states_age_and_mobile_layout(self):
        trace = credit_trace(self.usage)
        trace['billing']['invocation'].update(currency='USD', rate_card_id='rate-money', rate_version=7,
            reserved_cost='0.123456789012', billable_cost=None)
        trace['billing']['reservation'] = {**self.usage, 'reserved_points': '0.000000000000000001'}
        trace['execution'] = dict(id='run-unknown', owner_id='initiator-1', billing_owner_id='user-1',
            resource_type='work_turn', status='failed', attempt_no=1, created_at=self.usage['created_at'])
        self.trace_overrides['/api/billing/usage/run-unknown'] = trace
        dialog = await self.open_trace()
        await expect(dialog).to_contain_text('0.123456789012 USD')
        await expect(dialog).to_contain_text('0.000000000000000001 积分')
        await expect(dialog).to_contain_text('距受理时长')
        await expect(dialog).to_contain_text('非首次待核对时间')
        await expect(dialog).to_contain_text('initiator-1')
        await expect(dialog.get_by_text('失败', exact=True)).to_be_visible()
        await expect(dialog.get_by_text('待核对', exact=True).first).to_be_visible()
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        await self.save_evidence('invocation-trace-mobile.png')

    async def test_parent_and_summary_links_fetch_separately_authorized_details(self):
        trace = credit_trace(self.usage)
        child = credit_trace({**self.usage, 'run_id': 'summary-1', 'source': 'chat_summary'})
        trace['related'] = [dict(run_id='summary-1', source='chat_summary', status='unknown',
            created_at=self.usage['created_at'], relation='summary')]
        child['related'] = [dict(run_id='run-unknown', source='chat', status='unknown',
            created_at=self.usage['created_at'], relation='parent')]
        self.trace_overrides.update({'/api/billing/usage/run-unknown': trace, '/api/billing/usage/summary-1': child})
        dialog = await self.open_trace()
        await dialog.get_by_role('button', name='summary-1', exact=True).click()
        await expect(dialog.get_by_text('父对话调用', exact=False)).to_be_visible()
        await dialog.get_by_role('button', name='run-unknown', exact=True).click()
        await expect(dialog.get_by_text('会话摘要调用', exact=False)).to_be_visible()

    async def test_readonly_finance_has_details_without_reconciliation(self):
        self.user['permissions'].remove('ai:credits:reconcile')
        dialog = await self.open_trace()
        await expect(dialog.get_by_role('button', name='核对用量', exact=True)).to_have_count(0)
        await expect(dialog).to_contain_text('原生执行记录未关联')

    async def test_waived_points_do_not_turn_unknown_supplier_usage_into_zero(self):
        trace = credit_trace(self.usage)
        trace['billing']['reservation'] = {**self.usage, 'status': 'settled', 'charged_points': '0', 'failure_waived': True}
        self.trace_overrides['/api/billing/usage/run-unknown'] = trace
        dialog = await self.open_trace()
        await expect(dialog).to_contain_text('供应商未知用量仍保留为未知')
        await expect(dialog.get_by_role('button', name='核对用量', exact=True)).to_have_count(0)

    async def test_unknown_token_diagnostics_with_settled_search_points_do_not_settle_twice(self):
        trace = credit_trace(self.usage)
        trace['billing']['invocation'].update(operation='rerank', search_units=2)
        trace['billing']['reservation'] = {**self.usage, 'status': 'settled', 'charged_points': '20', 'billing_mode': 'search_unit', 'search_units': 2}
        self.trace_overrides['/api/billing/usage/run-unknown'] = trace
        dialog = await self.open_trace()
        await expect(dialog).to_contain_text('缺失的 Token 诊断仍为未知')
        await expect(dialog.get_by_role('button', name='核对用量', exact=True)).to_have_count(0)

    async def test_reconciliation_freezes_confirmed_zero_and_positive_counters_from_both_meters(self):
        trace = credit_trace(self.usage)
        trace['billing']['invocation'].update(input_tokens=1000, cache_tokens=0, reasoning_tokens=5)
        trace['billing']['reservation'] = {**self.usage, 'output_tokens': 200}
        self.trace_overrides['/api/billing/usage/run-unknown'] = trace
        dialog = await self.open_trace()
        await dialog.get_by_role('button', name='核对用量', exact=True).click()
        editor = self.page.get_by_role('dialog', name='核对积分消费', exact=True)
        for label, value in [('实际输入 Token', '1000'), ('实际输出 Token', '200'), ('其中缓存 Token', '0')]:
            await expect(editor.get_by_label(label, exact=True)).to_have_value(value)
            await expect(editor.get_by_label(label, exact=True)).to_be_disabled()
        await expect(editor.get_by_role('checkbox')).to_be_disabled()

    async def test_late_detail_after_close_cannot_reopen_the_modal(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def delayed(route):
            started.set()
            await release.wait()
            await route.fulfill(json=credit_trace(self.usage))
        await self.page.route(self.origin + '/api/billing/usage/run-unknown?ledger_page=1', delayed)
        await self.page.goto(self.origin + '/usage')
        await self.page.get_by_role('button', name='调用详情', exact=True).click()
        await asyncio.wait_for(started.wait(), 5)
        dialog = self.page.get_by_role('dialog', name='调用与费用详情', exact=True)
        await dialog.get_by_role('button', name='Close', exact=True).click()
        release.set()
        await expect(dialog).to_be_hidden()

    async def test_only_reconcile_permission_does_not_offer_account_writes(self):
        self.user['permissions'] = ['platform:usage:enabled', 'platform:usage:read', 'ai:credits:enabled', 'ai:credits:read', 'ai:credits:reconcile']
        await self.page.goto(self.origin + '/usage')
        await self.page.get_by_role('tab', name='用户积分', exact=True).click()
        for action in ['发放', '回收', '暂停']:
            await expect(self.page.get_by_role('button', name=action, exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='明细', exact=True).click()
        await expect(self.page.locator('.ant-drawer-open').get_by_role('button', name='核对', exact=True)).to_be_visible()

    async def test_revoking_adjust_closes_form_while_finance_read_remains(self):
        await self.page.goto(self.origin + '/usage')
        await self.page.get_by_role('tab', name='用户积分', exact=True).click()
        await self.page.get_by_role('button', name='发放', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='发放积分', exact=True)
        await expect(dialog).to_be_visible()
        self.user['permissions'].remove('ai:credits:adjust')
        await self.page.evaluate("async () => { const m = await import('/src/modules/identity/public.ts'); await m.restoreIdentity(true) }")
        await expect(dialog).to_be_hidden()
        await expect(self.page.get_by_role('button', name='发放', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button', name='明细', exact=True)).to_be_visible()


if __name__ == '__main__':
    unittest.main(verbosity=2)
