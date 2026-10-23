"""Original Work UI at controlled HTTP/SSE boundaries; no provider substitution in product."""
import asyncio
import contextlib
import copy
import json
import re
import unittest
from urllib.parse import urlparse

from playwright.async_api import expect
from developer_regressions import DeveloperBoundary

STAMP = '2026-10-23T00:00:00Z'


class WorkUIRegressions(DeveloperBoundary):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.available = [{'id': 'agent-a', 'name': '研究助手', 'description': '查证事实', 'published_version': 1}, {'id': 'agent-b', 'name': '写作助手', 'description': '归纳结果', 'published_version': 2}]
        self.task = {'id': 'task-1', 'owner_id': 'user-1', 'title': '研究目标', 'goal': '研究目标', 'status': 'active', 'version': 1, 'current_run_id': None, 'active_agent_id': 'agent-a', 'create_client_request_id': 'create-intent', 'created_at': STAMP, 'updated_at': STAMP, 'members': [self.member('agent-a'), self.member('agent-b')], 'messages': []}
        self.tasks = []
        self.runs = []
        self.memberships = []
        self.run = {'id': 'work-run', 'resource_type': 'work_turn', 'resource_id': 'task-1', 'status': 'completed', 'attempt_id': 'attempt-1', 'attempt_no': 1, 'revision': 3, 'projection_seq': 2, 'text': '核实后的结论', 'reasoning': '先查证再归纳', 'error': None, 'created_at': STAMP, 'usage': {'input_tokens': 20, 'output_tokens': 10, 'status': 'known'}}

    def member(self, agent_id):
        agent = next(item for item in self.available if item['id'] == agent_id)
        return {'agent_id': agent_id, 'agent_name_snapshot': agent['name'], 'published_version': agent['published_version'], 'latest_published_version': agent['published_version'], 'available': True, 'removed_at': None}

    def message(self, role, body, run=None):
        member = next(item for item in self.task['members'] if item['agent_id'] == body['agent_id'])
        return {'id': f"msg-{len(self.task['messages']) + 1}", 'client_request_id': body['client_request_id'], 'role': role, 'content': body['message'] if role == 'user' else (run or self.run)['text'], 'reasoning': '' if role == 'user' else (run or self.run)['reasoning'], 'run_id': None if run is None else run['id'], 'status': 'completed' if run is None else run['status'], 'usage': None if run is None else run['usage'], 'error': None if run is None else run['error'], 'agent_id': None if role == 'user' else member['agent_id'], 'agent_name_snapshot': None if role == 'user' else member['agent_name_snapshot'], 'published_version': None if role == 'user' else member['published_version'], 'sequence': len(self.task['messages']) + 1, 'created_at': STAMP}

    async def respond(self, route):
        path, method = urlparse(route.request.url).path, route.request.method
        if path == '/api/work/agents/available':
            await route.fulfill(json=self.available)
        elif path == '/api/work/tasks':
            if method == 'POST':
                body = route.request.post_data_json
                self.posts.append(body)
                self.task.update(goal=body['goal'], title=body['goal'][:40], active_agent_id=body['active_agent_id'], create_client_request_id=body['client_request_id'], members=[self.member(id_) for id_ in body['agent_ids']])
                self.task['messages'] = [self.message('user', {'agent_id': body['active_agent_id'], 'client_request_id': body['client_request_id'], 'message': body['goal']})]
                self.tasks = [self.task]
                await route.fulfill(status=201, json=self.task)
            else:
                await route.fulfill(json={'items': self.tasks, 'total': len(self.tasks), 'page': 1, 'page_size': 20})
        elif path == '/api/work/tasks/task-1':
            if method == 'PATCH':
                body = route.request.post_data_json
                self.posts.append(body)
                self.task.update({key: value for key, value in body.items() if key != 'expected_version'})
                self.task['version'] += 1
            if method == 'DELETE':
                self.tasks = []
                await route.fulfill(status=204)
            else:
                await route.fulfill(json=self.task)
        elif path == '/api/work/tasks/task-1/runs':
            body = route.request.post_data_json
            self.runs.append(copy.deepcopy(body))
            self.task.update(version=self.task['version'] + 1, current_run_id=None if self.run['status'] == 'completed' else self.run['id'], active_agent_id=body['agent_id'])
            if not any(item['client_request_id'] == body['client_request_id'] for item in self.task['messages']):
                self.task['messages'].append(self.message('user', body))
            self.task['messages'].append(self.message('assistant', body, self.run))
            await route.fulfill(status=202, json=self.run)
        elif path == '/api/work/tasks/task-1/runs/work-run':
            await route.fulfill(json=self.run)
        elif path == '/api/work/tasks/task-1/runs/work-run/events':
            await route.fulfill(content_type='text/event-stream', body='event: snapshot\ndata: ' + json.dumps(self.run) + '\n\n')
        elif path == '/api/work/tasks/task-1/runs/work-run/cancel':
            self.run.update(status='cancelled', revision=self.run['revision'] + 1)
            self.task.update(current_run_id=None, version=self.task['version'] + 1)
            if self.task['messages']:
                self.task['messages'][-1].update(status='cancelled', content=self.run['text'], reasoning=self.run['reasoning'])
            await route.fulfill(json=self.run)
        elif '/api/work/tasks/task-1/members/' in path or path.endswith('/task-1/members'):
            body = route.request.post_data_json if method != 'DELETE' else {}
            self.memberships.append((method, path, body))
            agent_id = path.rsplit('/', 1)[-1] if not path.endswith('/members') else body['agent_id']
            if method == 'POST': self.task['members'].append(self.member(agent_id))
            elif method == 'PATCH':
                member = next(item for item in self.task['members'] if item['agent_id'] == agent_id)
                member['published_version'] = body['expected_published_version']
            else:
                next(item for item in self.task['members'] if item['agent_id'] == agent_id)['removed_at'] = STAMP
            self.task['version'] += 1
            await route.fulfill(json=self.task)
        else:
            await super().respond(route)

    async def open_task(self):
        self.tasks = [self.task]
        await self.page.goto(self.origin + '/work?taskId=task-1')
        await expect(self.page.get_by_role('heading', name=self.task['title'], exact=True)).to_be_visible()

    async def create_task(self, await_heading=True):
        await self.page.goto(self.origin + '/work')
        await expect(self.page.get_by_role('heading', name='今天要完成什么工作？')).to_be_visible()
        await self.page.get_by_role('textbox', name='工作目标', exact=True).fill('研究目标')
        await self.page.get_by_role('button', name='添加智能体', exact=True).click()
        picker = self.page.get_by_role('dialog', name='添加智能体', exact=True)
        await picker.get_by_role('checkbox', name='研究助手', exact=True).check()
        await picker.get_by_role('checkbox', name='写作助手', exact=True).check()
        await picker.get_by_role('button', name=re.compile(r'^(?:OK|确\s*定)$')).click()
        await self.page.get_by_role('button', name='开始工作', exact=True).click()
        if await_heading:
            await expect(self.page.get_by_role('heading', name='研究目标', exact=True)).to_be_visible()

    async def test_create_runs_once_and_next_member_uses_new_intent(self):
        await self.create_task()
        await expect(self.page.locator('.work-message.assistant')).to_contain_text('核实后的结论')
        self.assertEqual(len(self.runs), 1)
        self.assertEqual(self.runs[0]['client_request_id'], self.posts[0]['client_request_id'])
        await self.page.locator('.work-composer .ant-select-selector').click()
        await self.page.locator('.ant-select-item-option-content').filter(has_text='写作助手').click()
        await self.page.get_by_role('textbox', name='工作消息', exact=True).fill('请总结结果')
        await self.page.get_by_role('button', name='发送', exact=True).click()
        await expect(self.page.locator('.work-message.assistant')).to_have_count(2)
        self.assertEqual(self.runs[-1]['agent_id'], 'agent-b')
        self.assertNotEqual(self.runs[-1]['client_request_id'], self.runs[0]['client_request_id'])
        self.assertEqual(self.runs[-1]['expected_version'], 2)
        await self.page.locator('.work-message__reasoning summary').last.click()
        await expect(self.page.locator('.work-message__reasoning p').last).to_have_text('先查证再归纳')

    async def test_completed_task_reopens_and_archived_task_is_read_only(self):
        self.task['status'] = 'completed'
        await self.open_task()
        await expect(self.page.get_by_role('textbox', name='工作消息', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='重新打开', exact=True).click()
        await expect(self.page.get_by_role('textbox', name='工作消息', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='归档', exact=True).click()
        await expect(self.page.get_by_text('此工作已归档，只能查看历史内容。', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='重新打开', exact=True)).to_have_count(0)

    async def test_original_work_mode_and_recent_navigation(self):
        await self.page.goto(self.origin + '/chat')
        await self.page.get_by_role('button', name='工作', exact=True).click()
        await expect(self.page).to_have_url(self.origin + '/work')
        await expect(self.page.get_by_role('heading', name='今天要完成什么工作？')).to_be_visible()
        await self.page.get_by_role('button', name='聊天', exact=True).click()
        await expect(self.page).to_have_url(self.origin + '/chat')


    async def test_lost_admission_retries_same_key_and_cas(self):
        first = True
        async def admission(route):
            nonlocal first
            if first:
                first = False
                self.runs.append(copy.deepcopy(route.request.post_data_json))
                await route.abort('failed')
            else:
                await self.respond(route)
        await self.page.route('**/api/work/tasks/task-1/runs', admission)
        await self.create_task()
        await expect(self.page.get_by_role('button', name='重试执行', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='重试执行', exact=True).click()
        await expect(self.page.locator('.work-message.assistant')).to_contain_text('核实后的结论')
        self.assertEqual(len(self.runs), 2)
        self.assertEqual(self.runs[0], self.runs[1])

    async def test_acknowledged_run_refresh_failure_recovers_without_new_generation(self):
        admitted = False
        fail_refresh = True
        async def admission(route):
            nonlocal admitted
            await self.respond(route)
            admitted = True
        async def detail(route):
            nonlocal fail_refresh
            if admitted and fail_refresh:
                fail_refresh = False
                await route.fulfill(status=503, json={'error': {'code': 'offline', 'message': '暂时无法读取任务'}})
            else: await self.respond(route)
        await self.page.route('**/api/work/tasks/task-1/runs', admission)
        await self.page.route('**/api/work/tasks/task-1', detail)
        await self.create_task()
        await expect(self.page.get_by_role('button', name='恢复查看', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='恢复查看', exact=True).click()
        await expect(self.page.locator('.work-message.assistant')).to_contain_text('核实后的结论')
        await expect(self.page.get_by_role('button', name='发送', exact=True)).to_be_visible()
        self.assertEqual(len(self.runs), 1)

    async def test_stop_retains_occupancy_until_ack_and_shares_single_request(self):
        self.run.update(status='running', text='', reasoning='正在查证')
        body = {'agent_id': 'agent-a', 'client_request_id': 'turn-1', 'message': '查证'}
        self.task['messages'] = [self.message('user', body), self.message('assistant', body, self.run)]
        self.task['current_run_id'] = 'work-run'
        requested, release = asyncio.Event(), asyncio.Event()
        calls = 0
        async def cancel(route):
            nonlocal calls
            calls += 1
            requested.set()
            await release.wait()
            with contextlib.suppress(Exception): await self.respond(route)
        await self.page.route('**/api/work/tasks/task-1/runs/work-run/cancel', cancel)
        try:
            await self.open_task()
            await self.page.get_by_role('button', name='停止', exact=True).click()
            await asyncio.wait_for(requested.wait(), timeout=5)
            await expect(self.page.get_by_role('button', name='停止', exact=True)).to_be_disabled()
            await expect(self.page.get_by_role('button', name='编辑工作信息', exact=True)).to_be_disabled()
            await expect(self.page.get_by_role('textbox', name='工作消息', exact=True)).to_be_disabled()
            release.set()
            await expect(self.page.get_by_role('button', name='停止', exact=True)).to_have_count(0)
            await expect(self.page.get_by_text('已取消', exact=True)).to_be_visible()
            self.assertEqual(calls, 1)
        finally: release.set()

    async def test_late_detail_cannot_fill_a_new_work_page(self):
        requested, release = asyncio.Event(), asyncio.Event()
        secret = {**self.task, 'title': '之前任务的私有标题'}
        async def detail(route):
            requested.set()
            await release.wait()
            with contextlib.suppress(Exception): await route.fulfill(json=secret)
        await self.page.route('**/api/work/tasks/task-1', detail)
        try:
            await self.page.goto(self.origin + '/work?taskId=task-1')
            await asyncio.wait_for(requested.wait(), timeout=5)
            await self.page.get_by_role('button', name='新建工作', exact=True).click()
            await expect(self.page.get_by_role('heading', name='今天要完成什么工作？')).to_be_visible()
            release.set()
            await self.page.wait_for_timeout(200)
            await expect(self.page.get_by_text('之前任务的私有标题', exact=True)).to_have_count(0)
        finally: release.set()

    async def test_editor_conflict_keeps_unsaved_title_and_goal(self):
        async def conflict(route):
            if route.request.method == 'PATCH': await route.fulfill(status=409, json={'error': {'code': 'work_version_conflict', 'message': '任务已更新，请刷新后重试'}})
            else: await self.respond(route)
        await self.page.route('**/api/work/tasks/task-1', conflict)
        await self.open_task()
        await self.page.get_by_role('button', name='编辑工作信息', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='编辑工作信息', exact=True)
        await dialog.get_by_label('标题', exact=True).fill('我的未保存标题')
        await dialog.get_by_label('完整目标', exact=True).fill('我的未保存目标')
        await dialog.get_by_role('button', name=re.compile(r'^(?:OK|确\s*定)$')).click()
        await expect(dialog).to_be_visible()
        await expect(dialog.get_by_label('标题', exact=True)).to_have_value('我的未保存标题')
        await expect(dialog.get_by_label('完整目标', exact=True)).to_have_value('我的未保存目标')
        await expect(self.page.get_by_text('任务已更新，请刷新后重试', exact=True)).to_be_visible()

    async def test_member_upgrade_is_explicit_and_removed_history_is_preserved(self):
        self.task['members'][0]['latest_published_version'] = 3
        await self.open_task()
        await expect(self.page.locator('.work-member').first).to_contain_text('v1')
        await self.page.get_by_role('button', name='升级', exact=True).click()
        await self.page.get_by_role('dialog').filter(has_text='确认将“研究助手”').get_by_role('button', name=re.compile(r'^确\s*定$')).click()
        await expect(self.page.locator('.work-member').first).to_contain_text('v3')
        self.assertEqual(self.memberships[0][2], {'expected_version': 1, 'expected_published_version': 3})
        await self.page.get_by_role('button', name='移除智能体', exact=True).first.click()
        await self.page.get_by_role('dialog').filter(has_text='确定移除“研究助手”').get_by_role('button', name=re.compile(r'^确\s*定$')).click()
        await expect(self.page.locator('.work-member')).to_have_count(1)
        self.assertEqual(self.task['members'][0]['removed_at'], STAMP)

    async def test_recent_work_rename_and_delete_use_versions(self):
        await self.open_task()
        recent = self.page.locator('.recent-work__item')
        await recent.hover()
        await recent.get_by_role('button', name='更多操作', exact=True).click()
        await self.page.get_by_role('menuitem', name='重命名', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='重命名工作', exact=True)
        await dialog.get_by_label('工作标题', exact=True).fill('新的工作标题')
        await dialog.get_by_role('button', name=re.compile(r'^(?:OK|确\s*定)$')).click()
        await expect(self.page.get_by_role('heading', name='新的工作标题', exact=True)).to_be_visible()
        self.assertEqual(self.posts[-1], {'title': '新的工作标题', 'expected_version': 1})
        await recent.hover()
        await recent.get_by_role('button', name='更多操作', exact=True).click()
        await self.page.get_by_role('menuitem', name='删除工作', exact=True).click()
        await self.page.get_by_role('dialog').filter(has_text='删除工作“新的工作标题”').get_by_role('button', name=re.compile(r'^确\s*定$')).click()
        await expect(self.page).to_have_url(self.origin + '/work')
        await expect(self.page.locator('.recent-work__item')).to_have_count(0)


    async def test_reloading_running_task_with_snapshot_failure_offers_observation_recovery(self):
        self.run.update(status='running', text='部分结果')
        body = {'agent_id': 'agent-a', 'client_request_id': 'turn-1', 'message': '查证'}
        self.task['messages'] = [self.message('user', body), self.message('assistant', body, self.run)]
        self.task['current_run_id'] = 'work-run'
        first = True
        async def snapshot(route):
            nonlocal first
            if first:
                first = False
                await route.fulfill(status=503, json={'error': {'code': 'snapshot_unavailable', 'message': '无法读取运行'}})
            else: await self.respond(route)
        await self.page.route('**/api/work/tasks/task-1/runs/work-run', snapshot)
        await self.open_task()
        await expect(self.page.get_by_role('button', name='恢复查看', exact=True)).to_be_visible()
        self.run.update(status='completed', revision=4)
        self.task['current_run_id'] = None
        await self.page.get_by_role('button', name='恢复查看', exact=True).click()
        await expect(self.page.get_by_role('button', name='停止', exact=True)).to_have_count(0)
        self.assertEqual(len(self.runs), 0)

    async def test_logout_discards_late_private_task(self):
        requested, release = asyncio.Event(), asyncio.Event()
        async def detail(route):
            requested.set()
            await release.wait()
            with contextlib.suppress(Exception): await route.fulfill(json={**self.task, 'title': '不能泄漏的工作标题'})
        await self.page.route('**/api/work/tasks/task-1', detail)
        await self.page.route('**/api/auth/logout', lambda route: route.fulfill(status=204))
        try:
            await self.page.goto(self.origin + '/work?taskId=task-1')
            await asyncio.wait_for(requested.wait(), timeout=5)
            await self.page.get_by_role('button', name='用户菜单', exact=True).click()
            await self.page.get_by_role('menuitem', name='退出登录', exact=True).click()
            await expect(self.page).to_have_url(self.origin + '/login')
            release.set()
            await self.page.wait_for_timeout(200)
            await expect(self.page.get_by_text('不能泄漏的工作标题', exact=True)).to_have_count(0)
        finally: release.set()


    async def test_acknowledged_creation_detail_failure_stays_on_known_task(self):
        first = True
        async def detail(route):
            nonlocal first
            if first:
                first = False
                await route.fulfill(status=503, json={'error': {'code': 'offline', 'message': '详情暂不可用'}})
            else: await self.respond(route)
        await self.page.route('**/api/work/tasks/task-1', detail)
        await self.create_task(await_heading=False)
        await expect(self.page).to_have_url(self.origin + '/work?taskId=task-1')
        await expect(self.page.get_by_text('详情暂不可用', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='开始工作', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name=re.compile(r'^重\s*试$')).click()
        await self.page.get_by_role('button', name='继续执行', exact=True).click()
        await expect(self.page.locator('.work-message.assistant')).to_have_count(1)
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.runs[0]['client_request_id'], self.posts[0]['client_request_id'])

    async def test_sidebar_respects_pending_admission_and_busy_current_task(self):
        self.run['status'] = 'running'
        requested, release = asyncio.Event(), asyncio.Event()
        async def admission(route):
            requested.set()
            await release.wait()
            with contextlib.suppress(Exception): await self.respond(route)
        await self.page.route('**/api/work/tasks/task-1/runs', admission)
        try:
            await self.create_task()
            await asyncio.wait_for(requested.wait(), timeout=5)
            await expect(self.page.locator('.recent-work__item .workspace-more-menu')).to_have_count(0)
            release.set()
            await expect(self.page.get_by_role('button', name='停止', exact=True)).to_be_visible()
            await expect(self.page.locator('.recent-work__item .workspace-more-menu')).to_have_count(0)
        finally: release.set()

    async def test_readonly_sidebar_and_running_status_are_explicit(self):
        await self.page.route('**/api/auth/me', lambda route: route.fulfill(json={'id': 'user-1', 'username': 'reader', 'is_admin': False}))
        self.task['owner_id'] = 'another-owner'
        self.run['status'] = 'cancel_requested'
        self.task['current_run_id'] = self.run['id']
        self.task['messages'] = [self.message('assistant', {'agent_id': 'agent-a', 'client_request_id': 'turn', 'message': 'question'}, self.run)]
        await self.open_task()
        await expect(self.page.locator('.recent-work__item .workspace-more-menu')).to_have_count(0)
        await expect(self.page.locator('.work-message').get_by_text('正在停止', exact=True)).to_be_visible()


    async def test_ime_enter_commits_text_without_starting_work(self):
        await self.open_task()
        textbox = self.page.get_by_role('textbox', name='工作消息', exact=True)
        await textbox.fill('中文输入')
        await textbox.dispatch_event('keydown', {'key': 'Enter', 'code': 'Enter', 'isComposing': True, 'bubbles': True})
        await self.page.wait_for_timeout(100)
        self.assertEqual(self.runs, [])
        await expect(textbox).to_have_value('中文输入')
        await textbox.press('Enter')
        await expect(self.page.locator('.work-message.assistant')).to_have_count(1)

    async def test_terminal_cancel_requires_refreshed_task_before_another_turn(self):
        self.run['status'] = 'running'
        admitted = False
        failures = 2
        async def admission(route):
            nonlocal admitted
            await self.respond(route)
            admitted = True
        async def read_detail(route):
            nonlocal failures
            if admitted and failures:
                failures -= 1
                await route.fulfill(status=503, json={'error': {'code': 'offline', 'message': '需要重新读取任务'}})
            else: await self.respond(route)
        await self.page.route('**/api/work/tasks/task-1/runs', admission)
        await self.page.route('**/api/work/tasks/task-1', read_detail)
        await self.create_task()
        await expect(self.page.get_by_role('button', name='恢复查看', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='停止', exact=True).click()
        await expect(self.page.get_by_role('button', name='恢复查看', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('textbox', name='工作消息', exact=True)).to_be_disabled()
        await expect(self.page.get_by_role('button', name='编辑工作信息', exact=True)).to_be_disabled()
        self.assertEqual(len(self.runs), 1)
        await self.page.get_by_role('button', name='恢复查看', exact=True).click()
        await expect(self.page.get_by_role('textbox', name='工作消息', exact=True)).to_be_enabled()
        self.run.update(status='completed', revision=5)
        await self.page.get_by_role('textbox', name='工作消息', exact=True).fill('新一轮')
        await self.page.get_by_role('button', name='发送', exact=True).click()
        await expect(self.page.locator('.work-message.assistant')).to_have_count(2)
        self.assertEqual(self.runs[-1]['expected_version'], 3)


    async def test_resolving_lost_creation_frees_the_next_create_intent(self):
        first = True
        async def collection(route):
            nonlocal first
            if route.request.method == 'POST' and first:
                first = False
                body = route.request.post_data_json
                self.posts.append(copy.deepcopy(body))
                self.task.update(goal=body['goal'], title=body['goal'], create_client_request_id=body['client_request_id'], active_agent_id=body['active_agent_id'])
                self.task['messages'] = [self.message('user', {'agent_id': body['active_agent_id'], 'client_request_id': body['client_request_id'], 'message': body['goal']})]
                self.tasks = [self.task]
                await route.abort('failed')
            else: await self.respond(route)
        await self.page.route('**/api/work/tasks', collection)
        await self.create_task(await_heading=False)
        await expect(self.page.get_by_text('无法连接服务，请检查网络后重试', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='刷新工作列表', exact=True).click()
        await expect(self.page.locator('.recent-work__open')).to_have_count(1)
        await self.page.locator('.recent-work__open').click()
        await self.page.get_by_role('button', name='继续执行', exact=True).click()
        await expect(self.page.locator('.work-message.assistant')).to_have_count(1)
        await self.page.get_by_role('button', name='新建工作', exact=True).click()
        await self.page.get_by_role('textbox', name='工作目标', exact=True).fill('研究目标')
        await self.page.get_by_role('button', name='添加智能体', exact=True).click()
        picker = self.page.get_by_role('dialog', name='添加智能体', exact=True)
        await picker.get_by_role('checkbox', name='研究助手', exact=True).check()
        await picker.get_by_role('checkbox', name='写作助手', exact=True).check()
        await picker.get_by_role('button', name=re.compile(r'^(?:OK|确\s*定)$')).click()
        await self.page.get_by_role('button', name='开始工作', exact=True).click()
        await expect(self.page.get_by_role('heading', name='研究目标', exact=True)).to_be_visible()
        self.assertEqual(len(self.posts), 2)
        self.assertNotEqual(self.posts[0]['client_request_id'], self.posts[1]['client_request_id'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
