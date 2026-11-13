"""Message actions through the real Vue UI with controlled HTTP/browser boundaries."""
from browser_clipboard import read_clipboard
import asyncio
import contextlib
import copy
import re
import unittest
from urllib.parse import parse_qs, urlparse
from playwright.async_api import expect
from developer_regressions import DeveloperBoundary

STAMP = '2026-10-28T00:00:00Z'


def chat_message(message_id, role, content, **extra):
    return {'id': message_id, 'role': role, 'content': content, 'run_id': None,
            'created_at': STAMP, 'reply_to_message_id': None, 'generation_index': 1,
            'is_complete': True, 'feedback': None, **extra}


class MessageActionsRegressions(DeveloperBoundary):
    async def asyncSetUp(self):
        self.history = [chat_message('q1', 'user', '原问题'),
                        chat_message('a1', 'assistant', '原回复', reply_to_message_id='q1')]
        self.feedback_calls = []
        self.regenerate_calls = []
        self.share_calls = []
        self.shares = []
        self.run = {'id': 'regen-run', 'resource_type': 'chat', 'resource_id': 'c1',
                    'status': 'completed', 'attempt_id': 'attempt-1', 'attempt_no': 1,
                    'revision': 3, 'projection_seq': 1, 'text': '新回复', 'error': None,
                    'created_at': STAMP, 'usage': {'input_tokens': 2, 'output_tokens': 3, 'status': 'known'}}
        await super().asyncSetUp()

    async def respond(self, route):
        path = urlparse(route.request.url).path
        method = route.request.method
        if path.startswith('/src/'):
            await route.continue_()
            return
        if path == '/api/chat/conversations':
            await route.fulfill(json=[{'id': 'c1', 'title': '操作会话', 'created_at': STAMP},
                                      {'id': 'c2', 'title': '另一会话', 'created_at': STAMP}])
        elif path == '/api/chat/conversations/c1/messages':
            await route.fulfill(json=self.history)
        elif path == '/api/chat/conversations/c2/messages':
            await route.fulfill(json=[chat_message('q2', 'user', '另一问题'), chat_message('b1', 'assistant', '另一回复')])
        elif path == '/api/chat/conversations/c1/messages/a1/regenerate':
            self.regenerate_calls.append(route.request.post_data_json)
            self.history.append(chat_message('a2', 'assistant', '新回复', reply_to_message_id='q1', generation_index=2, run_id='regen-run'))
            await route.fulfill(json=self.run)
        elif path == '/api/runs/regen-run':
            await route.fulfill(json=self.run)
        elif path == '/api/runs/regen-run/events':
            await route.fulfill(content_type='text/event-stream', body='')
        elif path.startswith('/api/chat/messages/') and path.endswith('/feedback'):
            self.feedback_calls.append(route.request.post_data_json)
            message_id = path.split('/')[4]
            value = route.request.post_data_json['value']
            next(item for item in self.history if item['id'] == message_id)['feedback'] = value
            await route.fulfill(json={'message_id': message_id, 'value': value})
        elif path == '/api/chat/messages/a1/share-preview':
            include = parse_qs(urlparse(route.request.url).query)['include_question'][0] == 'true'
            await route.fulfill(json={'message_id': 'a1', 'question': '原问题' if include else None, 'answer': '原回复', 'content_hash': 'with-q' if include else 'answer-only'})
        elif path == '/api/chat/messages/a1/shares':
            if method == 'POST':
                self.share_calls.append(route.request.post_data_json)
                share = {'id': f'share-{len(self.shares)+1}', 'message_id': 'a1', 'created_at': STAMP, 'expires_at': '2026-11-04T00:00:00Z', 'revoked_at': None}
                self.shares.append(share)
                await route.fulfill(json={**share, 'token': 'fixture-share-token'})
            else:
                await route.fulfill(json=self.shares)
        elif path.startswith('/api/chat/shares/'):
            self.assertEqual(route.request.post_data_json, {})
            self.shares[0]['revoked_at'] = STAMP
            await route.fulfill(status=204)
        elif path.startswith('/api/shared/messages/'):
            await route.fulfill(json={'question': '公开提问', 'answer': '公开回答 <script>window.shareInjected=true</script>', 'created_at': STAMP, 'expires_at': '2026-11-04T00:00:00Z'})
        else:
            await super().respond(route)

    async def open_chat(self):
        await self.page.goto(self.origin + '/chat')
        await self.select_conversation('操作会话')
        await expect(self.page.get_by_text('原回复', exact=True)).to_be_visible()

    async def open_unanswered_failure(self):
        self.history = [chat_message('q1', 'user', '原问题', run_id='failed-run')]
        failed = {**self.run, 'id': 'failed-run', 'status': 'failed', 'text': '',
                  'reasoning': '已经产生的思考内容',
                  'error': {'code': 'provider_timeout', 'message': '模型响应超时，请重新生成'}}
        await self.page.route(self.origin + '/api/runs/failed-run', lambda route: route.fulfill(json=failed))
        await self.page.goto(self.origin + '/chat')
        await self.select_conversation('操作会话')
        await expect(self.page.get_by_text('模型响应超时，请重新生成', exact=True)).to_be_visible()

    async def test_initial_failure_has_clean_status_and_regenerates_without_repeating_question(self):
        async def retry(route):
            self.regenerate_calls.append(route.request.post_data_json)
            self.history.append(chat_message('a2', 'assistant', '新回复', reply_to_message_id='q1', generation_index=2, run_id='regen-run'))
            await route.fulfill(status=202, json=self.run)
        await self.page.route(self.origin + '/api/chat/conversations/c1/messages/q1/regenerate', retry)
        await self.open_unanswered_failure()
        reply = self.page.locator('.message-row.assistant')
        await expect(reply.get_by_role('button', name='重新生成', exact=True)).to_be_visible()
        await expect(reply.locator('.message-meta .ant-tag')).to_have_count(0)
        await expect(reply.get_by_text('生成失败', exact=True)).to_be_visible()
        await expect(reply.get_by_text('本次运行未生成文本', exact=True)).to_have_count(0)
        await expect(reply.locator('.reasoning-status')).to_have_count(0)
        await expect(reply.get_by_role('toolbar', name='回复操作').get_by_role('button')).to_have_count(1)
        await self.page.get_by_role('textbox', name='消息内容').fill('要保留的下一条草稿')
        await reply.get_by_role('button', name='重新生成', exact=True).click()
        await expect(self.page.get_by_text('新回复', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('textbox', name='消息内容')).to_have_value('要保留的下一条草稿')
        await expect(self.page.locator('.message-row.user')).to_have_count(1)
        self.assertEqual(len(self.regenerate_calls), 1)

    async def test_unanswered_regeneration_uncertainty_reuses_request_and_preserves_draft(self):
        async def uncertain(route):
            self.regenerate_calls.append(route.request.post_data_json)
            await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '接纳结果未知'}})
        await self.page.route(self.origin + '/api/chat/conversations/c1/messages/q1/regenerate', uncertain)
        await self.open_unanswered_failure()
        await self.page.get_by_role('textbox', name='消息内容').fill('下一条草稿')
        await self.page.get_by_role('button', name='重新生成', exact=True).click()
        await expect(self.page.get_by_role('button', name='发送消息', exact=True)).to_be_disabled()
        await self.page.get_by_role('button', name='使用原重新生成请求重试', exact=True).click()
        await expect(self.page.get_by_text(re.compile('接纳结果未知'))).to_be_visible()
        self.assertEqual(len(self.regenerate_calls), 2)
        self.assertEqual(self.regenerate_calls[0], self.regenerate_calls[1])
        await expect(self.page.get_by_role('textbox', name='消息内容')).to_have_value('下一条草稿')

    async def test_ordinary_chat_has_no_knowledge_picker_and_sends_no_base_ids(self):
        requests = []
        async def admit(route):
            requests.append(route.request.post_data_json)
            await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '受控接纳失败'}})
        await self.page.route(self.origin + '/api/chat/conversations/c1/runs', admit)
        await self.open_chat()
        await expect(self.page.get_by_role('button', name='选择知识库', exact=True)).to_have_count(0)
        await self.page.get_by_role('textbox', name='消息内容').fill('普通聊天问题')
        await self.page.get_by_role('button', name='发送消息', exact=True).click()
        await expect(self.page.get_by_text('受控接纳失败', exact=True)).to_be_visible()
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].get('knowledge_base_ids', []), [])

    async def test_feedback_is_mutually_exclusive_clearable_and_survives_reload(self):
        await self.open_chat()
        up = self.page.get_by_role('button', name='赞同', exact=True)
        down = self.page.get_by_role('button', name='不赞同', exact=True)
        await up.click()
        await expect(up).to_have_attribute('aria-pressed', 'true')
        await down.click()
        await expect(up).to_have_attribute('aria-pressed', 'false')
        await expect(down).to_have_attribute('aria-pressed', 'true')
        await self.page.reload()
        await self.select_conversation('操作会话')
        await expect(down).to_have_attribute('aria-pressed', 'true')
        await down.click()
        await expect(down).to_have_attribute('aria-pressed', 'false')
        self.assertEqual(self.feedback_calls, [{'value': 'up'}, {'value': 'down'}, {'value': None}])

    async def test_feedback_busy_and_late_response_cannot_write_another_conversation(self):
        requested, release = asyncio.Event(), asyncio.Event()
        async def delayed(route):
            self.feedback_calls.append(route.request.post_data_json)
            requested.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await route.fulfill(json={'message_id': 'a1', 'value': 'up'})
        await self.page.route(self.origin + '/api/chat/messages/a1/feedback', delayed)
        try:
            await self.open_chat()
            await self.page.get_by_role('button', name='赞同', exact=True).click()
            await asyncio.wait_for(requested.wait(), timeout=10)
            await self.page.get_by_role('button', name='不赞同', exact=True).dispatch_event('click')
            self.assertEqual(len(self.feedback_calls), 1)
            await self.select_conversation('另一会话')
            release.set()
            await expect(self.page.get_by_text('另一回复', exact=True)).to_be_visible()
            await expect(self.page.get_by_role('button', name='赞同', exact=True)).to_have_attribute('aria-pressed', 'false')
        finally:
            release.set()

    async def test_regenerate_preserves_versions_and_the_next_draft(self):
        await self.open_chat()
        await self.page.get_by_role('textbox', name='消息内容').fill('下一条草稿')
        await self.page.get_by_role('button', name='重新生成', exact=True).click()
        await expect(self.page.get_by_text('新回复', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('textbox', name='消息内容')).to_have_value('下一条草稿')
        await self.page.get_by_role('button', name='上一个回复版本', exact=True).click()
        await expect(self.page.get_by_text('原回复', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('新回复', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='下一个回复版本', exact=True).click()
        await expect(self.page.get_by_text('新回复', exact=True)).to_be_visible()
        self.assertEqual(len(self.regenerate_calls), 1)

    async def test_history_started_before_confirmed_feedback_cannot_erase_it(self):
        requested, release = asyncio.Event(), asyncio.Event()
        initial = True
        async def delayed_history(route):
            nonlocal initial
            if initial:
                initial = False
                await route.fulfill(json=self.history)
                return
            snapshot = copy.deepcopy(self.history)
            requested.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await route.fulfill(json=snapshot)
        await self.page.route(re.compile(re.escape(self.origin + '/api/chat/conversations/c1/messages') + r'(?:\?.*)?$'), delayed_history)
        self.run.update(status='running', text='正在生成新的回复', revision=2)
        await self.page.route(self.origin + '/api/runs/regen-run/events', lambda route: route.fulfill(content_type='text/plain', body='broken stream'))
        try:
            await self.open_chat()
            await self.page.get_by_role('button', name='重新生成', exact=True).click()
            await asyncio.wait_for(requested.wait(), timeout=10)
            up = self.page.get_by_role('button', name='赞同', exact=True)
            await up.click()
            await expect(up).to_have_attribute('aria-pressed', 'true')
            release.set()
            await self.page.get_by_role('button', name='上一个回复版本', exact=True).click()
            await expect(up).to_have_attribute('aria-pressed', 'true')
            self.assertEqual(self.history[1]['feedback'], 'up')
        finally:
            release.set()

    async def test_unknown_regeneration_reuses_key_and_blocks_normal_send(self):
        async def uncertain(route):
            self.regenerate_calls.append(route.request.post_data_json)
            await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '接纳结果未知'}})
        await self.page.route(self.origin + '/api/chat/conversations/c1/messages/a1/regenerate', uncertain)
        await self.open_chat()
        await self.page.get_by_role('button', name='重新生成', exact=True).click()
        await expect(self.page.get_by_text(re.compile('接纳结果未知'))).to_be_visible()
        await self.page.get_by_role('textbox', name='消息内容').fill('不能越过未决请求')
        await expect(self.page.get_by_role('button', name='发送消息', exact=True)).to_be_disabled()
        await self.page.get_by_role('button', name='使用原重新生成请求重试', exact=True).click()
        await self.page.wait_for_timeout(150)
        self.assertEqual(len(self.regenerate_calls), 2)
        self.assertEqual(self.regenerate_calls[0], self.regenerate_calls[1])

    async def test_pending_original_send_blocks_regeneration_and_keeps_its_retry_identity(self):
        requests = []
        async def uncertain(route):
            requests.append(route.request.post_data_json)
            await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '普通发送接纳未知'}})
        await self.page.route(self.origin + '/api/chat/conversations/c1/runs', uncertain)
        await self.open_chat()
        await self.page.get_by_role('textbox', name='消息内容').fill('新的普通提问')
        await self.page.get_by_role('button', name='发送消息', exact=True).click()
        await expect(self.page.get_by_text('普通发送接纳未知', exact=True)).to_be_visible()
        regenerate = self.page.get_by_role('button', name='重新生成', exact=True)
        await expect(regenerate).to_have_attribute('aria-disabled', 'true')
        await regenerate.dispatch_event('click')
        self.assertEqual(self.regenerate_calls, [])
        await self.page.get_by_role('button', name='使用原请求重试', exact=True).click()
        await expect(self.page.get_by_text('普通发送接纳未知', exact=True)).to_be_visible()
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0], requests[1])

    async def test_refresh_discovers_assistant_placeholder_and_recovers_existing_run(self):
        self.history.append(chat_message('a2', 'assistant', '', reply_to_message_id='q1', generation_index=2, is_complete=False, run_id='regen-run'))
        await self.page.goto(self.origin + '/chat')
        await self.select_conversation('操作会话')
        await expect(self.page.get_by_text('新回复', exact=True)).to_be_visible()
        self.assertEqual(self.regenerate_calls, [])

    async def test_share_preview_requires_explicit_create_uses_hash_and_can_copy_revoke(self):
        await self.open_chat()
        await self.page.get_by_role('button', name='分享', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        await expect(dialog.get_by_text('原回复', exact=True)).to_be_visible()
        self.assertEqual(self.share_calls, [])
        await dialog.get_by_role('checkbox', name='附带提问', exact=True).uncheck()
        await expect(dialog.get_by_text('原问题', exact=True)).to_have_count(0)
        await dialog.get_by_role('button', name='创建分享链接', exact=True).click()
        await expect(dialog.get_by_text(self.origin + '/share/fixture-share-token', exact=True)).to_be_visible()
        self.assertEqual(self.share_calls, [{'include_question': False, 'expires_in_days': 7, 'expected_content_hash': 'answer-only'}])
        await dialog.get_by_role('button', name='复制分享链接', exact=True).click()
        self.assertEqual(await read_clipboard(self.page), self.origin + '/share/fixture-share-token')
        storage = await self.page.evaluate('JSON.stringify({local:{...localStorage},session:{...sessionStorage}})')
        self.assertNotIn('fixture-share-token', storage)
        await dialog.get_by_role('button', name='撤销分享', exact=True).click()
        await expect(dialog.get_by_text('已撤销', exact=True)).to_be_visible()
        await dialog.get_by_role('button', name='Close', exact=True).click()
        await expect(self.page.get_by_text(self.origin + '/share/fixture-share-token', exact=True)).to_have_count(0)

    async def test_public_share_is_safe_readonly_and_accessible_while_signed_in_or_anonymous(self):
        for anonymous in [False, True]:
            if anonymous:
                await self.page.route(self.origin + '/api/auth/me', lambda route: route.fulfill(status=401, json={'error': {'code': 'unauthenticated', 'message': '未登录'}}))
            await self.page.goto(self.origin + '/share/fixture-share-token')
            await expect(self.page.get_by_text('公开提问', exact=True)).to_be_visible()
            await expect(self.page.get_by_text(re.compile('公开回答'))).to_be_visible()
            self.assertFalse(await self.page.evaluate('Boolean(window.shareInjected)'))
            await expect(self.page.get_by_role('textbox', name='消息内容')).to_have_count(0)
        await self.page.route(self.origin + '/api/shared/messages/invalid', lambda route: route.fulfill(status=404, json={'error': {'code': 'not_found', 'message': '无效'}}))
        await self.page.goto(self.origin + '/share/invalid')
        await expect(self.page.get_by_text('分享链接无效、已过期或已撤销', exact=True)).to_be_visible()

    async def test_speech_has_one_owner_and_stops_on_version_switch_and_navigation(self):
        await self.page.add_init_script('''
        window.speechCalls=[]; window.speechCancels=0;
        window.SpeechSynthesisUtterance = class { constructor(text){this.text=text} };
        Object.defineProperty(window, 'speechSynthesis', {value: {
          speak(utterance) { window.speechCalls.push(utterance); utterance.onstart?.(); },
          cancel() { window.speechCancels++; }
        }});
        ''')
        await self.open_chat()
        await self.page.get_by_role('button', name='朗读', exact=True).click()
        await expect(self.page.get_by_role('button', name='停止朗读', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='重新生成', exact=True).click()
        await expect(self.page.get_by_text('新回复', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='停止朗读', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='朗读', exact=True).click()
        await self.page.get_by_role('link', name='模型目录', exact=True).click()
        await expect(self.page.get_by_role('heading', name='模型目录', exact=True)).to_be_visible()
        self.assertGreaterEqual(await self.page.evaluate('window.speechCancels'), 2)

    async def test_late_share_list_cannot_hide_a_newly_created_share(self):
        requested, release = asyncio.Event(), asyncio.Event()
        original = self.respond
        async def delayed(route):
            if route.request.method == 'GET':
                requested.set()
                await release.wait()
                with contextlib.suppress(Exception):
                    await route.fulfill(json=[])
            else:
                await original(route)
        await self.page.route(self.origin + '/api/chat/messages/a1/shares', delayed)
        try:
            await self.open_chat()
            await self.page.get_by_role('button', name='分享', exact=True).click()
            await asyncio.wait_for(requested.wait(), timeout=10)
            dialog = self.page.get_by_role('dialog')
            await expect(dialog.get_by_text('原回复', exact=True)).to_be_visible()
            await dialog.get_by_role('button', name='创建分享链接', exact=True).click()
            await expect(dialog.get_by_role('button', name='撤销分享', exact=True)).to_be_visible()
            release.set()
            await self.page.wait_for_timeout(200)
            await expect(dialog.get_by_role('button', name='撤销分享', exact=True)).to_be_visible()
        finally:
            release.set()

    async def test_historical_reply_cannot_regenerate_and_mobile_ime_does_not_submit(self):
        self.history += [chat_message('q2', 'user', '更新的问题'), chat_message('a2', 'assistant', '最新回复', reply_to_message_id='q2')]
        await self.open_chat()
        old = self.page.locator('#message-a1').get_by_role('button', name='重新生成', exact=True)
        await expect(old).to_have_attribute('aria-disabled', 'true')
        await old.dispatch_event('click')
        await expect(self.page.get_by_text('只能重新生成最新一轮回复', exact=True)).to_be_visible()
        self.assertEqual(self.regenerate_calls, [])
        await self.page.set_viewport_size({'width': 375, 'height': 812})
        textarea = self.page.get_by_role('textbox', name='消息内容')
        await textarea.fill('输入法中的内容')
        await textarea.dispatch_event('keydown', {'key': 'Enter', 'isComposing': True})
        await expect(textarea).to_have_value('输入法中的内容')
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))

    async def test_regeneration_knowledge_reconnect_preserves_next_draft(self):
        prep = {'kind': 'knowledge_preparation', 'id': 'regen-prep', 'resource_type': 'chat', 'resource_id': 'c1', 'status': 'waiting_models', 'phase': 'embedding', 'run': None, 'hits': [], 'error': None, 'created_at': STAMP, 'updated_at': STAMP}
        await self.page.route(self.origin + '/api/chat/conversations/c1/messages/a1/regenerate', lambda route: route.fulfill(status=202, json=prep))
        async def failed(route):
            await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '检索观察断开'}})
        await self.page.route(self.origin + '/api/knowledge/preparations/regen-prep', failed)
        await self.open_chat()
        await self.page.get_by_role('textbox', name='消息内容').fill('检索过程中要保留的下一条草稿')
        await self.page.get_by_role('button', name='重新生成', exact=True).click()
        await expect(self.page.get_by_text('检索观察断开', exact=True)).to_be_visible()
        self.history.append(chat_message('a2', 'assistant', '新回复', reply_to_message_id='q1', generation_index=2, run_id='regen-run'))
        prep.update(status='admitted', phase='admitted', run=self.run, updated_at='2026-10-28T00:00:01Z')
        await self.page.unroute(self.origin + '/api/knowledge/preparations/regen-prep', failed)
        await self.page.route(self.origin + '/api/knowledge/preparations/regen-prep', lambda route: route.fulfill(json=prep))
        await self.page.get_by_role('button', name='恢复查看', exact=True).click()
        await expect(self.page.get_by_text('新回复', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('textbox', name='消息内容')).to_have_value('检索过程中要保留的下一条草稿')

    async def test_failed_regeneration_can_retry_feedback_and_share_partial_text(self):
        self.run.update(status='failed', text='中途失败的内容', error={'code': 'provider_error', 'message': '受控模型失败'})
        self.history.append(chat_message('a2', 'assistant', '中途失败的内容', reply_to_message_id='q1', generation_index=2, is_complete=False, run_id='regen-run'))
        await self.page.route(self.origin + '/api/chat/messages/a2/share-preview?*', lambda route: route.fulfill(json={'message_id': 'a2', 'question': '原问题', 'answer': '中途失败的内容', 'content_hash': 'partial-hash'}))
        async def shares(route):
            if route.request.method == 'GET':
                await route.fulfill(json=[])
            else:
                self.share_calls.append(route.request.post_data_json)
                await route.fulfill(json={'id': 'partial-share', 'message_id': 'a2', 'token': 'partial-token', 'created_at': STAMP, 'expires_at': '2026-11-04T00:00:00Z', 'revoked_at': None})
        await self.page.route(self.origin + '/api/chat/messages/a2/shares', shares)
        await self.page.goto(self.origin + '/chat')
        await self.select_conversation('操作会话')
        await expect(self.page.get_by_text('受控模型失败', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='重新生成', exact=True)).to_have_attribute('aria-disabled', 'false')
        await self.page.get_by_role('button', name='赞同', exact=True).click()
        await expect(self.page.get_by_role('button', name='赞同', exact=True)).to_have_attribute('aria-pressed', 'true')
        await self.page.get_by_role('button', name='分享', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        await expect(dialog.get_by_text('中途失败的内容', exact=True)).to_be_visible()
        await dialog.get_by_role('button', name='创建分享链接', exact=True).click()
        await expect(dialog.get_by_text(self.origin + '/share/partial-token', exact=True)).to_be_visible()
        self.assertEqual(self.share_calls, [{'include_question': True, 'expires_in_days': 7, 'expected_content_hash': 'partial-hash'}])

    async def test_closing_share_during_create_discards_late_token(self):
        requested, release = asyncio.Event(), asyncio.Event()
        async def delayed(route):
            requested.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await route.fulfill(json={'id': 'late-share', 'message_id': 'a1', 'token': 'late-secret-token', 'created_at': STAMP, 'expires_at': '2026-11-04T00:00:00Z', 'revoked_at': None})
        await self.page.route(self.origin + '/api/chat/messages/a1/shares', lambda route: delayed(route) if route.request.method == 'POST' else route.fulfill(json=[]))
        try:
            await self.open_chat()
            await self.page.get_by_role('button', name='分享', exact=True).click()
            dialog = self.page.get_by_role('dialog')
            await expect(dialog.get_by_text('原回复', exact=True)).to_be_visible()
            await dialog.get_by_role('button', name='创建分享链接', exact=True).click()
            await asyncio.wait_for(requested.wait(), timeout=10)
            await dialog.get_by_role('button', name='Close', exact=True).click()
            release.set()
            await self.page.wait_for_timeout(150)
            await expect(self.page.get_by_role('dialog')).to_have_count(0)
            self.assertNotIn('late-secret-token', await self.page.locator('body').inner_text())
        finally:
            release.set()


if __name__ == '__main__':
    unittest.main(verbosity=2)
