"""Compact catalog uses enablement and preserves existing operations."""

import asyncio
import contextlib
import json
import re
import unittest
from urllib.parse import urlparse

import model_card_regressions as fixture

MODEL_ID, LONG_NAME = fixture.MODEL_ID, fixture.LONG_NAME
from playwright.async_api import expect


class CompactModels(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.ModelCardRegressions.asyncSetUp(self)
        self.rows[0]['context_window_tokens'] = 32768
        self.rows[0]['vision_enabled'] = True

    async def asyncTearDown(self):
        await fixture.ModelCardRegressions.asyncTearDown(self)

    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path.startswith('/api/models/') and route.request.method == 'DELETE':
            self.requests.append(('DELETE', path, None))
            self.rows = [r for r in self.rows if r['id'] != path.rsplit('/', 1)[-1]]
            return await route.fulfill(status=204)
        return await fixture.ModelCardRegressions.respond(self, route)

    async def open_models(self):
        await self.page.goto(self.origin + '/models')
        await expect(self.page.locator('.model-item')).to_have_count(3, timeout=10000)

    def row(self, name=LONG_NAME):
        return self.page.locator('.model-item').filter(has=self.page.get_by_role('button', name='查看模型详情：' + name, exact=True))

    async def detail(self, name=LONG_NAME):
        await self.row(name).get_by_role('button', name='查看模型详情：' + name, exact=True).click()
        dialog = self.page.locator('.model-detail-modal[role="dialog"]')
        await expect(dialog).to_be_visible()
        return dialog

    async def more(self, detail):
        await detail.get_by_role('button', name='更多模型操作', exact=True).click()
        return self.page.get_by_role('menu')

    async def accept(self):
        confirm = self.page.locator('.ant-modal-confirm')
        await confirm.get_by_role('button', name=re.compile(r'^确\s*定$')).click()

    async def test_compact_layout_and_centered_complete_details(self):
        await self.open_models()
        await expect(self.page.get_by_role('button', name='紧凑视图', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button', name='列表视图', exact=True)).to_have_count(0)
        await expect(self.page.locator('.model-card')).to_have_count(0)
        await expect(self.page.locator('.model-grid')).not_to_contain_text(MODEL_ID)
        await expect(self.page.locator('.model-grid')).not_to_contain_text('65,536')
        for width, columns in [(1440, 3), (1920, 4)]:
            await self.page.set_viewport_size({'width': width, 'height': 1000})
            count = await self.page.locator('.model-grid').evaluate('(el)=>getComputedStyle(el).gridTemplateColumns.split(" ").length')
            self.assertEqual(count, columns)
        dialog = await self.detail()
        await expect(dialog).to_contain_text(MODEL_ID)
        await expect(dialog).to_contain_text('provider/chat-with-a-very-long-upstream-model-code')
        await expect(dialog).to_contain_text('65,536')
        await expect(dialog).to_contain_text('32,768')
        await expect(dialog.locator('.ant-modal')).to_have_css('transform', 'none')
        bounds = await dialog.locator('.ant-modal').bounding_box()
        self.assertLessEqual(bounds['width'], 720)
        self.assertLess(abs(bounds['x'] + bounds['width']/2 - 960), 20)

    async def test_enable_is_the_only_model_availability_control(self):
        await self.open_models()
        row = self.row('结果重排序')
        reads = sum(path == '/api/models' for _, path, _ in self.requests)
        await row.get_by_role('switch').click()
        await expect(row.get_by_role('switch')).to_be_checked()
        await expect(row).not_to_contain_text('未发布')
        self.assertEqual(sum(path == '/api/models' for _, path, _ in self.requests), reads)
        dialog = await self.detail('结果重排序')
        await expect(dialog).to_contain_text('已启用')
        await expect(dialog).not_to_contain_text('发布状态')
        menu = await self.more(dialog)
        for label in ['发布', '取消发布']:
            await expect(menu.get_by_role('menuitem', name=label, exact=True)).to_have_count(0)
        self.assertFalse(any(path.endswith(('/publish', '/unpublish')) for _, path, _ in self.requests))

    async def test_disable_confirmation_can_cancel_without_mutation(self):
        await self.open_models()
        await self.row().get_by_role('switch').click()
        confirm = self.page.locator('.ant-modal-confirm')
        await expect(confirm).to_be_visible()
        await confirm.get_by_role('button', name=re.compile(r'^取\s*消$')).click()
        await expect(self.row().get_by_role('switch')).to_be_checked()
        self.assertFalse(any(path.endswith('/disable') for _, path, _ in self.requests))
        await self.row().get_by_role('switch').click()
        await self.accept()
        await expect(self.row().get_by_role('switch')).not_to_be_checked()
        self.assertEqual(sum(path.endswith('/disable') for _, path, _ in self.requests), 1)

    async def test_enable_failure_keeps_state_and_shows_error_in_details(self):
        async def fail(route):
            self.requests.append(('POST', urlparse(route.request.url).path, {}))
            await route.fulfill(status=409, json={'error': {'code': 'missing_rate', 'message': '请先配置模型费率'}})
        await self.page.route('**/api/models/rerank-id/enable', fail)
        await self.open_models()
        await self.row('结果重排序').get_by_role('switch').click()
        await expect(self.row('结果重排序')).to_contain_text('请先配置模型费率')
        await expect(self.row('结果重排序').get_by_role('switch')).not_to_be_checked()
        dialog = await self.detail('结果重排序')
        await expect(dialog).to_contain_text('请先配置模型费率')

    async def test_model_test_only_runs_on_explicit_click_and_uses_toast(self):
        await self.open_models()
        dialog = await self.detail()
        self.assertFalse(any(path.endswith('/test') for _, path, _ in self.requests))
        await dialog.get_by_role('button', name='测试连接', exact=True).click()
        await expect(self.page.locator('.ant-message-notice-content')).to_have_text('测试成功')
        await expect(self.page.locator('.test-result')).to_have_count(0)
        tests = [body for _, path, body in self.requests if path.endswith('/test')]
        self.assertEqual(len(tests), 1)
        self.assertIn('client_request_id', tests[0])

    async def test_pending_enable_sends_once_and_is_shared_with_details(self):
        requested, release = asyncio.Event(), asyncio.Event()
        async def delayed(route):
            requested.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await self.respond(route)
        await self.page.route('**/api/models/rerank-id/enable', delayed)
        try:
            await self.open_models()
            row = self.row('结果重排序')
            await row.get_by_role('switch').evaluate('(el)=>{el.click();el.click()}')
            await asyncio.wait_for(requested.wait(), timeout=10)
            dialog = await self.detail('结果重排序')
            await expect(dialog.get_by_role('switch')).to_be_disabled()
            release.set()
            await expect(dialog.get_by_role('switch')).to_be_checked()
            self.assertEqual(sum(path.endswith('/enable') for _, path, _ in self.requests), 1)
        finally:
            release.set()

    async def test_old_refresh_cannot_restore_a_committed_model_state(self):
        await self.open_models()
        requested, release = asyncio.Event(), asyncio.Event()
        stale = json.loads(json.dumps(self.rows))
        calls = 0
        async def delayed(route):
            nonlocal calls
            calls += 1
            if calls == 1:
                requested.set()
                await release.wait()
                with contextlib.suppress(Exception):
                    await route.fulfill(json=stale)
            else:
                await self.respond(route)
        await self.page.route('**/api/models?*', delayed)
        try:
            await self.page.get_by_role('button', name='刷新当前视图', exact=True).click()
            await asyncio.wait_for(requested.wait(), timeout=10)
            await self.row('结果重排序').get_by_role('switch').click()
            await expect(self.row('结果重排序').get_by_role('switch')).to_be_checked()
            release.set()
            await self.detail('结果重排序')
            await expect(self.page.locator('.model-detail-modal[role="dialog"]').get_by_role('switch')).to_be_checked()
        finally:
            release.set()

    async def test_deletion_closes_details_and_removes_only_target(self):
        await self.open_models()
        dialog = await self.detail()
        await (await self.more(dialog)).get_by_role('menuitem', name='删除模型', exact=True).click()
        await self.accept()
        await expect(dialog).not_to_be_visible()
        await expect(self.page.locator('.model-item')).to_have_count(2)
        await expect(self.row('知识向量')).to_be_visible()

    async def test_navigation_closes_pending_confirmation_without_submission(self):
        await self.page.goto(self.origin + '/developer')
        await self.page.get_by_role('link', name='模型目录', exact=True).click()
        await expect(self.page.locator('.model-item')).to_have_count(3)
        await self.row().get_by_role('switch').click()
        await expect(self.page.locator('.ant-modal-confirm')).to_be_visible()
        await self.page.go_back()
        await expect(self.page.get_by_role('heading', name='开发接入', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('dialog')).to_have_count(0)
        self.assertFalse(any(path.endswith('/disable') for _, path, _ in self.requests))

    async def test_pending_model_write_blocks_table_edit_controls(self):
        requested, release = asyncio.Event(), asyncio.Event()
        async def delayed(route):
            requested.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await self.respond(route)
        await self.page.route('**/api/models/rerank-id/enable', delayed)
        try:
            await self.open_models()
            await self.row('结果重排序').get_by_role('switch').click()
            await asyncio.wait_for(requested.wait(), timeout=10)
            await self.page.set_viewport_size({'width': 390, 'height': 844})
            row = self.page.locator('.model-table tbody tr').filter(has_text='结果重排序')
            await expect(row.get_by_role('button', name='更多操作', exact=True)).to_be_disabled()
            release.set()
            await expect(row.get_by_role('button', name='更多操作', exact=True)).to_be_enabled()
            await row.get_by_role('button', name='更多操作', exact=True).click()
            await expect(self.page.get_by_role('menuitem', name='编辑模型', exact=True)).to_be_enabled()
        finally:
            release.set()

    async def test_terminal_test_releases_button_for_a_new_explicit_test(self):
        state = 'completed'
        async def respond(route):
            self.requests.append(('POST', urlparse(route.request.url).path, route.request.post_data_json))
            await route.fulfill(status=201, json={
                'id': 'terminal-run', 'resource_type': 'connection_test', 'resource_id': MODEL_ID,
                'status': state, 'attempt_id': 'attempt-1', 'attempt_no': 1, 'revision': 1,
                'projection_seq': 1, 'text': '', 'error': None, 'created_at': '2026-11-05T00:00:00Z',
                'usage': {'input_tokens': 1, 'output_tokens': 1, 'status': 'known'},
            })
        await self.page.route('**/api/models/' + MODEL_ID + '/test', respond)
        await self.open_models()
        dialog = await self.detail()
        button = dialog.get_by_role('button', name='测试连接', exact=True)
        for expected_state, message in [('completed', '测试成功'), ('failed', '测试失败')]:
            state = expected_state
            await button.click()
            await expect(self.page.locator('.ant-message-notice-content').last).to_have_text(message)
            await expect(button).not_to_have_class(re.compile('ant-btn-loading'))
        requests = [body for _, path, body in self.requests if path.endswith('/test')]
        self.assertEqual(len(requests), 2)
        self.assertNotEqual(requests[0]['client_request_id'], requests[1]['client_request_id'])

    async def test_successful_retry_clears_the_previous_action_error(self):
        first = True
        async def respond(route):
            nonlocal first
            if first:
                first = False
                await route.fulfill(status=409, json={'error': {'code': 'rate_missing', 'message': '请先配置模型费率'}})
            else:
                await self.respond(route)
        await self.page.route('**/api/models/rerank-id/enable', respond)
        await self.open_models()
        row = self.row('结果重排序')
        await row.get_by_role('switch').click()
        await expect(row).to_contain_text('请先配置模型费率')
        await row.get_by_role('switch').click()
        await expect(row.get_by_role('switch')).to_be_checked()
        await expect(row).not_to_contain_text('请先配置模型费率')
        await expect(self.page.locator('.ai-model-center .ant-alert-error')).to_have_count(0)

    async def test_readonly_model_manager_can_distinguish_disabled_models(self):
        self.permissions = ['ai:models:read']
        await self.open_models()
        await expect(self.row('结果重排序').get_by_role('switch')).to_have_count(0)
        await expect(self.row('结果重排序')).to_contain_text('已停用')
        self.assertFalse(any(method != 'GET' for method, _, _ in self.requests))

    async def test_observation_retry_resumes_original_test_without_new_admission(self):
        initial = {'id': 'resume-run', 'resource_type': 'connection_test', 'resource_id': MODEL_ID,
                   'status': 'pending', 'attempt_id': 'attempt-1', 'attempt_no': 1, 'revision': 1,
                   'projection_seq': 1, 'text': '', 'error': None, 'created_at': '2026-11-05T00:00:00Z',
                   'usage': {'input_tokens': None, 'output_tokens': None, 'status': 'pending'}}
        events = 0
        async def admit(route):
            self.requests.append(('POST', urlparse(route.request.url).path, route.request.post_data_json))
            await route.fulfill(status=201, json=initial)
        async def observe(route):
            nonlocal events
            events += 1
            if events == 1:
                await route.fulfill(status=503, json={'error': {'code': 'offline', 'message': '暂时无法读取'}})
            else:
                done = {**initial, 'status': 'completed', 'revision': 2, 'projection_seq': 2,
                        'usage': {'input_tokens': 2, 'output_tokens': 1, 'status': 'known'}}
                await route.fulfill(content_type='text/event-stream', body='event: snapshot\ndata: ' + json.dumps(done) + '\n\n')
        await self.page.route('**/api/models/' + MODEL_ID + '/test', admit)
        await self.page.route('**/api/runs/resume-run/events', observe)
        await self.open_models()
        dialog = await self.detail()
        button = dialog.get_by_role('button', name='测试连接', exact=True)
        await button.click()
        await expect(self.page.locator('.ant-message-notice-content').last).to_have_text('测试结果获取失败，请再次点击测试查看结果')
        await expect(button).not_to_have_class(re.compile('ant-btn-loading'))
        await button.click()
        await expect(self.page.locator('.ant-message-notice-content').last).to_have_text('测试成功')
        self.assertEqual(sum(path.endswith('/test') for _, path, _ in self.requests), 1)
        self.assertEqual(events, 2)

    async def test_mobile_table_shows_enablement_without_publication(self):
        await self.open_models()
        await self.row('结果重排序').get_by_role('switch').click()
        await expect(self.row('结果重排序').get_by_role('switch')).to_be_checked()
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        row = self.page.locator('.model-table tbody tr').filter(has_text='结果重排序')
        await expect(row.get_by_text('已启用', exact=True)).to_be_visible()
        await expect(row.get_by_text('未发布', exact=True)).to_have_count(0)
        await expect(row).not_to_contain_text('512')


if __name__ == '__main__':
    unittest.main()
