"""Personal profile UI with isolated, same-origin API fixtures."""
import asyncio
import contextlib
import os
import unittest
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect


class ProfileRegressions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.context = await self.browser.new_context(viewport={'width': 1440, 'height': 1000})
        self.page = await self.context.new_page()
        self.page.set_default_timeout(10000)
        self.origin = os.environ.get('PLATFORM_BROWSER_URL', 'http://127.0.0.1:19080')
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.user = {'id': 'member-1', 'username': 'member', 'is_admin': False, 'permissions': []}
        self.profile = {
            **{key: self.user[key] for key in ('id', 'username', 'is_admin')},
            'display_name': '资料成员', 'email': 'member@example.com', 'phone': '13800000000',
            'department_name': '产品研发部', 'roles': ['普通成员'], 'posts': ['产品经理'],
            'created_at': '2026-10-01T00:00:00Z', 'version': 1,
        }
        self.updates = []
        self.password_updates = []
        self.profile_failure = None
        self.update_failure = None
        self.password_failure = None
        self.hold_method = None
        self.requested = asyncio.Event()
        self.release = asyncio.Event()
        self.replied = asyncio.Event()
        # Source-mode Vite modules contain /src/.../api/; only intercept HTTP API calls.
        await self.page.route(self.origin + '/api/**', self.respond)

    async def asyncTearDown(self):
        self.release.set()
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()
        self.assertEqual(self.errors, [])

    async def respond(self, route):
        path = urlparse(route.request.url).path
        method = route.request.method
        if path == '/api/auth/me':
            await route.fulfill(json=self.user)
        elif path == '/api/auth/login':
            await route.fulfill(json={'user': self.user})
        elif path == '/api/auth/logout':
            await route.fulfill(status=204)
        elif path == '/api/auth/profile':
            if method == 'GET':
                failure = self.profile_failure
                body = dict(self.profile)
            else:
                payload = route.request.post_data_json
                self.updates.append(payload)
                failure = self.update_failure
                if not failure:
                    self.profile.update({key: payload[key] for key in ('display_name', 'email', 'phone')})
                    self.profile['version'] += 1
                body = dict(self.profile)
            await self.reply_profile(route, method, body, failure)
        elif path == '/api/auth/password':
            self.password_updates.append(route.request.post_data_json)
            delayed = self.hold_method == 'PASSWORD'
            if delayed:
                self.requested.set()
                await self.release.wait()
            with contextlib.suppress(Exception):
                if self.password_failure:
                    status, message = self.password_failure
                    await route.fulfill(status=status, json={'error': {'code': 'password_failed', 'message': message}})
                else:
                    await route.fulfill(status=204)
            if delayed:
                self.replied.set()
        elif path in ('/api/models', '/api/chat/conversations', '/api/knowledge/available', '/api/skills/available'):
            await route.fulfill(json=[])
        else:
            await route.fulfill(status=404, json={'error': {'code': 'fixture_missing', 'message': path}})

    async def reply_profile(self, route, method, body, failure):
        delayed = self.hold_method == method
        if delayed:
            self.requested.set()
            await self.release.wait()
        with contextlib.suppress(Exception):
            if failure:
                status, message = failure
                await route.fulfill(status=status, json={'error': {'code': 'profile_failed', 'message': message}})
            else:
                await route.fulfill(json=body)
        if delayed:
            self.replied.set()

    async def open_profile(self):
        await self.page.goto(self.origin + '/profile')
        await expect(self.page.get_by_role('textbox', name='显示名称', exact=True)).to_have_value('资料成员')

    async def fill_passwords(self, current='old-password', new='new-password-123', confirmation=None):
        await self.page.get_by_label('当前密码', exact=True).fill(current)
        await self.page.get_by_label('新密码', exact=True).fill(new)
        await self.page.get_by_label('确认新密码', exact=True).fill(new if confirmation is None else confirmation)

    async def logout(self):
        await self.page.get_by_role('button', name='用户菜单', exact=True).click()
        await self.page.get_by_role('menuitem', name='退出登录', exact=True).click()
        await expect(self.page).to_have_url(self.origin + '/login')

    async def test_member_enters_profile_from_account_menu_without_new_primary_navigation(self):
        await self.page.goto(self.origin + '/chat')
        navigation = self.page.get_by_role('navigation', name='主导航', exact=True)
        await expect(navigation.get_by_role('link', name='个人资料', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='用户菜单', exact=True).click()
        await expect(self.page.get_by_role('menuitem').filter(has_text='后台管理')).to_have_count(0)
        await self.page.get_by_role('menuitem').filter(has_text='个人资料').get_by_role('link', name='个人资料', exact=True).click()
        await expect(self.page).to_have_url(self.origin + '/profile')
        await expect(self.page.get_by_role('textbox', name='显示名称', exact=True)).to_have_value('资料成员')
        facts = self.page.locator('.profile-facts')
        for value in ('member', '普通成员', '产品研发部', '2026'):
            await expect(facts).to_contain_text(value)
        await expect(facts).not_to_contain_text('产品经理')
        await expect(facts.get_by_text('岗位',exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('textbox', name='账号', exact=True)).to_have_count(0)

    async def test_admin_entry_remains_permission_based_in_account_menu(self):
        self.user['permissions'] = ['system:users:read']
        await self.open_profile()
        await self.page.get_by_role('button', name='用户菜单', exact=True).click()
        await expect(self.page.get_by_role('menuitem').filter(has_text='后台管理')).to_be_visible()
        await expect(self.page.get_by_role('menuitem').filter(has_text='个人资料')).to_be_visible()
        await expect(self.page.get_by_role('navigation', name='主导航', exact=True).get_by_role('link', name='后台管理', exact=True)).to_have_count(0)

    async def test_edit_validation_cancel_and_trimmed_save_use_only_editable_fields(self):
        await self.open_profile()
        await self.page.get_by_label('邮箱', exact=True).fill('invalid')
        await self.page.get_by_role('button', name='保存资料', exact=True).click()
        await expect(self.page.get_by_text('请输入有效的邮箱地址', exact=True)).to_be_visible()
        self.assertEqual(self.updates, [])
        await self.page.get_by_role('button', name='取消修改', exact=True).click()
        await expect(self.page.get_by_label('邮箱', exact=True)).to_have_value('member@example.com')
        await self.page.get_by_label('显示名称', exact=True).fill(' 更新成员 ')
        await self.page.get_by_label('邮箱', exact=True).fill(' updated@example.com ')
        await self.page.get_by_label('联系电话', exact=True).fill(' 123 ')
        await self.page.get_by_role('button', name='保存资料', exact=True).click()
        await expect(self.page.get_by_text('基本资料已保存', exact=True)).to_be_visible()
        self.assertEqual(self.updates, [{'display_name': '更新成员', 'email': 'updated@example.com', 'phone': '123', 'expected_version': 1}])
        await expect(self.page.get_by_label('显示名称', exact=True)).to_have_value('更新成员')

    async def test_save_failure_keeps_draft_and_allows_retry(self):
        self.update_failure = (503, '资料服务暂时不可用')
        await self.open_profile()
        await self.page.get_by_label('显示名称', exact=True).fill('保留草稿')
        await self.page.get_by_role('button', name='保存资料', exact=True).click()
        await expect(self.page.get_by_text('资料服务暂时不可用', exact=True)).to_be_visible()
        await expect(self.page.get_by_label('显示名称', exact=True)).to_have_value('保留草稿')
        await expect(self.page.get_by_text('基本资料已保存', exact=True)).to_have_count(0)
        self.update_failure = None
        await self.page.get_by_role('button', name='保存资料', exact=True).click()
        await expect(self.page.get_by_text('基本资料已保存', exact=True)).to_be_visible()

    async def test_conflict_preserves_draft_until_explicit_reload(self):
        self.update_failure = (409, '资料已变更')
        await self.open_profile()
        await self.page.get_by_label('显示名称', exact=True).fill('本地草稿')
        await self.page.get_by_role('button', name='保存资料', exact=True).click()
        await expect(self.page.get_by_label('显示名称', exact=True)).to_have_value('本地草稿')
        await expect(self.page.get_by_role('button', name='保存资料', exact=True)).to_be_disabled()
        self.profile.update(display_name='服务端最新', version=3)
        self.update_failure = None
        await self.page.get_by_role('button', name='放弃草稿并重新加载', exact=True).click()
        await expect(self.page.get_by_label('显示名称', exact=True)).to_have_value('服务端最新')
        await self.page.get_by_role('button', name='保存资料', exact=True).click()
        await expect(self.page.get_by_text('基本资料已保存', exact=True)).to_be_visible()
        self.assertEqual(self.updates[-1]['expected_version'], 3)

    async def test_password_validation_wrong_current_password_and_successful_relogin(self):
        await self.open_profile()
        await self.fill_passwords(new='short', confirmation='different')
        await self.page.get_by_role('button', name='修改密码', exact=True).click()
        await expect(self.page.get_by_text('两次输入的新密码不一致', exact=True)).to_be_visible()
        self.assertEqual(self.password_updates, [])
        self.password_failure = (400, '当前密码不正确')
        await self.fill_passwords()
        await self.page.get_by_role('button', name='修改密码', exact=True).click()
        await expect(self.page.get_by_text('当前密码不正确', exact=True)).to_be_visible()
        await expect(self.page).to_have_url(self.origin + '/profile')
        await expect(self.page.get_by_label('当前密码', exact=True)).to_have_value('')
        self.password_failure = None
        await self.fill_passwords(new=' new-password-123 ')
        await self.page.get_by_role('button', name='修改密码', exact=True).click()
        await expect(self.page).to_have_url(self.origin + '/login?passwordChanged=1')
        await expect(self.page.get_by_text('密码修改成功，请使用新密码重新登录', exact=True)).to_be_visible()
        self.assertEqual(self.password_updates[-1], {'current_password': 'old-password', 'new_password': ' new-password-123 ', 'expected_version': 1})
        storage = await self.page.evaluate('JSON.stringify([Object.entries(localStorage), Object.entries(sessionStorage)])')
        self.assertNotIn('new-password', storage)
        self.assertNotIn('old-password', storage)
        await expect(self.page.locator('.ant-alert-success')).to_contain_text('密码修改成功，请使用新密码重新登录')

    async def test_password_conflict_requires_reload_and_keeps_profile_draft(self):
        self.password_failure = (409, '账号已变更')
        await self.open_profile()
        await self.page.get_by_label('显示名称', exact=True).fill('尚未保存')
        await self.fill_passwords()
        await self.page.get_by_role('button', name='修改密码', exact=True).click()
        await expect(self.page.get_by_role('button', name='放弃草稿并重新加载', exact=True)).to_be_visible()
        await expect(self.page.get_by_label('显示名称', exact=True)).to_have_value('尚未保存')
        await expect(self.page.get_by_label('当前密码', exact=True)).to_have_value('')
        await expect(self.page).to_have_url(self.origin + '/profile')

    async def test_unexpected_password_success_status_does_not_clear_identity(self):
        self.password_failure = (200, 'unexpected body')
        await self.open_profile()
        await self.fill_passwords()
        await self.page.get_by_role('button', name='修改密码', exact=True).click()
        await expect(self.page.get_by_text('服务器未确认密码修改成功，请重新登录后检查', exact=True)).to_be_visible()
        await expect(self.page).to_have_url(self.origin + '/profile')
        await expect(self.page.get_by_role('button', name='用户菜单', exact=True)).to_be_visible()

    async def test_profile_of_another_account_is_rejected(self):
        self.profile.update(id='another-account', username='someone-else', display_name='其他账号的私有资料')
        await self.page.goto(self.origin + '/profile')
        await expect(self.page.get_by_text('返回的个人资料与当前账号不一致，请重新登录', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('其他账号的私有资料', exact=True)).to_have_count(0)

    async def test_failed_load_and_malformed_contract_offer_retry(self):
        self.profile_failure = (503, '无法读取当前资料')
        await self.page.goto(self.origin + '/profile')
        await expect(self.page.get_by_text('无法读取当前资料', exact=True)).to_be_visible()
        self.profile_failure = None
        self.profile['roles'] = 'invalid'
        await self.page.get_by_role('button', name='重试', exact=True).click()
        await expect(self.page.get_by_text('服务器返回的列表格式不正确', exact=True)).to_be_visible()
        self.profile['roles'] = ['普通成员']
        await self.page.get_by_role('button', name='重试', exact=True).click()
        await expect(self.page.get_by_label('显示名称', exact=True)).to_have_value('资料成员')

    async def test_late_profile_load_after_logout_does_not_restore_private_data(self):
        self.hold_method = 'GET'
        await self.page.goto(self.origin + '/profile')
        await asyncio.wait_for(self.requested.wait(), 10)
        await self.logout()
        self.release.set()
        await asyncio.wait_for(self.replied.wait(), 10)
        await expect(self.page.get_by_role('heading', name='登录', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('资料成员', exact=True)).to_have_count(0)

    async def test_late_save_cannot_fill_another_identity(self):
        await self.open_profile()
        self.hold_method = 'PATCH'
        self.replied.clear()
        await self.page.get_by_label('显示名称', exact=True).fill('上个账号的私有草稿')
        await self.page.get_by_role('button', name='保存资料', exact=True).click()
        await asyncio.wait_for(self.requested.wait(), 10)
        await self.logout()
        self.user = {'id': 'second', 'username': 'second', 'is_admin': False, 'permissions': []}
        self.profile.update(id='second', username='second', display_name='第二账号', email='', phone='', version=1)
        self.hold_method = None
        await self.page.get_by_role('textbox', name='账号', exact=True).fill('second')
        await self.page.get_by_label('密码', exact=True).fill('second-password-123')
        await self.page.get_by_role('button', name='登录', exact=True).click()
        await expect(self.page).to_have_url(self.origin + '/chat')
        await self.page.get_by_role('button', name='用户菜单', exact=True).click()
        await self.page.get_by_role('menuitem').filter(has_text='个人资料').get_by_role('link', name='个人资料', exact=True).click()
        await expect(self.page.get_by_label('显示名称', exact=True)).to_have_value('第二账号')
        self.release.set()
        await asyncio.wait_for(self.replied.wait(), 10)
        await expect(self.page.get_by_label('显示名称', exact=True)).to_have_value('第二账号')
        await expect(self.page.get_by_text('基本资料已保存', exact=True)).to_have_count(0)

    async def test_navigation_clears_passwords_and_ignores_late_password_success(self):
        await self.open_profile()
        await self.fill_passwords()
        self.hold_method = 'PASSWORD'
        self.replied.clear()
        await self.page.get_by_role('button', name='修改密码', exact=True).click()
        await asyncio.wait_for(self.requested.wait(), 10)
        await self.page.get_by_role('link', name='对话体验', exact=True).click()
        await expect(self.page).to_have_url(self.origin + '/chat')
        self.release.set()
        await asyncio.wait_for(self.replied.wait(), 10)
        await self.page.get_by_role('button', name='用户菜单', exact=True).click()
        await self.page.get_by_role('menuitem').filter(has_text='个人资料').get_by_role('link', name='个人资料', exact=True).click()
        await expect(self.page.get_by_label('当前密码', exact=True)).to_have_value('')
        await expect(self.page.get_by_label('新密码', exact=True)).to_have_value('')
        await expect(self.page.get_by_label('确认新密码', exact=True)).to_have_value('')
        await expect(self.page).to_have_url(self.origin + '/profile')

    async def test_mobile_profile_is_single_column_and_fits_viewport(self):
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        await self.open_profile()
        await self.page.get_by_role('button', name='打开主菜单', exact=True).click()
        await self.page.get_by_role('button', name='用户菜单', exact=True).click()
        await expect(self.page.get_by_role('menuitem').filter(has_text='个人资料')).to_be_visible()
        await self.page.keyboard.press('Escape')
        await self.page.keyboard.press('Escape')
        geometry = await self.page.locator('.profile-layout').evaluate('element => ({columns: getComputedStyle(element).gridTemplateColumns, width: element.getBoundingClientRect().width, scroll: document.documentElement.scrollWidth, viewport: window.innerWidth})')
        self.assertEqual(len(geometry['columns'].split()), 1)
        self.assertLessEqual(geometry['width'], 390)
        self.assertLessEqual(geometry['scroll'], geometry['viewport'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
