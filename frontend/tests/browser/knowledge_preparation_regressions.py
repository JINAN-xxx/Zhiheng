"""Knowledge preparation remains recoverable for assistants and Work."""
import asyncio
import unittest
from urllib.parse import parse_qs, urlparse
from playwright.async_api import expect
from knowledge_regressions import STAMP
from agents_regressions import AgentsUIRegressions
from work_regressions import WorkUIRegressions


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
        elif path == '/api/agents/agent-1/api-access':
            config = self.agent
            await route.fulfill(json={'application_id': config['application_id'],
                'revision': self.agent['version'],
                'model_ids': [config['model_id'], 'embed-1', 'rank-1']})
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
        await self.choose_agent_action('调试配置')
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await drawer.get_by_role('textbox', name='调试消息', exact=True).fill('工作时间？')
        await drawer.get_by_role('button', name='发送', exact=True).click()
        await expect(drawer.get_by_text('正在检索知识库', exact=True)).to_be_visible()
        await drawer.get_by_role('button', name='停止生成', exact=True).click()
        await expect(drawer.get_by_text('已停止', exact=True)).to_be_visible()
        await drawer.get_by_role('button', name='关闭调试', exact=True).click()
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
        self.agent.update({'status': 'enabled', 'application_id': 'draft-app', 'knowledge_base_ids': ['draft-base'], **{**self.config, 'model_id': 'published-model', 'application_id': 'app-1', 'knowledge_base_ids': ['base-1']}})
        await self.open_agents()
        await self.choose_agent_action('API')
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await drawer.get_by_role('button', name='创建密钥', exact=True).click()
        await expect(drawer.get_by_text('密钥仅显示一次', exact=True)).to_be_visible()
        self.assertEqual(self.posts[-1]['model_ids'], ['published-model', 'embed-1', 'rank-1'])
        await expect(drawer.get_by_text('202', exact=False).first).to_be_visible()


class WorkKnowledgePreparation(WorkUIRegressions):
    """Historical relay preparations remain readable without reviving the retired engine."""
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.task.update(mode='legacy', configuration=None, model_id=None,
                         preparation_id='prep-work', current_run_id=None)
        self.legacy_requests=[]
        self.page.on('request',lambda request:self.legacy_requests.append(urlparse(request.url).path))

    async def test_legacy_preparation_history_is_read_only(self):
        await self.open_task()
        await expect(self.page.get_by_text('历史任务仅供查看，新建任务可自动规划并处理资料。',exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button',name='停止',exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('textbox',name='工作消息',exact=True)).to_have_count(0)
        self.assertFalse(any('/knowledge/preparations/' in path for path in self.legacy_requests))

    async def test_reloading_history_does_not_resume_legacy_preparation(self):
        await self.open_task()
        await self.page.reload()
        await expect(self.page.get_by_text('历史任务仅供查看，新建任务可自动规划并处理资料。',exact=True)).to_be_visible()
        self.assertFalse(any('/knowledge/preparations/' in path for path in self.legacy_requests))
        self.assertEqual(self.runs,[])

    async def test_legacy_run_does_not_automatically_restore_old_executor(self):
        self.task['current_run_id']='old-run'
        await self.open_task()
        await expect(self.page.get_by_role('button',name='停止',exact=True)).to_have_count(0)
        self.assertFalse(any('/runs/old-run' in path for path in self.legacy_requests))

    async def test_new_task_uses_workspace_options_without_knowledge_retrieval(self):
        self.task.update(mode='agent', model_id='model-1', preparation_id=None,
                         configuration={'model_id':'model-1','skill_slugs':[]})
        await self.open_task()
        text=self.page.get_by_role('textbox',name='工作消息',exact=True)
        await text.fill('分析这些资料')
        await self.page.get_by_role('button',name='发送',exact=True).click()
        await expect(self.page.get_by_text('核实后的结论',exact=True)).to_be_visible()
        self.assertFalse(any(path.startswith('/api/knowledge/') for path in self.legacy_requests))


def load_tests(loader, tests, pattern):
    names = [
        (AgentKnowledgePreparation, 'test_preview_observes_and_cancels_knowledge_preparation'),
        (AgentKnowledgePreparation, 'test_editor_loads_knowledge_options_for_saved_agent_owner'),
        (AgentKnowledgePreparation, 'test_api_key_includes_published_knowledge_embedding_model_grants'),
        (WorkKnowledgePreparation, 'test_legacy_preparation_history_is_read_only'),
        (WorkKnowledgePreparation, 'test_reloading_history_does_not_resume_legacy_preparation'),
        (WorkKnowledgePreparation, 'test_legacy_run_does_not_automatically_restore_old_executor'),
        (WorkKnowledgePreparation, 'test_new_task_uses_workspace_options_without_knowledge_retrieval'),
    ]
    return unittest.TestSuite(case(name) for case, name in names)


if __name__ == '__main__':
    unittest.main(verbosity=2)
