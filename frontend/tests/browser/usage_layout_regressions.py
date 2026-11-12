"""Usage layouts preserve precise balances, permissions and unknown consumption."""

import re
import unittest

from playwright.async_api import expect

from billing_regressions import BillingRegressions


class UsageLayoutRegressions(BillingRegressions):
    async def test_mobile_account_actions_and_precise_balance_remain_available(self):
        self.account.update(available_points='0.000000000000000001')
        await self.page.set_viewport_size({'width': 320, 'height': 844})
        await self.page.goto(self.origin + '/usage')
        await expect(self.page.locator('.available-points')).to_have_text('0.000000000000000001')
        await expect(self.page.get_by_role('heading', level=1)).to_have_count(1)
        await self.page.get_by_role('tab', name='用户积分', exact=True).click()
        card = self.page.locator('.account-mobile')
        await expect(card).to_be_visible()
        await expect(card).to_contain_text('0.000000000000000001')
        await card.get_by_role('button', name='发放', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='发放积分', exact=True)
        await expect(dialog).to_contain_text('积分管理员')
        await dialog.get_by_role('button', name=re.compile(r'^取\s*消$')).click()
        await card.get_by_role('button', name='明细', exact=True).click()
        await expect(self.page.locator('.ant-drawer-open')).to_be_visible()
        self.assertEqual(self.adjustments, [])
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))

    async def test_mobile_unknown_consumption_does_not_become_zero(self):
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        await self.page.goto(self.origin + '/usage')
        card = self.page.locator('.credit-mobile-list .credit-usage-card')
        await expect(card).to_be_visible()
        await expect(card).to_contain_text('待核对，预占保留')
        await expect(card).to_contain_text('0.6 积分')
        await expect(card.get_by_role('button', name='核对', exact=True)).to_have_count(0)
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))

    async def test_readonly_mobile_accounts_do_not_expose_mutations(self):
        self.user['permissions'] = ['ai:credits:read']
        await self.page.set_viewport_size({'width': 320, 'height': 844})
        await self.page.goto(self.origin + '/usage')
        await self.page.get_by_role('tab', name='用户积分', exact=True).click()
        card = self.page.locator('.account-mobile')
        await expect(card).to_be_visible()
        for label in ['发放', '回收', '暂停', '恢复']:
            await expect(card.get_by_role('button', name=label, exact=True)).to_have_count(0)
        await card.get_by_role('button', name='明细', exact=True).click()
        await expect(self.page.locator('.ant-drawer-open').get_by_role('button', name='核对', exact=True)).to_have_count(0)
        self.assertEqual(self.posts, [])

    async def test_mobile_records_scroll_without_losing_top_level_tabs(self):
        await self.page.set_viewport_size({'width': 320, 'height': 844})
        await self.page.goto(self.origin + '/usage')
        card = self.page.locator('.credit-mobile-list .credit-usage-card')
        await expect(card).to_be_visible()
        tabs = self.page.locator('.usage-tabs > .ant-tabs-nav')
        before = await tabs.bounding_box()
        await card.scroll_into_view_if_needed()
        await expect(card).to_be_in_viewport()
        after = await tabs.bounding_box()
        self.assertAlmostEqual(before['y'], after['y'], delta=.5)
        await self.page.get_by_role('tab', name='用户积分', exact=True).click()
        await expect(self.page.locator('.account-mobile')).to_be_visible()

    async def test_waived_failure_is_explicit_and_keeps_provider_usage_unknown(self):
        self.usage.update(status='settled', charged_points='0', failure_waived=True)
        self.account.update(reserved_points='0', available_points='99.8')
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        await self.page.goto(self.origin + '/usage')
        card = self.page.locator('.credit-mobile-list .credit-usage-card')
        await expect(card).to_be_visible()
        await expect(card).to_contain_text('异常免计')
        await expect(card).to_contain_text('未提供')
        await expect(card.get_by_role('button', name='核对', exact=True)).to_have_count(0)
        await expect(self.page.locator('.reserved-points')).to_have_text('0')


def load_tests(loader, _tests, _pattern):
    return unittest.TestSuite(UsageLayoutRegressions(name)
                              for name in loader.getTestCaseNames(UsageLayoutRegressions)
                              if name in UsageLayoutRegressions.__dict__)


if __name__ == '__main__':
    unittest.main(verbosity=2)
