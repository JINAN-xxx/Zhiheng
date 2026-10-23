"""Agent knowledge picker integration using local, stateful API fixtures only."""

import asyncio
import copy
import os
import re

from playwright.async_api import async_playwright, expect
import configurable_agent_check as agent_smoke

BASE_URL = os.environ.get('KNOWLEDGE_CHECK_BASE_URL', 'http://127.0.0.1:12582')
agent_smoke.BASE_URL = BASE_URL
WELCOME = '你好，我会参考你选择的资料回答问题。\n请输入你的问题。'


class AgentKnowledgeFixture(agent_smoke.AgentFixture):
    def __init__(self):
        super().__init__()
        self.available_calls = 0
        self.agents['agent-seed']['knowledgeBaseIds'] = ['kb-legacy']
        self.agents['agent-seed']['welcomeMessage'] = WELCOME

    def respond(self, path, method, body):
        if path == '/ai/knowledge/available':
            self.available_calls += 1
            self.calls.append((path, method, copy.deepcopy(body)))
            if self.available_calls == 1:
                return {'code': 503, 'msg': '知识库列表暂不可用'}
            return {'code': 200, 'data': {'databases': [
                {'db_id': 'kb-1', 'name': '制度库'}, {'db_id': 'kb-2', 'name': '产品资料'},
            ]}}
        return super().respond(path, method, body)


def editor(page):
    return page.locator('.agent-editor-modal:visible')


async def select_knowledge(page, name):
    field = editor(page).get_by_label('知识库', exact=True)
    await field.locator('xpath=ancestor::div[contains(@class,"ant-select-selector")][1]').click()
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter(has_text=re.compile('^' + re.escape(name) + '$')).click()
    await editor(page).locator('.ant-modal-title').click()


async def check_centered(page, filename):
    box = await editor(page).bounding_box()
    viewport = page.viewport_size
    assert box and viewport
    assert box['x'] >= 0 and box['x'] + box['width'] <= viewport['width'] + 1, box
    assert abs(box['x'] + box['width'] / 2 - viewport['width'] / 2) <= 2, box
    assert abs(box['y'] + box['height'] / 2 - viewport['height'] / 2) <= 18, box
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    await page.screenshot(path=filename, full_page=True)


async def desktop(browser, fixture):
    context = await browser.new_context(viewport={'width': 1440, 'height': 900})
    await fixture.install(context)
    page = await context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        await page.goto(BASE_URL + '/extensions/agent', wait_until='networkidle')
        card = page.locator('.agent-card').filter(has_text='Published Support Agent')
        await agent_smoke.button(card, '配置').click()
        await expect(editor(page).get_by_label('欢迎语', exact=True)).to_have_value(WELCOME)
        await expect(editor(page).get_by_text('kb-legacy', exact=True)).to_be_visible()
        await expect(agent_smoke.button(editor(page), '知识库加载失败，点击重试')).to_be_visible()
        await agent_smoke.button(editor(page), '知识库加载失败，点击重试').click()
        await expect(agent_smoke.button(editor(page), '知识库加载失败，点击重试')).to_have_count(0)
        await select_knowledge(page, '制度库')
        await select_knowledge(page, '产品资料')
        await agent_smoke.button(editor(page), '保存').click()
        await expect(editor(page).get_by_text('草稿 v3', exact=True)).to_be_visible()
        assert fixture.agents['agent-seed']['knowledgeBaseIds'] == ['kb-legacy', 'kb-1', 'kb-2']
        assert fixture.agents['agent-seed']['welcomeMessage'] == WELCOME
        await editor(page).get_by_label('名称', exact=True).scroll_into_view_if_needed()
        await check_centered(page, '/tmp/agent-knowledge-desktop.png')
        await editor(page).get_by_role('button', name='Close', exact=True).click()
        await expect(editor(page)).to_have_count(0)
        await agent_smoke.button(card, '配置').click()
        for name in ('kb-legacy', '制度库', '产品资料'):
            await expect(editor(page).locator('.ant-select-selection-item').filter(has_text=name)).to_be_visible()
        await expect(editor(page).get_by_label('欢迎语', exact=True)).to_have_value(WELCOME)
        await agent_smoke.button(editor(page), '试聊').click()
        await expect(page.locator('.preview-welcome')).to_contain_text(WELCOME)
        assert not errors, errors
        print('agent desktop: unavailable-list retry, legacy bindings, multi-select save/reopen, centered editor and welcome preview passed')
    except Exception:
        await page.screenshot(path='/tmp/agent-knowledge-failure.png', full_page=True)
        print('page errors:', errors)
        print('visible text:', (await page.locator('body').inner_text())[-7000:])
        raise
    finally:
        await context.close()


async def mobile(browser, fixture):
    context = await browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True, has_touch=True)
    await fixture.install(context)
    page = await context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        await page.goto(BASE_URL + '/extensions/agent', wait_until='networkidle')
        await agent_smoke.button(page.locator('.agent-card').filter(has_text='Published Support Agent'), '配置').click()
        await expect(editor(page).get_by_label('欢迎语', exact=True)).to_have_value(WELCOME)
        await editor(page).get_by_label('知识库', exact=True).scroll_into_view_if_needed()
        await expect(editor(page).get_by_text('制度库', exact=True)).to_be_visible()
        await expect(editor(page).get_by_text('产品资料', exact=True)).to_be_visible()
        await expect(editor(page).get_by_text('kb-legacy', exact=True)).to_be_visible()
        await check_centered(page, '/tmp/agent-knowledge-mobile.png')
        assert not errors, errors
        print('agent mobile: selected references and welcome text preserved in centered 390px editor')
    finally:
        await context.close()


async def main():
    fixture = AgentKnowledgeFixture()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            await desktop(browser, fixture)
            await mobile(browser, fixture)
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
