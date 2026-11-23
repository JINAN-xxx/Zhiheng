"""Goal-driven workspace UI at isolated HTTP/SSE boundaries; no live model calls."""
import asyncio
import copy
import hashlib
import json
import re
import unittest
from urllib.parse import parse_qs, urlparse

from playwright.async_api import expect
from developer_regressions import DeveloperBoundary
from permission_fixture import granted_permissions

STAMP = '2026-11-17T12:00:00Z'


class WorkUIRegressions(DeveloperBoundary):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.options = {
            'models': [{'id': 'model-1', 'name': '办公工作模型', 'max_output_tokens': 8192}],
            'skills': [{'slug': 'documents', 'name': '文档制作', 'description': '整理和制作文档', 'version': 1}],
            'can_execute_code': True, 'sandbox_available': True,
        }
        self.available_skills = [
            {'id': f'skill-{index}', 'slug': slug, 'name': name, 'description': f'{name}的处理方法',
             'package_version': '1.0.0', 'version': 1, 'status': 'enabled',
             'source': 'builtin' if index < 5 else 'upload', 'revision_id': f'revision-{index}',
             'content_hash': f'hash-{index}', 'file_count': 1, 'total_bytes': 123}
            for index, (slug, name) in enumerate([
                ('documents', '文档制作'), ('sheets', '表格分析'), ('charts', '图表制作'),
                ('minutes', '会议纪要'), ('data', '数据核对'), ('report-check', '报告检查'),
                ('custom-review', '内部审阅')])
        ]
        self.options['skills'] = [{key: skill[key] for key in ('slug', 'name', 'description', 'version')}
                                  for skill in self.available_skills]
        self.task = self.make_task()
        self.tasks, self.runs, self.uploads, self.chat_requests = [], [], [], []
        self.run = {'id': 'work-run', 'resource_type': 'work_agent', 'resource_id': 'task-1',
                    'status': 'completed', 'attempt_id': 'attempt-1', 'attempt_no': 1,
                    'revision': 3, 'projection_seq': 2, 'text': '核实后的结论', 'reasoning': '',
                    'error': None, 'created_at': STAMP,
                    'usage': {'input_tokens': 20, 'output_tokens': 10, 'status': 'known'}}

    def make_task(self, identifier='task-1'):
        return {'id': identifier, 'owner_id': 'user-1', 'title': '研究目标', 'goal': '研究目标',
                'status': 'active', 'mode': 'agent', 'model_id': 'model-1', 'execution_state': 'idle',
                'version': 1, 'current_run_id': None, 'active_agent_id': None, 'preparation_id': None,
                'create_client_request_id': 'create-intent', 'created_at': STAMP, 'updated_at': STAMP,
                'configuration': {'model_id': 'model-1', 'skill_slugs': []},
                'members': [], 'messages': [], 'inputs': [], 'steps': [], 'artifacts': [], 'plan': []}

    def message(self, role, body, run=None):
        return {'id': f'msg-{len(self.task["messages"])+1}', 'client_request_id': body['client_request_id'],
                'role': role, 'content': body.get('message', '') if role == 'user' else (run or self.run)['text'],
                'reasoning': '' if role == 'user' else (run or self.run).get('reasoning', ''),
                'run_id': None if run is None else run['id'],
                'status': 'completed' if run is None else run['status'],
                'usage': None if run is None else run['usage'], 'error': None if run is None else run['error'],
                'agent_id': None, 'agent_name_snapshot': None, 'agent_revision': None,
                'sequence': len(self.task['messages'])+1, 'created_at': STAMP}

    async def respond(self, route):
        parsed, method = urlparse(route.request.url), route.request.method
        path = parsed.path
        if path.startswith('/api/chat/'):
            self.chat_requests.append((method, path))
        if path == '/api/work/options':
            return await route.fulfill(json=self.options)
        if path == '/api/skills/available':
            return await route.fulfill(json=self.available_skills)
        if path == '/api/work/tasks':
            if method == 'POST':
                body = route.request.post_data_json
                self.posts.append(copy.deepcopy(body))
                self.task = self.make_task()
                self.task.update(goal=body['goal'], title=body['goal'][:40] or '新任务',
                                 model_id=body['model_id'], create_client_request_id=body['client_request_id'],
                                 configuration={key: body[key] for key in ('model_id', 'skill_slugs')})
                if body['goal']:
                    self.task['messages'].append(self.message('user', {'client_request_id': body['client_request_id'], 'message': body['goal']}))
                self.tasks = [self.task]
                return await route.fulfill(status=201, json=self.task)
            return await route.fulfill(json={'items': self.tasks, 'total': len(self.tasks), 'page': 1, 'page_size': 20})
        match = re.fullmatch(r'/api/work/tasks/([^/]+)(.*)', path)
        if match:
            task_id, suffix = match.groups()
            selected = next((item for item in self.tasks if item['id'] == task_id), self.task if self.task['id'] == task_id else None)
            if selected is None:
                return await route.fulfill(status=404, json={'error': {'code': 'task_not_found', 'message': '任务不存在'}})
            if suffix == '':
                if method == 'PATCH':
                    body = route.request.post_data_json
                    self.posts.append(body)
                    selected.update({key: value for key, value in body.items() if key != 'expected_version'})
                    if selected['configuration'] is not None:
                        selected['configuration'].update({key: body[key] for key in ('model_id', 'skill_slugs') if key in body})
                    selected['version'] += 1
                if method == 'DELETE':
                    self.tasks = [item for item in self.tasks if item['id'] != task_id]
                    return await route.fulfill(status=204)
                return await route.fulfill(json=selected)
            if suffix == '/files' and method == 'POST':
                name = parse_qs(parsed.query)['filename'][0]
                content = route.request.post_data_buffer or b''
                self.uploads.append((name, content))
                version = max((item['version'] for item in selected['inputs'] if item['name'] == name), default=0)+1
                selected['inputs'].append({'file_id': f'file-{len(self.uploads)}', 'name': name, 'version': version,
                                          'size': len(content), 'mime': 'text/plain', 'sha256': hashlib.sha256(content).hexdigest(),
                                          'acl_version': 1, 'file_version': 1, 'created_at': STAMP})
                selected['version'] += 1
                return await route.fulfill(status=201, json=selected)
            if suffix == '/runs' and method == 'POST':
                body = route.request.post_data_json
                self.runs.append(copy.deepcopy(body))
                self.run['resource_id'] = task_id
                selected.update(version=selected['version']+1,
                                current_run_id=None if self.run['status'] == 'completed' else self.run['id'],
                                execution_state='completed' if self.run['status'] == 'completed' else 'running')
                if not selected['goal']:
                    selected.update(goal=body['message'], title=body['message'][:40])
                if not body.get('resume') and not any(item['client_request_id'] == body['client_request_id'] and item['role'] == 'user' for item in selected['messages']):
                    selected['messages'].append(self.message('user', body))
                selected['messages'].append(self.message('assistant', body, self.run))
                return await route.fulfill(status=202, json=self.run)
            if suffix == '/runs/work-run':
                return await route.fulfill(json=self.run)
            if suffix == '/runs/work-run/events':
                return await route.fulfill(content_type='text/event-stream', body='event: snapshot\ndata: '+json.dumps(self.run)+'\n\n')
            if suffix == '/runs/work-run/cancel':
                self.run.update(status='cancelled', revision=self.run['revision']+1)
                selected.update(current_run_id=None, version=selected['version']+1, execution_state='cancelled')
                if selected['messages']:
                    selected['messages'][-1].update(status='cancelled', content=self.run['text'])
                return await route.fulfill(json=self.run)
            if suffix.endswith('/download'):
                return await route.fulfill(body='name,value\na,42\n', content_type='text/csv')
            if suffix.endswith('/preview'):
                return await route.fulfill(body='<script>not executable</script>\n报告内容', content_type='text/plain')
        await super().respond(route)

    async def open_task(self):
        self.tasks = [self.task]
        await self.page.goto(self.origin+'/work?taskId='+self.task['id'])
        await expect(self.page.get_by_role('heading', name=self.task['title'], exact=True)).to_be_visible()

    async def open_work_history(self):
        await self.page.get_by_role('button', name='工作记录', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='工作记录', exact=True)
        await expect(dialog).to_be_visible()
        return dialog

    async def close_work_history(self):
        dialog = self.page.get_by_role('dialog', name='工作记录', exact=True)
        await dialog.get_by_role('button', name='Close', exact=True).click()
        await expect(dialog).to_be_hidden()

    async def task_action(self, label):
        await self.page.get_by_role('button', name='更多任务操作', exact=True).click()
        await self.page.get_by_role('menuitem', name=label, exact=True).click()

    async def start_work(self, goal='研究目标'):
        await self.page.goto(self.origin+'/work')
        await self.page.get_by_role('textbox', name='工作目标', exact=True).fill(goal)
        await expect(self.page.get_by_role('button', name='开始工作', exact=True)).to_be_enabled()
        await self.page.get_by_role('button', name='开始工作', exact=True).click()
        await expect(self.page.get_by_role('heading', name=goal[:40], exact=True)).to_be_visible()

    async def test_create_selects_model_and_runs_goal_once_without_members(self):
        await self.start_work()
        await expect(self.page.get_by_text('核实后的结论', exact=True)).to_be_visible()
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(len(self.runs), 1)
        self.assertEqual(self.posts[0]['model_id'], 'model-1')
        self.assertEqual(self.posts[0]['skill_slugs'], [])
        self.assertNotIn('allow_code_execution', self.posts[0])
        self.assertNotIn('agent_ids', self.posts[0])
        self.assertNotIn('agent_id', self.runs[0])
        self.assertEqual(self.posts[0]['client_request_id'], self.runs[0]['client_request_id'])
        self.assertEqual(self.runs[0]['message'], '研究目标')

    async def test_upload_first_creates_draft_then_uses_refreshed_version(self):
        await self.page.goto(self.origin+'/work')
        await expect(self.page.get_by_role('button', name='添加附件', exact=True)).to_be_enabled()
        async with self.page.expect_file_chooser() as chooser:
            await self.page.get_by_role('button', name='添加附件', exact=True).click()
        await (await chooser.value).set_files({'name': 'data.csv', 'mimeType': 'text/csv', 'buffer': b'amount\n42\n'})
        await expect(self.page.get_by_role('heading', name='新任务', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('data.csv', exact=True)).to_be_visible()
        self.assertEqual(self.posts[0]['goal'], '')
        self.assertEqual(self.runs, [])
        message = self.page.get_by_role('textbox', name='工作消息', exact=True)
        await message.fill('核对金额并给出结果')
        await self.page.get_by_role('button', name='发送', exact=True).click()
        await expect(self.page.get_by_text('核实后的结论', exact=True)).to_be_visible()
        self.assertEqual(self.runs[0]['expected_version'], 2)
        self.assertNotEqual(self.runs[0]['client_request_id'], self.posts[0]['client_request_id'])

    async def test_submitting_places_question_in_timeline_and_clears_input_immediately(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def held(route):
            entered.set()
            await release.wait()
            await self.respond(route)
        await self.open_task()
        await self.page.route(self.origin+'/api/work/tasks/task-1/runs', held)
        text = self.page.get_by_role('textbox', name='工作消息', exact=True)
        await text.fill('请处理我的资料')
        await self.page.get_by_role('button', name='发送', exact=True).click()
        try:
            await asyncio.wait_for(entered.wait(), 3)
            await expect(text).to_have_value('')
            await expect(self.page.locator('.work-message.user')).to_contain_text('请处理我的资料')
        finally:
            release.set()
        await expect(self.page.get_by_text('核实后的结论', exact=True)).to_be_visible()

    async def test_history_compact_rows_keep_goal_in_tooltip_and_one_column_modal(self):
        self.task.update(title='有明确目标的工作记录', goal='核对资料并制作成果。'*20)
        self.tasks = [self.task, {**copy.deepcopy(self.task), 'id': 'task-2', 'title': '正在执行的记录', 'current_run_id': 'run-2'}]
        await self.page.goto(self.origin+'/work')
        dialog = await self.open_work_history()
        cards = dialog.locator('.recent-work__item.history-record')
        await expect(cards).to_have_count(2)
        await expect(cards.first.get_by_role('button').first).to_have_attribute('title', self.task['title']+'\n'+self.task['goal'])
        await expect(cards.nth(1)).to_contain_text('正在执行')
        first, second = await cards.evaluate_all('(nodes)=>nodes.map(node=>node.getBoundingClientRect().toJSON())')
        self.assertAlmostEqual(first['x'], second['x'], delta=1)
        self.assertGreaterEqual(second['y'], first['y']+first['height'])
        self.assertLessEqual((await dialog.locator('.ant-modal-content').bounding_box())['width'], 622)

    async def test_history_search_updates_once_without_duplicate_value_handlers(self):
        warnings = []
        searches = []
        self.page.on('console', lambda message: warnings.append(message.text) if '[Vue warn]' in message.text else None)
        self.page.on('request', lambda request: searches.append(parse_qs(urlparse(request.url).query).get('search', [''])[0])
                     if urlparse(request.url).path == '/api/work/tasks' and 'search=' in request.url else None)
        await self.open_task()
        dialog = await self.open_work_history()
        search = dialog.get_by_role('textbox', name='搜索最近工作', exact=True)
        await search.fill('报告')
        await asyncio.sleep(0.5)
        self.assertEqual(searches.count('报告'), 1)
        await search.fill('')
        await asyncio.sleep(0.1)
        self.assertFalse(any('onUpdate:value' in message for message in warnings), warnings)

    async def test_history_modal_preserves_new_goal_and_current_message_drafts(self):
        await self.page.goto(self.origin+'/work')
        goal = self.page.get_by_role('textbox', name='工作目标', exact=True)
        await goal.fill('暂未发送的目标')
        await self.open_work_history()
        await self.close_work_history()
        await expect(goal).to_have_value('暂未发送的目标')
        await self.open_task()
        text = self.page.get_by_role('textbox', name='工作消息', exact=True)
        await text.fill('暂未发送的修改要求')
        await self.open_work_history()
        await self.close_work_history()
        await expect(text).to_have_value('暂未发送的修改要求')

    async def test_legacy_task_is_read_only_and_does_not_restore_old_preparation(self):
        self.task.update(mode='legacy', configuration=None, model_id=None, preparation_id='old-preparation', current_run_id='old-run')
        requests=[]
        self.page.on('request', lambda request: requests.append(urlparse(request.url).path))
        await self.open_task()
        await expect(self.page.get_by_text('历史任务仅供查看，新建任务可自动规划并处理资料。', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('textbox', name='工作消息', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button', name='停止', exact=True)).to_have_count(0)
        self.assertFalse(any('/knowledge/preparations/' in path for path in requests))
        self.assertFalse(any('/runs/old-run' in path for path in requests))

    async def test_skill_entry_groups_searches_and_selects_without_a_resource_dialog(self):
        await self.page.goto(self.origin+'/work')
        await expect(self.page.get_by_role('button', name='资源与能力', exact=True)).to_have_count(0)
        await expect(self.page.locator('.model-select')).to_be_visible()
        await self.page.get_by_role('button', name='选择技能', exact=True).click()
        picker = self.page.locator('.skill-picker-panel')
        await expect(picker.get_by_text('预置技能', exact=True)).to_be_visible()
        await expect(picker.get_by_text('我的技能', exact=True)).to_be_visible()
        search = picker.get_by_role('combobox', name='搜索技能', exact=True)
        await search.fill('报告')
        await expect(picker.get_by_role('option')).to_have_count(1)
        await picker.get_by_role('option', name='报告检查', exact=True).click()
        await search.fill('')
        await picker.get_by_role('option', name='文档制作', exact=True).click()
        await expect(self.page.get_by_role('button', name='移除技能：报告检查', exact=True)).to_be_visible()
        await picker.get_by_role('button', name='收起技能选择', exact=True).click()
        await expect(self.page.get_by_role('switch', name='文件处理与代码执行', exact=True)).to_have_count(0)
        await self.page.get_by_role('textbox', name='工作目标', exact=True).fill('制作报告')
        await self.page.get_by_role('button', name='开始工作', exact=True).click()
        await expect(self.page.get_by_text('核实后的结论', exact=True)).to_be_visible()
        self.assertNotIn('allow_code_execution', self.posts[0])
        self.assertEqual(self.posts[0]['skill_slugs'], ['report-check', 'documents'])

    async def test_task_skill_selection_saves_and_tag_removal_preserves_the_message(self):
        self.task['configuration']['skill_slugs'] = ['documents']
        await self.open_task()
        text = self.page.get_by_role('textbox', name='工作消息', exact=True)
        await text.fill('尚未发送的修改要求')
        await expect(self.page.get_by_role('button', name='移除技能：文档制作', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='选择技能', exact=True).click()
        picker = self.page.locator('.skill-picker-panel')
        await picker.get_by_role('option', name='报告检查', exact=True).click()
        await expect(self.page.get_by_role('button', name='移除技能：报告检查', exact=True)).to_be_visible()
        await expect(picker).to_be_visible()
        self.assertEqual(self.posts[-1], {'skill_slugs': ['documents', 'report-check'], 'expected_version': 1})
        await picker.get_by_role('button', name='收起技能选择', exact=True).click()
        await self.page.get_by_role('button', name='移除技能：文档制作', exact=True).click()
        await expect(self.page.get_by_role('button', name='移除技能：文档制作', exact=True)).to_have_count(0)
        await expect(text).to_have_value('尚未发送的修改要求')
        self.assertEqual(self.posts[-1], {'skill_slugs': ['report-check'], 'expected_version': 2})
        self.assertEqual(self.runs, [])

    async def test_skill_limit_and_mobile_layout_keep_the_composer_usable(self):
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        await self.page.goto(self.origin+'/work')
        await self.page.get_by_role('button', name='选择技能', exact=True).click()
        picker = self.page.locator('.skill-picker-panel')
        for name in ['文档制作', '表格分析', '图表制作', '会议纪要', '数据核对', '报告检查']:
            await picker.get_by_role('option', name=name, exact=True).click()
        await expect(picker.get_by_text('已选 5 / 5', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name=re.compile('^移除技能：'))).to_have_count(5)
        await picker.get_by_role('combobox', name='搜索技能', exact=True).press('Escape')
        await expect(picker).to_have_count(0)
        self.assertFalse(await self.page.evaluate('document.documentElement.scrollWidth > window.innerWidth'))
        await expect(self.page.get_by_role('button', name='添加附件', exact=True)).to_be_visible()

    async def test_running_tool_is_visible_without_expanding_nested_records(self):
        self.task.update(current_run_id='work-run', execution_state='running')
        self.run.update(status='running', text='', revision=1)
        self.task['messages'] = [self.message('assistant', {'client_request_id':'progress-intent'}, self.run)]
        self.task['messages'][0]['usage'] = {'status': 'pending'}
        self.task['steps'] = [{
            'id':'step-python', 'run_id':'work-run', 'action_id':'call-python', 'kind':'tool',
            'name':'run_python', 'status':'running', 'summary':'正在绘制图片',
            'details':{'arguments':{'code':'print("drawing")','packages':['opencv-python-headless']}},
            'result':None, 'created_at':STAMP, 'updated_at':STAMP,
        }]
        await self.open_task()
        progress=self.page.get_by_role('region',name='任务进度',exact=True)
        await expect(progress.get_by_text('run_python',exact=True)).to_be_visible()
        await expect(progress.get_by_text('正在绘制图片',exact=True)).to_be_visible()
        await expect(progress.get_by_text('正在执行',exact=True)).to_be_visible()
        await progress.get_by_text('查看处理代码',exact=True).click()
        await expect(progress.get_by_text('print("drawing")',exact=True)).to_be_visible()

    async def test_lost_admission_confirms_original_key_and_version(self):
        posted=[]
        async def admission(route):
            posted.append(copy.deepcopy(route.request.post_data_json))
            if len(posted)==1:
                return await route.fulfill(status=503,json={'error':{'code':'unavailable','message':'结果尚未确认'}})
            await self.respond(route)
        await self.open_task()
        await self.page.route(self.origin+'/api/work/tasks/task-1/runs',admission)
        await self.page.get_by_role('textbox',name='工作消息',exact=True).fill('请处理文件')
        await self.page.get_by_role('button',name='发送',exact=True).click()
        await self.page.get_by_role('button',name='确认原请求',exact=True).click()
        await expect(self.page.get_by_text('核实后的结论',exact=True)).to_be_visible()
        self.assertEqual(posted[0],posted[1])

    async def test_stop_deduplicates_and_waits_for_acknowledgement(self):
        self.task['current_run_id']='work-run'
        self.run.update(status='running',revision=1)
        held,release=asyncio.Event(),asyncio.Event()
        calls=[]
        async def stop(route):
            calls.append(route.request.url);held.set();await release.wait();await self.respond(route)
        await self.open_task()
        await self.page.route(self.origin+'/api/work/tasks/task-1/runs/work-run/cancel',stop)
        button=self.page.get_by_role('button',name='停止',exact=True)
        await button.click()
        try:
            await asyncio.wait_for(held.wait(),3)
            await expect(button).to_be_disabled()
            await expect(self.page.get_by_role('textbox',name='工作消息',exact=True)).to_be_disabled()
            self.assertEqual(len(calls),1)
        finally:release.set()
        await expect(self.page.get_by_role('textbox',name='工作消息',exact=True)).to_be_enabled()

    async def test_late_detail_cannot_replace_new_task_page(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def delayed(route):
            entered.set();await release.wait();await route.fulfill(json=self.task)
        await self.page.route(self.origin+'/api/work/tasks/task-1',delayed)
        await self.page.goto(self.origin+'/work?taskId=task-1')
        await asyncio.wait_for(entered.wait(),3)
        await self.page.get_by_role('button',name='新建工作',exact=True).click()
        release.set()
        await expect(self.page.get_by_role('textbox',name='工作目标',exact=True)).to_be_visible()
        await expect(self.page.get_by_role('heading',name='研究目标',exact=True)).to_have_count(0)

    async def test_task_resources_and_results_fit_mobile_drawer(self):
        await self.page.set_viewport_size({'width':390,'height':844})
        await self.open_task()
        await self.page.get_by_role('button',name='查看资料与成果',exact=True).click()
        drawer=self.page.locator('.ant-drawer-open .ant-drawer-content')
        await expect(drawer.get_by_text('资料与成果',exact=True).first).to_be_visible()
        size=await self.page.evaluate('({width:innerWidth,scroll:document.documentElement.scrollWidth})')
        self.assertLessEqual(size['scroll'],size['width']+1)

    async def test_execution_log_uses_actual_result_and_hides_internal_snapshot_fields(self):
        self.task['steps']=[{'id':'step-1','run_id':'work-run','action_id':'a','kind':'tool','name':'run_python','status':'succeeded','summary':'已经核对记录数','details':{'arguments_fingerprint':'hidden-fingerprint','arguments':{'code':'print(42)'}},'result':{'success':True,'data':{'stdout':'真实计算结果 42','stderr':''},'_workspace_files':[{'file_id':'hidden-snapshot'}]},'created_at':STAMP,'updated_at':STAMP}]
        body = {'client_request_id': 'original', 'message': '请核对资料'}
        self.task['messages'].append(self.message('user', body))
        self.task['messages'].append(self.message('assistant', body, self.run))
        await self.open_task()
        assistant = self.page.locator('.work-message.assistant')
        await expect(assistant.get_by_role('region', name='任务进度', exact=True)).to_be_visible()
        await assistant.get_by_text('执行记录',exact=True).click()
        await self.page.get_by_text('查看执行记录 · 1 步',exact=True).click()
        await expect(self.page.get_by_text('真实计算结果 42',exact=True)).to_be_visible()
        await expect(self.page.get_by_text('hidden-fingerprint',exact=False)).to_have_count(0)
        await expect(self.page.get_by_text('hidden-snapshot',exact=False)).to_have_count(0)

    async def test_reopen_and_archive_use_task_version(self):
        self.task['status']='completed'
        await self.open_task()
        await self.task_action('重新打开')
        await expect(self.page.get_by_role('textbox',name='工作消息',exact=True)).to_be_enabled()
        self.assertEqual(self.posts[-1]['expected_version'],1)
        await self.task_action('归档任务')
        await expect(self.page.get_by_text('任务已归档，资料和成果继续保留。',exact=True)).to_be_visible()
        self.assertEqual(self.posts[-1]['expected_version'],2)

    async def test_ime_enter_does_not_send_a_work_message(self):
        await self.open_task()
        text=self.page.get_by_role('textbox',name='工作消息',exact=True)
        await text.fill('尚在输入')
        await text.dispatch_event('keydown',{'key':'Enter','code':'Enter','isComposing':True})
        await expect(text).to_have_value('尚在输入')
        self.assertEqual(self.runs,[])

    async def test_direct_task_link_keeps_chat_and_workspace_independent(self):
        await self.open_task()
        self.assertEqual(self.chat_requests,[])
        await expect(self.page.get_by_role('textbox',name='工作消息',exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button',name='添加智能体',exact=True)).to_have_count(0)


    def add_artifact_reply(self, run_id, artifacts, content='文件已生成'):
        run = {**self.run, 'id': run_id, 'text': content}
        self.task['messages'].append(self.message('assistant', {'client_request_id': run_id}, run))
        self.task['artifacts'].extend([{**item, 'run_id': run_id} for item in artifacts])

    async def test_inline_artifacts_stay_on_their_reply_after_reload_and_download_original(self):
        old = self.artifact('old', '报告.md', 1)
        old.update(mime='text/markdown', preview_file_id=None)
        new = {**old, 'id': 'new', 'file_id': 'file-new', 'version': 2}
        self.add_artifact_reply('run-1', [old], '[查看报告](outputs/%E6%8A%A5%E5%91%8A.md)')
        self.add_artifact_reply('run-2', [new], '已按要求修改')
        await self.open_task()
        for reload in [False, True]:
            if reload:
                await self.page.reload()
            replies = self.page.locator('.work-message.assistant')
            await expect(replies.nth(0).locator('[id="work-artifact-old"]')).to_have_count(1)
            await expect(replies.nth(1).locator('[id="work-artifact-new"]')).to_have_count(1)
            await expect(replies.nth(0).locator('[id="work-artifact-new"]')).to_have_count(0)
        await self.page.get_by_role('link').filter(has_text='查看报告').click()
        drawer = self.page.locator('.work-artifact-preview')
        await expect(drawer.get_by_text('报告内容', exact=False)).to_be_visible()
        async with self.page.expect_download() as info:
            await drawer.get_by_role('button', name='下载', exact=True).click()
        self.assertEqual((await info.value).suggested_filename, '报告.md')
        self.assertTrue(self.page.url.endswith('/work?taskId=task-1'))

    async def test_inline_image_preview_switches_images_and_fits_mobile(self):
        import base64
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9X8AAAAASUVORK5CYII=')
        async def image(route):
            await route.fulfill(body=png, content_type='image/png')
        await self.page.route(self.origin + '/api/work/tasks/task-1/artifacts/*/preview', image)
        files = [{**self.artifact(str(i), f'图片{i}.png', 1), 'mime': 'image/png', 'preview_file_id': None} for i in [1, 2]]
        self.add_artifact_reply('work-run', files)
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        await self.open_task()
        first = self.page.locator('[id="work-artifact-1"]')
        await first.scroll_into_view_if_needed()
        await expect(first.get_by_role('img', name='图片1.png', exact=True)).to_be_visible()
        await first.get_by_role('button', name='预览 图片1.png', exact=True).click()
        drawer = self.page.locator('.work-artifact-preview')
        await expect(drawer.get_by_role('img', name='图片1.png', exact=True)).to_be_visible()
        await drawer.get_by_role('button', name='下一张图片', exact=True).click()
        await expect(drawer.get_by_role('img', name='图片2.png', exact=True)).to_be_visible()
        size = await self.page.evaluate('({width: innerWidth, scroll: document.documentElement.scrollWidth})')
        self.assertLessEqual(size['scroll'], size['width'] + 1)

    async def test_unpreviewable_document_still_opens_a_downloadable_file_panel(self):
        item = self.artifact('docx', '报告.docx', 1)
        item['preview_file_id'] = None
        self.add_artifact_reply('work-run', [item])
        await self.open_task()
        await self.page.get_by_role('button', name='预览 报告.docx', exact=True).click()
        drawer = self.page.locator('.work-artifact-preview')
        await expect(drawer.get_by_text('此格式暂不支持在线预览，可下载查看', exact=True)).to_be_visible()
        await expect(drawer.get_by_role('button', name='下载', exact=True)).to_be_enabled()

    async def test_new_task_discards_an_unfinished_artifact_preview(self):
        item = self.artifact('old', '报告.txt', 1)
        item.update(mime='text/plain', preview_file_id=None)
        self.add_artifact_reply('work-run', [item])
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(route):
            entered.set()
            await release.wait()
            try:
                await route.fulfill(body='过期预览内容', content_type='text/plain')
            except Exception:
                pass  # Navigation cancels the old request.
        await self.page.route(self.origin + '/api/work/tasks/task-1/artifacts/old/preview', delayed)
        await self.open_task()
        await self.page.get_by_role('button', name='预览 报告.txt', exact=True).click()
        try:
            await asyncio.wait_for(entered.wait(), 3)
            await self.page.goto(self.origin + '/work')
        finally:
            release.set()
        await expect(self.page.get_by_role('textbox', name='工作目标', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('过期预览内容', exact=True)).to_have_count(0)

    def artifact(self, identifier, name, version):
        return {'id':identifier,'run_id':'work-run','file_id':'file-'+identifier,'name':name,
                'logical_path':'outputs/'+name,'version':version,'size':16384,'mime':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                'sha256':'a'*64,'validation':{'valid':True},'preview_file_id':'preview-'+identifier,
                'created_at':STAMP,'url':f'/api/work/tasks/task-1/artifacts/{identifier}/download',
                'preview_url':f'/api/work/tasks/task-1/artifacts/{identifier}/preview'}

    async def test_work_and_chat_share_the_same_composer_geometry_and_typography(self):
        async def surface():
            return await self.page.locator('.composer').evaluate('''node => {
                const surface = node.querySelector('.composer-surface');
                const input = node.querySelector('textarea');
                const button = node.querySelector('.composer-submit');
                const style = getComputedStyle(input);
                return { width: surface.getBoundingClientRect().width,
                    radius: getComputedStyle(surface).borderRadius,
                    font: style.fontFamily, size: style.fontSize, lineHeight: style.lineHeight,
                    buttonWidth: button.getBoundingClientRect().width,
                    buttonRadius: getComputedStyle(button).borderRadius };
            }''')
        await self.page.goto(self.origin + '/work')
        await expect(self.page.get_by_role('textbox', name='工作目标', exact=True)).to_be_visible()
        work_surface = await surface()
        await self.page.goto(self.origin + '/chat')
        await expect(self.page.get_by_role('textbox', name='消息内容', exact=True)).to_be_visible()
        self.assertEqual(work_surface, await surface())

    async def test_inline_model_change_preserves_draft_and_uses_task_version(self):
        self.options['models'].append({'id': 'model-2', 'name': '备用模型', 'max_output_tokens': 8192})
        await self.open_task()
        draft = self.page.get_by_role('textbox', name='工作消息', exact=True)
        await draft.fill('继续核对我的资料')
        await self.page.locator('.model-select').click()
        await self.page.get_by_text('备用模型', exact=True).click()
        await expect(self.page.locator('.model-select')).to_contain_text('备用模型')
        await expect(draft).to_have_value('继续核对我的资料')
        self.assertEqual(self.posts[-1], {'expected_version': 1, 'model_id': 'model-2'})
        self.assertEqual(self.runs, [])

    async def test_task_resource_editing_remains_available_without_execution_permission(self):
        self.options['models'].append({'id': 'model-2', 'name': '另一个工作模型', 'max_output_tokens': 8192})
        await self.page.route(self.origin + '/api/auth/me', lambda route: route.fulfill(json={
            'id': 'user-1', 'username': 'task-editor', 'is_admin': False,
            'permissions': granted_permissions('platform:work:read', 'platform:work:update')}))
        await self.open_task()
        await expect(self.page.get_by_role('textbox', name='工作消息', exact=True)).to_have_count(0)
        await self.task_action('选择模型')
        dialog = self.page.get_by_role('dialog', name='选择模型', exact=True)
        await expect(dialog).to_be_visible()
        await dialog.locator('.ant-select').click()
        await self.page.get_by_text('另一个工作模型', exact=True).click()
        await expect(dialog).to_be_hidden()
        self.assertEqual(self.posts[-1]['expected_version'], 1)
        self.assertEqual(self.posts[-1]['model_id'], 'model-2')
        self.assertEqual(self.runs, [])

    async def test_existing_artifacts_default_to_results_and_history_downloads_exact_version(self):
        self.task['artifacts']=[self.artifact('old','分析报告.docx',1),self.artifact('new','分析报告.docx',2)]
        seen=[]
        self.page.on('request',lambda request:seen.append(urlparse(request.url).path))
        await self.open_task()
        await self.page.get_by_role('button', name='查看资料与成果', exact=True).click()
        await expect(self.page.get_by_role('tab',name='成果 1',exact=True)).to_have_attribute('aria-selected','true')
        card=self.page.locator('.work-file')
        await expect(card).to_have_count(1)
        await expect(card).to_contain_text('v2')
        await card.get_by_role('button',name='查看文件版本',exact=True).click()
        async with self.page.expect_download() as saved:
            await self.page.get_by_role('menuitem',name=re.compile(r'^v1')).click()
        self.assertEqual((await saved.value).suggested_filename,'分析报告.docx')
        self.assertIn('/api/work/tasks/task-1/artifacts/old/download',seen)

    async def test_preview_is_safe_text_and_later_artifacts_preserve_manual_tab_choice(self):
        self.task['artifacts']=[self.artifact('new','分析报告.docx',2)]
        await self.open_task()
        await self.page.get_by_role('button', name='查看资料与成果', exact=True).click()
        await self.page.locator('.work-file').get_by_role('button',name='预览',exact=True).click()
        preview=self.page.locator('.work-artifact-preview')
        await expect(preview.locator('pre')).to_contain_text('<script>not executable</script>')
        await expect(preview.locator('script')).to_have_count(0)
        await preview.get_by_role('button',name='Close',exact=True).click()
        await self.page.get_by_role('tab',name='资料 0',exact=True).click()
        async def upload(route):
            self.task['artifacts'].append(self.artifact('later','分析报告.docx',3))
            await self.respond(route)
        await self.page.route(self.origin+'/api/work/tasks/task-1/files?*',upload)
        async with self.page.expect_file_chooser() as chooser:
            await self.page.get_by_role('button',name='上传任务资料',exact=True).click()
        await (await chooser.value).set_files({'name':'extra.txt','mimeType':'text/plain','buffer':b'x'})
        await expect(self.page.locator('.ant-drawer-open').get_by_text('extra.txt',exact=True)).to_be_visible()
        await expect(self.page.get_by_role('tab',name='资料 1',exact=True)).to_have_attribute('aria-selected','true')

    async def test_failed_task_continue_uses_checkpoint_without_resending_goal(self):
        original = {'client_request_id': 'original-request', 'message': '只制作一次报告'}
        self.task['goal'] = original['message']
        self.task['messages'].append(self.message('user', original))
        failed = {**self.run, 'id': 'failed-run', 'status': 'failed', 'text': '',
                  'error': {'code': 'work_model_failed', 'message': '最终答复失败，成果已保留'}}
        self.task['messages'].append(self.message('assistant', original, failed))
        self.task.update(execution_state='failed', version=4)
        await self.open_task()
        await expect(self.page.get_by_role('button', name='重新执行', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='继续执行', exact=True).click()
        await expect(self.page.get_by_text('核实后的结论', exact=True)).to_be_visible()
        self.assertEqual(len(self.runs), 1)
        self.assertTrue(self.runs[0]['resume'])
        self.assertEqual(self.runs[0]['message'], '')
        self.assertNotEqual(self.runs[0]['client_request_id'], original['client_request_id'])
        self.assertEqual([item['content'] for item in self.task['messages'] if item['role'] == 'user'], [original['message']])

    async def test_budget_pause_has_accessible_continue_without_duplicate_user_question(self):
        self.run.update(status='interrupted',error={'code':'work_budget_exhausted','message':'本轮预算已用完'})
        body={'client_request_id':'original','message':'完成原始目标'}
        self.task['messages']=[self.message('user',body),self.message('assistant',body,self.run)]
        self.task['execution_state']='paused'
        await self.open_task()
        self.run.update(status='completed',error=None)
        button=self.page.get_by_role('button',name='继续执行',exact=True)
        await expect(button).to_be_enabled()
        await button.click()
        await expect(self.page.get_by_text('核实后的结论',exact=True).last).to_be_visible()
        self.assertTrue(self.runs[0]['resume'])
        self.assertEqual(self.runs[0]['message'],'')
        self.assertEqual(sum(message['role']=='user' for message in self.task['messages']),1)

    async def test_legacy_history_keeps_participant_names_without_member_commands(self):
        self.task.update(mode='legacy',model_id=None,configuration=None,members=[{'agent_id':'old-agent','agent_name_snapshot':'历史研究助手','agent_revision':1,'latest_agent_revision':None,'available':False,'removed_at':STAMP}])
        await self.open_task()
        await self.task_action('查看完整目标')
        info=self.page.get_by_role('dialog',name='工作目标',exact=True)
        await expect(info.get_by_text('历史研究助手',exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button',name='采用最新配置',exact=True)).to_have_count(0)


if __name__=='__main__':
    unittest.main(verbosity=2)
