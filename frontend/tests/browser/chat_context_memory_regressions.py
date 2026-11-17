"""Token memory state through the real Vue UI and isolated read-only HTTP fixtures."""
import asyncio
import contextlib
import json
import unittest
from urllib.parse import urlparse
from playwright.async_api import expect
from developer_regressions import DeveloperBoundary

STAMP = '2026-11-03T00:00:00Z'

def state(status='degraded'):
    return dict(status=status, strategy='token',
                effective_memory=dict(summary_used=status == 'summarized', covered_rounds=1 if status == 'summarized' else 0,
                                      retained_rounds=2, estimated_tokens=500),
                last_run_context=dict(run_id='run-1', summary_prepared=status == 'summarized',
                                      estimated_input_tokens=500, input_budget=8192,
                                      summary_trigger_tokens=6000, retained_token_budget=2048),
                reason='provider_unavailable' if status == 'degraded' else None)

class ChatContextMemoryRegressions(DeveloperBoundary):
    async def asyncSetUp(self):
        self.states = {'c1': state(), 'c2': state('full')}
        self.reads, self.context_writes = [], []
        self.owner = 'user-1'
        self.model_updates = []
        self.model = {'id': 'model-1', 'name': '日常助手', 'connection_id': 'connection-1', 'upstream_model': 'local-text', 'max_output_tokens': 512, 'context_window_tokens': 32768, 'model_type': 'chat', 'published': True, 'enabled': True, 'capabilities': ['chat', 'stream']}
        await super().asyncSetUp()

    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path == '/api/auth/me':
            await route.fulfill(json={'id': self.owner, 'username': 'admin', 'is_admin': True})
        elif path == '/api/auth/login':
            await route.fulfill(json={'user': {'id': self.owner, 'username': 'admin', 'is_admin': True}})
        elif path == '/api/models':
            await route.fulfill(json=[self.model])
        elif path == '/api/models/connections':
            await route.fulfill(json=[{'id': 'connection-1', 'name': 'Local', 'provider': 'openai_compatible', 'base_url': 'http://fixture.invalid/v1', 'has_api_key': True, 'enabled': True}])
        elif path == '/api/models/model-1' and route.request.method == 'PATCH':
            self.model_updates.append(route.request.post_data_json)
            self.model.update(route.request.post_data_json)
            await route.fulfill(json=self.model)
        elif path == '/api/auth/logout':
            await route.fulfill(status=204)
        elif path == '/api/chat/conversations':
            await route.fulfill(json=[{'id': 'c1', 'title': '较长会话', 'created_at': STAMP}, {'id': 'c2', 'title': '另一会话', 'created_at': STAMP}])
        elif path.startswith('/api/chat/conversations/') and path.endswith('/messages'):
            await route.fulfill(json=[{'id': 'q1', 'role': 'user', 'content': '保留的提问', 'run_id': None, 'created_at': STAMP}, {'id': 'a1', 'role': 'assistant', 'content': '保留的回答', 'run_id': None, 'created_at': STAMP}])
        elif '/context' in path and route.request.method != 'GET':
            self.context_writes.append(route.request.method)
            await route.fulfill(status=405)
        elif path.endswith('/context'):
            conversation = path.split('/')[4]
            self.reads.append(conversation)
            await route.fulfill(json=self.states[conversation])
        else:
            await super().respond(route)

    async def open_chat(self):
        await self.page.goto(self.origin + '/chat')
        await self.select_conversation('较长会话')
        await expect(self.page.get_by_text('保留的回答', exact=True)).to_be_visible()

    async def test_token_budget_summary_preserves_draft_and_history(self):
        self.states['c1'] = state('summarizing')
        await self.open_chat()
        await expect(self.page.get_by_text('正在按 Token 预算整理会话摘要。', exact=True)).to_be_visible()
        await self.page.get_by_role('textbox', name='消息内容', exact=True).fill('等待摘要时的草稿')
        self.states['c1'] = state('summarized')
        await expect(self.page.get_by_text('已按 Token 预算整理较早对话', exact=True)).to_be_visible(timeout=5000)
        await expect(self.page.get_by_role('textbox', name='消息内容', exact=True)).to_have_value('等待摘要时的草稿')
        await expect(self.page.get_by_text('保留的回答', exact=True)).to_be_visible()

    async def test_active_chat_refreshes_full_context_when_token_summarization_starts(self):
        self.states['c1'] = state('full')
        release, running_sent = asyncio.Event(), asyncio.Event()
        posts, streams = [], []
        snapshot = dict(id='active-summary-run', resource_type='chat', resource_id='c1', status='pending',
                        attempt_id='attempt-1', attempt_no=1, revision=1, projection_seq=1,
                        text='', error=None, created_at=STAMP,
                        usage=dict(input_tokens=None, output_tokens=None, status='pending'))

        async def running(route):
            path = urlparse(route.request.url).path
            if path == '/api/chat/conversations/c1/runs':
                posts.append(route.request.post_data_json)
                await route.fulfill(status=202, json=snapshot)
            elif path == '/api/runs/active-summary-run':
                await route.fulfill(json=snapshot)
            elif path == '/api/runs/active-summary-run/events':
                streams.append(path)
                if len(streams) == 1:
                    snapshot.update(status='running', revision=2, projection_seq=2)
                    await route.fulfill(content_type='text/event-stream', body='event: snapshot\ndata: ' + json.dumps(snapshot) + '\n\n')
                    running_sent.set()
                else:
                    await release.wait()
                    snapshot.update(status='completed', revision=3, projection_seq=3, text='已完成',
                                    usage=dict(input_tokens=8, output_tokens=2, status='known'))
                    with contextlib.suppress(Exception):
                        await route.fulfill(content_type='text/event-stream', body='event: snapshot\ndata: ' + json.dumps(snapshot) + '\n\n')
            else:
                await route.fallback()

        await self.page.route(self.origin + '/api/**', running)
        try:
            await self.open_chat()
            await expect(self.page.locator('.chat-context-notice')).to_have_count(0)
            await self.page.get_by_role('textbox', name='消息内容', exact=True).fill('触发 Token 整理')
            await self.page.get_by_role('button', name='发送消息', exact=True).click()
            await asyncio.wait_for(running_sent.wait(), timeout=5)
            self.states['c1'] = state('summarizing')
            await expect(self.page.get_by_text('正在按 Token 预算整理会话摘要。', exact=True)).to_be_visible(timeout=5000)
            self.states['c1'] = state('summarized')
            release.set()
            await expect(self.page.get_by_text('已按 Token 预算整理较早对话', exact=True)).to_be_visible(timeout=5000)
            self.assertEqual(len(posts), 1)
            self.assertEqual(self.context_writes, [])
        finally:
            release.set()

    async def test_token_degraded_guides_resubmission_without_legacy_summary_retry(self):
        await self.open_chat()
        await expect(self.page.get_by_text('会话摘要暂未按 Token 预算整理完成，已保留原有效记忆；请重新发送问题或重新生成回复。', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='重试整理', exact=True)).to_have_count(0)
        await self.page.get_by_role('textbox', name='消息内容', exact=True).fill('明确重新发送的问题')
        await expect(self.page.get_by_role('textbox', name='消息内容', exact=True)).to_have_value('明确重新发送的问题')
        self.assertEqual(self.context_writes, [])

    async def test_polling_pauses_while_hidden_and_stops_after_publication(self):
        self.states['c1'] = state('summarizing')
        await self.open_chat()
        await expect(self.page.get_by_text('正在按 Token 预算整理会话摘要。', exact=True)).to_be_visible()
        await self.page.evaluate("Object.defineProperty(document,'visibilityState',{configurable:true,get:()=> 'hidden'});document.dispatchEvent(new Event('visibilitychange'))")
        count = len(self.reads)
        await self.page.wait_for_timeout(1900)
        self.assertEqual(len(self.reads), count)
        self.states['c1'] = state('summarized')
        await self.page.evaluate("Object.defineProperty(document,'visibilityState',{configurable:true,get:()=> 'visible'});document.dispatchEvent(new Event('visibilitychange'))")
        await expect(self.page.get_by_text('已按 Token 预算整理较早对话', exact=True)).to_be_visible()
        await self.page.wait_for_timeout(1700)
        self.assertEqual(len(self.reads), count + 1)

    async def held_context(self):
        requested, release = asyncio.Event(), asyncio.Event()
        async def delayed(route):
            if requested.is_set():
                await route.fulfill(json=state('full'))
                return
            requested.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await route.fulfill(json=state())
        await self.page.route('**/api/chat/conversations/c1/context', delayed)
        return requested, release

    async def test_late_context_cannot_replace_a_new_conversation(self):
        requested, release = await self.held_context()
        try:
            await self.open_chat()
            await asyncio.wait_for(requested.wait(), timeout=5)
            await self.select_conversation('另一会话')
            release.set()
            await self.page.wait_for_timeout(250)
            await expect(self.page.locator('.chat-context-notice')).to_have_count(0)
        finally:
            release.set()

    async def test_logout_discards_late_owner_context(self):
        requested, release = await self.held_context()
        try:
            await self.open_chat()
            await asyncio.wait_for(requested.wait(), timeout=5)
            await self.page.get_by_role('button', name='用户菜单', exact=True).click()
            await self.page.get_by_role('menuitem', name='退出登录', exact=True).click()
            await expect(self.page).to_have_url(self.origin + '/login')
            self.owner = 'user-2'
            await self.page.get_by_label('账号', exact=True).fill('other-owner')
            await self.page.get_by_label('密码', exact=True).fill('platform-test-password')
            await self.page.get_by_role('button', name='登录', exact=True).click()
            chat_link = self.page.get_by_role('link', name='对话体验', exact=True)
            await expect(chat_link).to_be_visible()
            await chat_link.click()
            await expect(self.page).to_have_url(self.origin + '/chat')
            await self.select_conversation('较长会话')
            release.set()
            await self.page.wait_for_timeout(250)
            await expect(self.page.locator('.chat-context-notice')).to_have_count(0)
        finally:
            release.set()

    async def test_model_window_field_roundtrips_and_can_restore_platform_budget(self):
        await self.page.goto(self.origin + '/models')
        item = self.page.locator('.model-item').first
        await expect(item).to_be_visible()
        async def edit():
            detail = self.page.locator('.model-detail-modal[role="dialog"], .model-detail-modal [role="dialog"]')
            if not await detail.is_visible():
                await item.get_by_role('button', name='查看模型详情：日常助手', exact=True).click()
            await detail.get_by_role('button', name='更多模型操作', exact=True).click()
            await self.page.get_by_role('menuitem', name='编辑模型', exact=True).click()
            drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
            await expect(drawer).to_contain_text('上下文窗口 tokens（可选）')
            return drawer, drawer.locator('.ant-form-item').filter(has_text='上下文窗口 tokens（可选）').get_by_role('spinbutton')
        drawer, field = await edit()
        await expect(field).to_have_value('32768')
        await field.fill('65536')
        await drawer.get_by_role('button', name='保存模型', exact=True).click()
        await expect(drawer).to_be_hidden()
        self.assertEqual(self.model_updates[-1]['context_window_tokens'], 65536)
        drawer, field = await edit()
        await field.fill('')
        await drawer.get_by_role('button', name='保存模型', exact=True).click()
        await expect(drawer).to_be_hidden()
        self.assertIsNone(self.model_updates[-1]['context_window_tokens'])

if __name__ == '__main__':
    unittest.main(verbosity=2)
