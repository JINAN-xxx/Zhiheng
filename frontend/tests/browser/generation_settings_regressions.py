"""Generation settings through the Vue UI, with every API call kept at a fixture boundary."""
import asyncio
import contextlib
import re
import unittest
from urllib.parse import urlparse

from playwright.async_api import expect
from developer_regressions import DeveloperBoundary

STAMP = '2026-10-29T00:00:00Z'
CUSTOM = {'model_id': 'model-1', 'system_prompt': '历史提示词', 'temperature': 0.3, 'max_output_tokens': 96}


def message(message_id, role, content, config=None):
    return {'id': message_id, 'role': role, 'content': content, 'run_id': None,
            'created_at': STAMP, 'generation_config': config}


class GenerationSettingsRegressions(DeveloperBoundary):
    async def asyncSetUp(self):
        self.run_calls = []
        self.regenerate_calls = []
        self.histories = {
            'c1': [message('q1', 'user', '参数问题', CUSTOM), message('a1', 'assistant', '参数回复', CUSTOM)],
            'c2': [message('q2', 'user', '旧问题'), message('a2', 'assistant', '旧回复')]
        }
        await super().asyncSetUp()

    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path == '/api/chat/conversations':
            if route.request.method == 'POST':
                await route.fulfill(json={'id': 'c3', 'title': '新对话', 'created_at': STAMP})
            else:
                await route.fulfill(json=[{'id': 'c1', 'title': '参数会话', 'created_at': STAMP},
                                          {'id': 'c2', 'title': '旧会话', 'created_at': STAMP}])
        elif path.endswith('/messages') and path.startswith('/api/chat/conversations/'):
            await route.fulfill(json=self.histories.get(path.split('/')[4], []))
        elif path.endswith('/runs') and path.startswith('/api/chat/conversations/'):
            self.run_calls.append(route.request.post_data_json)
            await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '接纳结果待确认'}})
        elif path.endswith('/regenerate'):
            self.regenerate_calls.append(route.request.post_data_json)
            await route.fulfill(status=422, json={'error': {'code': 'model_limit_changed', 'message': '原模型输出上限已降低，请调整后续消息设置'}})
        else:
            await super().respond(route)

    async def open_chat(self, conversation=None):
        await self.page.goto(self.origin + '/chat')
        await expect(self.page.get_by_role('button', name='对话设置', exact=True)).to_be_enabled()
        if conversation:
            await self.select_conversation(conversation)

    async def select_conversation(self, name):
        await self.page.get_by_role('button', name='历史记录', exact=True).click()
        await self.page.get_by_role('button', name=name, exact=True).click()
        await expect(self.page.get_by_role('dialog', name='历史记录', exact=True)).not_to_be_visible()

    async def settings_dialog(self):
        await self.page.get_by_role('button', name='对话设置', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='对话设置', exact=True)
        await expect(dialog).to_be_visible()
        return dialog

    async def save_settings(self, prompt='新的提示词', temperature='0.7', length='128'):
        dialog = await self.settings_dialog()
        await dialog.get_by_label('系统提示词', exact=True).fill(prompt)
        await dialog.get_by_label('温度', exact=True).fill(temperature)
        await dialog.get_by_label('输出长度', exact=True).fill(length)
        await dialog.get_by_role('button', name='保存设置', exact=True).click()
        await expect(dialog).not_to_be_visible()

    async def test_settings_validate_save_cancel_and_restore_defaults(self):
        await self.open_chat()
        dialog = await self.settings_dialog()
        await expect(dialog.get_by_text('设置仅对后续消息生效；重新生成沿用原消息参数。', exact=True)).to_be_visible()
        await expect(dialog.get_by_text(re.compile('默认不预设输出长度上限'))).to_be_visible()
        temperature = dialog.get_by_label('温度', exact=True)
        length = dialog.get_by_label('输出长度', exact=True)
        save = dialog.get_by_role('button', name='保存设置', exact=True)
        for value in ['2.1', '-0.1', 'Infinity', 'NaN', '错误']:
            await temperature.fill(value)
            await expect(save).to_be_disabled()
            await expect(dialog.get_by_role('alert')).to_contain_text('温度须为')
        await temperature.fill('0')
        for value in ['0', '513', '1.5', 'Infinity']:
            await length.fill(value)
            await expect(save).to_be_disabled()
            await expect(dialog.get_by_role('alert')).to_contain_text('输出长度须为')
        await length.fill('512')
        await expect(save).to_be_enabled()
        await dialog.get_by_label('系统提示词', exact=True).fill('只在内存的提示词')
        await save.click()
        await expect(dialog).not_to_be_visible()
        dialog = await self.settings_dialog()
        await expect(dialog.get_by_label('温度', exact=True)).to_have_value('0')
        await dialog.get_by_label('温度', exact=True).fill('1.8')
        await dialog.get_by_role('button', name='取消', exact=True).click()
        dialog = await self.settings_dialog()
        await expect(dialog.get_by_label('温度', exact=True)).to_have_value('0')
        await dialog.get_by_role('button', name='恢复默认', exact=True).click()
        await expect(dialog.get_by_label('系统提示词', exact=True)).to_have_value('')
        await expect(dialog.get_by_label('温度', exact=True)).to_have_value('')
        await expect(dialog.get_by_label('输出长度', exact=True)).to_have_value('')
        await dialog.get_by_role('button', name='保存设置', exact=True).click()
        self.assertEqual(self.run_calls, [])
        stored = await self.page.evaluate('JSON.stringify({...localStorage}) + JSON.stringify({...sessionStorage})')
        self.assertNotIn('只在内存的提示词', stored)

    async def test_uncertain_retry_keeps_frozen_settings_and_edit_gets_a_new_key(self):
        await self.open_chat('参数会话')
        await self.save_settings()
        await self.page.get_by_role('textbox', name='消息内容').fill('参数提问')
        await self.page.get_by_role('button', name='发送消息', exact=True).click()
        await expect(self.page.get_by_text('接纳结果待确认', exact=True)).to_be_visible()
        self.assertEqual({key: self.run_calls[0][key] for key in ('system_prompt', 'temperature', 'max_output_tokens')},
                         {'system_prompt': '新的提示词', 'temperature': 0.7, 'max_output_tokens': 128})
        await self.save_settings('修改后的提示词', '1.2', '256')
        await self.page.get_by_role('button', name='使用原请求重试', exact=True).click()
        await expect(self.page.get_by_text('接纳结果待确认', exact=True)).to_be_visible()
        self.assertEqual(len(self.run_calls), 2)
        self.assertEqual(self.run_calls[0], self.run_calls[1])
        await self.page.get_by_role('button', name='发送消息', exact=True).click()
        await expect(self.page.get_by_text('接纳结果待确认', exact=True)).to_be_visible()
        self.assertEqual(len(self.run_calls), 3)
        self.assertNotEqual(self.run_calls[0]['client_request_id'], self.run_calls[2]['client_request_id'])
        self.assertEqual(self.run_calls[2]['system_prompt'], '修改后的提示词')
        self.assertEqual(self.run_calls[2]['temperature'], 1.2)
        self.assertEqual(self.run_calls[2]['max_output_tokens'], 256)

    async def test_switching_and_new_conversation_restore_snapshots_without_browser_storage(self):
        await self.open_chat('参数会话')
        dialog = await self.settings_dialog()
        await expect(dialog.get_by_label('系统提示词', exact=True)).to_have_value('历史提示词')
        await expect(dialog.get_by_label('温度', exact=True)).to_have_value('0.3')
        await expect(dialog.get_by_label('输出长度', exact=True)).to_have_value('96')
        await dialog.get_by_role('button', name='取消', exact=True).click()
        await self.save_settings()
        await self.select_conversation('旧会话')
        dialog = await self.settings_dialog()
        await expect(dialog.get_by_label('系统提示词', exact=True)).to_have_value('')
        await expect(dialog.get_by_label('温度', exact=True)).to_have_value('')
        await expect(dialog.get_by_label('输出长度', exact=True)).to_have_value('')
        await dialog.get_by_role('button', name='取消', exact=True).click()
        await self.select_conversation('参数会话')
        dialog = await self.settings_dialog()
        await expect(dialog.get_by_label('系统提示词', exact=True)).to_have_value('历史提示词')
        await dialog.get_by_role('button', name='取消', exact=True).click()
        await self.page.get_by_role('button', name='新建对话', exact=True).click()
        dialog = await self.settings_dialog()
        await expect(dialog.get_by_label('系统提示词', exact=True)).to_have_value('')
        await dialog.get_by_role('button', name='取消', exact=True).click()
        await self.page.get_by_role('textbox', name='消息内容').fill('默认提问')
        await self.page.get_by_role('button', name='发送消息', exact=True).click()
        await expect(self.page.get_by_text('接纳结果待确认', exact=True)).to_be_visible()
        for field in ('system_prompt', 'temperature', 'max_output_tokens'):
            self.assertNotIn(field, self.run_calls[0])
        await self.page.reload()
        dialog = await self.settings_dialog()
        await expect(dialog.get_by_label('系统提示词', exact=True)).to_have_value('')

    async def test_regenerate_uses_original_message_contract_and_shows_real_limit_error(self):
        await self.open_chat('参数会话')
        await self.save_settings('后续消息提示词', '1.9', '256')
        await self.page.get_by_role('button', name='重新生成', exact=True).click()
        await expect(self.page.get_by_text('原模型输出上限已降低，请调整后续消息设置', exact=True)).to_be_visible()
        self.assertEqual(len(self.regenerate_calls), 1)
        self.assertEqual(set(self.regenerate_calls[0]), {'client_request_id'})

    async def test_conversation_restores_its_model_and_validates_that_models_limit(self):
        model = {'id': 'model-2', 'name': '历史模型', 'connection_id': 'connection-1',
                 'upstream_model': 'fixture-only', 'max_output_tokens': 256, 'model_type': 'chat',
                 'published': True, 'enabled': True, 'capabilities': ['chat', 'stream']}
        await self.page.route('**/api/models', lambda route: route.fulfill(json=[{**model, 'id': 'model-1', 'name': '日常助手', 'max_output_tokens': 512}, model]))
        for item in self.histories['c1']:
            item['generation_config'] = {**CUSTOM, 'model_id': 'model-2'}
        await self.open_chat('参数会话')
        await expect(self.page.locator('.model-select .ant-select-selection-item')).to_have_text('历史模型')
        dialog = await self.settings_dialog()
        await expect(dialog.get_by_text(re.compile('默认不预设输出长度上限'))).to_be_visible()
        await dialog.get_by_label('输出长度', exact=True).fill('257')
        await expect(dialog.get_by_role('button', name='保存设置', exact=True)).to_be_disabled()
        await expect(dialog.get_by_role('alert')).to_contain_text('1–256')
        self.assertEqual(self.run_calls, [])

    async def test_unavailable_historical_model_requires_an_explicit_replacement(self):
        for item in self.histories['c1']:
            item['generation_config'] = {**CUSTOM, 'model_id': 'removed-model'}
        await self.open_chat('参数会话')
        await expect(self.page.get_by_text('原模型当前不可用，请重新选择模型', exact=True)).to_be_visible()
        await self.page.get_by_role('textbox', name='消息内容').fill('等待重新选择模型')
        await expect(self.page.get_by_role('button', name='发送消息', exact=True)).to_be_disabled()
        await self.page.get_by_role('button', name='历史记录', exact=True).click()
        async with self.page.expect_response(lambda response: urlparse(response.url).path == '/api/models'):
            await self.page.get_by_role('button', name='刷新会话', exact=True).click()
        await self.close_history()
        await expect(self.page.get_by_role('button', name='发送消息', exact=True)).to_be_disabled()
        await self.page.get_by_role('combobox').click()
        await self.page.get_by_text('日常助手', exact=True).last.click()
        await expect(self.page.get_by_text('原模型当前不可用，请重新选择模型', exact=True)).not_to_be_visible()
        await expect(self.page.get_by_role('button', name='发送消息', exact=True)).to_be_enabled()
        self.assertEqual(self.run_calls, [])

    async def test_busy_submission_disables_settings(self):
        requested, release = asyncio.Event(), asyncio.Event()
        async def delayed(route):
            requested.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '接纳结果待确认'}})
        await self.page.route('**/api/chat/conversations/c1/runs', delayed)
        try:
            await self.open_chat('参数会话')
            await self.page.get_by_role('textbox', name='消息内容').fill('等待接纳')
            await self.page.get_by_role('button', name='发送消息', exact=True).click()
            await asyncio.wait_for(requested.wait(), timeout=5)
            dialog = await self.settings_dialog()
            for field in ('系统提示词', '温度', '输出长度'):
                await expect(dialog.get_by_label(field, exact=True)).to_be_disabled()
            await expect(dialog.get_by_role('button', name='保存设置', exact=True)).to_be_disabled()
            await expect(dialog.get_by_role('button', name='恢复默认', exact=True)).to_be_disabled()
        finally:
            release.set()


if __name__ == '__main__':
    unittest.main()
