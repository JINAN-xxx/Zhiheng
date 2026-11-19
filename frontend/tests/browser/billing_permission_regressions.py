"""Credit dialogs must recheck current rights before opening or submitting."""
from urllib.parse import urlparse

from playwright.async_api import expect

from billing_regressions import BillingRegressions


class BillingPermissionRegressions(BillingRegressions):
    async def asyncSetUp(self):
        self.fail_identity = False
        await super().asyncSetUp()

    async def respond(self, route):
        if urlparse(route.request.url).path == '/api/auth/me' and self.fail_identity:
            return await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '身份确认暂不可用'}})
        return await super().respond(route)

    def revoke_management(self):
        self.user['is_admin'] = False
        self.user['permissions'] = ['platform:usage:enabled', 'platform:usage:read', 'ai:credits:enabled', 'ai:credits:read']

    async def accounts(self):
        await self.page.goto(self.origin + '/usage')
        await self.page.get_by_role('tab', name='用户积分', exact=True).click()
        await expect(self.page.get_by_role('button', name='发放', exact=True)).to_be_visible()

    async def test_revocation_prevents_opening_cached_grant_button(self):
        await self.accounts()
        self.revoke_management()
        await self.page.get_by_role('button', name='发放', exact=True).click()
        await expect(self.page.get_by_role('button', name='发放', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('dialog', name='发放积分', exact=True)).to_have_count(0)
        self.assertEqual(self.adjustments, [])

    async def test_revocation_prevents_opening_cached_account_button(self):
        await self.accounts()
        self.revoke_management()
        await self.page.get_by_role('button', name='暂停', exact=True).click()
        await expect(self.page.get_by_role('button', name='暂停', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('dialog', name='暂停积分账户', exact=True)).to_have_count(0)
        self.assertEqual(self.posts, [])

    async def test_revocation_before_confirmation_sends_no_adjustment(self):
        await self.accounts()
        await self.page.get_by_role('button', name='发放', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='发放积分', exact=True)
        await dialog.get_by_label('积分数量', exact=True).fill('1')
        await dialog.get_by_label('操作原因', exact=True).fill('测试权限变化')
        self.revoke_management()
        await dialog.get_by_role('button', name='确认发放', exact=True).click()
        await expect(dialog).to_be_hidden()
        self.assertEqual(self.adjustments, [])

    async def test_identity_lookup_failure_does_not_open_dialog(self):
        await self.accounts()
        self.fail_identity = True
        await self.page.get_by_role('button', name='发放', exact=True).click()
        await expect(self.page.get_by_text('身份确认暂不可用', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('dialog', name='发放积分', exact=True)).to_have_count(0)
        self.assertEqual(self.adjustments, [])

    async def test_changed_account_cannot_confirm_original_dialog(self):
        await self.accounts()
        await self.page.get_by_role('button', name='发放', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='发放积分', exact=True)
        await dialog.get_by_label('积分数量', exact=True).fill('1')
        await dialog.get_by_label('操作原因', exact=True).fill('测试身份变化')
        self.user = {**self.user, 'id': 'another-manager', 'username': 'another-manager'}
        await dialog.get_by_role('button', name='确认发放', exact=True).click()
        await expect(dialog).to_be_hidden()
        self.assertEqual(self.adjustments, [])

    async def test_revocation_before_account_confirmation_sends_no_state_change(self):
        await self.accounts()
        await self.page.get_by_role('button', name='暂停', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='暂停积分账户', exact=True)
        await dialog.get_by_label('操作原因', exact=True).fill('测试权限变化')
        self.revoke_management()
        await dialog.get_by_role('button', name='确认暂停', exact=True).click()
        await expect(dialog).to_be_hidden()
        self.assertEqual(self.posts, [])

    async def test_admin_revocation_prevents_opening_cached_billing_settings(self):
        self.user['is_admin'] = True
        await self.page.goto(self.origin + '/usage')
        button = self.page.get_by_role('button', name='计费设置', exact=True)
        await expect(button).to_be_enabled()
        self.revoke_management()
        await button.click()
        await expect(button).to_have_count(0)
        await expect(self.page.get_by_role('dialog', name='积分计费设置', exact=True)).to_have_count(0)

    async def test_open_billing_settings_close_after_admin_revocation(self):
        self.user['is_admin'] = True
        await self.page.goto(self.origin + '/usage')
        await self.page.get_by_role('button', name='计费设置', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='积分计费设置', exact=True)
        await expect(dialog).to_be_visible()
        self.revoke_management()
        await self.page.evaluate("window.dispatchEvent(new Event('focus'))")
        await expect(self.page.get_by_role('button', name='计费设置', exact=True)).to_have_count(0)
        await expect(dialog).to_be_hidden()
        self.assertEqual(self.posts, [])

    async def test_admin_revocation_before_confirmation_sends_no_mode_change(self):
        self.user['is_admin'] = True
        await self.page.goto(self.origin + '/usage')
        await self.page.get_by_role('button', name='计费设置', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='积分计费设置', exact=True)
        await dialog.get_by_role('switch', name='启用积分计费', exact=True).check()
        self.revoke_management()
        await dialog.get_by_role('button', name='保存设置', exact=True).click()
        await expect(dialog).to_be_hidden()
        self.assertEqual(self.posts, [])
