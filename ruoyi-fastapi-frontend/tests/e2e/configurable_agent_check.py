"""Stateful browser smoke checks; all backend responses are local test fixtures."""

import asyncio
import copy
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

from platform_shell_check import ai_route, response_for


BASE_URL = os.environ.get('AGENT_CHECK_BASE_URL', 'http://127.0.0.1:12581')
ARTIFACTS = Path('/tmp')
MODEL_ID = 'mdl_22222222222242228222222222222222'
DRAFT_MODEL_ID = 'mdl_33333333333343338333333333333333'
STAMP = '2026-09-08T08:00:00Z'
WELCOME = '你好，我是文档助手。\n可以把你的问题发给我。'
CONFIG_FIELDS = (
    'name', 'description', 'modelId', 'systemPrompt', 'welcomeMessage', 'skillSlugs',
    'knowledgeBaseIds', 'applicationId', 'temperature', 'maxTokens',
)


class AgentFixture:
    def __init__(self):
        self.calls = []
        self.preview_successes = 0
        self.applications = [
            {'applicationId': 'app-seed', 'applicationCode': 'seed', 'applicationName': 'Published API app', 'status': 'active', 'version': 1},
            {'applicationId': 'app-draft', 'applicationCode': 'draft', 'applicationName': 'Draft API app', 'status': 'active', 'version': 1},
        ]
        config = {
            'name': 'Published Support Agent',
            'description': 'Customer support reference agent with a published configuration.',
            'modelId': MODEL_ID, 'systemPrompt': 'Answer support questions precisely.', 'welcomeMessage': '',
            'skillSlugs': ['deep-research'], 'knowledgeBaseIds': [],
            'applicationId': 'app-seed', 'temperature': 0.7, 'maxTokens': 2048,
        }
        self.agents = {'agent-seed': {
            **config, 'agentId': 'agent-seed', 'ownerUserId': 1,
            'version': 2, 'status': 'published', 'publishedVersion': 2,
            'publishedConfig': copy.deepcopy(config), 'publishedAt': STAMP,
            'createdAt': STAMP, 'updatedAt': STAMP,
        }}
        self.keys = {}
        self.issued_count = 0

    def respond(self, path, method, body):
        self.calls.append((path, method, copy.deepcopy(body)))
        if path == '/getInfo':
            return {**response_for(path, method, 'all'), 'permissions': ['*:*:*']}
        if path == '/getRouters':
            routes = response_for(path, method, 'all')['data']
            extension = next(item for item in routes if item['name'] == 'AgentExtension')
            extension['redirect'] = '/extensions/agent'
            extension['children'].append(ai_route('AgentExtensionAgent', 'agent', 'plugin/agent_extension/agent/index', '智能体'))
            return {'code': 200, 'data': routes}
        if path == '/ai/models':
            rows = [
                {'modelId': model_id, 'modelCode': code, 'displayName': title, 'title': title,
                 'modelType': 'chat', 'lifecycleStatus': 'active', 'publishedAt': STAMP,
                 'bindingStatus': 'active', 'binding': {'bindingStatus': 'active'}}
                for model_id, code, title in [
                    (MODEL_ID, 'smoke-chat', 'Smoke Chat'),
                    (DRAFT_MODEL_ID, 'smoke-chat-draft', 'Smoke Chat Draft'),
                ]
            ]
            return {'code': 200, 'rows': rows, 'total': len(rows), 'hasNext': False}
        if path == '/ai/knowledge/available':
            return {'code': 200, 'data': {'databases': [{'db_id': 'kb-smoke', 'name': 'Smoke Knowledge'}]}}
        if path == '/ai/applications':
            if method == 'POST':
                app = {**body, 'applicationId': 'app-created', 'status': 'active', 'version': 1}
                self.applications.append(app)
                return {'code': 200, 'data': app}
            return {'code': 200, 'rows': self.applications, 'total': len(self.applications), 'hasNext': False}
        if path.startswith('/ai/applications/') and '/keys' in path:
            application_id = path.split('/')[3]
            keys = self.keys.setdefault(application_id, [])
            if path.endswith('/revoke'):
                key_id = path.split('/')[-2]
                key = next(item for item in keys if item['keyId'] == key_id)
                key['status'] = 'revoked'
                return {'code': 200, 'data': key}
            if method == 'POST':
                self.issued_count += 1
                key = {
                    'keyId': f'key-{self.issued_count}', 'applicationId': application_id,
                    'keyName': body['keyName'], 'maskedKey': 'sk-smoke-********',
                    'fingerprint': f'smoke-fingerprint-{self.issued_count}', 'status': 'active',
                    'grants': [{'grantId': 'grant-1', 'modelId': value, 'status': 'active'} for value in body['modelIds']],
                    'quota': body['quota'],
                }
                keys.append(key)
                return {'code': 200, 'data': {**key, 'apiKey': f'sk-e2e-agent-once-{self.issued_count}'}}
            return {'code': 200, 'data': keys}
        if path == '/agent_extension/agents':
            if method == 'POST':
                agent = {
                    **body, 'agentId': 'agent-created', 'ownerUserId': 1,
                    'version': 1, 'status': 'draft', 'publishedVersion': None,
                    'publishedConfig': None, 'publishedAt': None, 'createdAt': STAMP, 'updatedAt': STAMP,
                }
                self.agents[agent['agentId']] = agent
                return {'code': 200, 'data': agent}
            return {'code': 200, 'rows': list(self.agents.values()), 'total': len(self.agents)}
        if path.startswith('/agent_extension/agents/'):
            agent_id = path.split('/')[3]
            agent = self.agents[agent_id]
            if path.endswith('/preview'):
                if agent['knowledgeBaseIds']:
                    return {'code': 503, 'msg': {'code': 'AGENT_KNOWLEDGE_UNAVAILABLE', 'message': 'Agent knowledge retrieval is unavailable.'}}
                self.preview_successes += 1
                return {'code': 200, 'data': {
                    'id': f'completion-{self.preview_successes}', 'object': 'chat.completion',
                    'model': agent['modelId'],
                    'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': f'Smoke answer {self.preview_successes}'}, 'finish_reason': 'stop'}],
                }}
            if method in ('PUT', 'POST'):
                if body.get('expectedVersion') != agent['version']:
                    return {'code': 409, 'msg': 'Stale agent version'}
                if path.endswith('/publish'):
                    agent['version'] += 1
                    agent.update(status='published', publishedVersion=agent['version'], publishedAt=STAMP,
                                 publishedConfig={key: copy.deepcopy(agent[key]) for key in CONFIG_FIELDS})
                elif path.endswith('/unpublish'):
                    agent['version'] += 1
                    agent.update(status='draft', publishedVersion=None, publishedConfig=None, publishedAt=None)
                else:
                    agent.update({key: copy.deepcopy(body[key]) for key in CONFIG_FIELDS})
                    agent['version'] += 1
            return {'code': 200, 'data': agent}
        return response_for(path, method, 'all')

    async def install(self, context):
        async def handle(route):
            request = route.request
            path = urlparse(request.url).path
            for prefix in ('/dev-api', '/docker-api'):
                if path.startswith(prefix + '/'):
                    path = path[len(prefix):]
                    break
            body = request.post_data_json if request.post_data else {}
            payload = self.respond(path, request.method, body)
            status = 503 if payload.get('code') == 503 else 200
            await route.fulfill(status=status, content_type='application/json', body=json.dumps(payload, ensure_ascii=False))

        for pattern in ('**/dev-api/**', '**/docker-api/**', '**/transport/crypto/frontend-config', '**/captchaImage'):
            await context.route(pattern, handle)
        await context.add_cookies([{'name': 'Admin-Token', 'value': 'agent-e2e-token', 'url': BASE_URL}])


def button(scope, text):
    prefix = r'^(?:[A-Za-z][A-Za-z0-9_-]*\s+)*'
    return scope.get_by_role('button', name=re.compile(prefix + r'\s*'.join(map(re.escape, text)) + r'\s*$'))


def drawer(page):
    return page.locator('.agent-editor-modal:visible, .ant-drawer-content:visible').last


def agent_card(page):
    return page.locator('.agent-card').filter(has_text='Smoke Agent')


def unexpected_console_errors(errors):
    return [item for item in errors if not (
        item['text'] == 'Failed to load resource: the server responded with a status of 503 (Service Unavailable)'
        and urlparse(item['url']).path == '/dev-api/agent_extension/agents/agent-created/preview'
    )]


async def select_option(page, label, text):
    await drawer(page).get_by_label(label, exact=True).locator('xpath=ancestor::div[contains(@class, "ant-select-selector")][1]').click()
    await page.locator('.ant-select-dropdown:visible').get_by_text(text, exact=True).click()


async def close_drawer(page):
    await drawer(page).locator('.ant-drawer-close, .ant-modal-close').click()
    await expect(page.locator('.agent-editor-modal:visible, .ant-drawer-content:visible')).to_have_count(0)


async def assert_layout(page, filename):
    await expect(page.locator('.ant-message-notice:visible')).to_have_count(0, timeout=7000)
    await page.wait_for_function('''() => [...document.querySelectorAll('.agent-editor-modal')]
      .filter(el => el.getBoundingClientRect().width)
      .every(el => {
        const rect = el.getBoundingClientRect();
        return Math.abs(rect.left + rect.width / 2 - innerWidth / 2) <= 2
          && Math.abs(rect.top + rect.height / 2 - innerHeight / 2) <= 2
          && rect.top >= 0 && rect.bottom <= innerHeight;
      })''', timeout=5000)
    await page.wait_for_function('''() => [...document.querySelectorAll('.ant-drawer-open .ant-drawer-content')]
      .every(el => {
        const rect = el.getBoundingClientRect();
        return rect.left >= -1 && rect.right <= window.innerWidth + 1;
      })''', timeout=5000)
    sizes = await page.evaluate('''() => ({
        width: window.innerWidth,
        html: document.documentElement.scrollWidth,
        body: document.body.scrollWidth,
        drawers: [...document.querySelectorAll('.ant-drawer-content')]
          .filter(el => el.getBoundingClientRect().width && el.closest('.ant-drawer-open'))
          .map(el => ({left: el.getBoundingClientRect().left, right: el.getBoundingClientRect().right}))
    })''')
    assert max(sizes['html'], sizes['body']) <= sizes['width'] + 1, sizes
    assert all(item['left'] >= -1 and item['right'] <= sizes['width'] + 1 for item in sizes['drawers']), sizes
    await page.screenshot(path=str(ARTIFACTS / filename), full_page=True)


async def desktop_check(browser, fixture, artifacts):
    context = await browser.new_context(viewport={'width': 1440, 'height': 900})
    await fixture.install(context)
    page = await context.new_page()
    page.set_default_timeout(15000)
    errors = []
    console_errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('console', lambda message: console_errors.append({'text': message.text, 'url': message.location.get('url', '')}) if message.type == 'error' else None)
    try:
        await page.goto(BASE_URL + '/extensions/agent', wait_until='networkidle')
        await expect(page.locator('.agent-card')).to_have_count(1)
        await assert_layout(page, 'agent-desktop-list.png')
        artifacts.append('agent-desktop-list.png')

        await button(page, '新建智能体').click()
        await drawer(page).get_by_label('名称', exact=True).fill('Smoke Agent')
        await drawer(page).get_by_label('描述', exact=True).fill('A stateful browser fixture for configurable agent verification.')
        await drawer(page).get_by_label('欢迎语', exact=True).fill(WELCOME)
        await select_option(page, '模型', 'Smoke Chat')
        await drawer(page).get_by_label('系统提示词', exact=True).fill('Use the selected references and answer precisely.')
        await select_option(page, '技能', '深度研究')
        await select_option(page, '知识库', 'Smoke Knowledge')
        await drawer(page).get_by_label('知识库', exact=True).press('Tab')
        await drawer(page).get_by_role('button', name='新建 API 应用', exact=True).click()
        modal = page.locator('.ant-modal:visible').filter(has=page.get_by_label('应用编码', exact=True))
        await modal.get_by_label('应用名称', exact=True).fill('Smoke API app')
        await modal.get_by_label('应用编码', exact=True).fill('smoke-api')
        await button(modal, '确定').click()
        await expect(modal).to_have_count(0)
        await button(drawer(page), '保存').click()
        await expect(drawer(page).get_by_text('草稿 v1', exact=True)).to_be_visible()
        saved = fixture.agents['agent-created']
        assert saved['skillSlugs'] == ['deep-research'] and saved['knowledgeBaseIds'] == ['kb-smoke'], saved
        assert saved['applicationId'] == 'app-created' and saved['modelId'] == MODEL_ID, saved
        assert saved['systemPrompt'] == 'Use the selected references and answer precisely.'
        assert saved['welcomeMessage'] == WELCOME
        print('desktop: configuration and application creation saved', flush=True)
        await drawer(page).get_by_label('名称', exact=True).scroll_into_view_if_needed()
        await assert_layout(page, 'agent-desktop-form.png')
        artifacts.append('agent-desktop-form.png')

        await button(drawer(page), '试聊').click()
        await expect(drawer(page).get_by_text('已保存草稿 v1', exact=True)).to_be_visible()
        await expect(drawer(page).locator('.preview-welcome')).to_contain_text(WELCOME)
        await expect(drawer(page).get_by_text('暂无消息', exact=True)).to_have_count(0)
        await drawer(page).get_by_label('试聊消息').fill('Find the reference answer.')
        await button(drawer(page), '发送').click()
        await expect(drawer(page).get_by_text('Agent knowledge retrieval is unavailable.', exact=True)).to_be_visible()
        await expect(drawer(page).get_by_label('试聊消息')).to_have_value('Find the reference answer.')
        await assert_layout(page, 'agent-desktop-knowledge-error.png')
        artifacts.append('agent-desktop-knowledge-error.png')
        await close_drawer(page)

        await button(agent_card(page), '配置').click()
        knowledge = drawer(page).locator('.ant-form-item').filter(has_text='知识库')
        await knowledge.locator('.ant-select-selection-item-remove').click()
        await expect(button(drawer(page), '试聊')).to_be_disabled()
        await button(drawer(page), '保存').click()
        await expect(drawer(page).get_by_text('草稿 v2', exact=True)).to_be_visible()
        await button(drawer(page), '试聊').click()
        await expect(drawer(page).get_by_text('已保存草稿 v2', exact=True)).to_be_visible()
        await expect(drawer(page).locator('.preview-welcome')).to_contain_text(WELCOME)
        for question, answer in [('First question', 'Smoke answer 1'), ('Follow-up question', 'Smoke answer 2')]:
            await drawer(page).get_by_label('试聊消息').fill(question)
            await button(drawer(page), '发送').click()
            await expect(drawer(page).get_by_text(answer, exact=True)).to_be_visible()
        previews = [body for path, method, body in fixture.calls if path.endswith('/preview')]
        assert len(previews) == 3
        assert previews[-1] == {'messages': [
            {'role': 'user', 'content': 'First question'},
            {'role': 'assistant', 'content': 'Smoke answer 1'},
            {'role': 'user', 'content': 'Follow-up question'},
        ], 'stream': False}, previews[-1]
        assert all(WELCOME not in json.dumps(payload, ensure_ascii=False) for payload in previews)
        print('desktop: knowledge error recovery and multi-turn preview passed', flush=True)
        await assert_layout(page, 'agent-desktop-trial.png')
        artifacts.append('agent-desktop-trial.png')
        await drawer(page).get_by_role('button', name='清空对话', exact=True).click()
        await expect(drawer(page).locator('.preview-welcome')).to_contain_text(WELCOME)
        await expect(drawer(page).locator('.preview-message:not(.preview-welcome)')).to_have_count(0)
        await close_drawer(page)

        await button(agent_card(page), '配置').click()
        await button(drawer(page), '发布').click()
        await expect(drawer(page).get_by_text('已发布', exact=True)).to_be_visible()
        assert fixture.agents['agent-created']['publishedVersion'] == 3
        await drawer(page).get_by_label('系统提示词', exact=True).fill('Unpublished draft prompt.')
        await drawer(page).get_by_label('欢迎语', exact=True).fill('尚未发布的新欢迎语')
        await select_option(page, '模型', 'Smoke Chat Draft')
        await select_option(page, 'API 应用', 'Draft API app')
        await expect(button(drawer(page), '发布')).to_be_disabled()
        await button(drawer(page), '保存').click()
        await expect(drawer(page).get_by_text('草稿 v4', exact=True)).to_be_visible()
        saved = fixture.agents['agent-created']
        assert saved['publishedConfig']['modelId'] == MODEL_ID and saved['modelId'] == DRAFT_MODEL_ID
        assert saved['publishedConfig']['applicationId'] == 'app-created' and saved['applicationId'] == 'app-draft'
        assert saved['publishedConfig']['systemPrompt'] != saved['systemPrompt']
        assert saved['publishedConfig']['welcomeMessage'] == WELCOME
        assert saved['welcomeMessage'] == '尚未发布的新欢迎语'
        print('desktop: publication and subsequent draft isolation passed', flush=True)
        await close_drawer(page)
        await expect(agent_card(page).get_by_text('有未发布更改', exact=True)).to_be_visible()

        await button(agent_card(page), 'API').click()
        await expect(drawer(page).get_by_text('应用 app-created', exact=True)).to_be_visible()
        await expect(drawer(page).locator('.api-contract')).to_contain_text(MODEL_ID)
        await expect(drawer(page).locator('.api-contract')).not_to_contain_text(DRAFT_MODEL_ID)
        await expect(drawer(page).locator('.api-endpoint')).to_contain_text('/v1/agents/agent-created/chat/completions')
        await assert_layout(page, 'agent-desktop-api.png')
        artifacts.append('agent-desktop-api.png')
        await drawer(page).get_by_label('密钥名称', exact=True).fill('Smoke one-time key')
        await button(drawer(page), '创建密钥').click()
        await expect(drawer(page).get_by_text('sk-e2e-agent-once-1', exact=True)).to_be_visible()
        key_calls = [(path, body) for path, method, body in fixture.calls if path.endswith('/keys') and method == 'POST']
        assert key_calls[-1][0] == '/ai/applications/app-created/keys'
        assert key_calls[-1][1]['modelIds'] == [MODEL_ID]
        storage = await page.evaluate('JSON.stringify({local: {...localStorage}, session: {...sessionStorage}})')
        assert 'sk-e2e-agent-once-1' not in storage
        await close_drawer(page)
        await button(agent_card(page), 'API').click()
        await expect(drawer(page).get_by_text('sk-e2e-agent-once-1', exact=True)).to_have_count(0)
        await expect(button(drawer(page), '创建密钥')).to_be_enabled()
        await drawer(page).get_by_role('button', name='撤销密钥', exact=True).click()
        await button(page.locator('.ant-popover:visible'), '撤销').click()
        await expect(drawer(page).locator('.api-key-row')).to_contain_text('已撤销')
        assert fixture.keys['app-created'][0]['status'] == 'revoked'
        await drawer(page).get_by_label('密钥名称', exact=True).fill('Smoke hide key')
        await button(drawer(page), '创建密钥').click()
        await expect(drawer(page).get_by_text('sk-e2e-agent-once-2', exact=True)).to_be_visible()
        await drawer(page).get_by_role('button', name='隐藏明文密钥', exact=True).click()
        await expect(drawer(page).get_by_text('sk-e2e-agent-once-2', exact=True)).to_have_count(0)
        assert not errors, errors
        assert not unexpected_console_errors(console_errors), console_errors
        print('desktop: create, save, application creation, knowledge failure recovery, multi-turn preview, publication, draft isolation, key creation/revocation and plaintext lifecycle passed')
    except Exception:
        await page.screenshot(path=str(ARTIFACTS / 'agent-smoke-failure.png'), full_page=True)
        print('page errors:', errors)
        print('console errors:', console_errors)
        print('visible text:', (await page.locator('body').inner_text())[-7000:])
        raise
    finally:
        await context.close()


async def mobile_check(browser, fixture, artifacts):
    context = await browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
    await fixture.install(context)
    page = await context.new_page()
    errors = []
    console_errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('console', lambda message: console_errors.append({'text': message.text, 'url': message.location.get('url', '')}) if message.type == 'error' else None)
    try:
        await page.goto(BASE_URL + '/extensions/agent', wait_until='networkidle')
        await expect(agent_card(page)).to_be_visible()
        await assert_layout(page, 'agent-mobile-list.png')
        artifacts.append('agent-mobile-list.png')
        await button(agent_card(page), '配置').click()
        await expect(drawer(page).get_by_label('名称', exact=True)).to_have_value('Smoke Agent')
        await expect(drawer(page).get_by_label('欢迎语', exact=True)).to_have_value('尚未发布的新欢迎语')
        await assert_layout(page, 'agent-mobile-form.png')
        artifacts.append('agent-mobile-form.png')
        await expect(button(drawer(page), '保存')).to_be_visible()
        await close_drawer(page)
        await button(agent_card(page), 'API').click()
        await expect(drawer(page).get_by_text('应用 app-created', exact=True)).to_be_visible()
        await assert_layout(page, 'agent-mobile-api.png')
        artifacts.append('agent-mobile-api.png')
        await expect(drawer(page).get_by_text('sk-e2e-agent-once-2', exact=True)).to_have_count(0)
        assert not errors, errors
        assert not console_errors, console_errors
        print('mobile: list, configuration and published API drawers passed at 390x844 with no document overflow or page errors')
    except Exception:
        await page.screenshot(path=str(ARTIFACTS / 'agent-mobile-failure.png'), full_page=True)
        raise
    finally:
        await context.close()


async def main():
    fixture = AgentFixture()
    artifacts = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            await desktop_check(browser, fixture, artifacts)
            await mobile_check(browser, fixture, artifacts)
        finally:
            await browser.close()
    print(json.dumps({'artifacts': [str(ARTIFACTS / name) for name in artifacts], 'mockedRequests': len(fixture.calls)}, indent=2))


if __name__ == '__main__':
    asyncio.run(main())
