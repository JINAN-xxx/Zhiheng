"""Run against the existing platform preview: python tests/browser/ui_regressions.py.

Uses real Chromium UI interactions with controlled HTTP/SSE boundary responses.
Set PLATFORM_BROWSER_URL to target another already running instance.
"""

import asyncio
import contextlib
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
            'enabled': True, 'model_type': 'chat', 'published': True, 'capabilities': ['chat', 'stream']
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
            body = [{'id': 'connection-1', 'name': '测试服务', 'provider': 'openai_compatible', 'base_url': 'http://provider.example/v1', 'has_api_key': True, 'enabled': True}]
        elif path == '/api/models':
            if method == 'POST':
                submitted = route.request.post_data_json
                self.posts.append(submitted)
                body = {**self.model, **submitted, 'id': 'new-model'}
            else:
                body = [self.model]
        elif path == '/api/usage':
            body = {'limit_tokens': 100000, 'used_tokens': 1, 'reserved_tokens': 0, 'records': [
                {'id': 'usage-1', 'run_id': 'r1', 'model_id': 'm1', 'source': 'chat', 'status': 'unknown', 'input_tokens': None, 'output_tokens': None, 'charged_tokens': None, 'created_at': '2026-10-23T00:00:00Z'}
            ]}
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

    async def test_next_send_shows_submission_instead_of_previous_completion(self):
        self.completed = True
        started, release = asyncio.Event(), asyncio.Event()
        await self.page.route('**/api/chat/conversations/c1/messages', lambda route: route.fulfill(json=[{'id': 'message-1', 'role': 'user', 'content': '你好', 'run_id': 'r1', 'created_at': '2026-10-23T00:00:00Z'}]))
        async def submit(route):
            started.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '暂时不可用'}})
        await self.page.route('**/api/chat/conversations/c1/runs', submit)
        try:
            await self.page.goto(self.origin + '/chat')
            await self.page.get_by_role('button', name='恢复中会话').click()
            await expect(self.page.locator('.run-state')).to_have_text('已完成')
            await self.page.get_by_role('textbox', name='消息内容').fill('下一轮问题')
            await self.page.get_by_role('button', name='发送消息').click()
            await asyncio.wait_for(started.wait(), timeout=5)
            await expect(self.page.locator('.run-state')).to_have_text('提交中')
            await expect(self.page.get_by_text('恢复后的完整回复', exact=True)).to_be_visible()
            release.set()
            await expect(self.page.get_by_text('暂时不可用', exact=True)).to_be_visible()
            await expect(self.page.locator('.run-state')).to_have_text('已完成')
        finally:
            release.set()

    async def test_empty_output_limit_does_not_submit_previous_value(self):
        await self.page.goto(self.origin + '/models')
        await self.page.get_by_role('button', name='接入模型', exact=True).click()
        dialog = self.page.locator('.ant-drawer-open .ant-drawer-content')
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
        await expect(dialog.get_by_text('请填写模型信息，输出上限须为 1–131072 的整数', exact=True)).to_be_visible()
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
        await self.page.get_by_role('button', name='整理思路', exact=True).click()
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


    async def test_original_sidebar_collapses_without_losing_history(self):
        await self.page.goto(self.origin + '/chat')
        sidebar = self.page.locator('.platform-shell__sidebar')
        await expect(sidebar).to_be_visible()
        self.assertEqual(round((await sidebar.bounding_box())['width']), 230)
        await self.page.get_by_role('button', name='收起侧栏', exact=True).click()
        await expect(sidebar).to_have_css('width', '56px')
        await expect(self.page.get_by_role('textbox', name='消息内容')).to_be_visible()
        await expect(self.page.get_by_role('button', name='智能体扩展', exact=True)).to_be_enabled()
        await expect(self.page.get_by_role('button', name='选择技能', exact=True)).to_be_enabled()
        await self.page.get_by_role('button', name='展开侧栏', exact=True).click()
        await expect(sidebar.get_by_role('button', name='恢复中会话')).to_be_visible()
        await expect(self.page.locator('.conversation-panel')).to_have_count(0)

    async def test_mobile_navigation_drawer_closes_after_navigation_and_escape(self):
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        await self.page.goto(self.origin + '/chat')
        await expect(self.page.locator('.platform-shell__sidebar')).to_be_hidden()
        await self.page.get_by_role('button', name='打开主菜单', exact=True).click()
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await expect(drawer.get_by_role('button', name='恢复中会话')).to_be_visible()
        await drawer.get_by_role('button', name='模型中心', exact=True).click()
        await drawer.get_by_role('link', name='模型目录', exact=True).click()
        await expect(drawer).to_be_hidden()
        await expect(self.page.get_by_role('heading', name='模型目录', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='打开主菜单', exact=True).click()
        await self.page.keyboard.press('Escape')
        await expect(drawer).to_be_hidden()
        await expect(self.page.get_by_role('button', name='打开主菜单', exact=True)).to_be_focused()
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))

    async def test_original_chat_starter_populates_draft_without_submitting(self):
        await self.page.goto(self.origin + '/ai/chat')
        await expect(self.page.get_by_role('heading', name='有什么可以帮你？')).to_be_visible()
        await self.page.get_by_role('button', name='解释代码', exact=True).click()
        await expect(self.page.get_by_role('textbox', name='消息内容')).to_have_value('帮我解释这段代码：')
        self.assertEqual(sum(count for (method, _path), count in self.counts.items() if method == 'POST'), 0)

    async def test_model_code_copy_uses_public_platform_id(self):
        await self.context.grant_permissions(['clipboard-read', 'clipboard-write'])
        await self.page.goto(self.origin + '/models')
        copy = self.page.get_by_role('button', name='复制模型代码', exact=True)
        await expect(copy).to_be_visible()
        await copy.click()
        self.assertEqual(await self.page.evaluate('navigator.clipboard.readText()'), self.model['id'])
        await self.page.set_viewport_size({'width': 375, 'height': 812})
        await expect(copy).to_be_visible()
        await copy.click()
        self.assertEqual(await self.page.evaluate('navigator.clipboard.readText()'), self.model['id'])
        await self.page.route(re.compile(r'/api/models(?:\?.*)?$'), lambda route: route.fulfill(status=200, json=[{**self.model, 'id': ''}]))
        await self.page.get_by_role('button', name='刷新当前视图', exact=True).click()
        await expect(copy).to_be_disabled()
        self.assertEqual(await self.page.evaluate('navigator.clipboard.readText()'), self.model['id'])

    async def test_mobile_model_and_usage_preserve_original_compact_views(self):
        await self.page.set_viewport_size({'width': 375, 'height': 812})
        await self.page.goto(self.origin + '/models')
        await expect(self.page.locator('.model-table th')).to_have_count(2)
        test_button = self.page.get_by_role('button', name='更多操作', exact=True)
        await expect(test_button).to_be_visible()
        button_box = await test_button.bounding_box()
        self.assertLessEqual(button_box['x'] + button_box['width'], 375)
        await test_button.click()
        await expect(self.page.get_by_role('menuitem', name='测试连接', exact=True)).to_be_enabled()
        await self.page.keyboard.press('Escape')
        await expect(self.page.locator('.workspace-toolbar__subtitle')).to_have_count(0)
        await expect(self.page.locator('.panel-toolbar')).to_be_visible()
        await self.page.goto(self.origin + '/ai/usage')
        await expect(self.page.locator('.usage-mobile-item')).to_have_count(1)
        await expect(self.page.locator('.usage-mobile-item__meta')).to_contain_text('令牌 待核对 / 待核对')
        await expect(self.page.locator('.usage-mobile-item__header')).to_contain_text('待核对')
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))

    async def test_mobile_navigation_drawer_tracks_viewport_resize(self):
        await self.page.set_viewport_size({'width': 375, 'height': 812})
        await self.page.goto(self.origin + '/chat')
        await self.page.get_by_role('button', name='打开主菜单', exact=True).click()
        drawer = self.page.locator('.platform-mobile-drawer .ant-drawer-content-wrapper')
        await expect(drawer).to_have_css('width', '300px')
        await self.page.set_viewport_size({'width': 320, 'height': 812})
        await expect(drawer).to_have_css('width', '275.188px')
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))

    async def test_assistant_code_preserves_highlighting_and_copy(self):
        content = '```python\nprint("hello")\n```'
        async def history(route):
            await route.fulfill(status=200, json=[{'id':'code-message','role':'assistant','content':content,'run_id':'done','created_at':'2026-10-23T00:00:00Z'}])
        await self.context.grant_permissions(['clipboard-read', 'clipboard-write'])
        await self.page.route('**/api/chat/conversations/c1/messages', history)
        await self.page.goto(self.origin + '/chat')
        await self.page.get_by_role('button', name='恢复中会话').click()
        await expect(self.page.locator('.ai-message-content pre code span[style*="color"]')).not_to_have_count(0, timeout=10000)
        copy = self.page.locator('.ai-message-content').get_by_role('button', name='Copy', exact=True)
        await expect(copy).to_have_count(1)
        await copy.click()
        self.assertEqual((await self.page.evaluate('navigator.clipboard.readText()')).strip(), 'print("hello")')

    async def test_assistant_markdown_renders_structure_and_filters_active_content(self):
        async def history(route):
            await route.fulfill(status=200, json=[{'id':'safe-message','role':'assistant','content':'**安全标题**\n\n[危险链接](javascript:alert(1))\n\n<script>window.markdownInjected = true</script>','run_id':'done','created_at':'2026-10-23T00:00:00Z'}])
        await self.page.route('**/api/chat/conversations/c1/messages', history)
        await self.page.goto(self.origin + '/chat')
        await self.page.get_by_role('button', name='恢复中会话').click()
        await expect(self.page.locator('.ai-message-content strong')).to_have_text('安全标题', timeout=2000)
        self.assertIsNone(await self.page.evaluate('window.markdownInjected'))
        self.assertEqual(await self.page.locator('.ai-message-content script, .ai-message-content a[href^="javascript:"]').count(), 0)

    async def test_model_resource_shell_preserves_original_insets(self):
        await self.page.goto(self.origin + '/models')
        await expect(self.page.get_by_role('heading', name='模型目录', exact=True)).to_be_visible()
        desktop = await self.page.locator('.ai-model-center').bounding_box()
        self.assertEqual(round(desktop['x']), 230)
        self.assertEqual(round((await self.page.locator('.model-workspace').bounding_box())['x']), 230)
        await self.page.set_viewport_size({'width': 375, 'height': 812})
        await expect(self.page.locator('.platform-shell__sidebar')).to_be_hidden()
        await expect(self.page.locator('.ai-model-center')).to_have_css('padding', '0px')
        mobile = await self.page.locator('.ai-model-center').bounding_box()
        self.assertEqual(round(mobile['x']), 20)
        self.assertEqual(round(mobile['width']), 335)
        self.assertEqual(round(mobile['y'] + mobile['height']), 732)

    async def test_chat_initial_navigation_loads_lists_once(self):
        await self.page.goto(self.origin + '/chat')
        await expect(self.page.locator('.model-select')).to_contain_text('测试模型')
        await self.page.wait_for_timeout(100)
        self.assertEqual(self.counts[('GET', '/api/models')], 1)
        self.assertEqual(self.counts[('GET', '/api/chat/conversations')], 1)

    async def test_chat_refreshes_models_when_first_opened_from_model_center(self):
        await self.page.goto(self.origin + '/models')
        await expect(self.page.get_by_role('heading', name='模型目录', exact=True)).to_be_visible()
        await self.page.route('**/api/models', lambda route: route.fulfill(status=200, json=[{**self.model, 'id':'m2', 'name':'首次进入聊天前接入的模型'}]))
        await self.page.get_by_role('link', name='智能对话', exact=True).click()
        await expect(self.page.locator('.model-select')).to_contain_text('首次进入聊天前接入的模型')

    async def test_chat_reloads_models_after_returning_from_model_center(self):
        await self.page.goto(self.origin + '/chat')
        await expect(self.page.locator('.model-select')).to_contain_text('测试模型')
        await self.page.get_by_role('button', name='模型中心', exact=True).click()
        await self.page.get_by_role('link', name='模型目录', exact=True).click()
        await self.page.route('**/api/models', lambda route: route.fulfill(status=200, json=[{**self.model, 'id':'m2', 'name':'刚接入的模型'}]))
        await self.page.get_by_role('link', name='智能对话', exact=True).click()
        await expect(self.page.locator('.model-select')).to_contain_text('刚接入的模型', timeout=2000)

    async def test_original_login_validates_empty_fields_without_storing_passwords(self):
        await self.page.route('**/api/auth/me', lambda route: route.fulfill(status=401, json={'error': {'code':'unauthenticated','message':'请先登录'}}))
        await self.page.goto(self.origin + '/login')
        await expect(self.page.get_by_role('button', name='登录', exact=True)).to_be_enabled()
        await self.page.get_by_role('button', name='登录', exact=True).click()
        await expect(self.page.get_by_text('请输入您的账号', exact=True)).to_be_visible(timeout=2000)
        await expect(self.page.get_by_text('请输入您的密码', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('checkbox', name='记住密码', exact=True)).to_be_disabled()
        await self.page.get_by_placeholder('密码', exact=True).fill('must-remain-in-memory')
        self.assertNotIn('must-remain-in-memory', await self.page.evaluate('JSON.stringify(localStorage) + document.cookie'))
        self.assertEqual(sum(count for (method, _path), count in self.counts.items() if method == 'POST'), 0)

    async def test_original_theme_switch_changes_surface_and_survives_reload(self):
        await self.page.goto(self.origin + '/chat')
        await self.page.get_by_role('button', name='切换主题', exact=True).click()
        await expect(self.page.locator('html')).to_have_class(re.compile('dark'), timeout=2000)
        await expect(self.page.locator('.platform-chat')).to_have_css('background-color', 'rgb(8, 8, 8)')
        await self.page.reload()
        await expect(self.page.locator('html')).to_have_class(re.compile('dark'))
        await self.page.get_by_role('button', name='切换主题', exact=True).click()
        await expect(self.page.locator('html')).not_to_have_class(re.compile('dark'))

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
        await self.page.get_by_placeholder('账号', exact=True).fill('admin')
        await self.page.get_by_placeholder('密码', exact=True).fill('browser-test-password')
        return requests

    async def test_login_button_posts_credentials_and_opens_chat(self):
        requests = await self.prepare_login()
        await self.page.get_by_role('button', name='登录', exact=True).click()
        await expect(self.page).to_have_url(re.compile('/chat$'), timeout=3000)
        self.assertEqual(requests, [{'username': 'admin', 'password': 'browser-test-password'}])
        self.assertEqual(self.counts[('GET', '/api/auth/me')], 0)
        await expect(self.page.get_by_role('textbox', name='消息内容')).to_be_visible()

    async def test_login_enter_posts_credentials_and_opens_chat(self):
        requests = await self.prepare_login()
        await self.page.get_by_placeholder('密码', exact=True).press('Enter')
        await expect(self.page).to_have_url(re.compile('/chat$'), timeout=3000)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]['username'], 'admin')
        self.assertEqual(self.counts[('GET', '/api/auth/me')], 0)

    async def test_login_failure_is_visible_and_can_be_retried(self):
        requests = await self.prepare_login()
        await self.page.get_by_placeholder('密码', exact=True).fill('incorrect-password')
        await self.page.get_by_role('button', name='登录', exact=True).click()
        await expect(self.page.get_by_text('用户名或密码错误', exact=True)).to_be_visible(timeout=3000)
        await expect(self.page).to_have_url(re.compile('/login$'))
        await expect(self.page.get_by_placeholder('密码', exact=True)).to_have_value('')
        await self.page.get_by_placeholder('密码', exact=True).fill('browser-test-password')
        await self.page.get_by_role('button', name='登录', exact=True).click()
        await expect(self.page).to_have_url(re.compile('/chat$'))
        self.assertEqual(len(requests), 2)
        self.assertEqual(self.counts[('GET', '/api/auth/me')], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
