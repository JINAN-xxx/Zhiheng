"""History modal layout with real Vue navigation and isolated HTTP boundaries."""
import unittest
from urllib.parse import parse_qs, urlparse

from playwright.async_api import expect
from developer_regressions import DeveloperBoundary


class ChatHistoryLayoutRegressions(DeveloperBoundary):
    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path == '/api/chat/conversations':
            archived = parse_qs(urlparse(route.request.url).query).get('archived', ['false'])[0] == 'true'
            await route.fulfill(json=[
                {'id': 'c3', 'title': '归档样例', 'archived': True, 'created_at': '2026-10-29T00:00:00Z'},
            ] if archived else [
                {'id': 'c1', 'title': '历史样例', 'archived': False, 'created_at': '2026-10-29T00:00:00Z'},
                {'id': 'c2', 'title': '另一条记录', 'archived': False, 'created_at': '2026-10-29T00:00:00Z'},
            ])
        elif path == '/api/chat/conversations/c1/messages':
            await route.fulfill(json=[
                {'id': 'q1', 'role': 'user', 'content': '保留的历史提问', 'run_id': None, 'created_at': '2026-10-29T00:00:00Z'},
                {'id': 'a1', 'role': 'assistant', 'content': '保留的历史回复', 'run_id': None, 'created_at': '2026-10-29T00:00:01Z'},
            ])
        else:
            await super().respond(route)

    async def open_chat(self):
        await self.page.goto(self.origin + '/chat')
        await expect(self.page.get_by_role('textbox', name='消息内容', exact=True)).to_be_visible()

    def assert_same_workspace_bounds(self, before, after):
        for dimension in ['x', 'y', 'width', 'height']:
            self.assertAlmostEqual(before[dimension], after[dimension], delta=1, msg=dimension)

    async def test_history_defaults_closed_without_a_history_column(self):
        await self.open_chat()
        await expect(self.page.get_by_role('dialog', name='历史记录', exact=True)).to_be_hidden()
        await expect(self.page.get_by_role('complementary', name='对话历史', exact=True)).to_have_count(0)
        await expect(self.page.locator('.conversation-panel')).to_have_count(0)
        toolbar = self.page.locator('.chat-toolbar')
        await expect(toolbar.get_by_role('button', name='历史记录', exact=True)).to_contain_text('历史记录')
        await expect(toolbar.get_by_role('button', name='新建对话', exact=True)).to_be_visible()
        self.assertIsNone(await self.page.evaluate("localStorage.getItem('zhiheng-chat-history-expanded')"))

    async def test_modal_preserves_workspace_bounds_messages_and_draft(self):
        await self.open_chat()
        before = await self.page.locator('.chat-workspace').bounding_box()
        dialog = await self.open_history()
        await dialog.get_by_role('button', name='历史样例', exact=True).click(trial=True)
        modal = await dialog.locator('.ant-modal').bounding_box()
        viewport = self.page.viewport_size
        self.assertAlmostEqual(modal['x'] + modal['width'] / 2, viewport['width'] / 2, delta=2)
        self.assert_same_workspace_bounds(before, await self.page.locator('.chat-workspace').bounding_box())
        await dialog.get_by_role('button', name='历史样例', exact=True).click()
        await expect(dialog).to_be_hidden()
        await expect(self.page.get_by_text('保留的历史回复', exact=True)).to_be_visible()
        await self.page.get_by_role('textbox', name='消息内容', exact=True).fill('尚未发送的草稿')
        selected = await self.page.locator('.chat-workspace').bounding_box()
        dialog = await self.open_history()
        self.assert_same_workspace_bounds(selected, await self.page.locator('.chat-workspace').bounding_box())
        await self.close_history()
        self.assert_same_workspace_bounds(selected, await self.page.locator('.chat-workspace').bounding_box())
        await expect(self.page.get_by_text('保留的历史回复', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('textbox', name='消息内容', exact=True)).to_have_value('尚未发送的草稿')
        await expect(self.page.locator('.chat-toolbar').get_by_role('button', name='历史记录', exact=True)).to_be_focused()
        self.assertEqual(self.posts, [])

    async def test_search_archived_filter_and_new_chat_shortcut(self):
        await self.open_chat()
        dialog = await self.open_history()
        await expect(dialog.get_by_role('button', name='历史样例', exact=True)).to_be_visible()
        await expect(dialog.get_by_role('button', name='另一条记录', exact=True)).to_be_visible()
        search = dialog.get_by_role('textbox', name='搜索最近会话', exact=True)
        await search.fill('历史')
        await expect(dialog.get_by_role('button', name='历史样例', exact=True)).to_be_visible()
        await expect(dialog.get_by_role('button', name='另一条记录', exact=True)).to_have_count(0)
        await search.fill('')
        await dialog.get_by_text('已归档', exact=True).click()
        await expect(dialog.get_by_role('button', name='归档样例', exact=True)).to_be_visible()
        await expect(dialog.get_by_role('button', name='历史样例', exact=True)).to_have_count(0)
        await dialog.get_by_text('最近会话', exact=True).click()
        await dialog.get_by_role('button', name='历史样例', exact=True).click()
        await expect(dialog).to_be_hidden()
        await expect(self.page.get_by_text('保留的历史回复', exact=True)).to_be_visible()
        await self.page.get_by_role('textbox', name='消息内容', exact=True).fill('新建时清理的草稿')
        await self.page.locator('.chat-toolbar').get_by_role('button', name='新建对话', exact=True).click()
        await expect(dialog).to_be_hidden()
        await expect(self.page.get_by_text('保留的历史回复', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('textbox', name='消息内容', exact=True)).to_have_value('')
        await expect(self.page).to_have_url(self.origin + '/chat')
        self.assertEqual(self.posts, [])

    async def test_mobile_modal_selects_and_closes_without_horizontal_overflow(self):
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        await self.open_chat()
        before = await self.page.locator('.chat-workspace').bounding_box()
        dialog = await self.open_history()
        self.assert_same_workspace_bounds(before, await self.page.locator('.chat-workspace').bounding_box())
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
        await dialog.get_by_role('button', name='历史样例', exact=True).click(trial=True)
        modal = await dialog.locator('.ant-modal').bounding_box()
        self.assertGreaterEqual(modal['x'], 0)
        self.assertLessEqual(modal['x'] + modal['width'], 391)
        await dialog.get_by_role('button', name='历史样例', exact=True).click()
        await expect(dialog).to_be_hidden()
        await expect(self.page.get_by_text('保留的历史回复', exact=True)).to_be_visible()
        self.assert_same_workspace_bounds(before, await self.page.locator('.chat-workspace').bounding_box())
        await self.page.get_by_role('textbox', name='消息内容', exact=True).fill('手机草稿')
        await self.open_history()
        await self.page.keyboard.press('Escape')
        await expect(dialog).to_be_hidden()
        await expect(self.page.get_by_role('textbox', name='消息内容', exact=True)).to_have_value('手机草稿')
        await expect(self.page.locator('.chat-toolbar').get_by_role('button', name='新建对话', exact=True)).to_be_visible()
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
        self.assertEqual(self.posts, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
