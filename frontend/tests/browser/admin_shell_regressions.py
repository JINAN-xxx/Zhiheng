"""Platform entry and published announcements with controlled HTTP boundaries."""
import unittest
from urllib.parse import urlparse

from playwright.async_api import expect
from developer_regressions import DeveloperBoundary


class AdminShellRegressions(DeveloperBoundary):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.notice_reads = 0

    async def respond(self, route):
        if urlparse(route.request.url).path == '/api/admin/notices/published':
            self.notice_reads += 1
            await route.fulfill(json={'items': [{'id': 'notice-1', 'title': '维护安排', 'content': '<script>window.unexpectedNoticeExecution = true</script>\n维护窗口已安排', 'kind': 'announcement', 'status': 'published', 'remark': '', 'version': 1, 'created_at': '2026-10-28T00:00:00Z', 'updated_at': '2026-10-28T00:00:00Z'}], 'total': 1, 'page': 1, 'page_size': 20})
        else:
            await super().respond(route)

    async def test_published_notices_load_on_click_and_render_as_text(self):
        await self.page.goto(self.origin + '/chat')
        await expect(self.page.get_by_role('textbox', name='消息内容', exact=True)).to_be_visible()
        self.assertEqual(self.notice_reads, 0)
        await self.page.get_by_role('button', name='通知', exact=True).click(timeout=2000)
        await expect(self.page.get_by_role('heading', name='维护安排', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('<script>window.unexpectedNoticeExecution = true</script>', exact=False)).to_be_visible()
        self.assertFalse(await self.page.evaluate('Boolean(window.unexpectedNoticeExecution)'))
        self.assertEqual(self.notice_reads, 1)
        await self.page.get_by_role('button', name='刷新公告', exact=True).click()
        await expect(self.page.get_by_role('button', name='刷新公告', exact=True)).to_be_enabled()
        self.assertEqual(self.notice_reads, 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
