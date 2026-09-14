"""Browser acceptance for the chat/work split, with stateful API fixtures."""

import asyncio
import copy
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from playwright.async_api import async_playwright, expect
from platform_shell_check import response_for

BASE_URL = os.environ.get('CHAT_WORK_CHECK_BASE_URL', 'http://127.0.0.1:12584')
ARTIFACTS = Path('/tmp/chat-work-acceptance')
PREFIX = '/agent_extension/work-tasks'
STAMP = '2026-09-11T08:00:00'
AGENTS = [
    {'agentId': '11111111-1111-4111-8111-111111111111', 'name': '研究助手',
     'description': '梳理背景和资料', 'status': 'published', 'publishedVersion': 2},
    {'agentId': '22222222-2222-4222-8222-222222222222', 'name': '数据分析师',
     'description': '分析关键指标', 'status': 'published', 'publishedVersion': 3},
]


def button(scope, label):
    return scope.get_by_role('button', name=re.compile(
        r'^(?:[A-Za-z][A-Za-z0-9_-]*\s+)*' + r'\s*'.join(map(re.escape, label)) + r'\s*$'))


def event(name, payload):
    return 'event: ' + name + '\ndata: ' + json.dumps(payload, ensure_ascii=False) + '\n\n'


class WorkFixture:
    def __init__(self, permissions=None):
        self.permissions = permissions if permissions is not None else ['*:*:*']
        self.tasks = {}
        self.calls = []
        self.fail_create = True
        self.run_count = 0

    def member(self, task_id, agent_id):
        agent = next(agent for agent in AGENTS if agent['agentId'] == agent_id)
        return {'taskId': task_id, 'agentId': agent_id, 'agentNameSnapshot': agent['name'],
                'publishedVersion': agent['publishedVersion'], 'available': True,
                'latestPublishedVersion': agent['publishedVersion'], 'addedAt': STAMP, 'removedAt': None}

    def respond(self, path, method, body, query):
        self.calls.append((path, method, copy.deepcopy(body)))
        if path == '/getInfo':
            return {**response_for(path, method, 'all'), 'permissions': self.permissions}
        if path == '/ai/models':
            return {'code': 200, 'rows': [{'modelId': 'mdl_22222222222242228222222222222222',
                                          'displayName': '测试聊天模型', 'modelCode': 'test-chat',
                                          'modelType': 'chat', 'lifecycleStatus': 'active'}], 'total': 1}
        if path == '/ai/chat/send':
            assert 'knowledgeBaseIds' not in body, body
            return event('meta', {'sessionId': 'chat-created', 'runId': 'chat-run'}) + event(
                'content', {'delta': '聊天技能回复'}) + event('done', {'status': 'completed'})
        if not path.startswith(PREFIX):
            return response_for(path, method, 'all')
        suffix = path[len(PREFIX):]
        if suffix == '/available-agents':
            return {'code': 200, 'data': copy.deepcopy(AGENTS)}
        if suffix == '' and method == 'GET':
            status = query.get('status', [None])[0]
            rows = [{key: copy.deepcopy(value) for key, value in task.items()
                     if key not in {'members', 'messages'}} for task in self.tasks.values() if
                    (task['status'] == status if status else task['status'] != 'archived')]
            return {'code': 200, 'rows': copy.deepcopy(rows), 'total': len(rows), 'pageNum': 1, 'pageSize': 20}
        if suffix == '' and method == 'POST':
            if self.fail_create:
                self.fail_create = False
                return {'code': 409, 'msg': '模拟创建失败，请重试'}
            existing = next((task for task in self.tasks.values() if
                             task['messages'][0]['clientMessageId'] == body['clientMessageId']), None)
            if existing:
                return {'code': 200, 'data': copy.deepcopy(existing)}
            task_id = str(uuid4())
            task = {'taskId': task_id, 'ownerUserId': 1, 'title': body['goal'][:40], 'goal': body['goal'],
                    'status': 'active', 'activeAgentId': body['activeAgentId'], 'currentRunId': None, 'version': 1,
                    'createdAt': STAMP, 'updatedAt': STAMP,
                    'members': [self.member(task_id, agent_id) for agent_id in body['agentIds']],
                    'messages': [{'messageId': str(uuid4()), 'taskId': task_id, 'role': 'user',
                                  'content': body['goal'], 'clientMessageId': body['clientMessageId'],
                                  'createdAt': STAMP}]}
            self.tasks[task_id] = task
            return {'code': 200, 'data': copy.deepcopy(task)}
        task_id = suffix.strip('/').split('/')[0]
        task = self.tasks.get(task_id)
        if not task:
            return {'code': 404, 'msg': '工作任务不存在'}
        if suffix.endswith('/runs') and method == 'POST':
            member = next(member for member in task['members'] if member['agentId'] == body['agentId'])
            assert task['status'] == 'active' and member['available'] and not member['removedAt']
            user = next((message for message in task['messages'] if
                         message.get('clientMessageId') == body['clientMessageId']), None)
            if user is None:
                user = {'messageId': str(uuid4()), 'taskId': task_id, 'role': 'user',
                        'content': body['message'], 'clientMessageId': body['clientMessageId'], 'createdAt': STAMP}
                task['messages'].append(user)
            self.run_count += 1
            run_id, message_id = str(uuid4()), str(uuid4())
            task['currentRunId'] = run_id
            text = member['agentNameSnapshot'] + '完成：' + body['message']
            task['activeAgentId'] = member['agentId']
            task['messages'].append({'messageId': message_id, 'taskId': task_id, 'role': 'assistant',
                                     'replyToMessageId': user['messageId'], 'content': text,
                                     'reasoningContent': '', 'agentId': member['agentId'],
                                     'agentNameSnapshot': member['agentNameSnapshot'],
                                     'publishedVersion': member['publishedVersion'], 'runId': run_id,
                                     'runStatus': 'completed', 'createdAt': STAMP})
            task['version'] += 1
            task['currentRunId'] = None
            return event('meta', {'taskId': task_id, 'runId': run_id, 'messageId': message_id,
                                  'agentId': member['agentId'], 'agentName': member['agentNameSnapshot'],
                                  'publishedVersion': member['publishedVersion']}) + event(
                'content', {'taskId': task_id, 'runId': run_id, 'delta': text}) + event(
                'done', {'taskId': task_id, 'runId': run_id, 'status': 'completed'})
        if '/members' in suffix:
            assert body.get('expectedVersion', task['version']) == task['version']
            if method == 'POST':
                task['members'].append(self.member(task_id, body['agentId']))
            else:
                member = next(member for member in task['members'] if member['agentId'] == suffix.split('/')[-1])
                if method == 'DELETE':
                    member['removedAt'] = STAMP
                elif method == 'PUT':
                    member['publishedVersion'] = member['latestPublishedVersion']
            task['version'] += 1
        elif method == 'PUT':
            assert body['expectedVersion'] == task['version']
            task.update({key: value for key, value in body.items() if key != 'expectedVersion'})
            task['version'] += 1
        elif method == 'DELETE':
            del self.tasks[task_id]
            return {'code': 200, 'msg': '已删除'}
        return {'code': 200, 'data': copy.deepcopy(task)}

    async def install(self, context):
        async def handle(route):
            request = route.request
            parsed = urlparse(request.url)
            path = re.sub(r'^/(dev-api|docker-api)', '', parsed.path)
            body = request.post_data_json if request.post_data else {}
            payload = self.respond(path, request.method, body or {}, parse_qs(parsed.query))
            if isinstance(payload, str):
                await route.fulfill(content_type='text/event-stream', body=payload)
            else:
                await route.fulfill(content_type='application/json', body=json.dumps(payload, ensure_ascii=False))
        await context.route('**/dev-api/**', handle)
        await context.route('**/docker-api/**', handle)
        await context.add_cookies([{'name': 'Admin-Token', 'value': 'work-test-token', 'url': BASE_URL}])


async def desktop(browser):
    context = await browser.new_context(viewport={'width': 1440, 'height': 960})
    fixture = WorkFixture()
    await fixture.install(context)
    page = await context.new_page()
    page.set_default_timeout(20000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        await page.goto(BASE_URL + '/ai/chat', wait_until='networkidle')
        switch = page.get_by_role('group', name='选择聊天或工作')
        await expect(switch).to_be_visible(timeout=30000)
        await expect(page.get_by_label('选择技能', exact=True)).to_be_visible()
        await expect(page.get_by_label('选择知识库', exact=True)).to_have_count(0)
        assert not any('/ai/knowledge' in path for path, _, _ in fixture.calls)
        await page.screenshot(path=str(ARTIFACTS / 'chat-empty.png'), full_page=True)

        await button(page, '选择技能').click()
        await page.locator('.skill-option').filter(has_text='深度研究').click()
        await button(page.locator('.chat-skill-picker'), '完成').click()
        await page.locator('.composer textarea').fill('整理近期研究方向')
        await button(page, '发送').click()
        await expect(page.get_by_text('聊天技能回复', exact=True)).to_be_visible()
        sent = next(body for path, method, body in fixture.calls if path == '/ai/chat/send' and method == 'POST')
        assert sent['skillSlugs'] == ['deep-research'] and 'knowledgeBaseIds' not in sent
        await expect(switch).to_have_count(0)
        await button(page, '新建聊天').click()
        await expect(switch).to_be_visible()

        await button(switch, '工作').click()
        await expect(page).to_have_url(re.compile(r'/ai/work$'))
        await expect(page.get_by_label('工作目标', exact=True)).to_be_visible()
        await button(page, '添加智能体').click()
        await page.get_by_role('checkbox', name='研究助手', exact=True).check()
        await page.get_by_role('checkbox', name='数据分析师', exact=True).check()
        await button(page.locator('.ant-modal:visible'), '确定').click()
        await page.get_by_label('工作目标', exact=True).fill('整理发布资料')
        await page.screenshot(path=str(ARTIFACTS / 'work-empty.png'), full_page=True)
        await button(page, '开始工作').click()
        await expect(page.get_by_text('模拟创建失败，请重试', exact=True).first).to_be_visible()
        await expect(page.get_by_label('工作目标', exact=True)).to_have_value('整理发布资料')
        await button(page, '开始工作').click()
        await expect(page).to_have_url(re.compile(r'taskId='))
        await expect(page.get_by_text('研究助手完成：整理发布资料', exact=True)).to_be_visible()
        assert len(fixture.tasks) == 1 and fixture.run_count == 1
        await expect(switch).to_have_count(0)
        executor = page.get_by_label('执行智能体', exact=True)
        await executor.locator('.ant-select-selector').click()
        await page.locator('.ant-select-dropdown:visible').get_by_text('数据分析师', exact=True).click()
        await page.get_by_label('工作消息', exact=True).fill('补充关键指标')
        await button(page, '发送').click()
        await expect(page.get_by_text('数据分析师完成：补充关键指标', exact=True)).to_be_visible()
        run_calls = [body for path, method, body in fixture.calls if path.endswith('/runs') and method == 'POST']
        assert run_calls[-1]['agentId'] == AGENTS[1]['agentId']
        assert not {'modelId', 'skillSlugs', 'knowledgeBaseIds', 'systemPrompt'} & run_calls[-1].keys()
        task_url = page.url
        await page.reload(wait_until='networkidle')
        await expect(page.get_by_text('数据分析师完成：补充关键指标', exact=True)).to_be_visible()
        await page.screenshot(path=str(ARTIFACTS / 'work-task.png'), full_page=True)
        await button(page, '新建聊天').click()
        await expect(page).to_have_url(re.compile(r'/ai/chat$'))
        await expect(page.get_by_role('region', name='最近工作')).to_have_count(0)
        await page.go_back(wait_until='networkidle')
        await expect(page).to_have_url(task_url)
        assert not errors, errors
        print('desktop: center switch, no chat knowledge requests, create recovery, two agents and task history passed')
    except Exception:
        await page.screenshot(path=str(ARTIFACTS / 'failure.png'), full_page=True)
        print('errors:', errors)
        print((await page.locator('body').inner_text())[-5000:])
        raise
    finally:
        await context.close()


async def mobile(browser):
    context = await browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True)
    fixture = WorkFixture()
    fixture.fail_create = False
    task = fixture.respond(PREFIX, 'POST', {
        'goal': '完成一项持续任务', 'agentIds': [agent['agentId'] for agent in AGENTS],
        'activeAgentId': AGENTS[0]['agentId'], 'clientMessageId': 'mobile-create',
    }, {})['data']
    fixture.tasks[task['taskId']]['title'] = '长任务标题' * 20
    await fixture.install(context)
    page = await context.new_page()
    try:
        await page.goto(BASE_URL + '/ai/work?taskId=' + task['taskId'], wait_until='networkidle')
        await expect(page.get_by_label('工作消息', exact=True)).to_be_visible(timeout=30000)
        for label in ('发送',):
            box = await button(page, label).bounding_box()
            assert box and box['x'] >= 0 and box['x'] + box['width'] <= 390, (label, box)
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.get_by_label('工作消息', exact=True).fill('继续推进')
        await button(page, '发送').click()
        await expect(page.get_by_text('研究助手完成：继续推进', exact=True)).to_be_visible()
        await page.screenshot(path=str(ARTIFACTS / 'work-mobile.png'), full_page=True)
        print('mobile: full-permission task, long title, executor and send controls fit 390px')
    finally:
        await context.close()


async def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            await desktop(browser)
            await mobile(browser)
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
