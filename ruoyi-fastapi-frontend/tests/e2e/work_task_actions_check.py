"""Browser coverage for task lifecycle, member versions and run recovery."""

import asyncio
import copy
import re

from playwright.async_api import async_playwright, expect

from chat_work_mode_check import AGENTS, ARTIFACTS, BASE_URL, PREFIX, WorkFixture, button, event
from work_access_check import seed_task


class ActionFixture(WorkFixture):
    def __init__(self):
        super().__init__()
        self.hold_runs = False
        self.release_claim = False
        self.cancel_count = 0
        self.fail_next_run = False

    def respond(self, path, method, body, query):
        suffix = path[len(PREFIX):] if path.startswith(PREFIX) else ''
        task_id = suffix.strip('/').split('/')[0]
        task = self.tasks.get(task_id)
        if task and suffix.endswith('/cancel') and method == 'POST':
            self.calls.append((path, method, copy.deepcopy(body)))
            self.cancel_count += 1
            task['messages'][-1]['runStatus'] = 'cancelled'
            return {'code': 200}
        if task and suffix == '/' + task_id and method == 'GET' and self.release_claim:
            task['currentRunId'] = None
        if task and '/members/' in suffix and method == 'PUT':
            member = next(item for item in task['members'] if item['agentId'] == suffix.split('/')[-1])
            assert body['expectedPublishedVersion'] == member['latestPublishedVersion']
            member['available'] = True
        result = super().respond(path, method, body, query)
        if task and suffix.endswith('/runs') and method == 'POST' and self.fail_next_run:
            self.fail_next_run = False
            assistant = task['messages'][-1]
            assistant.update(content='', runStatus='failed', errorSnapshot={'message': '模拟执行失败'})
            task['currentRunId'] = None
            meta = {'taskId': task_id, 'runId': assistant['runId'], 'messageId': assistant['messageId'],
                    'agentId': assistant['agentId'], 'agentName': assistant['agentNameSnapshot'],
                    'publishedVersion': assistant['publishedVersion']}
            return event('meta', meta) + event('error', {**meta, 'message': '模拟执行失败'}) + event(
                'done', {**meta, 'status': 'failed'})
        if task and '/members/' in suffix and method == 'DELETE':
            if not any(item['agentId'] == task['activeAgentId'] and not item['removedAt'] for item in task['members']):
                task['activeAgentId'] = next(item['agentId'] for item in task['members'] if not item['removedAt'])
            return {'code': 200, 'data': copy.deepcopy(task)}
        if task and suffix.endswith('/runs') and method == 'POST' and self.hold_runs:
            assistant = task['messages'][-1]
            assistant.update(content='已保留的部分输出', runStatus='streaming')
            task['currentRunId'] = assistant['runId']
            meta = {'taskId': task_id, 'runId': assistant['runId'], 'messageId': assistant['messageId'],
                    'agentId': assistant['agentId'], 'agentName': assistant['agentNameSnapshot'],
                    'publishedVersion': assistant['publishedVersion'], 'replay': True}
            return event('meta', meta) + event('content', {**meta, 'delta': assistant['content']}) + event(
                'done', {**meta, 'status': 'streaming'})
        return result


async def confirm(page):
    await button(page.locator('.ant-modal:visible'), '确定').click()


async def lifecycle(browser):
    context = await browser.new_context(viewport={'width': 1440, 'height': 960})
    fixture = ActionFixture()
    task = seed_task(fixture)
    task_id = task['taskId']
    fixture.respond(PREFIX + '/' + task_id + '/runs', 'POST', {
        'agentId': AGENTS[0]['agentId'], 'clientMessageId': 'access-check-create', 'message': task['goal'],
    }, {})
    fixture.tasks[task_id]['members'][0].update(latestPublishedVersion=4, available=False)
    await fixture.install(context)
    page = await context.new_page()
    try:
        await page.goto(BASE_URL + '/ai/chat/work?taskId=' + task_id, wait_until='networkidle')
        await expect(page.get_by_label('执行智能体', exact=True)).to_contain_text('数据分析师', timeout=30000)
        research = page.locator('.work-member').filter(has_text='研究助手')
        await button(research, '升级').click()
        await confirm(page)
        await expect(research.get_by_text('v4', exact=True)).to_be_visible()
        history = page.locator('.work-message').filter(has_text='研究助手完成：' + task['goal'])
        await expect(history.get_by_text('v2', exact=True)).to_be_visible()
        await button(research, '移除智能体').click()
        await confirm(page)
        await expect(research).to_have_count(0)
        await expect(history).to_be_visible()

        await button(page, '完成工作').click()
        await expect(page.locator('.work-task-header').get_by_text('已完成', exact=True)).to_be_visible()
        await expect(button(page, '发送')).to_have_count(0)
        await page.reload(wait_until='networkidle')
        await expect(button(page, '重新打开')).to_be_visible()
        await button(page, '重新打开').click()
        await expect(button(page, '发送')).to_be_visible()
        await button(page, '归档').click()
        await expect(page.locator('.work-task-header').get_by_text('已归档', exact=True)).to_be_visible()
        await expect(button(page, '发送')).to_have_count(0)
        recent = page.get_by_role('region', name='最近工作', exact=True)
        await expect(recent.get_by_text(task['title'], exact=True)).to_have_count(0)
        await recent.locator('label.ant-radio-button-wrapper').filter(has_text='已归档').click()
        await expect(recent.get_by_text(task['title'], exact=True)).to_be_visible()
        row = recent.locator('.recent-work__item').filter(has_text=task['title'])
        await row.hover()
        await button(row, '更多操作').click()
        await expect(page.get_by_role('menuitem', name=re.compile('重命名'))).to_have_count(0)
        await page.get_by_role('menuitem', name=re.compile('删除工作')).click()
        await confirm(page)
        await expect(page).to_have_url(re.compile(r'/ai/chat/work$'))
        assert task_id not in fixture.tasks
        print('lifecycle: unavailable executor, explicit upgrade, historical version, removal, completion, reopen, archive and deletion passed')
    except Exception:
        await page.screenshot(path=str(ARTIFACTS / 'work-actions-failure.png'), full_page=True)
        print((await page.locator('body').inner_text())[-3500:])
        raise
    finally:
        await context.close()


async def replay_and_cancel(browser):
    context = await browser.new_context(viewport={'width': 1440, 'height': 960})
    fixture = ActionFixture()
    task = seed_task(fixture)
    fixture.hold_runs = True
    await fixture.install(context)
    page = await context.new_page()
    try:
        await page.goto(BASE_URL + '/ai/chat/work?taskId=' + task['taskId'], wait_until='networkidle')
        await button(page, '继续执行').click()
        await expect(page.get_by_text('已保留的部分输出', exact=True)).to_be_visible()
        await expect(button(page, '停止')).to_be_visible()
        await expect(button(page, '发送')).to_have_count(0)
        await button(page, '停止').click()
        await expect(page.get_by_text('已取消', exact=True)).to_be_visible()
        assert fixture.cancel_count == 1
        assert fixture.tasks[task['taskId']]['currentRunId']
        await expect(button(page, '发送')).to_have_count(0)
        fixture.release_claim = True
        await expect(button(page, '发送')).to_be_visible(timeout=10000)
        await page.reload(wait_until='networkidle')
        await expect(page.get_by_text('已取消', exact=True)).to_be_visible()
        await expect(page.get_by_text('已保留的部分输出', exact=True)).to_be_visible()
        await expect(button(page, '停止')).to_have_count(0)
        await page.screenshot(path=str(ARTIFACTS / 'work-cancelled.png'), full_page=True)
        print('run recovery: active replay stays observed; cancellation holds the claim until release and survives refresh')
    except Exception:
        await page.screenshot(path=str(ARTIFACTS / 'work-cancel-failure.png'), full_page=True)
        print((await page.locator('body').inner_text())[-3500:])
        raise
    finally:
        await context.close()


async def failed_run_retry(browser):
    context = await browser.new_context(viewport={'width': 1440, 'height': 960})
    fixture = ActionFixture()
    task = seed_task(fixture)
    fixture.respond(PREFIX + '/' + task['taskId'] + '/runs', 'POST', {
        'agentId': AGENTS[0]['agentId'], 'clientMessageId': 'access-check-create', 'message': task['goal'],
    }, {})
    fixture.fail_next_run = True
    await fixture.install(context)
    page = await context.new_page()
    try:
        await page.goto(BASE_URL + '/ai/chat/work?taskId=' + task['taskId'], wait_until='networkidle')
        composer = page.get_by_label('工作消息', exact=True)
        await expect(composer).to_be_visible(timeout=30000)
        await composer.fill('请继续分析')
        await button(page, '发送').click()
        await expect(button(page, '重试执行')).to_be_visible(timeout=10000)
        await expect(composer).to_have_value('请继续分析')
        attempts = [body for path, method, body in fixture.calls if path.endswith('/runs') and method == 'POST']
        failed_key = attempts[-1]['clientMessageId']
        await button(page, '重试执行').click()
        await expect(page.get_by_text('研究助手完成：请继续分析', exact=True)).to_be_visible()
        attempts = [body for path, method, body in fixture.calls if path.endswith('/runs') and method == 'POST']
        assert attempts[-1]['clientMessageId'] != failed_key
        assert attempts[-1]['message'] == '请继续分析'
        assert sum(message.get('runStatus') == 'failed' for message in fixture.tasks[task['taskId']]['messages']) == 1
        print('failed retry: a failed stream preserves input and history; explicit retry sends a new idempotency key')
    except Exception:
        await page.screenshot(path=str(ARTIFACTS / 'work-retry-failure.png'), full_page=True)
        print((await page.locator('body').inner_text())[-3500:])
        raise
    finally:
        await context.close()


async def main():
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            results = await asyncio.gather(
                lifecycle(browser), replay_and_cancel(browser), failed_run_retry(browser), return_exceptions=True,
            )
            failures = [result for result in results if isinstance(result, Exception)]
            if failures:
                raise failures[0]
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
