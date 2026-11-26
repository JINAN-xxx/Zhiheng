"""Original agent editor, preview and API access against the new core contracts."""
import asyncio
import contextlib
import json
import re
import unittest
from urllib.parse import urlparse

from playwright.async_api import expect
from developer_regressions import DeveloperBoundary
from permission_fixture import granted_permissions


class AgentsUIRegressions(DeveloperBoundary):
    async def stop_then_leave(self, action, early=False):
        requested, release = asyncio.Event(), asyncio.Event()
        admission, release_admission = asyncio.Event(), asyncio.Event()
        calls = 0
        failures = []
        self.page.on('requestfailed', lambda request: failures.append(request.url) if request.url.endswith('/cancel') else None)
        async def cancel(route):
            nonlocal calls
            calls += 1
            requested.set()
            await release.wait()
            self.run.update(status='cancelled', revision=3)
            with contextlib.suppress(Exception): await route.fulfill(json=self.run)
        await self.page.route('**/api/runs/preview-run/cancel', cancel)
        if early:
            async def admit(route):
                admission.set()
                await release_admission.wait()
                with contextlib.suppress(Exception): await route.fulfill(status=202, json=self.run)
            await self.page.route('**/api/agents/agent-1/preview', admit)
        try:
            await self.open_agents()
            await self.choose_agent_action('调试')
            drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
            await drawer.get_by_role('textbox', name='调试消息', exact=True).fill('请回答')
            await drawer.get_by_role('button', name='发送', exact=True).click()
            if early:
                await asyncio.wait_for(admission.wait(), timeout=5)
                await drawer.get_by_role('button', name='停止生成', exact=True).click()
                release_admission.set()
            else:
                await expect(drawer.locator('.reasoning-section')).to_contain_text('先核实资料')
                await drawer.get_by_role('button', name='停止生成', exact=True).click()
            await asyncio.wait_for(requested.wait(), timeout=5)
            await drawer.get_by_role('button', name='关闭调试' if action == 'close' else '清空对话', exact=True).click()
            await self.page.wait_for_timeout(200)
            await expect(drawer).to_be_visible()
            await expect(drawer.locator('.reasoning-section')).to_contain_text('先核实资料')
            self.assertEqual(failures, [])
            release.set()
            if action == 'close': await expect(drawer).not_to_be_visible()
            else: await expect(drawer.locator('.reasoning-section')).to_have_count(0)
            self.assertEqual(calls, 1)
        finally:
            release.set()
            release_admission.set()

    async def test_close_waits_for_the_existing_stop_request(self):
        await self.stop_then_leave('close')

    async def test_clear_waits_for_the_existing_stop_request(self):
        await self.stop_then_leave('clear')

    async def test_early_stop_and_close_share_one_cancellation(self):
        await self.stop_then_leave('close', early=True)

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.config = {'name': '研究助手', 'description': '归纳研究重点', 'model_id': 'model-1', 'system_prompt': '草稿提示', 'welcome_message': '你好，我是助手', 'skill_slugs': [], 'knowledge_base_ids': [], 'application_id': None, 'temperature': 0.7, 'max_tokens': 512}
        self.agent = {**self.config, 'id': 'agent-1', 'owner_id': 'user-1', 'version': 1, 'status': 'disabled', 'created_at': '2026-10-23T00:00:00Z', 'updated_at': '2026-10-23T00:00:00Z'}
        self.agents = [self.agent]
        self.previews = []
        self.run = {'id': 'preview-run', 'resource_type': 'agent_preview', 'resource_id': 'agent-1', 'status': 'running', 'attempt_id': 'attempt-1', 'attempt_no': 1, 'revision': 2, 'projection_seq': 1, 'text': '', 'reasoning': '先核实资料', 'error': None, 'created_at': '2026-10-23T00:00:00Z', 'usage': {'input_tokens': None, 'output_tokens': None, 'status': 'pending'}}

    async def respond(self, route):
        path, method = urlparse(route.request.url).path, route.request.method
        if path == '/api/agents':
            if method == 'POST':
                body = route.request.post_data_json
                self.posts.append(body)
                self.agent.update(body)
                self.agents = [self.agent]
                await route.fulfill(status=201, json=self.agent)
            else:
                await route.fulfill(json={'items': self.agents, 'total': len(self.agents), 'page': 1, 'page_size': 12})
        elif path == '/api/agents/agent-1':
            if method == 'PATCH':
                self.posts.append(route.request.post_data_json)
                self.agent.update({key: value for key, value in route.request.post_data_json.items() if key != 'expected_version'})
                self.agent['version'] += 1
            if method == 'DELETE': self.agents = []
            await route.fulfill(json=self.agent)
        elif path.endswith('/agent-1/enable'):
            self.agent.update(status='enabled', version=self.agent['version'] + 1)
            await route.fulfill(json=self.agent)
        elif path.endswith('/agent-1/disable'):
            self.agent.update({'status': 'disabled', 'version': self.agent['version'] + 1})
            await route.fulfill(json=self.agent)
        elif path == '/api/agents/agent-1/preview':
            self.previews.append(route.request.post_data_json)
            await route.fulfill(status=202, json=self.run)
        elif path == '/api/runs/preview-run':
            await route.fulfill(json=self.run)
        elif path == '/api/runs/preview-run/events':
            await route.fulfill(content_type='text/event-stream', body='event: snapshot\ndata: ' + json.dumps(self.run) + '\n\n')
        elif path == '/api/runs/preview-run/cancel':
            self.run.update(status='cancelled', revision=3)
            await route.fulfill(json=self.run)
        elif path == '/api/skills/available':
            await route.fulfill(json=[])
        else:
            await super().respond(route)

    async def open_agents(self):
        await self.page.goto(self.origin + '/agents?tab=mine')
        await expect(self.page.get_by_role('heading', name='智能助手', exact=True)).to_be_visible()

    async def test_plaza_publish_action_is_separate_from_enable_permission(self):
        self.agent.update(status='enabled', version=5)
        permissions = list(granted_permissions('platform:agents:publish', 'platform:agents:debug'))
        calls = []

        async def who_am_i(route):
            await route.fulfill(json={
                'id': 'user-1', 'username': 'publisher', 'is_admin': False, 'permissions': permissions
            })

        async def publish(route):
            calls.append((route.request.method, route.request.post_data_json))
            self.agent.update(version=6, plaza_version=6)
            await route.fulfill(json=self.agent)

        await self.page.route('**/api/auth/me', who_am_i)
        await self.page.route('**/api/agents/agent-1/plaza', publish)
        await self.page.route('**/api/agents/plaza/departments', lambda route: route.fulfill(json=[]))
        await self.open_agents()
        await self.page.locator('.agent-card').get_by_role('button', name='更多操作').click()
        await expect(self.page.get_by_role('menuitem', name='调试')).to_be_visible()
        await expect(self.page.get_by_role('menuitem', name='上架广场')).to_have_count(0)

        permissions.extend(granted_permissions('platform:agents:plaza_publish'))
        self.agent['skill_slugs'] = ['personal-workflow']
        await self.open_agents()
        await self.choose_agent_action('上架广场')
        dialog = self.page.get_by_role('dialog')
        await expect(dialog.get_by_text('绑定的个人技能仅通过此助手使用，技能详情和文件仍为个人资源。')).to_be_visible()
        await dialog.get_by_role('button', name='上架广场', exact=True).click()
        await expect(self.page.get_by_text('已上架', exact=True)).to_be_visible()
        self.assertEqual(calls, [('PUT', {'expected_version': 5, 'department_id': None, 'allow_knowledge': False})])

    async def test_superadmin_can_unpublish_directly_from_plaza_card(self):
        published = {
            'id': 'other-assistant', 'name': '他人助手', 'description': '', 'welcome_message': '',
            'plaza_version': 7
        }
        rows, calls = [published], []

        async def list_plaza(route):
            await route.fulfill(json={'items': rows, 'total': len(rows)})

        async def remove_plaza(route):
            calls.append((route.request.method, route.request.post_data_json))
            rows.clear()
            await route.fulfill(status=204)

        await self.page.route('**/api/agents/plaza?*', list_plaza)
        await self.page.route('**/api/agents/other-assistant/plaza', remove_plaza)
        await self.page.goto(self.origin + '/agents?tab=plaza')
        await self.page.get_by_role('button', name='更多操作', exact=True).click()
        await self.page.get_by_role('menuitem', name='紧急下架').click()
        await self.page.get_by_role('dialog').get_by_role('button', name=re.compile(r'确\s*定')).click()
        await expect(self.page.get_by_text('暂无上架助手', exact=True)).to_be_visible()
        self.assertEqual(calls, [('DELETE', {'expected_version': 7})])

    async def test_plaza_card_opens_chat_with_public_metadata(self):
        detail = {'id': 'agent-1', 'name': '广场助手示例', 'description': '总结资料', 'welcome_message': '欢迎使用'}
        requests = []

        async def plaza(route):
            path = urlparse(route.request.url).path
            requests.append(path)
            if path == '/api/agents/plaza':
                await route.fulfill(json={'items': [detail], 'total': 1})
            elif path == '/api/agents/plaza/agent-1':
                await route.fulfill(json=detail)
            else:
                await route.fulfill(json=[])

        await self.page.route('**/api/agents/plaza**', plaza)
        await self.page.route('**/api/agents/agent-1/conversations?plaza=true', plaza)
        self.page.on('request', lambda request: requests.append(urlparse(request.url).path)
                     if urlparse(request.url).path == '/api/agents/agent-1' else None)
        await self.page.goto(self.origin + '/agents')
        await expect(self.page.get_by_role('tab')).to_have_text(['助手广场', '我的助手'])
        await expect(self.page.get_by_role('tab', name='助手广场')).to_have_attribute('aria-selected', 'true')
        await expect(self.page.get_by_role('button', name='新建智能助手')).to_have_count(0)
        await self.page.get_by_role('button', name='使用广场助手示例').click()
        await expect(self.page.get_by_role('heading', name='广场助手示例')).to_be_visible()
        await expect(self.page.locator('main.platform-page')).to_have_class(re.compile(r'\bis-conversation\b'))
        self.assertNotIn('/api/agents/agent-1', requests)
        await self.page.get_by_role('button', name='返回智能助手').click()
        await expect(self.page.get_by_role('tab', name='助手广场')).to_have_attribute('aria-selected', 'true')

    async def test_switching_agent_tabs_preserves_search_and_toolbar(self):
        paths = []
        detail = {'id': 'agent-1', 'name': '广场助手示例', 'description': '总结资料', 'welcome_message': ''}

        async def plaza(route):
            paths.append(urlparse(route.request.url).path)
            if paths[-1] == '/api/agents/plaza':
                await route.fulfill(json={'items': [detail], 'total': 1})
            else:
                await route.fulfill(json=[])

        await self.page.route('**/api/agents/plaza**', plaza)
        for width in (1440, 390):
            with self.subTest(width=width):
                paths.clear()
                await self.page.set_viewport_size({'width': width, 'height': 850})
                await self.open_agents()
                await expect(self.page.get_by_role('heading', name=self.agent['name'])).to_be_visible()
                toolbar = self.page.get_by_role('button', name='刷新智能助手')
                personal = self.page.get_by_role('textbox', name='搜索智能助手')
                original = await toolbar.bounding_box()
                personal_position = await personal.bounding_box()
                await self.page.get_by_role('tab', name='助手广场').click()
                await expect(self.page.get_by_role('button', name='使用广场助手示例')).to_be_visible()
                plaza_position = await toolbar.bounding_box()
                for axis in ('x', 'y'):
                    self.assertAlmostEqual(original[axis], plaza_position[axis], delta=1)
                plaza_search = self.page.get_by_role('textbox', name='搜索广场助手')
                panel = self.page.get_by_role('tabpanel', name='助手广场')
                await expect(panel.get_by_role('textbox', name='搜索广场助手')).to_be_visible()
                plaza_search_position = await plaza_search.bounding_box()
                for axis in ('x', 'y'):
                    self.assertAlmostEqual(personal_position[axis], plaza_search_position[axis], delta=1)
                await plaza_search.fill('总结')
                requests = paths.count('/api/agents/plaza')
                await self.page.get_by_role('tab', name='我的助手').click()
                await expect(personal).to_be_visible()
                await self.page.get_by_role('tab', name='助手广场').click()
                await expect(plaza_search).to_have_value('总结')
                await expect(self.page.get_by_role('button', name='使用广场助手示例')).to_be_visible()
                self.assertEqual(paths.count('/api/agents/plaza'), requests)

    async def test_inline_application_creation_only_requires_name(self):
        submissions = []
        async def application(route):
            if route.request.method != 'POST':
                await self.respond(route)
                return
            body = route.request.post_data_json
            submissions.append(body)
            await route.fulfill(status=201, json={**self.app, 'id': 'app-new', 'name': body['name'], 'code': 'app_generated_fixture'})
        await self.page.route(self.origin + '/api/developer/applications', application)
        await self.page.goto(self.origin + '/agents/agent-1')
        await self.page.get_by_role('button', name='新建 API 应用', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='新建 API 应用', exact=True)
        await expect(dialog.get_by_label('应用编码', exact=True)).to_have_count(0)
        await dialog.get_by_label('应用名称', exact=True).fill('关联应用')
        await dialog.locator('.ant-modal-footer .ant-btn-primary').click()
        await expect(dialog).to_be_hidden()
        await expect(self.page.locator('.application-field .ant-select-selection-item')).to_have_text('关联应用')
        await expect(self.page.get_by_text('app_generated_fixture', exact=True)).to_have_count(0)
        self.assertEqual(submissions, [{'name': '关联应用'}])

    async def open_agent_editor(self):
        await self.page.locator('.agent-card').get_by_role('button', name=re.compile(r'^(编辑智能助手|查看智能助手配置)$')).click()

    async def choose_agent_action(self, name):
        await self.page.locator('.agent-card').get_by_role('button', name='更多操作', exact=True).click()
        await self.page.get_by_role('menuitem', name=name, exact=True).click()

    async def test_resource_card_keeps_use_primary_and_debug_in_more(self):
        self.agent.update(version=7, skill_slugs=['research', 'writing'], knowledge_base_ids=['kb-1'])
        await self.open_agents()
        card = self.page.locator('.agent-card.resource-card')
        await expect(card).to_contain_text('2 个技能')
        await expect(card).to_contain_text('1 个知识库')
        await expect(card.locator('.resource-card__status')).to_have_text('未启用')
        self.assertNotRegex(await card.inner_text(), r'\bv\d+\b')
        await expect(card.get_by_role('button', name='使用', exact=True)).to_be_visible()
        await expect(card.get_by_role('button', name='复制智能助手 ID', exact=True)).to_have_count(0)
        await card.get_by_role('button', name='更多操作', exact=True).click()
        await expect(self.page.get_by_role('menuitem', name='API', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('menuitem', name='复制 ID', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('menuitem', name='调试', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('menuitem', name='配置', exact=True)).to_have_count(0)
        await self.open_agent_editor()
        editor = self.page.locator('.agent-editor--page')
        await expect(editor.get_by_label('系统提示词', exact=True)).to_have_value('草稿提示')
        await expect(editor.get_by_label('欢迎语', exact=True)).to_have_value('你好，我是助手')
        await expect(editor.get_by_role('button', name='删除智能助手', exact=True)).to_be_visible()

    async def test_resource_card_shows_latest_saved_configuration(self):
        self.agent.update(status='enabled', version=8, description='最新保存的说明')
        await self.open_agents()
        card = self.page.locator('.agent-card.resource-card')
        await expect(card.locator('.resource-card__status')).to_have_text('已启用')
        await expect(card).to_contain_text('最新保存的说明')
        await expect(card.locator('.agent-pending')).to_have_count(0)

    async def test_long_agent_title_opens_configuration_with_keyboard_on_mobile(self):
        self.agent['name'] = '很长的智能助手名称用于检查手机卡片标题省略和操作宽度' * 3
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        await self.open_agents()
        card = self.page.locator('.agent-card.resource-card')
        await expect(self.page).to_have_url(self.origin + '/agents')
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        title = card.get_by_role('button', name='编辑' + self.agent['name'], exact=True)
        await title.focus()
        await title.press('Enter')
        await expect(self.page).to_have_url(self.origin + '/agents/agent-1')

    async def test_reader_can_inspect_own_configuration_without_debug_or_mutations(self):
        permissions = granted_permissions('platform:agents:read', 'platform:agents:use')
        await self.page.route('**/api/auth/me', lambda route: route.fulfill(json={
            'id': 'user-1', 'username': 'reader', 'is_admin': False, 'permissions': permissions,
        }))
        await self.open_agents()
        card = self.page.locator('.agent-card.resource-card')
        await expect(card.get_by_role('button', name='使用', exact=True)).to_be_visible()
        await expect(card.get_by_role('button', name='更多操作', exact=True)).to_have_count(0)
        await self.open_agent_editor()
        editor = self.page.locator('.agent-editor--page')
        await expect(editor.get_by_label('系统提示词', exact=True)).to_be_disabled()
        for label in ['保存','启用','停用','删除智能助手']:
            await expect(editor.get_by_role('button', name=label, exact=True)).to_have_count(0)
        await expect(editor.locator('.agent-preview-panel').get_by_role('button', name='发送', exact=True)).to_be_disabled()
        self.assertEqual(self.previews, [])
        self.assertEqual(self.posts, [])
        # Editing the draft never implies permission to execute a debug run.
        permissions[:] = granted_permissions('platform:agents:update')
        await self.page.reload()
        await expect(editor.get_by_label('系统提示词', exact=True)).to_be_enabled()
        await expect(editor.locator('.agent-preview-panel').get_by_role('textbox', name='调试消息', exact=True)).to_be_disabled()
        permissions[:] = granted_permissions('platform:agents:update', 'platform:agents:debug')
        await self.page.reload()
        await expect(editor.locator('.agent-preview-panel').get_by_role('textbox', name='调试消息', exact=True)).to_be_enabled()
        self.assertEqual(self.previews, [])
        self.assertEqual(self.posts, [])

    async def test_agent_id_is_available_in_api_docs_without_a_basic_form_row(self):
        self.agent.update(status='enabled', application_id='app-1')
        await self.open_agents()
        await self.open_agent_editor()
        editor = self.page.locator('.agent-editor--page')
        await expect(editor.get_by_role('group', name='智能助手标识', exact=True)).to_have_count(0)
        await editor.get_by_role('button', name='API 说明', exact=True).click()
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await expect(drawer.locator('.api-endpoint')).to_contain_text('/v1/agents/agent-1/chat/completions')
        self.assertEqual(self.posts, [])

    async def test_preview_keeps_caller_history_after_latest_configuration_save(self):
        self.run.update(status='completed', revision=3, text='完整回答')
        await self.open_agents()
        await self.open_agent_editor()
        editor = self.page.locator('.agent-editor--page')
        preview = editor.locator('.agent-preview-panel')
        await expect(editor.get_by_role('slider', name='上下文轮数')).to_have_count(0)
        for question in ['第一轮问题', '第二轮问题']:
            await preview.get_by_role('textbox', name='调试消息', exact=True).fill(question)
            await preview.get_by_role('button', name='发送', exact=True).click()
            await expect(preview.get_by_role('button', name='发送', exact=True)).to_be_visible()
        await editor.get_by_label('系统提示词', exact=True).fill('保存后的配置')
        await editor.get_by_role('button', name='保存', exact=True).click()
        await expect(editor.get_by_text('所有更改已保存', exact=True)).to_be_visible()
        self.assertNotIn('history_rounds', self.posts[-1])
        await expect(preview.locator('.message-row.user')).to_have_count(2)
        await preview.get_by_role('textbox', name='调试消息', exact=True).fill('继续问题')
        await preview.get_by_role('button', name='发送', exact=True).click()
        await expect(preview.get_by_role('button', name='发送', exact=True)).to_be_visible()
        self.assertEqual([item['content'] for item in self.previews[-1]['messages']],
                         ['第一轮问题','完整回答','第二轮问题','完整回答','继续问题'])

    async def test_editor_save_does_not_include_history_round_setting(self):
        await self.open_agents()
        await self.open_agent_editor()
        editor = self.page.locator('.agent-editor--page')
        await expect(editor.get_by_role('slider', name='上下文轮数')).to_have_count(0)
        await editor.get_by_label('智能助手名称', exact=True).fill('自动记忆助手')
        await editor.get_by_role('button', name='保存', exact=True).click()
        await expect(editor.get_by_text('所有更改已保存', exact=True)).to_be_visible()
        self.assertNotIn('history_rounds',self.posts[-1])

    async def test_original_editor_creates_saved_draft_with_model_limit(self):
        self.agents = []
        await self.open_agents()
        await self.page.get_by_role('button', name='新建智能助手', exact=True).click()
        dialog = self.page.locator('.agent-editor--page')
        await dialog.get_by_label('智能助手名称', exact=True).fill('研究助手')
        await dialog.get_by_role('combobox', name=re.compile(r'模型$')).click()
        await self.page.locator('.ant-select-item-option-content').filter(has_text='日常助手').click()
        await dialog.get_by_label('欢迎语', exact=True).fill('你好，我是助手')
        await dialog.get_by_label('系统提示词', exact=True).fill('草稿提示')
        length = dialog.get_by_label('最大输出 Token', exact=True)
        await expect(length).to_have_value('2048')
        await expect(dialog.get_by_text(re.compile('输出长度超过模型.*512 Token'))).to_be_visible()
        await dialog.get_by_role('button', name='保存', exact=True).click()
        self.assertEqual(self.posts, [])
        await length.fill('512')
        await dialog.get_by_role('button', name='保存', exact=True).click()
        await expect(dialog.get_by_text('所有更改已保存', exact=True)).to_be_visible()
        self.assertEqual(self.posts[-1]['max_tokens'], 512)
        self.assertEqual(self.posts[-1]['welcome_message'], '你好，我是助手')
        self.assertEqual(self.posts[-1]['model_id'], 'model-1')

    async def test_output_length_over_limit_is_inline_and_blocks_save_until_corrected(self):
        await self.page.goto(self.origin + '/agents/agent-1')
        editor = self.page.locator('.agent-editor--page')
        length = editor.get_by_label('最大输出 Token', exact=True)
        await expect(editor.get_by_text('当前模型最多支持输出 512 Token', exact=True)).to_be_visible()
        await editor.get_by_label('系统提示词', exact=True).fill('保留未保存的提示词')
        await length.fill('513')
        error = editor.get_by_text(re.compile('输出长度超过模型「日常助手」.*512 Token'))
        await expect(error).to_be_visible()
        await expect(length).to_have_attribute('aria-invalid', 'true')
        await editor.get_by_role('button', name='保存', exact=True).click()
        await expect(length).to_be_in_viewport()
        self.assertEqual(self.posts, [])
        await expect(length).to_have_value('513')
        await expect(editor.get_by_label('系统提示词', exact=True)).to_have_value('保留未保存的提示词')
        await length.fill('512')
        await expect(error).to_have_count(0)
        await expect(length).not_to_have_attribute('aria-invalid', 'true')
        await editor.get_by_role('button', name='保存', exact=True).click()
        await expect(editor.get_by_text('所有更改已保存', exact=True)).to_be_visible()
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.posts[0]['max_tokens'], 512)
        self.assertEqual(self.posts[0]['system_prompt'], '保留未保存的提示词')

    async def test_switching_to_lower_model_preserves_output_and_updates_inline_limit(self):
        models = [
            {'id': model_id, 'name': name, 'connection_id': 'connection-1', 'upstream_model': 'fixture',
             'max_output_tokens': limit, 'model_type': 'chat', 'enabled': True, 'capabilities': ['chat', 'stream']}
            for model_id, name, limit in [('model-1', '日常助手', 512), ('model-2', '短输出模型', 256)]
        ]
        await self.page.route(self.origin + '/api/models', lambda route: route.fulfill(json=models))
        await self.page.goto(self.origin + '/agents/agent-1')
        editor = self.page.locator('.agent-editor--page')
        length = editor.get_by_label('最大输出 Token', exact=True)
        await expect(length).to_have_value('512')
        await editor.get_by_role('combobox', name=re.compile(r'模型$')).press('ArrowDown')
        await self.page.locator('.ant-select-item-option-content').filter(has_text='短输出模型').click()
        await expect(length).to_have_value('512')
        await expect(editor.get_by_text(re.compile('输出长度超过模型「短输出模型」.*256 Token'))).to_be_visible()
        await editor.get_by_role('button', name='保存', exact=True).click()
        self.assertEqual(self.posts, [])
        await editor.get_by_role('combobox', name=re.compile(r'模型$')).press('ArrowDown')
        await self.page.locator('.ant-select-item-option-content').filter(has_text='日常助手').click()
        await expect(length).to_have_value('512')
        await expect(editor.get_by_text(re.compile('输出长度超过模型'))).to_have_count(0)

    async def test_existing_agent_above_current_model_limit_shows_error_on_load(self):
        self.agent['max_tokens'] = 1024
        await self.page.goto(self.origin + '/agents/agent-1')
        editor = self.page.locator('.agent-editor--page')
        await expect(editor.get_by_label('最大输出 Token', exact=True)).to_have_value('1024')
        await expect(editor.get_by_text(re.compile('输出长度超过模型.*512 Token'))).to_be_visible()
        await editor.get_by_role('button', name='保存', exact=True).click()
        self.assertEqual(self.posts, [])

    async def test_conflict_preserves_unsaved_editor_values(self):
        await self.page.route('**/api/agents/agent-1', lambda route: route.fulfill(status=409, json={'error': {'code': 'version_conflict', 'message': '智能助手已更新，请刷新'}}) if route.request.method == 'PATCH' else route.fulfill(json=self.agent))
        await self.open_agents()
        await self.open_agent_editor()
        dialog = self.page.locator('.agent-editor--page')
        await dialog.get_by_label('系统提示词', exact=True).fill('未保存的新提示')
        await dialog.get_by_role('button', name='保存', exact=True).click()
        await expect(dialog.get_by_text('智能助手已更新，请刷新', exact=True)).to_be_visible()
        await expect(dialog.get_by_label('系统提示词', exact=True)).to_have_value('未保存的新提示')

    async def test_save_latest_configuration_enable_disable_and_delete(self):
        self.agent['application_id']='app-1'
        await self.open_agents()
        await self.open_agent_editor()
        editor=self.page.locator('.agent-editor--page')
        await editor.get_by_role('button',name='启用',exact=True).click()
        await expect(editor.get_by_role('button',name='停用',exact=True)).to_be_visible()
        await editor.get_by_label('系统提示词',exact=True).fill('新的配置提示')
        await editor.get_by_role('button',name='保存',exact=True).click()
        await expect(editor.get_by_text('所有更改已保存',exact=True)).to_be_visible()
        self.assertEqual(self.agent['system_prompt'],'新的配置提示')
        self.assertEqual(self.agent['status'],'enabled')
        self.assertNotIn('published_config',self.agent)
        await expect(editor.get_by_role('button',name='启用',exact=True)).to_be_disabled()
        await editor.get_by_role('button',name='停用',exact=True).click()
        await self.page.get_by_role('dialog').filter(has_text='停用后将暂停新的正式对话').get_by_role('button',name=re.compile(r'^确\s*定$')).click()
        await expect(editor.get_by_role('button',name='停用',exact=True)).to_have_count(0)
        await editor.get_by_role('button',name='删除智能助手',exact=True).click()
        await self.page.get_by_role('dialog').filter(has_text='删除智能助手「研究助手」').get_by_role('button',name=re.compile(r'^确\s*定$')).click()
        await expect(self.page.get_by_text('暂无智能助手',exact=True)).to_be_visible()

    async def test_preview_displays_welcome_reasoning_before_content_and_stops(self):
        await self.open_agents()
        await self.choose_agent_action('调试')
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await expect(drawer.get_by_text('你好，我是助手', exact=True)).to_be_visible()
        await drawer.get_by_role('textbox', name='调试消息', exact=True).fill('请回答')
        await drawer.get_by_role('button', name='发送', exact=True).click()
        await expect(drawer.locator('.reasoning-section')).to_contain_text('先核实资料')
        self.assertEqual(self.previews[0]['messages'], [{'role': 'user', 'content': '请回答'}])
        await drawer.get_by_role('button', name='停止生成', exact=True).click()
        await expect(drawer.get_by_text('已停止', exact=True)).to_be_visible()
        self.assertEqual(len(self.previews), 1)

    async def leave_after_lost_subscription(self, action):
        await self.page.route('**/api/runs/preview-run/events', lambda route: route.fulfill(content_type='text/plain', body='broken stream'))
        async def cancel(route):
            self.run.update(status='cancel_requested', revision=3)
            await route.fulfill(json=self.run)
        await self.page.route('**/api/runs/preview-run/cancel', cancel)
        await self.open_agents()
        await self.choose_agent_action('调试')
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await drawer.get_by_role('textbox', name='调试消息', exact=True).fill('请回答')
        await drawer.get_by_role('button', name='发送', exact=True).click()
        await expect(drawer.get_by_role('button', name='恢复查看', exact=True)).to_be_visible(timeout=10000)
        await drawer.get_by_role('button', name='关闭调试' if action == 'close' else '清空对话', exact=True).click()
        if action == 'close': await expect(drawer).not_to_be_visible()
        else:
            await expect(drawer.locator('.reasoning-section')).to_have_count(0)
            await expect(drawer.get_by_role('button', name='恢复查看', exact=True)).to_have_count(0)

    async def test_close_accepts_confirmed_stop_after_subscription_failure(self):
        await self.leave_after_lost_subscription('close')

    async def test_clear_accepts_confirmed_stop_after_subscription_failure(self):
        await self.leave_after_lost_subscription('clear')

    async def test_api_key_grants_follow_published_configuration(self):
        self.agent.update({'status': 'enabled', 'application_id': 'draft-application', **{**self.config, 'application_id': 'app-1', 'model_id': 'published-model'}})
        await self.open_agents()
        await self.choose_agent_action('API')
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await expect(drawer.locator('.api-endpoint')).to_contain_text('/v1/agents/agent-1/chat/completions')
        await expect(drawer.locator('.api-published')).to_contain_text('app-1')
        await drawer.get_by_role('button', name='创建密钥', exact=True).click()
        await expect(drawer.locator('.issued-key code')).to_have_text(self.secret)
        self.assertEqual(self.posts[-1]['model_ids'], [])
        self.assertEqual(self.posts[-1]['agent_ids'], ['agent-1'])
        self.assertEqual(self.posts[-1]['scopes'], ['agents:invoke'])
        await expect(drawer.locator('.api-key-row').get_by_text('有效 · 已授权此智能体', exact=True)).to_be_visible()
        await drawer.get_by_role('button', name='隐藏明文密钥', exact=True).click()
        await expect(drawer.get_by_text(self.secret, exact=True)).to_have_count(0)

    async def test_api_key_monthly_points_limit_keeps_zero_as_a_string(self):
        self.agent.update({'status': 'enabled', 'application_id': 'draft-application', **{**self.config, 'application_id': 'app-1', 'model_id': 'published-model'}})
        await self.open_agents()
        await self.choose_agent_action('API')
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await drawer.get_by_label('月积分上限', exact=True).fill('0')
        await drawer.get_by_role('button', name='创建密钥', exact=True).click()
        await expect(drawer.locator('.issued-key code')).to_have_text(self.secret)
        self.assertEqual(self.posts[-1]['monthly_points_limit'], '0')
        self.assertEqual(self.posts[-1]['model_ids'], [])
        self.assertNotIn('monthly_budget', self.posts[-1])
        self.assertNotIn('allocated_points', self.posts[-1])

    async def test_api_key_status_checks_scope_and_agent_identity(self):
        self.agent.update(
            {"status": "enabled", **{**self.config, "application_id": "app-1"}}
        )
        base = {
            "application_id": "app-1",
            "prefix": "fixture",
            "status": "active",
            "model_ids": ["model-1"],
            "rpm_limit": None,
            "tpm_limit": None,
            "concurrency_limit": None,
            "expires_at": None,
            "last_used_at": None,
        }
        self.keys = [
            {**base, "id": "model", "name": "模型服务", "scopes": ["models:invoke"]},
            {
                **base,
                "id": "other",
                "name": "其他助手",
                "scopes": ["agents:invoke"],
                "agent_ids": ["agent-other"],
                "model_ids": [],
            },
        ]
        await self.open_agents()
        await self.choose_agent_action("API")
        rows = self.page.locator(".ant-drawer-open .api-key-row")
        await expect(rows.filter(has_text="模型服务")).to_contain_text(
            "缺少智能体调用能力"
        )
        await expect(rows.filter(has_text="其他助手")).to_contain_text("未授权此智能体")

    async def test_key_creation_waits_for_revoke_and_later_revocation_still_works(self):
        self.agent.update({'status': 'enabled', 'application_id': 'app-1', **{**self.config, 'application_id': 'app-1'}})
        self.keys = [{'id': 'key-old', 'application_id': 'app-1', 'name': '旧客户端', 'prefix': 'sk-zh_old', 'status': 'active', 'model_ids': ['model-1'], 'rpm_limit': 60, 'tpm_limit': None, 'concurrency_limit': 2, 'expires_at': None, 'last_used_at': None}]
        requested, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def revoke(route):
            key_id = urlparse(route.request.url).path.split('/')[-2]
            calls.append(key_id)
            if key_id == 'key-old':
                requested.set()
                await release.wait()
            row = next(item for item in self.keys if item['id'] == key_id)
            row['status'] = 'revoked'
            with contextlib.suppress(Exception): await route.fulfill(json=row)
        await self.page.route('**/api/developer/applications/app-1/keys/*/revoke', revoke)
        try:
            await self.open_agents()
            await self.choose_agent_action('API')
            drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
            await drawer.get_by_role('button', name='撤销密钥', exact=True).click()
            await self.page.mouse.move(50, 50)
            await self.page.get_by_role('button', name=re.compile(r'^撤\s*销$')).click()
            await asyncio.wait_for(requested.wait(), timeout=5)
            await drawer.locator('.agent-drawer-title').click()
            await expect(drawer.get_by_role('button', name='创建密钥', exact=True)).to_be_disabled()
            self.assertEqual(self.posts, [])
            release.set()
            await expect(drawer.get_by_role('button', name='创建密钥', exact=True)).to_be_enabled()
            await drawer.get_by_role('button', name='创建密钥', exact=True).click()
            await expect(drawer.locator('.issued-key code')).to_have_text(self.secret)
            await drawer.locator('.api-key-row').filter(has_text=self.posts[-1]['name']).get_by_role('button', name='撤销密钥', exact=True).click()
            await self.page.mouse.move(50, 50)
            await self.page.get_by_role('button', name=re.compile(r'^撤\s*销$')).click()
            await expect(drawer.get_by_text(self.secret, exact=True)).to_have_count(0)
            self.assertEqual(calls, ['key-old', 'key-1'])
        finally:
            release.set()

    async def test_enabled_configuration_does_not_need_a_second_publication(self):
        self.agent.update(status='enabled', application_id='app-1', version=4)
        await self.open_agents()
        await self.open_agent_editor()
        editor=self.page.locator('.agent-editor--page')
        await expect(editor.get_by_role('button',name='启用',exact=True)).to_be_disabled()
        await expect(editor.get_by_role('button',name='停用',exact=True)).to_be_visible()


if __name__ == '__main__':
    unittest.main(verbosity=2)
