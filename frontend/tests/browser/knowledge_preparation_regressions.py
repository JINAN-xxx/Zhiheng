"""Durable retrieval preparation must not be treated as an already admitted Run."""
import asyncio
import json
import unittest
from urllib.parse import parse_qs, urlparse
from playwright.async_api import expect
from chat_files_regressions import ChatFilesUIRegressions
from knowledge_regressions import STAMP
from agents_regressions import AgentsUIRegressions
from work_regressions import WorkUIRegressions


class ChatKnowledgePreparation(ChatFilesUIRegressions):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        # A complete base DTO is shared with the knowledge-page contract fixture.
        self.base = {'id': 'base-1', 'name': '业务知识', 'description': '业务资料', 'owner_id': 'user-1', 'access': 'private', 'version': 1, 'acl_version': 1, 'embed_model_id': 'embed-1', 'chunk_size': 1000, 'chunk_overlap': 200, 'active_revision': 'revision-1', 'active_job_id': None, 'index_status': 'ready', 'document_count': 1, 'last_error': None, 'enable_knowledge_graph': False, 'llm_model_id': None, 'graph_status': 'disabled', 'created_at': STAMP, 'updated_at': STAMP, 'can_manage': True}
        self.preparation = {'kind': 'knowledge_preparation', 'id': 'prep-chat', 'resource_type': 'chat', 'resource_id': 'c1', 'status': 'waiting_models', 'phase': 'embedding', 'run': None, 'hits': [], 'error': None, 'created_at': STAMP, 'updated_at': STAMP}
        self.release = False
        self.cancels = 0
        self.accepted = False
        self.run = {'id': 'knowledge-run', 'resource_type': 'chat', 'resource_id': 'c1', 'status': 'completed', 'attempt_id': 'a1', 'attempt_no': 1, 'revision': 3, 'projection_seq': 1, 'text': '根据业务知识回答。', 'reasoning': '', 'error': None, 'created_at': STAMP, 'usage': {'input_tokens': 20, 'output_tokens': 10, 'status': 'known'}}

    async def respond(self, route):
        path, method = urlparse(route.request.url).path, route.request.method
        if path == '/api/knowledge/available':
            await route.fulfill(json=[self.base])
        elif path == '/api/chat/conversations/c1/runs':
            self.changes.append(route.request.post_data_json)
            self.conversation['preparation_id'] = 'prep-chat'
            await route.fulfill(status=202, json=self.preparation)
        elif path == '/api/knowledge/preparations/prep-chat':
            if self.release and self.preparation['status'] != 'cancelled':
                self.preparation.update(status='admitted', phase='admitted', run=self.run)
                self.conversation['preparation_id'] = None
                self.accepted = True
            await route.fulfill(json=self.preparation)
        elif path == '/api/knowledge/preparations/prep-chat/cancel':
            self.cancels += 1
            self.preparation.update(status='cancelled', phase='cancelled')
            self.conversation['preparation_id'] = None
            await route.fulfill(json=self.preparation)
        elif path == '/api/runs/knowledge-run':
            await route.fulfill(json=self.run)
        elif path == '/api/runs/knowledge-run/events':
            await route.fulfill(content_type='text/event-stream', body='event: snapshot\ndata: ' + json.dumps(self.run) + '\n\n')
        elif path == '/api/chat/conversations/c1/messages':
            await route.fulfill(json=[] if not self.accepted else [{'id': 'answer-1', 'role': 'assistant', 'content': self.run['text'], 'reasoning': '', 'run_id': self.run['id'], 'created_at': STAMP, 'attachments': [], 'skill_snapshots': [], 'knowledge_sources': []}])
        else:
            await super().respond(route)

    async def start_preparation(self, wait_for_phase=True):
        await self.page.goto(self.origin + '/chat')
        await self.select_conversation('附件会话')
        await self.page.get_by_role('button', name='选择知识库', exact=True).click()
        await self.page.get_by_role('button', name='选择知识库：业务知识', exact=True).click()
        await self.page.get_by_role('button', name='完成选择知识库', exact=True).click()
        await self.page.get_by_role('textbox', name='消息内容', exact=True).fill('工作时间？')
        await self.page.get_by_role('button', name='发送消息', exact=True).click()
        if wait_for_phase:
            await expect(self.page.locator('.run-state')).to_have_text('正在检索知识库')

    async def test_preparation_blocks_resend_and_restores_after_reload(self):
        await self.start_preparation()
        self.assertEqual(self.changes[0]['knowledge_base_ids'], ['base-1'])
        await expect(self.page.get_by_role('button', name='选择知识库', exact=True)).to_be_disabled()
        await self.page.reload()
        await self.select_conversation('附件会话')
        await expect(self.page.locator('.run-state')).to_have_text('正在检索知识库')
        self.release = True
        await expect(self.page.get_by_text('根据业务知识回答。', exact=True)).to_be_visible()
        self.assertEqual(len(self.changes), 1)
        self.assertEqual(self.cancels, 0)

    async def test_explicit_stop_cancels_preparation_without_run_cancel_endpoint(self):
        await self.start_preparation()
        await self.page.get_by_role('button', name='停止生成', exact=True).click()
        await expect(self.page.get_by_role('button', name='停止生成', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button', name='发送消息', exact=True)).to_be_enabled()
        self.assertEqual(self.cancels, 1)
        self.assertEqual(len(self.changes), 1)

    async def test_refresh_recovers_preparation_after_a_lost_admission_response(self):
        async def lost(route):
            self.changes.append(route.request.post_data_json)
            self.conversation['preparation_id'] = 'prep-chat'
            await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '接纳响应丢失'}})
        await self.page.route(self.origin + '/api/chat/conversations/c1/runs', lost)
        await self.start_preparation(wait_for_phase=False)
        await expect(self.page.get_by_text('接纳响应丢失', exact=True)).to_be_visible()
        dialog = await self.open_history()
        await dialog.get_by_role('button', name='刷新会话', exact=True).click()
        await self.close_history()
        await expect(self.page.locator('.run-state')).to_have_text('正在检索知识库')
        await expect(self.page.get_by_role('button', name='使用原请求重试', exact=True)).to_have_count(0)
        self.release = True
        await expect(self.page.get_by_text('根据业务知识回答。', exact=True)).to_be_visible()
        self.assertEqual(len(self.changes), 1)

    async def test_first_preparation_read_failure_does_not_leave_recovered_chat_locked(self):
        self.conversation['preparation_id'] = 'prep-chat'
        reads = []
        async def observe(route):
            reads.append(route.request.url)
            if len(reads) == 1:
                await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '准备读取失败'}})
            else:
                self.accepted = True
                self.preparation.update(status='admitted', phase='admitted', run=self.run)
                self.conversation['preparation_id'] = None
                await route.fulfill(json=self.preparation)
        await self.page.route(self.origin + '/api/knowledge/preparations/prep-chat', observe)
        await self.page.goto(self.origin + '/chat')
        await self.select_conversation('附件会话')
        await expect(self.page.get_by_text('准备读取失败', exact=True)).to_be_visible()
        dialog = await self.open_history()
        await dialog.get_by_role('button', name='刷新会话', exact=True).click()
        await self.close_history()
        await expect(self.page.get_by_text('根据业务知识回答。', exact=True)).to_be_visible()
        await self.page.get_by_role('textbox', name='消息内容', exact=True).fill('下一条问题')
        await expect(self.page.get_by_role('button', name='发送消息', exact=True)).to_be_enabled()
        self.assertEqual(self.changes, [])

    async def test_lost_preparation_stop_continues_to_the_admitted_main_run(self):
        self.run.update(status='running', text='', revision=2)
        async def unreadable(route):
            await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '准备观察中断'}})
        await self.page.route(self.origin + '/api/knowledge/preparations/prep-chat', unreadable)
        await self.page.route(self.origin + '/api/knowledge/preparations/prep-chat/cancel', lambda route: route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '取消接纳响应丢失'}}))
        stopped = asyncio.Event()
        calls = []
        async def stop_main(route):
            calls.append(route.request.url)
            self.run.update(status='cancelled', revision=9)
            await route.fulfill(json=self.run)
            stopped.set()
        await self.page.route(self.origin + '/api/runs/knowledge-run/cancel', stop_main)
        await self.start_preparation()
        await expect(self.page.get_by_text('准备观察中断', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='停止生成', exact=True).click()
        await expect(self.page.get_by_text('取消接纳响应丢失', exact=True)).to_be_visible()
        await self.page.unroute(self.origin + '/api/knowledge/preparations/prep-chat', unreadable)
        self.release = True
        await self.page.get_by_role('button', name='恢复查看', exact=True).click()
        try:
            await asyncio.wait_for(stopped.wait(), timeout=4)
        except TimeoutError:
            self.fail('恢复后未继续停止已接纳的主运行')
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.changes), 1)

    async def test_lost_stop_retries_preparation_cancel_while_still_waiting(self):
        async def unreadable(route):
            await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '准备观察中断'}})
        calls, confirmed = [], asyncio.Event()
        async def cancel(route):
            calls.append(route.request.url)
            if len(calls) == 1:
                await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '停止响应丢失'}})
            else:
                self.preparation.update(status='cancelled', phase='cancelled')
                self.conversation['preparation_id'] = None
                await route.fulfill(json=self.preparation)
                confirmed.set()
        await self.page.route(self.origin + '/api/knowledge/preparations/prep-chat', unreadable)
        await self.page.route(self.origin + '/api/knowledge/preparations/prep-chat/cancel', cancel)
        await self.start_preparation()
        await expect(self.page.get_by_text('准备观察中断', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='停止生成', exact=True).click()
        await expect(self.page.get_by_text('停止响应丢失', exact=True)).to_be_visible()
        await self.page.unroute(self.origin + '/api/knowledge/preparations/prep-chat', unreadable)
        await self.page.get_by_role('button', name='恢复查看', exact=True).click()
        try:
            await asyncio.wait_for(confirmed.wait(), timeout=3)
        except TimeoutError:
            self.fail('准备仍等待时未继续确认停止操作')
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(self.changes), 1)


class AgentKnowledgePreparation(AgentsUIRegressions):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.config['knowledge_base_ids'] = ['base-1']
        self.agent['knowledge_base_ids'] = ['base-1']
        self.preparation = {'kind': 'knowledge_preparation', 'id': 'prep-agent', 'resource_type': 'agent_preview', 'resource_id': 'agent-1', 'status': 'waiting_models', 'phase': 'embedding', 'run': None, 'hits': [], 'error': None, 'created_at': STAMP, 'updated_at': STAMP}
        self.preparation_cancels = 0
        self.knowledge_queries = []

    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path == '/api/knowledge/available':
            self.knowledge_queries.append(parse_qs(urlparse(route.request.url).query))
            await route.fulfill(json=[{'id': 'base-1', 'name': '业务知识', 'description': '', 'owner_id': self.agent['owner_id'], 'access': 'private', 'version': 1, 'acl_version': 1, 'embed_model_id': 'embed-1', 'chunk_size': 1000, 'chunk_overlap': 200, 'active_revision': 'revision-1', 'active_job_id': None, 'index_status': 'ready', 'document_count': 1, 'last_error': None, 'enable_knowledge_graph': False, 'llm_model_id': None, 'graph_status': 'disabled', 'created_at': STAMP, 'updated_at': STAMP, 'can_manage': True}])
        elif path == '/api/agents/agent-1/preview':
            self.previews.append(route.request.post_data_json)
            await route.fulfill(status=202, json=self.preparation)
        elif path == '/api/knowledge/preparations/prep-agent':
            await route.fulfill(json=self.preparation)
        elif path == '/api/knowledge/preparations/prep-agent/cancel':
            self.preparation_cancels += 1
            self.preparation.update(status='cancelled', phase='cancelled')
            await route.fulfill(json=self.preparation)
        else:
            await super().respond(route)

    async def test_preview_observes_and_cancels_knowledge_preparation(self):
        await self.open_agents()
        await self.page.get_by_role('button', name='试聊', exact=True).click()
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await drawer.get_by_role('textbox', name='试聊消息', exact=True).fill('工作时间？')
        await drawer.get_by_role('button', name='发送', exact=True).click()
        await expect(drawer.get_by_text('正在检索知识库', exact=True)).to_be_visible()
        await drawer.get_by_role('button', name='停止生成', exact=True).click()
        await expect(drawer.get_by_text('已停止', exact=True)).to_be_visible()
        await drawer.get_by_role('button', name='关闭试聊', exact=True).click()
        await expect(drawer).not_to_be_visible()
        self.assertEqual(self.preparation_cancels, 1)
        self.assertEqual(len(self.previews), 1)

    async def test_editor_loads_knowledge_options_for_saved_agent_owner(self):
        self.agent['owner_id'] = 'resource-owner'
        await self.open_agents()
        await self.choose_agent_action('配置')
        await expect(self.page.locator('.agent-editor--page').get_by_text('业务知识', exact=True)).to_be_visible()
        self.assertEqual(self.knowledge_queries, [{'agent_id': ['agent-1']}])

    async def test_api_key_includes_published_knowledge_embedding_model_grants(self):
        self.agent.update(status='published', application_id='draft-app', knowledge_base_ids=['draft-base'], published_version=1, published_config={**self.config, 'model_id': 'published-model', 'application_id': 'app-1', 'knowledge_base_ids': ['base-1']})
        await self.open_agents()
        await self.choose_agent_action('API')
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await drawer.get_by_role('button', name='创建密钥', exact=True).click()
        await expect(drawer.get_by_text('密钥仅显示一次', exact=True)).to_be_visible()
        self.assertEqual(self.posts[-1]['model_ids'], ['published-model', 'embed-1'])
        await expect(drawer.get_by_text('202', exact=False).first).to_be_visible()


class WorkKnowledgePreparation(WorkUIRegressions):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.task['preparation_id'] = 'prep-work'
        self.preparation = {'kind': 'knowledge_preparation', 'id': 'prep-work', 'resource_type': 'work_turn', 'resource_id': 'task-1', 'status': 'waiting_models', 'phase': 'embedding', 'run': None, 'hits': [], 'error': None, 'created_at': STAMP, 'updated_at': STAMP}
        self.preparation_cancels = 0

    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path == '/api/knowledge/preparations/prep-work':
            await route.fulfill(json=self.preparation)
        elif path == '/api/knowledge/preparations/prep-work/cancel':
            self.preparation_cancels += 1
            self.preparation.update(status='cancelled', phase='cancelled')
            self.task.update(preparation_id=None, version=self.task['version'] + 1)
            await route.fulfill(json=self.preparation)
        else:
            await super().respond(route)

    async def test_known_preparation_restores_task_lock_and_can_cancel(self):
        await self.open_task()
        await expect(self.page.get_by_text('正在检索知识库', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('textbox', name='工作消息', exact=True)).to_be_disabled()
        await expect(self.page.get_by_role('button', name='编辑工作信息', exact=True)).to_be_disabled()
        await self.page.reload()
        await expect(self.page.get_by_text('正在检索知识库', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='停止', exact=True).click()
        await expect(self.page.get_by_role('textbox', name='工作消息', exact=True)).to_be_enabled()
        self.assertEqual(self.preparation_cancels, 1)
        self.assertEqual(self.runs, [])

    async def test_lost_preparation_stop_continues_after_task_run_handoff(self):
        await self.open_task()
        await expect(self.page.get_by_text('正在检索知识库', exact=True)).to_be_visible()
        await self.page.route(self.origin + '/api/knowledge/preparations/prep-work', lambda route: route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '工作准备观察中断'}}))
        await self.page.route(self.origin + '/api/knowledge/preparations/prep-work/cancel', lambda route: route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '工作取消接纳响应丢失'}}))
        await expect(self.page.get_by_text('工作准备观察中断', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='停止', exact=True).click()
        await expect(self.page.get_by_text('工作取消接纳响应丢失', exact=True)).to_be_visible()
        self.task.update(preparation_id=None, current_run_id='work-run', version=2)
        self.run.update(status='running', text='', revision=2)
        stopped = asyncio.Event()
        calls = []
        async def stop_main(route):
            calls.append(route.request.url)
            self.run.update(status='cancelled', revision=9)
            self.task.update(current_run_id=None, version=3)
            await route.fulfill(json=self.run)
            stopped.set()
        await self.page.route(self.origin + '/api/work/tasks/task-1/runs/work-run/cancel', stop_main)
        await self.page.get_by_role('button', name='恢复查看', exact=True).click()
        try:
            await asyncio.wait_for(stopped.wait(), timeout=4)
        except TimeoutError:
            self.fail('恢复后未继续停止已接纳的主运行')
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.runs, [])

    async def test_known_preparation_first_read_failure_has_a_recovery_action(self):
        reads = []
        async def observe(route):
            reads.append(route.request.url)
            if len(reads) == 1:
                await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '工作准备读取失败'}})
            else:
                await route.fulfill(json=self.preparation)
        await self.page.route(self.origin + '/api/knowledge/preparations/prep-work', observe)
        await self.open_task()
        await expect(self.page.get_by_text('工作准备读取失败', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='恢复查看', exact=True).click()
        await expect(self.page.get_by_text('工作准备读取失败', exact=True)).not_to_be_visible()
        await self.page.get_by_role('button', name='停止', exact=True).click()
        await expect(self.page.get_by_role('textbox', name='工作消息', exact=True)).to_be_enabled()
        self.assertGreaterEqual(len(reads), 2)
        self.assertEqual(self.runs, [])

    async def test_lost_stop_retries_preparation_cancel_before_task_run_exists(self):
        await self.open_task()
        await expect(self.page.get_by_text('正在检索知识库', exact=True)).to_be_visible()
        async def unreadable(route):
            await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '工作准备观察中断'}})
        calls, confirmed = [], asyncio.Event()
        async def cancel(route):
            calls.append(route.request.url)
            if len(calls) == 1:
                await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '工作停止响应丢失'}})
            else:
                self.preparation.update(status='cancelled', phase='cancelled')
                self.task.update(preparation_id=None, version=2)
                await route.fulfill(json=self.preparation)
                confirmed.set()
        await self.page.route(self.origin + '/api/knowledge/preparations/prep-work', unreadable)
        await self.page.route(self.origin + '/api/knowledge/preparations/prep-work/cancel', cancel)
        await expect(self.page.get_by_text('工作准备观察中断', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='停止', exact=True).click()
        await expect(self.page.get_by_text('工作停止响应丢失', exact=True)).to_be_visible()
        await self.page.unroute(self.origin + '/api/knowledge/preparations/prep-work', unreadable)
        await self.page.get_by_role('button', name='恢复查看', exact=True).click()
        try:
            await asyncio.wait_for(confirmed.wait(), timeout=3)
        except TimeoutError:
            self.fail('工作准备仍等待时未继续确认停止操作')
        await expect(self.page.get_by_role('textbox', name='工作消息', exact=True)).to_be_enabled()
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.runs, [])


def load_tests(loader, tests, pattern):
    names = [
        (ChatKnowledgePreparation, 'test_preparation_blocks_resend_and_restores_after_reload'),
        (ChatKnowledgePreparation, 'test_explicit_stop_cancels_preparation_without_run_cancel_endpoint'),
        (ChatKnowledgePreparation, 'test_refresh_recovers_preparation_after_a_lost_admission_response'),
        (ChatKnowledgePreparation, 'test_first_preparation_read_failure_does_not_leave_recovered_chat_locked'),
        (ChatKnowledgePreparation, 'test_lost_preparation_stop_continues_to_the_admitted_main_run'),
        (ChatKnowledgePreparation, 'test_lost_stop_retries_preparation_cancel_while_still_waiting'),
        (AgentKnowledgePreparation, 'test_preview_observes_and_cancels_knowledge_preparation'),
        (AgentKnowledgePreparation, 'test_editor_loads_knowledge_options_for_saved_agent_owner'),
        (AgentKnowledgePreparation, 'test_api_key_includes_published_knowledge_embedding_model_grants'),
        (WorkKnowledgePreparation, 'test_known_preparation_restores_task_lock_and_can_cancel'),
        (WorkKnowledgePreparation, 'test_lost_preparation_stop_continues_after_task_run_handoff'),
        (WorkKnowledgePreparation, 'test_known_preparation_first_read_failure_has_a_recovery_action'),
        (WorkKnowledgePreparation, 'test_lost_stop_retries_preparation_cancel_before_task_run_exists'),
    ]
    return unittest.TestSuite(case(name) for case, name in names)


if __name__ == '__main__':
    unittest.main(verbosity=2)
