"""Exercise work route and action permissions in the real frontend shell."""

import asyncio
import re
from pathlib import Path

from playwright.async_api import async_playwright, expect

from chat_work_mode_check import AGENTS, BASE_URL, PREFIX, WorkFixture, button

ARTIFACTS = Path('/tmp/chat-work-acceptance')


def seed_task(fixture):
    fixture.fail_create = False
    task = fixture.respond(PREFIX, 'POST', {
        'goal': '查看已有工作资料',
        'agentIds': [agent['agentId'] for agent in AGENTS],
        'activeAgentId': AGENTS[0]['agentId'],
        'clientMessageId': 'access-check-create',
    }, {})['data']
    fixture.calls.clear()
    return task


async def check_read_access(browser, permissions, name, width=1440):
    context = await browser.new_context(viewport={'width': width, 'height': 960})
    fixture = WorkFixture(permissions=permissions)
    task = seed_task(fixture)
    await fixture.install(context)
    page = await context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        url = BASE_URL + '/ai/chat/work?taskId=' + task['taskId']
        await page.goto(url, wait_until='networkidle')
        await expect(page).to_have_url(url)
        await expect(page.get_by_text(task['goal'], exact=True).first).to_be_visible(timeout=30000)
        for label in ('新建对话', '开始工作', '添加智能体', '发送'):
            await expect(button(page, label)).to_have_count(0)
        assert not any(path == PREFIX + '/available-agents' for path, _, _ in fixture.calls)
        assert not any(path.startswith(PREFIX) and method != 'GET' for path, method, _ in fixture.calls)
        if 'agent_extension:work:list' not in permissions:
            assert not any(path == PREFIX and method == 'GET' for path, method, _ in fixture.calls)
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors, errors
        await page.screenshot(path=str(ARTIFACTS / (name + '.png')), full_page=True)
        print(name + ': deep link readable; creation, member use and execution inaccessible')
    except Exception:
        await page.screenshot(path=str(ARTIFACTS / (name + '-failure.png')), full_page=True)
        print(name, 'page errors:', errors)
        print((await page.locator('body').inner_text())[-4000:])
        raise
    finally:
        await context.close()


async def check_no_work_access(browser):
    context = await browser.new_context(viewport={'width': 1440, 'height': 960})
    fixture = WorkFixture(permissions=['ai:chat:list'])
    task = seed_task(fixture)
    await fixture.install(context)
    page = await context.new_page()
    try:
        await page.goto(BASE_URL + '/ai/chat/work?taskId=' + task['taskId'], wait_until='networkidle')
        await expect(page).to_have_url(re.compile(r'/ai/chat$'))
        await expect(button(page, '新建工作')).to_have_count(0)
        await expect(button(page, '工作')).to_have_count(0)
        assert not any(path.startswith(PREFIX) for path, _, _ in fixture.calls)
        print('no-work-access: unauthorized deep link redirected before requesting task data')
    finally:
        await context.close()


async def check_small_task_layout(browser):
    context = await browser.new_context(viewport={'width': 320, 'height': 844}, is_mobile=True)
    fixture = WorkFixture()
    task = seed_task(fixture)
    stored = fixture.tasks[task['taskId']]
    stored['title'] = '最长任务标题' * 16 + '任务标题'
    stored['goal'] = '整理资料并核对关键事实，形成可追溯的工作结论。' * 80
    stored['members'][0]['agentNameSnapshot'] = '负责复杂资料整理的研究智能体' * 6
    stored['members'].extend({**stored['members'][0], 'agentId': f'layout-member-{index}',
                              'latestPublishedVersion': 9} for index in range(8))
    await fixture.install(context)
    page = await context.new_page()
    try:
        await page.goto(BASE_URL + '/ai/chat/work?taskId=' + task['taskId'], wait_until='networkidle')
        await expect(page.get_by_label('工作消息', exact=True)).to_be_visible(timeout=30000)
        await page.get_by_label('工作消息', exact=True).fill('\n'.join(['请继续整理当前阶段的项目情况'] * 7))
        send = button(page, '发送')
        await expect(send).to_be_visible()
        await expect(send).to_be_in_viewport(ratio=1)
        box = await send.bounding_box()
        assert box and box['x'] >= 0 and box['x'] + box['width'] <= 320, box
        assert box['y'] >= 0 and box['y'] + box['height'] <= 844, box
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.screenshot(path=str(ARTIFACTS / 'work-small-task.png'), full_page=True)
        print('small-task-layout: long title, 10 members and 7 input lines preserve visible controls at 320px')
    finally:
        await context.close()


async def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            await check_read_access(browser, ['agent_extension:work:query'], 'work-query-only')
            await check_read_access(browser, ['agent_extension:work:query'], 'work-query-only-mobile', width=320)
            await check_read_access(browser, [
                'agent_extension:work:' + action
                for action in ('list', 'query', 'add', 'edit', 'remove', 'run')
            ], 'work-without-agent-use')
            await check_no_work_access(browser)
            await check_small_task_layout(browser)
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
