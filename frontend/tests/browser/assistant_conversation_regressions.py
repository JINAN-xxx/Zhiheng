"""Formal assistant use persists sessions, restores runs and separates draft debugging."""

import asyncio
import json
import re
import unittest
from urllib.parse import urlparse

from playwright.async_api import expect

from developer_regressions import DeveloperBoundary


class AssistantConversationRegressions(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        self.release_stream.set()
        self.submit_gate.set()
        await DeveloperBoundary.asyncTearDown(self)

    async def asyncSetUp(self):
        self.config = {'name': '制度助手', 'description': '解答公司制度', 'model_id': 'model-1',
                       'system_prompt': 'PRIVATE CONFIG', 'welcome_message': '你好，我会根据公司制度为你解答。',
                       'skill_slugs': [], 'knowledge_base_ids': [], 'application_id': None,
                       'temperature': 0.7, 'max_tokens': 32, 'history_rounds': 20}
        self.agent = {**self.config, 'id': 'assistant-1', 'owner_id': 'user-1', 'version': 2,
                      'status': 'published', 'published_version': 1, 'published_config': self.config.copy(),
                      'created_at': '2026-11-12T01:00:00Z', 'updated_at': '2026-11-12T01:00:00Z'}
        self.sessions = []
        self.turns = {}
        self.submissions = []
        self.completed = True
        self.hold_events = False
        self.release_stream = asyncio.Event()
        self.fail_submit = False
        self.deny_detail = False
        self.deleted = []
        self.renames = []
        self.knowledge_mode = False
        self.preparations = {}
        self.preparation_state = 'waiting_models'
        self.submit_gate = asyncio.Event()
        self.submit_gate.set()
        self.submission_received = asyncio.Event()
        await DeveloperBoundary.asyncSetUp(self)

    def session(self, identifier):
        return {'id': identifier, 'agent_id': 'assistant-1', 'title': '新对话', 'version': 1, 'name': '制度助手',
                'description': '解答公司制度', 'welcome_message': self.config['welcome_message'],
                'published_version': 1, 'created_at': '2026-11-12T01:00:00Z',
                'updated_at': '2026-11-12T01:00:00Z', 'preparation_id': None}

    def snapshot(self, run_id, cid):
        return {'id': run_id, 'resource_type': 'agent_chat', 'resource_id': cid,
                'status': 'completed' if self.completed else 'running',
                'attempt_id': 'attempt', 'attempt_no': 1, 'revision': 2 if self.completed else 1,
                'projection_seq': 2 if self.completed else 1, 'text': '**制度答复**：请查看相关条款。' if self.completed else '',
                'reasoning': '', 'error': None, 'created_at': '2026-11-12T01:00:00Z',
                'usage': {'input_tokens': 8, 'output_tokens': 2, 'status': 'known' if self.completed else 'pending'}}

    def preparation_info(self, pid):
        item = self.preparations[pid]
        cid = item['conversation_id']
        session = next(s for s in self.sessions if s['id'] == cid)
        run = None
        if self.preparation_state == 'admitted':
            old = next((t for t in self.turns[cid] if t['request_id'] == item['request_id']), None)
            if old is None:
                old = {'id': 'prepared-turn-' + pid, 'sequence': len(self.turns[cid]) + 1,
                       'content': item['content'], 'run_id': 'prepared-run-' + pid,
                       'request_id': item['request_id'], 'request': item['request_id'], 'sources': [],
                       'agent_name': '制度助手', 'published_version': 1, 'created_at': '2026-11-12T01:00:00Z'}
                self.turns[cid].append(old)
                session['title'] = item['content']
                session['version'] += 1
            session['preparation_id'] = None
            run = self.snapshot(old['run_id'], cid)
        if self.preparation_state in ('failed', 'cancelled'):
            session['preparation_id'] = None
        return {'kind': 'knowledge_preparation', 'id': pid, 'resource_type': 'agent_chat',
                'resource_id': cid, 'status': self.preparation_state,
                'phase': 'embedding', 'run': run, 'hits': [],
                'error': {'code': 'knowledge_embedding_failed', 'message': '检索服务临时失败'} if self.preparation_state == 'failed' else None,
                'created_at': '2026-11-12T01:00:00Z', 'updated_at': '2026-11-12T01:00:01Z'}

    async def respond(self, route):
        request = route.request
        path = urlparse(request.url).path
        root = '/api/agents/assistant-1/conversations'
        if path.startswith('/api/knowledge/preparations/'):
            pid = path.split('/')[4]
            if path.endswith('/cancel'):
                self.preparation_state = 'cancelled'
            return await route.fulfill(json=self.preparation_info(pid))
        if path == '/api/agents' and request.method == 'GET':
            return await route.fulfill(json={'items': [self.agent], 'total': 1, 'page': 1, 'page_size': 100})
        if path == '/api/agents/assistant-1':
            return await route.fulfill(json=self.agent)
        if path == root:
            if request.method == 'POST':
                key = request.post_data_json['client_request_id']
                existing = next((x for x in self.sessions if x.get('createId') == key), None)
                if not existing:
                    existing = {**self.session('session-' + str(len(self.sessions) + 1)), 'createId': key}
                    self.sessions.insert(0, existing)
                    self.turns[existing['id']] = []
                return await route.fulfill(status=201, json=existing)
            return await route.fulfill(json=self.sessions)
        if path.startswith(root + '/'):
            cid = path[len(root) + 1:].split('/')[0]
            if self.deny_detail:
                return await route.fulfill(status=404, json={'error': {'code': 'assistant_conversation_not_found', 'message': '助手对话不存在'}})
            session = next(s for s in self.sessions if s['id'] == cid)
            if request.method == 'PATCH':
                self.renames.append(request.post_data_json)
                if request.post_data_json['expected_version'] != session['version']:
                    return await route.fulfill(status=409, json={'error': {'code': 'assistant_conversation_changed', 'message': '会话已更新，请刷新历史记录后重试'}})
                session['title'] = request.post_data_json['title']
                session['version'] += 1
                return await route.fulfill(json=session)
            if request.method == 'DELETE':
                if request.post_data_json['expected_version'] != session['version']:
                    return await route.fulfill(status=409, json={'error': {'code': 'assistant_conversation_changed', 'message': '会话已更新，请刷新历史记录后重试'}})
                self.deleted.append(cid)
                self.sessions.remove(session)
                return await route.fulfill(status=204)
            if path.endswith('/runs'):
                body = request.post_data_json
                self.submissions.append(body)
                if self.knowledge_mode:
                    pid = 'prep-' + body['client_request_id']
                    self.preparations[pid] = {'request_id': body['client_request_id'], 'content': body['message'], 'conversation_id': cid}
                    session['preparation_id'] = pid
                    self.submission_received.set()
                    await self.submit_gate.wait()
                    return await route.fulfill(status=202, json=self.preparation_info(pid))
                old = next((t for t in self.turns[cid] if t['request'] == body['client_request_id']), None)
                if old is None:
                    old = {'id': 'turn-' + str(len(self.turns[cid]) + 1), 'sequence': len(self.turns[cid]) + 1,
                           'content': body['message'], 'run_id': 'run-' + cid + '-' + str(len(self.turns[cid]) + 1),
                           'sources': [], 'agent_name': '制度助手', 'published_version': 1, 'request': body['client_request_id'], 'request_id': body['client_request_id'], 'created_at': '2026-11-12T01:00:00Z'}
                    self.turns[cid].append(old)
                    session['title'] = body['message']
                    session['version'] += 1
                if self.fail_submit:
                    self.fail_submit = False
                    return await route.abort('failed')
                return await route.fulfill(status=202, json=self.snapshot(old['run_id'], cid))
            return await route.fulfill(json={'conversation': session, 'preparing_message': self.preparations.get(session['preparation_id']), 'turns': [
                {**t, 'run': self.snapshot(t['run_id'], cid)} for t in self.turns[cid]
            ], 'next_before': None})
        if path.startswith('/api/runs/'):
            rid = path.split('/')[3]
            cid = next(cid for cid, values in self.turns.items() if any(t['run_id'] == rid for t in values))
            if path.endswith('/events'):
                if self.hold_events:
                    await self.release_stream.wait()
                return await route.fulfill(content_type='text/event-stream', body='event: snapshot\ndata: ' + json.dumps(self.snapshot(rid, cid)) + '\n\n')
            if path.endswith('/cancel'):
                self.completed = True
            return await route.fulfill(json=self.snapshot(rid, cid))
        return await DeveloperBoundary.respond(self, route)

    async def open_history(self):
        await self.page.get_by_role('button', name='历史记录', exact=True).click()
        await expect(self.page.get_by_role('dialog', name='历史记录', exact=True)).to_be_visible()

    async def close_history(self):
        await self.page.get_by_role('dialog', name='历史记录', exact=True).get_by_role('button', name='Close', exact=True).click()
        await expect(self.page.get_by_role('dialog', name='历史记录', exact=True)).to_be_hidden()

    async def open_chat(self):
        await self.page.goto(self.origin + '/agents/assistant-1/chat')
        await expect(self.page.get_by_role('textbox', name='发送给助手的消息')).to_be_enabled()

    async def ask(self, text):
        await self.page.get_by_role('textbox', name='发送给助手的消息').fill(text)
        await self.page.get_by_role('button', name='发送', exact=True).click()

    async def test_list_use_starts_private_persistent_conversation_with_welcome(self):
        await self.page.goto(self.origin + '/agents')
        await expect(self.page.get_by_role('heading', name='智能助手', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='使用', exact=True).click()
        await expect(self.page.locator('.assistant-chat__welcome')).to_contain_text(self.config['welcome_message'])
        self.assertEqual(self.submissions, [])
        await self.ask('报销需要哪些材料？')
        await expect(self.page.locator('.assistant-chat__reply')).to_contain_text('制度答复')
        await self.open_history()
        await expect(self.page.get_by_role('region', name='助手历史对话')).to_contain_text('报销需要哪些材料？')
        await self.close_history()
        self.assertEqual(len(self.sessions), 1)
        await self.page.reload()
        await expect(self.page.locator('.assistant-chat__question')).to_contain_text('报销需要哪些材料？')
        await expect(self.page.locator('.assistant-chat__reply')).to_contain_text('制度答复')
        self.assertEqual(len(self.submissions), 1)

    async def test_new_conversation_and_history_switch_do_not_merge_messages(self):
        await self.open_chat()
        await self.ask('会话一的问题')
        await expect(self.page.locator('.assistant-chat__reply')).to_contain_text('制度答复')
        await self.page.get_by_role('button', name='新建对话', exact=True).click()
        await expect(self.page.locator('.assistant-chat__welcome')).to_be_visible()
        await expect(self.page.locator('.assistant-chat__question')).to_have_count(0)
        await self.ask('会话二的问题')
        await expect(self.page.locator('.assistant-chat__reply')).to_contain_text('制度答复')
        await self.open_history()
        nav = self.page.get_by_role('region', name='助手历史对话')
        await nav.get_by_role('button', name='会话一的问题', exact=True).click()
        await expect(self.page.locator('.assistant-chat__question')).to_have_text('会话一的问题')
        await expect(self.page.locator('.message-history')).not_to_contain_text('会话二的问题')
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))

    async def test_unknown_submission_recovers_original_request_after_reload(self):
        self.fail_submit = True
        await self.open_chat()
        await self.ask('只接纳一次')
        await expect(self.page.get_by_role('button', name='确认原提交')).to_be_enabled()
        await self.page.reload()
        await expect(self.page.get_by_role('button', name='确认原提交')).to_be_enabled()
        await self.page.get_by_role('button', name='确认原提交').click()
        await expect(self.page.get_by_role('button', name='确认原提交')).to_have_count(0)
        self.assertEqual(len(self.turns['session-1']), 1)
        self.assertEqual(self.submissions[0], self.submissions[1])

    async def test_refresh_observes_existing_run_without_resubmitting(self):
        self.completed = False
        self.hold_events = True
        await self.open_chat()
        await self.ask('继续观察')
        await expect(self.page.get_by_role('button', name='停止生成')).to_be_visible()
        await self.page.reload()
        await expect(self.page.get_by_role('button', name='停止生成')).to_be_visible()
        self.assertEqual(len(self.submissions), 1)
        self.completed = True
        self.release_stream.set()
        self.submit_gate.set()
        await expect(self.page.locator('.assistant-chat__reply')).to_contain_text('制度答复')

    async def test_draft_is_read_only_and_foreign_session_error_clears_display(self):
        self.agent.update(status='draft', published_version=None, published_config=None)
        await self.page.goto(self.origin + '/agents/assistant-1/chat')
        await expect(self.page.get_by_role('alert')).to_contain_text('助手尚未发布')
        await expect(self.page.get_by_role('textbox', name='发送给助手的消息')).to_be_disabled()
        self.agent.update(status='published', published_version=1, published_config=self.config.copy())
        await self.page.reload()
        await self.ask('PRIVATE QUESTION')
        await expect(self.page.locator('.assistant-chat__reply')).to_contain_text('制度答复')
        self.deny_detail = True
        await self.page.reload()
        await expect(self.page.get_by_role('alert')).to_contain_text('助手对话不存在')
        await expect(self.page.locator('.assistant-chat__question')).to_have_count(0)



    async def test_history_rename_delete_cancel_and_selected_session_reset(self):
        await self.open_chat()
        await self.ask('会话管理测试')
        await expect(self.page.locator('.assistant-chat__reply')).to_contain_text('制度答复')
        await self.open_history()
        row = self.page.locator('.assistant-session').filter(has=self.page.get_by_role('button', name='会话管理测试', exact=True))
        await row.hover()
        await row.get_by_role('button', name='更多操作').click()
        await self.page.get_by_role('menuitem', name='重命名', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='重命名会话', exact=True)
        await dialog.get_by_role('textbox', name='会话标题').fill('   ')
        await dialog.get_by_role('button', name='保存', exact=True).click()
        await expect(dialog.get_by_role('alert')).to_contain_text('标题需为')
        self.assertEqual(self.renames, [])
        await dialog.get_by_role('textbox', name='会话标题').fill('科研记录')
        await dialog.get_by_role('button', name='保存', exact=True).click()
        await expect(dialog).to_be_hidden()
        await expect(self.page.locator('.assistant-session')).to_contain_text('科研记录')
        await self.close_history()
        await expect(self.page.locator('.chat-session-heading')).to_have_text('科研记录')
        await self.page.reload()
        await expect(self.page.locator('.chat-session-heading')).to_have_text('科研记录')
        await self.open_history()
        row = self.page.locator('.assistant-session')
        await row.hover()
        await row.get_by_role('button', name='更多操作').click()
        await self.page.get_by_role('menuitem', name='删除', exact=True).click()
        confirm = self.page.locator('.ant-modal-confirm')
        await confirm.get_by_role('button', name=re.compile(r'取\s*消')).click()
        self.assertEqual(self.deleted, [])
        await row.get_by_role('button', name='更多操作').click()
        await self.page.get_by_role('menuitem', name='删除', exact=True).click()
        await confirm.get_by_role('button', name=re.compile(r'确\s*定')).click()
        await expect(self.page.locator('.assistant-session')).to_have_count(0)
        await self.close_history()
        await expect(self.page.locator('.assistant-chat__question')).to_have_count(0)
        await expect(self.page.locator('.assistant-chat__welcome')).to_be_visible()
        self.assertEqual(self.deleted, ['session-1'])
        await self.page.reload()
        await self.open_history()
        await expect(self.page.get_by_role('region', name='助手历史对话')).to_contain_text('暂无会话')



    async def test_welcome_uses_the_same_left_markdown_message_ui(self):
        self.config['welcome_message'] = '**科研助理**\n\n- 帮你梳理研究问题'
        self.agent['published_config'] = self.config.copy()
        await self.open_chat()
        welcome = self.page.locator('.assistant-chat__welcome')
        await expect(welcome.locator('.ai-message-container')).to_be_visible()
        await expect(welcome.locator('strong')).to_have_text('科研助理')
        await expect(welcome.locator('li')).to_contain_text('帮你梳理研究问题')
        self.assertEqual(self.submissions, [])
        await self.ask('开始研究')
        await expect(self.page.locator('.assistant-chat__reply')).to_contain_text('制度答复')
        await expect(welcome).to_be_visible()
        greeting = await welcome.bounding_box()
        reply = await self.page.locator('.assistant-chat__reply').bounding_box()
        self.assertAlmostEqual(greeting['x'], reply['x'], delta=1)
        fonts = await self.page.locator('.message-row.assistant .ai-message-content').evaluate_all('(nodes) => nodes.map(n => getComputedStyle(n).fontSize)')
        self.assertEqual(len(set(fonts)), 1)
        typography = await self.page.locator('.assistant-chat__welcome .ai-message-content, .assistant-chat__reply .ai-message-content, .user-message, .composer textarea').evaluate_all(
            '(nodes) => nodes.map(n => { const s = getComputedStyle(n); return {family:s.fontFamily, size:s.fontSize, line:s.lineHeight}; })')
        self.assertEqual(len(typography), 4)
        self.assertTrue(all(style == typography[0] for style in typography), typography)
        self.assertEqual(typography[0]['size'], '17px')
        await self.page.goto(self.origin + '/chat')
        await expect(self.page.locator('.platform-chat .composer textarea')).to_be_visible()
        ordinary = await self.page.locator('.platform-chat .composer textarea').evaluate(
            '(n) => {const s=getComputedStyle(n);return {family:s.fontFamily,size:s.fontSize,line:s.lineHeight};}')
        self.assertEqual(typography[0], ordinary)

    async def test_question_is_immediate_and_retrieval_stays_in_reply_after_reload(self):
        self.knowledge_mode = True
        self.submit_gate.clear()
        await self.open_chat()
        await self.ask('请查一下相关制度')
        await asyncio.wait_for(self.submission_received.wait(), timeout=5)
        await expect(self.page.get_by_role('textbox', name='发送给助手的消息')).to_have_value('')
        await expect(self.page.locator('.assistant-chat__question')).to_have_text('请查一下相关制度')
        await expect(self.page.locator('.assistant-chat__pending')).to_contain_text('正在提交')
        await expect(self.page.locator('.composer')).not_to_contain_text('正在提交')
        self.submit_gate.set()
        await expect(self.page.locator('.assistant-chat__pending')).to_contain_text('正在检索知识库')
        await expect(self.page.locator('.composer')).not_to_contain_text('检索')
        self.assertEqual(self.turns['session-1'], [])
        await self.page.evaluate('sessionStorage.clear()')
        await self.page.reload()
        await expect(self.page.locator('.assistant-chat__question')).to_have_text('请查一下相关制度')
        await expect(self.page.locator('.assistant-chat__pending')).to_contain_text('正在检索知识库')
        self.assertEqual(len(self.submissions), 1)
        self.preparation_state = 'admitted'
        await expect(self.page.locator('.assistant-chat__reply')).to_contain_text('制度答复')
        await expect(self.page.locator('.assistant-chat__pending')).to_have_count(0)
        await expect(self.page.locator('.assistant-chat__question')).to_have_count(1)
        self.assertEqual(len(self.submissions), 1)

    async def test_retrieval_failure_is_shown_beside_the_question(self):
        self.knowledge_mode = True
        await self.open_chat()
        await self.ask('检索失败场景')
        await expect(self.page.locator('.assistant-chat__pending')).to_contain_text('正在检索知识库')
        self.preparation_state = 'failed'
        await expect(self.page.locator('.assistant-chat__pending').get_by_role('alert')).to_contain_text('检索服务临时失败')
        await expect(self.page.locator('.assistant-chat__question')).to_have_text('检索失败场景')
        await expect(self.page.get_by_role('textbox', name='发送给助手的消息')).to_have_value('')
        await expect(self.page.get_by_role('textbox', name='发送给助手的消息')).to_be_enabled()
        await expect(self.page.locator('.composer')).not_to_contain_text('检索服务临时失败')

    async def test_stopping_retrieval_updates_the_assistant_message(self):
        self.knowledge_mode = True
        await self.open_chat()
        await self.ask('停止检索场景')
        await expect(self.page.locator('.assistant-chat__pending')).to_contain_text('正在检索知识库')
        await self.page.get_by_role('button', name='停止生成', exact=True).click()
        await expect(self.page.locator('.assistant-chat__pending')).to_contain_text('已停止检索')
        await expect(self.page.get_by_role('textbox', name='发送给助手的消息')).to_have_value('')
        await expect(self.page.get_by_role('textbox', name='发送给助手的消息')).to_be_enabled()
        self.assertEqual(len(self.submissions), 1)


if __name__ == '__main__':
    import os
    os.environ.setdefault('PLATFORM_BROWSER_URL', 'http://127.0.0.1:19080')
    unittest.main()
