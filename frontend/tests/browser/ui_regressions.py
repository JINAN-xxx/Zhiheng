"""Run against the existing platform preview: python tests/browser/ui_regressions.py.

Uses real Chromium UI interactions with controlled HTTP/SSE boundary responses.
Set PLATFORM_BROWSER_URL to target another already running instance.
"""

import asyncio
import json
import re
import os
import unittest
from collections import Counter
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect


class PlatformUIRegressions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=True, args=['--no-sandbox']
        )
        self.context = await self.browser.new_context(viewport={'width': 1440, 'height': 1000})
        self.page = await self.context.new_page()
        self.counts = Counter()
        self.posts = []
        self.completed = False
        self.model = {
            'id': 'm1', 'name': '测试模型', 'connection_id': 'connection-1',
            'upstream_model': 'test-model', 'max_output_tokens': 512,
            'enabled': True, 'capabilities': ['chat', 'stream']
        }
        await self.page.route('**/api/**', self.respond)
        self.origin = os.environ.get('PLATFORM_BROWSER_URL', 'http://127.0.0.1:15191')

    async def asyncTearDown(self):
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()

    async def respond(self, route):
        path = urlparse(route.request.url).path
        method = route.request.method
        self.counts[(method, path)] += 1
        if path == '/api/auth/me':
            body = {'id': 'user-1', 'username': 'admin', 'is_admin': True}
        elif path == '/api/models/connections':
            body = [{'id': 'connection-1', 'name': '测试服务', 'provider': 'openai_compatible', 'base_url': 'http://provider.example/v1', 'has_api_key': True}]
        elif path == '/api/models':
            if method == 'POST':
                submitted = route.request.post_data_json
                self.posts.append(submitted)
                body = {**self.model, **submitted, 'id': 'new-model'}
            else:
                body = [self.model]
        elif path == '/api/chat/conversations':
            body = [{'id': 'c1', 'title': '恢复中会话', 'created_at': '2026-10-23T00:00:00Z'}]
        elif path == '/api/chat/conversations/c1/messages':
            body = [{'id': 'message-1', 'role': 'user', 'content': '你好', 'run_id': 'r1', 'created_at': '2026-10-23T00:00:00Z'}]
            if self.completed:
                body.append({'id': 'message-2', 'role': 'assistant', 'content': '恢复后的完整回复', 'run_id': 'r1', 'created_at': '2026-10-23T00:00:01Z'})
        elif path == '/api/runs/r1/events':
            await route.fulfill(status=200, content_type='text/plain', body='broken stream')
            return
        elif path == '/api/runs/r1':
            body = {
                'id': 'r1', 'resource_type': 'chat', 'resource_id': 'c1',
                'status': 'completed' if self.completed else 'running',
                'attempt_id': 'a1', 'attempt_no': 1,
                'revision': 3 if self.completed else 2, 'projection_seq': 1,
                'text': '恢复后的完整回复' if self.completed else '正在生成',
                'error': None, 'created_at': '2026-10-23T00:00:00Z',
                'usage': {'input_tokens': None, 'output_tokens': None, 'status': 'pending'}
            }
        else:
            await route.fulfill(status=404, content_type='application/json', body=json.dumps({'error': {'code': 'fixture_missing', 'message': path}}))
            return
        await route.fulfill(status=200, content_type='application/json', body=json.dumps(body))

    async def test_chat_recovery_survives_conversation_refresh(self):
        await self.page.goto(self.origin + '/chat')
        await self.page.get_by_role('button', name='恢复中会话').click()
        recovery = self.page.get_by_role('button', name='恢复查看', exact=True)
        await expect(recovery).to_be_visible(timeout=10000)
        self.assertEqual(self.counts[('GET', '/api/runs/r1/events')], 4)
        async with self.page.expect_response(lambda response: urlparse(response.url).path == '/api/chat/conversations'):
            await self.page.get_by_role('button', name='刷新会话').click()
        await expect(self.page.locator('.page-spinner')).to_have_count(0)
        await expect(recovery).to_be_visible(timeout=1200)
        gets_before_recovery = self.counts[('GET', '/api/runs/r1')]
        self.completed = True
        await recovery.click()
        await expect(self.page.get_by_text('恢复后的完整回复', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='停止生成')).to_have_count(0)
        self.assertGreater(self.counts[('GET', '/api/runs/r1')], gets_before_recovery)
        await self.page.get_by_role('textbox', name='消息内容').fill('下一条问题')
        await expect(self.page.get_by_role('button', name='发送消息')).to_be_enabled()
        self.assertEqual(sum(count for (method, _path), count in self.counts.items() if method == 'POST'), 0)

    async def test_empty_output_limit_does_not_submit_previous_value(self):
        await self.page.goto(self.origin + '/models')
        await self.page.get_by_role('button', name=re.compile('添加模型$')).click()
        dialog = self.page.get_by_role('dialog')
        await dialog.get_by_placeholder('例如：日常助手').fill('新增模型')
        await dialog.get_by_placeholder('填写服务商提供的模型 ID').fill('test-model')
        await dialog.locator('.ant-select').click()
        await self.page.locator('.ant-select-item-option-content').filter(has_text='测试服务').click()
        output_limit = dialog.get_by_role('spinbutton')
        await expect(output_limit).to_have_value('512')
        await output_limit.fill('')
        await dialog.get_by_role('button', name='保存模型', exact=True).click()
        await self.page.wait_for_timeout(150)
        self.assertEqual(self.posts, [], 'An empty limit must not submit the previous 512 value')
        await expect(dialog).to_be_visible()
        await expect(dialog.get_by_text('请填写模型信息，输出上限须为 1–8192 的整数', exact=True)).to_be_visible()
        await expect(output_limit).to_have_value('')
        await output_limit.fill('256')
        await dialog.get_by_role('button', name='保存模型', exact=True).click()
        await expect(dialog).to_have_count(0)
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.posts[0]['max_output_tokens'], 256)


    async def prepare_new_send(self, hold_refresh=False):
        create_started = asyncio.Event()
        release_create = asyncio.Event()
        refresh_started = asyncio.Event()
        release_refresh = asyncio.Event()
        sent = asyncio.Event()
        captured = []
        get_count = 0

        async def conversations(route):
            nonlocal get_count
            if route.request.method == 'POST':
                create_started.set()
                await release_create.wait()
                await route.fulfill(status=200, json={'id': 'c2', 'title': '新建会话', 'created_at': '2026-10-23T00:00:00Z'})
            else:
                get_count += 1
                if hold_refresh and get_count > 1:
                    refresh_started.set()
                    await release_refresh.wait()
                await route.fulfill(status=200, json=[])

        async def models(route):
            await route.fulfill(status=200, json=[self.model, {**self.model, 'id': 'm2', 'name': '第二模型'}])

        async def submit(route):
            captured.append(route.request.post_data_json)
            await route.fulfill(status=202, json={
                'id': 'r2', 'resource_type': 'chat', 'resource_id': 'c2', 'status': 'completed',
                'attempt_id': 'a2', 'attempt_no': 1, 'revision': 3, 'projection_seq': 1,
                'text': '发送完成', 'error': None, 'created_at': '2026-10-23T00:00:00Z',
                'usage': {'input_tokens': 1, 'output_tokens': 1, 'status': 'known'}
            })
            sent.set()

        await self.page.route('**/api/models', models)
        await self.page.route('**/api/chat/conversations', conversations)
        await self.page.route('**/api/chat/conversations/c2/messages', lambda route: route.fulfill(status=200, json=[]))
        await self.page.route('**/api/chat/conversations/c2/runs', submit)
        await self.page.goto(self.origin + '/chat')
        await self.page.locator('.model-select').click()
        await self.page.locator('.ant-select-item-option-content').filter(has_text='第二模型').click()
        await self.page.get_by_role('textbox', name='消息内容').fill('My original question')
        await self.page.get_by_role('button', name='发送消息').click()
        await asyncio.wait_for(create_started.wait(), timeout=5)
        return release_create, refresh_started, release_refresh, sent, captured

    async def test_send_freezes_input_before_creating_conversation(self):
        release_create, _, _, sent, captured = await self.prepare_new_send()
        await self.page.get_by_role('button', name=re.compile('整理一个想法')).click()
        async with self.page.expect_response(lambda response: urlparse(response.url).path == '/api/chat/conversations'):
            await self.page.get_by_role('button', name='刷新会话').click()
        await expect(self.page.locator('.page-spinner')).to_have_count(0)
        release_create.set()
        await asyncio.wait_for(sent.wait(), timeout=5)
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]['model_id'], 'm2')
        self.assertEqual(captured[0]['message'], 'My original question')
        self.assertTrue(captured[0]['client_request_id'])

    async def test_old_list_cannot_remove_a_new_conversation(self):
        release_create, refresh_started, release_refresh, sent, _ = await self.prepare_new_send(hold_refresh=True)
        await self.page.get_by_role('button', name='刷新会话').click()
        await asyncio.wait_for(refresh_started.wait(), timeout=5)
        release_create.set()
        await asyncio.wait_for(sent.wait(), timeout=5)
        await expect(self.page.locator('.conversation-item')).to_have_count(1)
        release_refresh.set()
        await self.page.wait_for_timeout(200)
        await expect(self.page.locator('.conversation-item')).to_have_count(1)
        await expect(self.page.get_by_role('button', name=re.compile('新建会话$'))).to_be_visible()

    async def test_enter_cannot_send_until_conversation_history_is_restored(self):
        history_started = asyncio.Event()
        release_history = asyncio.Event()
        posts = []

        async def history(route):
            history_started.set()
            await release_history.wait()
            await route.fulfill(status=200, json=[{'id': 'message-1', 'role': 'user', 'content': '原问题', 'run_id': 'r1', 'created_at': '2026-10-23T00:00:00Z'}])

        async def submit(route):
            posts.append(route.request.post_data_json)
            await route.fulfill(status=409, json={'error': {'code': 'active_run', 'message': '已有运行中任务'}})

        await self.page.route('**/api/chat/conversations/c1/messages', history)
        await self.page.route('**/api/chat/conversations/c1/runs', submit)
        await self.page.goto(self.origin + '/chat')
        await self.page.get_by_role('button', name='恢复中会话').click()
        await asyncio.wait_for(history_started.wait(), timeout=5)
        async with self.page.expect_response(lambda response: urlparse(response.url).path == '/api/chat/conversations'):
            await self.page.get_by_role('button', name='刷新会话').click()
        await self.page.get_by_role('textbox', name='消息内容').fill('不可提前发送')
        await self.page.get_by_role('textbox', name='消息内容').press('Enter')
        await self.page.wait_for_timeout(150)
        self.assertEqual(posts, [], 'Keyboard sending must obey the pending history load')
        await expect(self.page.get_by_role('button', name='发送消息')).to_be_disabled()
        release_history.set()
        await expect(self.page.get_by_role('button', name=re.compile('停止生成$'))).to_be_visible()
        self.assertEqual(posts, [])


    async def prepare_login(self):
        requests = []
        async def login(route):
            credentials = route.request.post_data_json
            requests.append(credentials)
            if credentials['password'] == 'browser-test-password':
                await route.fulfill(status=200, json={'user': {'id': 'user-1', 'username': credentials['username'], 'is_admin': True}})
            else:
                await route.fulfill(status=401, json={'error': {'code': 'invalid_credentials', 'message': '用户名或密码错误'}})
        async def unauthenticated(route):
            self.counts[('GET', '/api/auth/me')] += 1
            await route.fulfill(status=401, json={'error': {'code': 'unauthenticated', 'message': '请先登录'}})
        await self.page.route('**/api/auth/login', login)
        await self.page.route('**/api/auth/me', unauthenticated)
        await self.page.goto(self.origin + '/login')
        await self.page.get_by_placeholder('输入用户名').fill('admin')
        await self.page.get_by_placeholder('输入密码').fill('browser-test-password')
        return requests

    async def test_login_button_posts_credentials_and_opens_chat(self):
        requests = await self.prepare_login()
        await self.page.get_by_role('button', name='登录工作空间', exact=True).click()
        await expect(self.page).to_have_url(re.compile('/chat$'), timeout=3000)
        self.assertEqual(requests, [{'username': 'admin', 'password': 'browser-test-password'}])
        self.assertEqual(self.counts[('GET', '/api/auth/me')], 0)
        await expect(self.page.get_by_role('textbox', name='消息内容')).to_be_visible()

    async def test_login_enter_posts_credentials_and_opens_chat(self):
        requests = await self.prepare_login()
        await self.page.get_by_placeholder('输入密码').press('Enter')
        await expect(self.page).to_have_url(re.compile('/chat$'), timeout=3000)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]['username'], 'admin')
        self.assertEqual(self.counts[('GET', '/api/auth/me')], 0)

    async def test_login_failure_is_visible_and_can_be_retried(self):
        requests = await self.prepare_login()
        await self.page.get_by_placeholder('输入密码').fill('incorrect-password')
        await self.page.get_by_role('button', name='登录工作空间', exact=True).click()
        await expect(self.page.get_by_text('用户名或密码错误', exact=True)).to_be_visible(timeout=3000)
        await expect(self.page).to_have_url(re.compile('/login$'))
        await expect(self.page.get_by_placeholder('输入密码')).to_have_value('')
        await self.page.get_by_placeholder('输入密码').fill('browser-test-password')
        await self.page.get_by_role('button', name='登录工作空间', exact=True).click()
        await expect(self.page).to_have_url(re.compile('/chat$'))
        self.assertEqual(len(requests), 2)
        self.assertEqual(self.counts[('GET', '/api/auth/me')], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
