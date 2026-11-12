"""Original skill pages and chat picker against strict core HTTP contracts."""

import asyncio
import contextlib
import re
import unittest
from urllib.parse import urlparse

from developer_regressions import DeveloperBoundary
from playwright.async_api import expect


class SkillsUIRegressions(DeveloperBoundary):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.member = False
        self.skill = {'id': 'skill-1', 'slug': 'example', 'name': '示例技能', 'description': '用于验收的提示词', 'package_version': '1.0.0', 'version': 1, 'status': 'enabled', 'source': 'upload', 'revision_id': 'revision-1', 'content_hash': 'hash-1', 'file_count': 1, 'total_bytes': 123, 'created_at': '2026-10-23T00:00:00Z', 'updated_at': '2026-10-23T00:00:00Z'}
        self.items = [self.skill]
        self.content = '---\nname: example\nversion: 1.0.0\n---\n# 技能正文\n\n**安全说明**\n\n<script>window.skillExecuted=true</script>\n\n[危险链接](javascript:window.skillExecuted=true)'
        self.upload = None
        self.confirmations = 0
        self.updates, self.sent, self.chat_history = [], [], []

    async def respond(self, route):
        path, method = urlparse(route.request.url).path, route.request.method
        if path == '/api/auth/me':
            await route.fulfill(json={'id': 'user-1', 'username': 'member' if self.member else 'admin', 'is_admin': not self.member})
        elif path == '/api/skills':
            await route.fulfill(json={'items': self.items, 'total': len(self.items), 'page': 1, 'page_size': 12})
        elif path == '/api/skills/available':
            await route.fulfill(json=self.items)
        elif path == '/api/skills/import/prepare':
            self.upload = {'body': route.request.post_data_buffer, 'content_type': route.request.headers.get('content-type')}
            await route.fulfill(status=201, json={**self.skill, 'draft_id': 'draft-1', 'expires_at': '2026-10-23T01:00:00Z'})
        elif path == '/api/skills/import/draft-1/confirm':
            self.confirmations += 1
            self.items = [self.skill]
            await route.fulfill(status=201, json=self.skill)
        elif path == '/api/skills/skill-1/content':
            await route.fulfill(json={'content': self.content})
        elif path == '/api/skills/skill-1/status':
            body = route.request.post_data_json
            self.updates.append(body)
            self.skill.update(status=body['status'], version=self.skill['version'] + 1)
            await route.fulfill(json=self.skill)
        elif path == '/api/skills/skill-1':
            if method == 'DELETE': self.items = []
            await route.fulfill(json=self.skill)
        elif path == '/api/chat/conversations' and method == 'POST':
            await route.fulfill(status=201, json={'id': 'c2', 'title': '新对话', 'created_at': '2026-10-23T00:00:00Z'})
        elif path == '/api/chat/conversations/c2/messages':
            await route.fulfill(json=self.chat_history)
        elif path == '/api/chat/conversations/c2/runs':
            self.sent.append(route.request.post_data_json)
            self.chat_history = [{'id': 'msg-1', 'role': 'user', 'content': self.sent[-1]['message'], 'skill_snapshots': [{'slug': 'example', 'name': '示例技能', 'revision_id': 'revision-1', 'content_hash': 'hash-1'}], 'run_id': 'r2', 'created_at': '2026-10-23T00:00:00Z'}]
            await route.fulfill(status=202, json={'id': 'r2', 'resource_type': 'chat', 'resource_id': 'c2', 'status': 'completed', 'attempt_id': 'a2', 'attempt_no': 1, 'revision': 3, 'projection_seq': 1, 'text': '已按技能回复', 'error': None, 'created_at': '2026-10-23T00:00:00Z', 'usage': {'input_tokens': 10, 'output_tokens': 2, 'status': 'known'}})
        else:
            await super().respond(route)

    async def open_skills(self):
        await self.page.goto(self.origin + '/skills')
        await expect(self.page.get_by_role('heading', name='我的技能', exact=True)).to_be_visible()

    async def open_skill_detail(self, name='示例技能'):
        item = self.page.locator('.skill-item').filter(has_text=name)
        await item.get_by_role('button', name=name, exact=True).click()
        detail = self.page.locator('.skill-detail-modal[role="dialog"]')
        await expect(detail).to_be_visible()
        return detail

    async def open_skill_delete(self, detail):
        await detail.get_by_role('button', name='更多技能操作', exact=True).click()
        delete = self.page.get_by_role('menuitem', name='删除技能', exact=True)
        await expect(delete).to_be_enabled()
        await delete.click()
        confirmation = self.page.get_by_role('dialog').filter(
            has=self.page.get_by_role('button', name=re.compile(r'^确\s*定$'))
        )
        await expect(confirmation).to_be_visible()
        return confirmation

    async def prepare_upload(self, raw=b'fixture skill'):
        await self.page.get_by_role('button', name='上传技能', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        await dialog.locator('input[type=file]').set_input_files({'name': 'SKILL.md', 'mimeType': 'text/markdown', 'buffer': raw})
        await dialog.get_by_role('button', name='解析技能', exact=True).click()
        await expect(dialog.get_by_text('示例技能', exact=True)).to_be_visible()
        return dialog

    async def test_upload_previews_then_confirms_original_bytes(self):
        self.items = []
        await self.open_skills()
        raw = b'---\nname: example\ndisplay_name: Example\ndescription: Test\n---\nContent'
        dialog = await self.prepare_upload(raw)
        self.assertEqual(self.upload, {'body': raw, 'content_type': 'application/octet-stream'})
        self.assertEqual(self.confirmations, 0)
        await expect(self.page.locator('.skill-item')).to_have_count(0)
        await dialog.get_by_role('button', name='确认安装', exact=True).click()
        await expect(dialog).not_to_be_visible()
        await expect(self.page.locator('.skill-item')).to_have_count(1)
        self.assertEqual(self.confirmations, 1)

    async def test_upload_preview_hides_package_and_current_versions(self):
        await self.page.route(
            '**/api/skills/import/prepare?*',
            lambda route: route.fulfill(status=201, json={
                **self.skill, 'draft_id': 'draft-1',
                'expires_at': '2026-10-23T01:00:00Z', 'action': 'update',
                'target_skill_id': 'skill-1', 'expected_version': 1,
                'current_package_version': '0.9.0',
            }),
        )
        await self.open_skills()
        dialog = await self.prepare_upload()
        await expect(dialog.get_by_text('版本', exact=True)).to_have_count(0)
        await expect(dialog.get_by_text('当前版本', exact=True)).to_have_count(0)
        await expect(dialog.get_by_text('1.0.0', exact=True)).to_have_count(0)
        await expect(dialog.get_by_text('0.9.0', exact=True)).to_have_count(0)
        await expect(dialog.get_by_role('button', name='确认替换', exact=True)).to_be_enabled()
        await expect(dialog.get_by_text('替换现有内容', exact=True)).to_be_visible()
        await expect(dialog).not_to_contain_text('历史')

    async def test_detail_renders_safe_markdown_and_status_uses_version_before_delete(self):
        await self.open_skills()
        detail = await self.open_skill_detail()
        markdown = detail.locator('.skill-markdown')
        await expect(markdown.get_by_role('heading', name='技能正文', exact=True)).to_be_visible()
        await expect(markdown.locator('strong')).to_have_text('安全说明')
        await expect(markdown.locator('script')).to_have_count(0)
        await expect(markdown.locator('a[href^="javascript:"]')).to_have_count(0)
        await expect(detail.get_by_text('1.0.0', exact=True)).to_have_count(0)
        await expect(detail.get_by_text('版本', exact=True)).to_have_count(0)
        self.assertIsNone(await self.page.evaluate('window.skillExecuted'))
        toggle = detail.get_by_role('switch', name='启用技能“示例技能”', exact=True)
        await expect(toggle).to_be_checked()
        await toggle.click()
        await expect(toggle).not_to_be_checked()
        self.assertEqual(self.updates, [{'status': 'disabled', 'expected_version': 1}])
        confirmation = await self.open_skill_delete(detail)
        await confirmation.get_by_role('button', name=re.compile(r'^确\s*定$')).click()
        await expect(detail).not_to_be_visible()
        await expect(self.page.locator('.skill-item')).to_have_count(0)

    async def test_member_manages_own_skills_without_shared_write_permission(self):
        self.member = True
        await self.open_skills()
        await expect(self.page.get_by_role('button', name='上传技能', exact=True)).to_be_enabled()
        detail = await self.open_skill_detail()
        await expect(detail.locator('.skill-markdown')).to_contain_text('技能正文')
        await expect(detail.get_by_role('switch', name='启用技能“示例技能”', exact=True)).to_be_enabled()
        await self.open_skill_delete(detail)

    async def test_desktop_skill_items_fit_three_and_four_columns(self):
        self.items = [{**self.skill, 'id': f'skill-{index}', 'name': f'技能{index}'} for index in range(8)]
        for width, columns in [(1440, 3), (1920, 4)]:
            await self.page.set_viewport_size({'width': width, 'height': 900})
            await self.open_skills()
            items = self.page.locator('.skill-item')
            await expect(items).to_have_count(8)
            bounds = [await item.bounding_box() for item in await items.all()]
            self.assertEqual(len([box for box in bounds if abs(box['y'] - bounds[0]['y']) < 1]), columns)
            for box in bounds:
                self.assertGreaterEqual(box['x'], 0)
                self.assertLessEqual(box['x'] + box['width'], width)

    async def test_failed_confirmation_keeps_preview_and_visible_error(self):
        attempts = 0
        async def reject(route):
            nonlocal attempts
            attempts += 1
            await route.fulfill(status=409, json={'error': {'code': 'skill_exists', 'message': '同名技能已存在'}})
        await self.page.route('**/api/skills/import/draft-1/confirm', reject)
        await self.open_skills()
        dialog = await self.prepare_upload()
        await dialog.get_by_role('button', name='确认安装', exact=True).click()
        await expect(dialog.get_by_text('同名技能已存在', exact=True)).to_be_visible()
        await expect(dialog.get_by_text('示例技能', exact=True)).to_be_visible()
        await self.page.wait_for_timeout(150)
        self.assertEqual(attempts, 1)

    async def test_late_prepare_cannot_reopen_upload_after_navigation(self):
        requested, release = asyncio.Event(), asyncio.Event()
        async def prepare(route):
            requested.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await route.fulfill(status=201, json={**self.skill, 'draft_id': 'draft-1', 'expires_at': '2026-10-23T01:00:00Z'})
        await self.page.route('**/api/skills/import/prepare?*', prepare)
        try:
            await self.page.goto(self.origin + '/models')
            await expect(self.page.get_by_role('heading', name='模型目录', exact=True)).to_be_visible()
            await self.page.get_by_role('link', name='技能管理', exact=True).click()
            await self.page.get_by_role('button', name='上传技能', exact=True).click()
            dialog = self.page.get_by_role('dialog')
            await dialog.locator('input[type=file]').set_input_files({'name': 'SKILL.md', 'mimeType': 'text/markdown', 'buffer': b'fixture skill'})
            await dialog.get_by_role('button', name='解析技能', exact=True).click()
            await asyncio.wait_for(requested.wait(), timeout=5)
            await self.page.go_back()
            await expect(self.page.get_by_role('heading', name='模型目录', exact=True)).to_be_visible()
            release.set()
            await self.page.wait_for_timeout(200)
            await expect(self.page.get_by_role('dialog')).to_have_count(0)
        finally:
            release.set()

    async def test_removing_file_during_prepare_restores_cancel_and_ignores_response(self):
        requested, release = asyncio.Event(), asyncio.Event()
        async def prepare(route):
            requested.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await route.fulfill(status=201, json={**self.skill, 'draft_id': 'draft-1', 'expires_at': '2026-10-23T01:00:00Z'})
        await self.page.route('**/api/skills/import/prepare?*', prepare)
        try:
            await self.open_skills()
            await self.page.get_by_role('button', name='上传技能', exact=True).click()
            dialog = self.page.get_by_role('dialog')
            await dialog.locator('input[type=file]').set_input_files({'name': 'SKILL.md', 'mimeType': 'text/markdown', 'buffer': b'fixture skill'})
            await dialog.get_by_role('button', name='解析技能', exact=True).click()
            await asyncio.wait_for(requested.wait(), timeout=5)
            await dialog.get_by_title('Remove file', exact=True).click()
            release.set()
            await expect(dialog.get_by_role('button', name=re.compile(r'^取\s*消$'))).to_be_enabled()
            await expect(dialog.get_by_role('button', name='确认安装', exact=True)).to_have_count(0)
            await dialog.get_by_role('button', name=re.compile(r'^取\s*消$')).click()
            await expect(dialog).not_to_be_visible()
        finally:
            release.set()

    async def test_chat_at_picker_sends_selection_and_keeps_history_label(self):
        await self.page.goto(self.origin + '/chat')
        composer = self.page.get_by_role('textbox', name='消息内容')
        await composer.fill('按技能回答 @')
        picker = self.page.locator('.skill-picker-panel')
        await expect(picker).to_be_visible()
        await picker.get_by_role('option', name='示例技能', exact=True).click()
        await picker.get_by_role('button', name='收起技能选择', exact=True).click()
        await expect(composer).to_have_value('按技能回答')
        await expect(self.page.locator('.selected-skills')).to_contain_text('示例技能')
        await self.page.get_by_role('button', name='发送消息', exact=True).click()
        await expect(self.page.get_by_text('已按技能回复', exact=True)).to_be_visible()
        self.assertEqual(self.sent[0]['skill_slugs'], ['example'])
        self.assertEqual(self.sent[0]['message'], '按技能回答')
        await expect(self.page.locator('.message-skills')).to_contain_text('示例技能')
        await self.page.get_by_role('button', name='新建对话', exact=True).click()
        await expect(self.page.locator('.selected-skills')).to_have_count(0)

    async def test_chat_picker_keyboard_selects_without_sending_and_escape_preserves_draft(self):
        self.items.append({**self.skill, 'id': 'skill-2', 'slug': 'document-summary', 'name': '文档总结'})
        await self.page.goto(self.origin + '/chat')
        composer = self.page.get_by_role('textbox', name='消息内容')
        await composer.fill('保留这段草稿 @')
        search = self.page.get_by_role('combobox', name='搜索技能', exact=True)
        await expect(search).to_be_focused()
        await expect(self.page.get_by_role('option', name='文档总结', exact=True)).to_be_visible()
        await search.press('ArrowDown')
        await search.press('Enter')
        await expect(self.page.get_by_role('option', name='文档总结', exact=True)).to_have_attribute('aria-selected', 'true')
        await expect(composer).to_have_value('保留这段草稿')
        self.assertEqual(self.sent, [])
        await search.press('Escape')
        await expect(self.page.locator('.skill-picker-panel')).to_have_count(0)
        await expect(composer).to_be_focused()
        await composer.press('Enter')
        await expect(self.page.get_by_text('已按技能回复', exact=True)).to_be_visible()
        self.assertEqual(self.sent[0]['skill_slugs'], ['document-summary'])
        self.assertEqual(self.sent[0]['message'], '保留这段草稿')

    async def test_chat_picker_empty_search_and_outside_click_keep_draft(self):
        await self.page.goto(self.origin + '/chat')
        composer = self.page.get_by_role('textbox', name='消息内容')
        await composer.fill('不要提前发送')
        await self.page.get_by_role('button', name='选择技能', exact=True).click()
        search = self.page.get_by_role('combobox', name='搜索技能', exact=True)
        await search.fill('找不到这个技能')
        await search.press('Enter')
        await expect(self.page.get_by_text('没有匹配的技能', exact=True)).to_be_visible()
        self.assertEqual(self.sent, [])
        await self.page.locator('.chat-toolbar').click(position={'x': 300, 'y': 30})
        await expect(self.page.locator('.skill-picker-panel')).to_have_count(0)
        await expect(composer).to_have_value('不要提前发送')

    async def test_chat_picker_selection_limit_and_ime_enter(self):
        self.items = [{**self.skill, 'id': f'skill-{index}', 'slug': f'skill-{index}', 'name': f'技能 {index}'} for index in range(6)]
        await self.page.goto(self.origin + '/chat')
        await self.page.get_by_role('button', name='选择技能', exact=True).click()
        search = self.page.get_by_role('combobox', name='搜索技能', exact=True)
        await expect(self.page.get_by_role('option', name='技能 0', exact=True)).to_be_visible()
        await search.dispatch_event('keydown', {'key': 'Enter', 'isComposing': True})
        await expect(self.page.locator('.selected-skills')).to_have_count(0)
        for index in range(6):
            await self.page.get_by_role('option', name=f'技能 {index}', exact=True).click()
        await expect(self.page.get_by_text('每条消息最多选择 5 个技能', exact=True)).to_be_visible()
        await expect(self.page.locator('.selected-skill')).to_have_count(5)
        await expect(self.page.get_by_role('option', name='技能 5', exact=True)).to_have_attribute('aria-selected', 'false')
        await self.page.get_by_role('option', name='技能 0', exact=True).click()
        await self.page.get_by_role('option', name='技能 5', exact=True).click()
        await expect(self.page.locator('.selected-skill')).to_have_count(5)
        await expect(self.page.get_by_role('option', name='技能 5', exact=True)).to_have_attribute('aria-selected', 'true')
        self.assertEqual(self.sent, [])

    async def test_chat_picker_textarea_ime_confirmation_does_not_submit(self):
        await self.page.goto(self.origin + '/chat')
        composer = self.page.get_by_role('textbox', name='消息内容')
        await composer.fill('输入法确认时保留草稿')
        await self.page.get_by_role('button', name='选择技能', exact=True).click()
        await expect(self.page.get_by_role('option', name='示例技能', exact=True)).to_be_visible()
        await composer.focus()
        await composer.dispatch_event('keydown', {'key': 'Enter', 'keyCode': 229, 'isComposing': False})
        await self.page.wait_for_timeout(150)
        self.assertEqual(self.sent, [])
        await expect(composer).to_have_value('输入法确认时保留草稿')
        await expect(self.page.locator('.skill-picker-panel')).to_be_visible()

    async def test_late_detail_cannot_replace_another_skill(self):
        second = {**self.skill, 'id': 'skill-2', 'slug': 'second', 'name': '第二技能'}
        self.items.append(second)
        requested, release = asyncio.Event(), asyncio.Event()
        async def first(route):
            requested.set()
            await release.wait()
            with contextlib.suppress(Exception): await route.fulfill(json=self.skill)
        await self.page.route('**/api/skills/skill-1', first)
        await self.page.route('**/api/skills/skill-2', lambda route: route.fulfill(json=second))
        await self.page.route('**/api/skills/skill-2/content', lambda route: route.fulfill(json={'content': '第二技能正文'}))
        try:
            await self.open_skills()
            await self.open_skill_detail()
            await asyncio.wait_for(requested.wait(), timeout=5)
            await self.page.locator('.skill-detail-modal').get_by_role('button', name='Close', exact=True).click()
            detail = await self.open_skill_detail('第二技能')
            await expect(detail.locator('.skill-markdown')).to_have_text('第二技能正文')
            release.set()
            await self.page.wait_for_timeout(200)
            await expect(detail.get_by_role('heading', name='第二技能', exact=True)).to_be_visible()
        finally:
            release.set()

    async def test_delete_confirmation_closes_when_leaving_skill_page(self):
        self.skill['status'] = 'disabled'
        await self.page.goto(self.origin + '/models')
        await expect(self.page.get_by_role('heading', name='模型目录', exact=True)).to_be_visible()
        await self.page.get_by_role('link', name='技能管理', exact=True).click()
        detail = await self.open_skill_detail()
        await self.open_skill_delete(detail)
        await self.page.go_back()
        await expect(self.page.get_by_role('heading', name='模型目录', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('dialog')).to_have_count(0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
