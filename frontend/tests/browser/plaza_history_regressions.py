"""Open a conversation from a later history page after its assistant is unpublished."""

from urllib.parse import parse_qs, urlparse

from playwright.async_api import expect

from developer_regressions import DeveloperBoundary


class PlazaHistoryRegression(DeveloperBoundary):
    async def test_more_history_preserves_loaded_conversations_and_opens_unpublished_assistant(self):
        agent_id = 'unpublished-assistant'
        before_queries = []

        def conversation(index):
            return {
                'id': f'conversation-{index}',
                'agent_id': agent_id,
                'title': f'会话 {index}',
                'name': '已下架助手',
                'description': '',
                'welcome_message': '',
                'version': 1,
                'created_at': '2026-11-24T12:00:00Z',
                'updated_at': '2026-11-24T12:00:00Z',
                'memory_available': True,
                'summary_used': False,
            }

        async def fixtures(route):
            request = urlparse(route.request.url)
            if request.path == '/api/agents/plaza':
                await route.fulfill(json={'items': [], 'total': 0})
            elif request.path == '/api/agents/plaza/history':
                before = parse_qs(request.query).get('before', [None])[0]
                before_queries.append(before)
                indexes = range(14, 2, -1) if before is None else (2, 1)
                await route.fulfill(json=[conversation(index) for index in indexes])
            elif request.path == f'/api/agents/plaza/{agent_id}':
                await route.fulfill(
                    status=404,
                    json={'error': {'code': 'assistant_not_on_plaza', 'message': '此助手未上架或已下架'}},
                )
            elif request.path == f'/api/agents/{agent_id}/conversations':
                await route.fulfill(json=[conversation(1)])
            elif request.path == f'/api/agents/{agent_id}/conversations/conversation-1':
                await route.fulfill(json={'conversation': conversation(1), 'turns': []})
            else:
                await self.respond(route)

        await self.page.route('**/api/agents/**', fixtures)
        await self.page.goto(self.origin + '/agents?tab=plaza')
        await expect(self.page.get_by_text('暂无上架助手', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='历史记录').click()
        dialog = self.page.get_by_role('dialog', name='广场历史记录')
        await expect(dialog.get_by_role('button', name='已下架助手 · 会话 14')).to_be_visible()
        await dialog.get_by_role('button', name='加载更多会话').click()
        await expect(dialog.get_by_role('button', name='已下架助手 · 会话 1', exact=True)).to_be_visible()
        await expect(dialog.get_by_role('button', name='已下架助手 · 会话 14')).to_have_count(1)
        await expect(dialog.get_by_role('button', name='加载更多会话')).to_have_count(0)
        self.assertEqual(before_queries, [None, 'conversation-3'])
        await dialog.get_by_role('button', name='已下架助手 · 会话 1', exact=True).click()
        await expect(self.page).to_have_url(
            self.origin + f'/agents/plaza/{agent_id}/chat?conversation=conversation-1'
        )
        await expect(self.page.get_by_role('heading', name='已下架助手')).to_be_visible()
