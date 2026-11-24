"""A foreground chat must discard the previous cookie identity's private workspace."""
import os
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import expect

from developer_regressions import DeveloperBoundary
from files_regressions import FILE
from permission_fixture import granted_permissions


class ChatIdentityRegressions(DeveloperBoundary):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.owner = 'owner-A'
        self.permissions = granted_permissions('platform:chat:use')
        self.model_reads = 0
        self.admissions = []
        self.auth_reads = []
        self.conversation = {'id': 'private-A', 'title': 'A的私有会话',
                             'created_at': '2026-11-16T00:00:00Z', 'version': 1, 'archived': False}

    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path == '/api/auth/me':
            self.auth_reads.append(self.owner)
            return await route.fulfill(json={'id': self.owner, 'username': self.owner, 'is_admin': False,
                'permissions': self.permissions})
        if path == '/api/models':
            self.model_reads += 1
            if 'platform:chat:use' not in self.permissions and 'platform:models:read' not in self.permissions:
                return await route.fulfill(status=403, json={'error': {'code': 'forbidden', 'message': '无模型依赖读取权限'}})
        if path == '/api/chat/conversations':
            return await route.fulfill(json=[self.conversation] if self.owner == 'owner-A' else [])
        if path == '/api/chat/conversations/private-A/messages':
            return await route.fulfill(json=[{'id': 'private-message-A', 'role': 'assistant',
                'content': 'A账号的私有正文', 'run_id': 'run-A', 'created_at': '2026-11-16T00:00:00Z',
                'is_complete': True}])
        if path == '/api/chat/conversations/private-A/context':
            return await route.fulfill(json={'status': 'full', 'strategy': 'token',
                'effective_memory': {'summary_used': False, 'covered_rounds': 0,
                                     'retained_rounds': 1, 'estimated_tokens': None},
                'last_run_context': None, 'reason': None})
        if path == '/api/chat/conversations/private-A/runs':
            self.admissions.append({'owner': self.owner, 'body': route.request.post_data_json})
            return await route.fulfill(status=503, json={'error': {
                'code': 'unavailable', 'message': '提交响应丢失，请使用原请求核对'}})
        if path == '/api/chat/attachments':
            return await route.fulfill(status=201, json={**FILE, 'id': 'private-file-A',
                'original_filename': 'A-private.txt', 'display_name': 'A-private.txt',
                'owner_id': 'owner-A', 'uploader_id': 'owner-A'})
        if path == '/api/admin/notices/published':
            return await route.fulfill(json={'items': [], 'total': 0, 'page': 1, 'page_size': 20})
        return await super().respond(route)

    async def test_focus_identity_replacement_clears_private_chat_and_unknown_request(self):
        await self.page.goto(self.origin + '/chat')
        await self.select_conversation('A的私有会话')
        await expect(self.page.get_by_text('A账号的私有正文', exact=True)).to_be_visible()
        draft = self.page.get_by_role('textbox', name='消息内容', exact=True)
        await draft.fill('A账号的私有待确认问题')
        await self.page.get_by_role('button', name='发送消息', exact=True).click()
        retry = self.page.get_by_role('button', name='使用原请求重试', exact=True)
        await expect(retry).to_be_visible()
        await expect(draft).to_have_value('')
        await expect(self.page.locator('.message-row.user').get_by_text('A账号的私有待确认问题', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='添加图片或附件', exact=True).click()
        await self.page.locator('input[type=file]').set_input_files({
            'name': 'A-private.txt', 'mimeType': 'text/plain', 'buffer': b'private bytes'})
        await expect(self.page.get_by_role('button', name='移除附件：A-private.txt', exact=True)).to_be_visible()
        self.assertEqual(len(self.admissions), 1)
        evidence = Path(os.environ.get('CHAT_IDENTITY_EVIDENCE', '/tmp/chat-identity-regressions'))
        evidence.mkdir(parents=True, exist_ok=True)
        await self.page.screenshot(path=str(evidence / 'owner-A-private-state.png'), full_page=True)
        # Another tab changed the shared login cookie. This tab learns the new actor on focus.
        self.owner = 'owner-B'
        await self.page.evaluate("window.dispatchEvent(new Event('focus'))")
        await expect(self.page.get_by_role('button', name='用户菜单', exact=True)).to_contain_text('owner-B')
        self.assertIn('owner-B', self.auth_reads)
        await expect(self.page.get_by_text('A账号的私有正文', exact=True)).to_have_count(0)
        await expect(self.page.get_by_text('A账号的私有待确认问题', exact=True)).to_have_count(0)
        await expect(self.page.get_by_text('A的私有会话', exact=True)).to_have_count(0)
        await expect(draft).to_have_value('')
        await expect(self.page.get_by_role('button', name='移除附件：A-private.txt', exact=True)).to_have_count(0)
        await expect(retry).to_have_count(0)
        await draft.press('Enter')
        self.assertEqual([item['owner'] for item in self.admissions], ['owner-A'])
        await self.page.screenshot(path=str(evidence / 'owner-B-clean-state.png'), full_page=True)

    async def test_read_only_history_has_no_chat_write_controls_even_with_model_read(self):
        for extra_models in [False, True]:
            with self.subTest(model_read=extra_models):
                self.permissions = granted_permissions('platform:chat:read', *(['platform:models:read'] if extra_models else []))
                self.model_reads = 0
                await self.page.goto(self.origin + '/chat')
                await self.select_conversation('A的私有会话')
                await expect(self.page.get_by_text('A账号的私有正文', exact=True)).to_be_visible()
                await expect(self.page.get_by_role('button', name='新建对话', exact=True)).to_have_count(0)
                await expect(self.page.get_by_role('button', name='发送消息', exact=True)).to_have_count(0)
                await expect(self.page.get_by_role('button', name='添加图片或附件', exact=True)).to_have_count(0)
                await expect(self.page.get_by_role('button', name='重新生成', exact=True)).to_have_count(0)
                await expect(self.page.get_by_role('button', name='复制回复', exact=True)).to_be_visible()
                await expect(self.page.get_by_role('button', name='赞同', exact=True)).to_be_visible()
                await expect(self.page.get_by_role('button', name='分享', exact=True)).to_have_count(0)
                dialog = await self.open_history()
                await expect(dialog.locator('.recent-session').get_by_role('button', name='更多操作', exact=True)).to_have_count(0)
                await self.close_history()
                self.assertEqual(self.model_reads, 0)
                self.assertEqual(self.admissions, [])
