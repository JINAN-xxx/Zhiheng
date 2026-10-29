"""Control-console integration checks with isolated HTTP boundary fixtures."""
import os
import asyncio
import re
import unittest
from urllib.parse import urlparse
from playwright.async_api import async_playwright, expect
from developer_regressions import ChatHistoryInteractions

class ConsoleUIRegressions(ChatHistoryInteractions, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.context = await self.browser.new_context(viewport={'width': 1440, 'height': 1000}, permissions=['clipboard-read', 'clipboard-write'])
        self.page = await self.context.new_page()
        self.origin = os.environ.get('PLATFORM_BROWSER_URL', 'http://127.0.0.1:15210')
        self.admin = True
        self.fail_knowledge = False
        self.chat_history = False
        self.writes = []
        self.errors = []
        self.unexpected = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        await self.page.route(self.origin + '/api/**', self.respond)

    async def asyncTearDown(self):
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()

    async def respond(self, route):
        path = urlparse(route.request.url).path
        if route.request.method != 'GET':
            self.writes.append(path)
            await route.fulfill(status=500, json={'error': {'code': 'unexpected_write', 'message': path}})
            return
        config = {'name': '核对助手', 'description': '真实接口夹具', 'model_id': 'm1', 'system_prompt': '核对内容', 'welcome_message': '', 'skill_slugs': [], 'knowledge_base_ids': [], 'application_id': None, 'temperature': 0.7, 'max_tokens': 512}
        agent = {**config, 'id': 'a1', 'owner_id': 'u1', 'version': 1, 'status': 'draft', 'published_version': None, 'published_config': None}
        skill = {'id': 's1', 'slug': 'review', 'name': '核对技能', 'description': '按原文核对', 'package_version': '1.0.0', 'version': 1, 'status': 'enabled', 'source': 'builtin', 'revision_id': 'rev1', 'content_hash': 'hash', 'file_count': 1, 'total_bytes': 123}
        kb = {'id': 'k1', 'name': '核对知识库', 'description': '', 'owner_id': 'u1', 'access': 'private', 'version': 1, 'acl_version': 1, 'embed_model_id': 'e1', 'chunk_size': 800, 'chunk_overlap': 100, 'active_revision': None, 'active_job_id': None, 'index_status': 'empty', 'document_count': 0, 'last_error': None, 'enable_knowledge_graph': False, 'llm_model_id': None, 'graph_status': 'disabled', 'created_at': '2026-10-28T01:00:00Z', 'updated_at': '2026-10-28T01:00:00Z', 'can_manage': True}
        if path == '/api/auth/me':
            body = {'id': 'u1', 'username': 'admin' if self.admin else 'member', 'is_admin': self.admin}
        elif path == '/api/models':
            body = [{'id': 'm1', 'name': '核对模型', 'connection_id': 'c1', 'upstream_model': 'test', 'max_output_tokens': 512, 'enabled': True, 'model_type': 'chat', 'published': True, 'vision_enabled': False, 'capabilities': ['chat', 'stream']}]
        elif path == '/api/models/connections':
            body = [{'id': 'c1', 'name': '核对连接', 'provider': 'openai_compatible', 'base_url': 'http://provider.example/v1', 'has_api_key': True, 'enabled': True}]
        elif path == '/api/agents':
            body = {'items': [agent], 'total': 2}
        elif path == '/api/skills':
            body = {'items': [skill], 'total': 4}
        elif path == '/api/knowledge/bases':
            if self.fail_knowledge:
                await route.fulfill(status=503, json={'error': {'code': 'temporarily_unavailable', 'message': '知识服务暂不可用'}})
                return
            body = [{**kb, 'id': 'k' + str(i), 'name': '核对知识库' + str(i)} for i in range(1, 4)]
        elif path == '/api/chat/conversations':
            body = [{'id': 'c1', 'title': '对话样例', 'created_at': '2026-10-28T01:00:00Z'}] if self.chat_history else []
        elif path == '/api/chat/conversations/c1/messages':
            body = [
                {'id': 'q1', 'role': 'user', 'content': '第一行问题\n第二行问题', 'run_id': None, 'created_at': '2026-10-28T01:00:00Z'},
                {'id': 'a1', 'role': 'assistant', 'content': '**完整回复**\n\n```text\n保留代码与换行\n```', 'reasoning': '不应混入复制正文的推理', 'run_id': None, 'created_at': '2026-10-28T01:00:01Z'}
            ]
        elif path == '/api/work/tasks':
            body = {'items': [], 'total': 0}
        elif path in ['/api/skills/available', '/api/knowledge/available', '/api/work/agents', '/api/work/available-agents', '/api/work/agents/available']:
            body = []
        else:
            self.unexpected.append(path)
            await route.fulfill(status=404, json={'error': {'code': 'fixture_missing', 'message': path}})
            return
        await route.fulfill(status=200, json=body)

    async def test_workspace_uses_real_counts_and_full_navigation(self):
        await self.page.goto(self.origin + '/overview')
        await expect(self.page.get_by_role('heading', name='工作台', exact=True)).to_be_visible(timeout=8000)
        for label, value in [('模型', '1'), ('智能体', '2'), ('知识库', '3'), ('技能', '4')]:
            await expect(self.page.get_by_role('link', name=label + '数量', exact=True)).to_contain_text(value)
        navigation = self.page.get_by_role('navigation', name='主导航', exact=True)
        for label in ['工作台', '对话体验', '模型目录', '智能体', '技能管理', '工作空间', '知识库', '开发接入', '用量与调用']:
            await expect(navigation.get_by_role('link', name=label, exact=True)).to_be_visible()
        await expect(navigation.get_by_role('link', name='文件管理', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='搜索资源', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='搜索资源', exact=True)
        await expect(dialog.get_by_role('textbox', name='搜索资源名称', exact=True)).to_be_focused()
        await expect(dialog.get_by_role('link', name='核对助手', exact=True)).to_be_visible()
        await expect(dialog.get_by_role('link', name='核对模型', exact=True)).to_be_visible()
        self.assertEqual(self.writes, [])
        self.assertEqual(self.errors, [])
        self.assertEqual(self.unexpected, [])

    async def test_workspace_handles_partial_failure_and_non_admin_navigation(self):
        self.admin = False
        self.fail_knowledge = True
        await self.page.goto(self.origin + '/overview')
        await expect(self.page.get_by_role('heading', name='工作台', exact=True)).to_be_visible(timeout=8000)
        await expect(self.page.get_by_role('link', name='知识库数量', exact=True)).to_contain_text('暂不可用')
        await expect(self.page.get_by_role('link', name='智能体数量', exact=True)).to_contain_text('2')
        navigation = self.page.get_by_role('navigation', name='主导航', exact=True)
        await expect(navigation.get_by_role('link', name='模型目录', exact=True)).to_have_count(0)
        await expect(navigation.get_by_role('link', name='文件管理', exact=True)).to_have_count(0)
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        await self.page.get_by_role('button', name='打开主菜单', exact=True).click()
        drawer = self.page.locator('.platform-mobile-drawer')
        await expect(drawer.get_by_role('link', name='知识库', exact=True)).to_be_visible()
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.assertEqual(self.writes, [])
        self.assertEqual(self.errors, [])
        self.assertEqual(self.unexpected, [])

    async def test_chat_messages_keep_both_roles_and_copy_raw_text(self):
        self.chat_history = True
        await self.page.goto(self.origin + '/chat')
        await self.select_conversation('对话样例')
        user = self.page.locator('.message-row.user')
        assistant = self.page.locator('.message-row.assistant')
        await expect(self.page.get_by_role('toolbar', name='回复操作', exact=True)).to_be_visible(timeout=8000)
        ua = await user.locator('.message-avatar').bounding_box()
        aa = await assistant.locator('.message-avatar').bounding_box()
        self.assertGreater(ua['x'], aa['x'] + 100)
        for label, text in [('复制提问', '第一行问题\n第二行问题'), ('复制回复', '**完整回复**\n\n```text\n保留代码与换行\n```')]:
            await self.page.get_by_role('button', name=label, exact=True).click()
            self.assertEqual(await self.page.evaluate('navigator.clipboard.readText()'), text)
        toolbar = self.page.get_by_role('toolbar', name='回复操作', exact=True)
        await expect(toolbar.get_by_role('button')).to_have_count(6)
        for label in ['重新生成', '赞同', '不赞同', '朗读', '分享']:
            await expect(toolbar.get_by_role('button', name=label, exact=True)).to_be_enabled()
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        await expect(self.page.get_by_role('dialog', name='历史记录', exact=True)).to_be_hidden()
        await self.page.get_by_role('button', name='复制回复', exact=True).click()
        self.assertEqual(await self.page.evaluate('navigator.clipboard.readText()'), '**完整回复**\n\n```text\n保留代码与换行\n```')
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.assertEqual(self.writes, [])
        self.assertEqual(self.errors, [])
        self.assertEqual(self.unexpected, [])

    async def test_work_history_reopens_after_mobile_to_desktop_resize(self):
        await self.page.set_viewport_size({'width': 375, 'height': 844})
        await self.page.goto(self.origin + '/work')
        history = self.page.get_by_role('complementary', name='工作任务列表', exact=True)
        await expect(history).to_be_hidden()
        await self.page.set_viewport_size({'width': 1440, 'height': 1000})
        await expect(history).to_be_visible(timeout=8000)
        await expect(history.get_by_role('button', name='新建工作', exact=True)).to_be_visible()
        self.assertEqual(self.writes, [])
        self.assertEqual(self.errors, [])
        self.assertEqual(self.unexpected, [])

    async def test_initial_navigation_waits_for_async_route_before_showing_links(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def hold_asset(route):
            started.set()
            await release.wait()
            await route.continue_()
        await self.page.route(re.compile(r'/(assets/ModelsView-[^/?]+\.js|src/modules/models/views/ModelsView\.vue)(?:\?.*)?$'), hold_asset)
        navigation = asyncio.create_task(self.page.goto(self.origin + '/models', wait_until='domcontentloaded'))
        try:
            await asyncio.wait_for(started.wait(), timeout=8)
            await expect(self.page.get_by_role('navigation', name='主导航', exact=True)).to_have_count(0, timeout=1000)
        finally:
            release.set()
            await navigation
        await expect(self.page.get_by_role('heading', name='模型目录', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('navigation', name='主导航', exact=True)).to_be_visible()
        self.assertEqual(self.errors, [])
        self.assertEqual(self.unexpected, [])

if __name__ == '__main__':
    unittest.main()
