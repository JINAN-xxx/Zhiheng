"""Platform entry and published announcements with controlled HTTP boundaries."""
import unittest
from urllib.parse import urlparse

from playwright.async_api import expect
from developer_regressions import DeveloperBoundary


class AdminShellRegressions(DeveloperBoundary):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.notice_reads = 0
        self.notice_version = 1
        self.notice_available = True
        self.user_override = None
        self.admin_reads = []

    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path == '/api/auth/me' and self.user_override is not None:
            await route.fulfill(json=self.user_override)
        elif path == '/api/admin/menus':
            self.admin_reads.append(path)
            await route.fulfill(json={'items': [{'name': '用户管理', 'path': '/admin/users', 'permission': 'system:users:read', 'sort_order': 10, 'is_visible': True}], 'total': 1, 'page': 1, 'page_size': 100})
        elif path == '/api/admin/users':
            self.admin_reads.append(path)
            await route.fulfill(json={'items': [], 'total': 0, 'page': 1, 'page_size': 20})
        elif path == '/api/admin/notices/published':
            self.notice_reads += 1
            await route.fulfill(json={'items': [{'id': 'notice-1', 'title': '维护安排', 'content': '<script>window.unexpectedNoticeExecution = true</script>\n维护窗口已安排', 'kind': 'announcement', 'status': 'published', 'remark': '', 'version': self.notice_version, 'created_at': '2026-10-28T00:00:00Z', 'updated_at': '2026-10-28T00:00:00Z'}] if self.notice_available else [], 'total': int(self.notice_available), 'page': 1, 'page_size': 20})
        else:
            await super().respond(route)

    async def test_published_notices_load_automatically_and_render_as_text(self):
        await self.page.goto(self.origin + '/chat')
        await expect(self.page.get_by_role('textbox', name='消息内容', exact=True)).to_be_visible()
        await expect(self.page.locator('.platform-notices-dot')).to_be_visible()
        self.assertEqual(self.notice_reads, 1)
        await self.page.get_by_role('button', name='通知', exact=True).click(timeout=2000)
        await expect(self.page.get_by_role('heading', name='维护安排', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('<script>window.unexpectedNoticeExecution = true</script>', exact=False)).to_be_visible()
        self.assertFalse(await self.page.evaluate('Boolean(window.unexpectedNoticeExecution)'))
        self.assertEqual(self.notice_reads, 2)
        await expect(self.page.locator('.platform-notices-dot')).to_have_count(0)
        await self.page.get_by_role('button', name='刷新公告', exact=True).click()
        await expect(self.page.get_by_role('button', name='刷新公告', exact=True)).to_be_enabled()
        self.assertEqual(self.notice_reads, 3)

    async def test_new_publication_lights_up_without_click_and_read_survives_reload(self):
        self.notice_available = False
        await self.page.goto(self.origin + '/chat')
        await expect(self.page.get_by_role('textbox', name='消息内容', exact=True)).to_be_visible()
        self.assertEqual(self.notice_reads, 1)
        await expect(self.page.locator('.platform-notices-dot')).to_have_count(0)
        self.notice_available = True
        await expect(self.page.locator('.platform-notices-dot')).to_be_visible(timeout=15000)
        await expect(self.page.get_by_role('button', name='通知', exact=True)).to_have_attribute('title', '通知（1 条未读）')
        await self.page.get_by_role('button', name='通知', exact=True).click()
        await expect(self.page.get_by_role('heading', name='维护安排', exact=True)).to_be_visible()
        await expect(self.page.locator('.platform-notices-dot')).to_have_count(0)
        await self.page.reload()
        await expect(self.page.get_by_role('textbox', name='消息内容', exact=True)).to_be_visible()
        await expect(self.page.locator('.platform-notices-dot')).to_have_count(0)
        self.notice_version += 1
        await self.page.evaluate("window.dispatchEvent(new Event('focus'))")
        await expect(self.page.locator('.platform-notices-dot')).to_be_visible()

    async def test_notice_reads_are_per_user_and_shared_between_tabs(self):
        await self.page.goto(self.origin + '/chat')
        await expect(self.page.locator('.platform-notices-dot')).to_be_visible()
        other = await self.context.new_page()
        await other.route(self.origin + '/api/**', self.respond)
        await other.goto(self.origin + '/chat')
        await expect(other.locator('.platform-notices-dot')).to_be_visible()
        await self.page.get_by_role('button', name='通知', exact=True).click()
        await expect(self.page.locator('.platform-notices-dot')).to_have_count(0)
        await expect(other.locator('.platform-notices-dot')).to_have_count(0)
        await other.close()
        self.user_override = {'id': 'another-member', 'username': 'other', 'is_admin': False, 'permissions': []}
        await self.page.reload()
        await expect(self.page.locator('.platform-notices-dot')).to_be_visible()

    async def test_notice_read_works_when_storage_is_blocked(self):
        await self.page.add_init_script("Storage.prototype.getItem = Storage.prototype.setItem = () => { throw new DOMException('blocked', 'SecurityError') }")
        await self.page.goto(self.origin + '/chat')
        await expect(self.page.locator('.platform-notices-dot')).to_be_visible()
        await self.page.get_by_role('button', name='通知', exact=True).click()
        await expect(self.page.get_by_role('heading', name='维护安排', exact=True)).to_be_visible()
        await expect(self.page.locator('.platform-notices-dot')).to_have_count(0)

    async def test_hidden_tab_pauses_notice_reads_and_return_refreshes(self):
        await self.page.goto(self.origin + '/chat')
        await expect(self.page.locator('.platform-notices-dot')).to_be_visible()
        await self.page.evaluate("Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange'))")
        reads = self.notice_reads
        await self.page.wait_for_timeout(10500)
        self.assertEqual(self.notice_reads, reads)
        await self.page.evaluate("Object.defineProperty(document, 'hidden', { configurable: true, get: () => false }); document.dispatchEvent(new Event('visibilitychange'))")
        await expect(self.page.get_by_role('button', name='通知', exact=True)).to_be_visible()
        for _ in range(20):
            if self.notice_reads > reads:
                break
            await self.page.wait_for_timeout(50)
        self.assertGreater(self.notice_reads, reads)

    async def test_admin_entry_uses_account_menu_and_opens_real_route(self):
        await self.page.goto(self.origin + '/chat')
        navigation = self.page.get_by_role('navigation', name='主导航', exact=True)
        await expect(navigation).to_be_visible()
        await expect(navigation.get_by_role('link', name='后台管理', exact=True)).to_have_count(0)
        await expect(navigation.get_by_text('平台管理', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='用户菜单', exact=True).click()
        await self.page.get_by_role('menuitem').filter(has_text='后台管理').get_by_role('link', name='后台管理', exact=True).click()
        await expect(self.page).to_have_url(self.origin + '/admin/users')
        await expect(self.page.get_by_role('heading', name='用户管理', exact=True)).to_be_visible()

    async def test_reader_enters_console_from_account_menu(self):
        self.user_override = {'id': 'reader', 'username': 'reader', 'is_admin': False, 'permissions': ['system:users:read']}
        await self.page.goto(self.origin + '/chat')
        await self.page.get_by_role('button', name='用户菜单', exact=True).click()
        await self.page.get_by_role('menuitem').filter(has_text='后台管理').get_by_role('link', name='后台管理', exact=True).click()
        await expect(self.page).to_have_url(self.origin + '/admin/users')
        await expect(self.page.get_by_role('heading', name='用户管理', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='新建用户', exact=True)).to_have_count(0)

    async def test_member_has_no_console_entry_or_console_data_access(self):
        self.user_override = {'id': 'member', 'username': 'member', 'is_admin': False, 'permissions': []}
        await self.page.goto(self.origin + '/chat')
        await self.page.get_by_role('button', name='用户菜单', exact=True).click()
        await expect(self.page.get_by_role('menuitem').filter(has_text='后台管理')).to_have_count(0)
        await self.page.goto(self.origin + '/admin')
        await expect(self.page).to_have_url(self.origin + '/chat')
        self.assertEqual(self.admin_reads, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
