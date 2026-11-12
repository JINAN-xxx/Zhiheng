"""Copy actions on LAN HTTP and on browsers denying the async clipboard API."""
import os
import unittest

from playwright.async_api import expect
from message_actions_regressions import MessageActionsRegressions


class ClipboardRegressions(MessageActionsRegressions):
    async def read_clipboard(self):
        reader = await self.context.new_page()
        try:
            await reader.goto(os.environ.get('CLIPBOARD_READ_ORIGIN', 'http://127.0.0.1:19080') + '/platform.html')
            return (await reader.evaluate('navigator.clipboard.readText()')).replace('\r\n', '\n')
        finally:
            await reader.close()

    async def test_reply_copy_without_async_clipboard_preserves_multiline_content(self):
        self.history[1]['content'] = '中文回复\n\n商品,销量\n手机,12\n'
        await self.page.add_init_script(
            "Object.defineProperty(navigator, 'clipboard', {value: undefined, configurable: true})"
        )
        await self.page.goto(self.origin + '/chat')
        await self.select_conversation('操作会话')
        button = self.page.get_by_role('button', name='复制回复', exact=True)
        await button.click()
        await expect(button).to_have_class('active')
        self.assertEqual(await self.read_clipboard(), self.history[1]['content'])
        await expect(button).to_be_focused()
        await expect(self.page.locator('textarea[readonly]')).to_have_count(0)

    async def test_share_link_copy_without_async_clipboard_stays_inside_dialog(self):
        await self.page.add_init_script(
            "Object.defineProperty(navigator, 'clipboard', {value: undefined, configurable: true})"
        )
        await self.open_chat()
        await self.page.get_by_role('button', name='分享', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        await expect(dialog.get_by_text('原回复', exact=True)).to_be_visible()
        await dialog.get_by_role('button', name='创建分享链接', exact=True).click()
        button = dialog.get_by_role('button', name='复制分享链接', exact=True)
        await button.click()
        await expect(self.page.get_by_text('分享链接已复制', exact=True)).to_be_visible()
        self.assertEqual(await self.read_clipboard(), self.origin + '/share/fixture-share-token')
        await expect(button).to_be_focused()
        await expect(dialog).to_be_visible()
        await expect(self.page.locator('textarea[readonly]')).to_have_count(0)

    async def test_async_clipboard_denial_falls_back_to_selection(self):
        await self.page.add_init_script("""
            Object.defineProperty(navigator, 'clipboard', {
                configurable: true,
                value: {writeText: () => Promise.reject(new DOMException('Denied', 'NotAllowedError'))}
            })
        """)
        await self.open_chat()
        button = self.page.get_by_role('button', name='复制回复', exact=True)
        await button.click()
        await expect(button).to_have_class('active')
        self.assertEqual(await self.read_clipboard(), '原回复')

    async def test_rejected_copy_reports_failure_without_success_state(self):
        await self.page.add_init_script("""
            Object.defineProperty(navigator, 'clipboard', {value: undefined, configurable: true})
            document.execCommand = () => false
        """)
        await self.open_chat()
        button = self.page.get_by_role('button', name='复制回复', exact=True)
        await button.click()
        await expect(self.page.get_by_text('复制失败，请手动选择内容后复制', exact=True)).to_be_visible()
        await expect(button).not_to_have_class('active')
        await expect(self.page.locator('textarea[readonly]')).to_have_count(0)


def load_tests(loader, _tests, _pattern):
    return unittest.TestSuite(
        ClipboardRegressions(name)
        for name in ClipboardRegressions.__dict__
        if name.startswith('test_')
    )


if __name__ == '__main__':
    unittest.main(verbosity=2)
