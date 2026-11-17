"""Assistant controller cancellation handoffs use only local HTTP fixtures."""

import unittest
from urllib.parse import urlparse

from playwright.async_api import expect

from assistant_conversation_regressions import AssistantConversationRegressions


class AgentsControllerRegressions(AssistantConversationRegressions):
    async def asyncSetUp(self):
        self.cancelled_runs = set()
        self.preparation_cancels = 0
        self.run_cancels = []
        self.lose_preparation_cancel = False
        await super().asyncSetUp()
        self.knowledge_mode = True
        self.completed = False
        self.hold_events = True

    def snapshot(self, run_id, cid):
        snapshot = super().snapshot(run_id, cid)
        if run_id in self.cancelled_runs:
            snapshot.update(status='cancelled', revision=5, projection_seq=5, text='')
        return snapshot

    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path == '/api/auth/me':
            return await route.fulfill(json={
                'id': 'user-1', 'username': 'controller-fixture', 'is_admin': False,
                'permissions': [
                    'platform:agents:enabled', 'platform:agents:read', 'platform:agents:use'
                ]
            })
        if path.startswith('/api/knowledge/preparations/') and path.endswith('/cancel'):
            self.preparation_cancels += 1
            if self.lose_preparation_cancel:
                return await route.fulfill(status=503, json={
                    'error': {'code': 'unavailable', 'message': '停止确认暂未收到'}
                })
            self.preparation_state = 'admitted'
            return await route.fulfill(json=self.preparation_info(path.split('/')[4]))
        if path.startswith('/api/runs/') and path.endswith('/cancel'):
            run_id = path.split('/')[3]
            self.run_cancels.append(run_id)
            self.cancelled_runs.add(run_id)
            cid = next(cid for cid, turns in self.turns.items()
                       if any(turn['run_id'] == run_id for turn in turns))
            return await route.fulfill(json=self.snapshot(run_id, cid))
        return await super().respond(route)

    async def test_preparation_stop_cancels_the_run_admitted_during_handoff(self):
        await self.open_chat()
        await self.ask('停止交接场景')
        await expect(self.page.locator('.assistant-chat__pending')).to_contain_text('正在检索知识库')
        await self.page.get_by_role('button', name='停止生成', exact=True).click()
        await expect(self.page.get_by_role('textbox', name='发送给助手的消息')).to_be_enabled()
        await expect(self.page.locator('.assistant-chat__reply')).to_contain_text('已停止')
        self.assertEqual(self.preparation_cancels, 1)
        self.assertEqual(len(self.run_cancels), 1)
        self.assertEqual(len(self.submissions), 1)

    async def test_recovery_keeps_a_lost_stop_intent_after_preparation_admission(self):
        self.lose_preparation_cancel = True
        await self.open_chat()
        await self.ask('停止响应丢失场景')
        await expect(self.page.locator('.assistant-chat__pending')).to_contain_text('正在检索知识库')
        await self.page.get_by_role('button', name='停止生成', exact=True).click()
        await expect(self.page.get_by_text('停止确认暂未收到', exact=True)).to_be_visible()
        self.preparation_state = 'admitted'
        await self.page.get_by_role('button', name='恢复查看', exact=True).click()
        await expect(self.page.get_by_role('textbox', name='发送给助手的消息')).to_be_enabled()
        await expect(self.page.locator('.assistant-chat__reply')).to_contain_text('已停止')
        self.assertEqual(self.preparation_cancels, 1)
        self.assertEqual(len(self.run_cancels), 1)
        self.assertEqual(len(self.submissions), 1)


if __name__ == '__main__':
    suite = unittest.TestSuite(
        AgentsControllerRegressions(name) for name in (
            'test_preparation_stop_cancels_the_run_admitted_during_handoff',
            'test_recovery_keeps_a_lost_stop_intent_after_preparation_admission',
        )
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
