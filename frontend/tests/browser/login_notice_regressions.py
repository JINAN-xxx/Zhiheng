"""Login navigation failures are one-time notices; live login errors remain visible."""
import os
import re
import unittest
from urllib.parse import parse_qs, urlencode, urlparse

from playwright.async_api import expect

import profile_regressions as profile_fixture


class LoginNoticeRegressions(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = profile_fixture.ProfileRegressions.asyncTearDown
    reply_profile = profile_fixture.ProfileRegressions.reply_profile

    async def asyncSetUp(self):
        await profile_fixture.ProfileRegressions.asyncSetUp(self)
        self.authenticated = False
        self.identity_failure = None

    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path == '/api/auth/me':
            if self.identity_failure is not None:
                status = self.identity_failure
                self.identity_failure = None
                return await route.fulfill(status=status, content_type='text/plain', body='Temporary upstream failure')
            if not self.authenticated:
                return await route.fulfill(status=401, json={'error': {'code': 'unauthenticated', 'message': '请先登录'}})
            return await route.fulfill(json=self.user)
        if path == '/api/auth/login':
            body = route.request.post_data_json
            if body['username'] != 'member' or body['password'] != 'fixture-password-only':
                return await route.fulfill(status=401, json={'error': {'code': 'invalid_credentials', 'message': '用户名或密码错误'}})
            self.authenticated = True
            return await route.fulfill(json={'user': self.user})
        return await profile_fixture.ProfileRegressions.respond(self, route)

    async def sign_in(self, password='fixture-password-only'):
        await self.page.get_by_role('textbox', name='账号', exact=True).fill('member')
        await self.page.get_by_role('textbox', name='密码', exact=True).fill(password)
        await self.page.get_by_role('button', name='登录', exact=True).click()

    async def test_old_gateway_notice_is_removed_from_url_and_does_not_reappear_after_refresh(self):
        query = urlencode({'reason': '请求失败（502）'})
        await self.page.goto(self.origin + '/login?' + query)
        await expect(self.page.get_by_role('alert')).to_contain_text('上次访问未成功：请求失败（502）')
        await expect(self.page).to_have_url(self.origin + '/login')
        await self.page.reload()
        await expect(self.page.get_by_role('button', name='登录', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('alert')).to_have_count(0)
        await self.sign_in()
        await expect(self.page).to_have_url(self.origin + '/chat')

    async def test_reason_cleanup_preserves_redirect_success_notice_and_hash(self):
        query = urlencode({'reason': '请求失败（502）', 'redirect': '/profile', 'passwordChanged': '1'})
        await self.page.goto(self.origin + '/login?' + query + '#account')
        await expect(self.page).not_to_have_url(re.compile('reason='))
        parsed = urlparse(self.page.url)
        self.assertEqual(parse_qs(parsed.query), {'redirect': ['/profile'], 'passwordChanged': ['1']})
        self.assertEqual(parsed.fragment, 'account')
        await self.page.reload()
        await expect(self.page.get_by_role('alert')).to_contain_text('密码修改成功')
        await expect(self.page.get_by_text('请求失败（502）', exact=False)).to_have_count(0)
        await self.sign_in()
        await expect(self.page).to_have_url(self.origin + '/profile')

    async def test_real_login_failure_replaces_historical_notice(self):
        await self.page.goto(self.origin + '/login?' + urlencode({'reason': '请求失败（502）'}))
        await expect(self.page).to_have_url(self.origin + '/login')
        await self.sign_in('wrong-password')
        alert = self.page.get_by_role('alert')
        await expect(alert).to_contain_text('用户名或密码错误')
        await expect(alert).not_to_contain_text('502')
        await expect(self.page.get_by_role('textbox', name='账号', exact=True)).to_have_value('member')
        await expect(self.page.get_by_role('textbox', name='密码', exact=True)).to_have_value('')

    async def test_gateway_failure_from_auth_guard_is_consumed_after_redirect(self):
        self.identity_failure = 502
        await self.page.goto(self.origin + '/profile')
        await expect(self.page).to_have_url(self.origin + '/login')
        await expect(self.page.get_by_role('alert')).to_contain_text('上次访问未成功')
        await self.page.reload()
        await expect(self.page.get_by_role('button', name='登录', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('alert')).to_have_count(0)
        await self.sign_in()
        await expect(self.page).to_have_url(self.origin + '/chat')


if __name__ == '__main__':
    os.environ.setdefault('PLATFORM_BROWSER_URL', 'http://127.0.0.1:19080')
    unittest.main()
