"""The plaza has no history entry, and existing conversation links still work."""

from urllib.parse import urlparse

from playwright.async_api import expect

from developer_regressions import DeveloperBoundary


class PlazaHistoryRegression(DeveloperBoundary):
    async def test_plaza_has_no_history_entry_and_supports_existing_conversation_links(self):
        agent_id = 'unpublished-assistant'
        history_requests = []

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
                history_requests.append(request.path)
                await route.fulfill(json=[])
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
        await expect(self.page.get_by_role('button', name='历史记录', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('textbox', name='搜索广场助手')).to_be_visible()
        await self.page.goto(self.origin + f'/agents/plaza/{agent_id}/chat?conversation=conversation-1')
        await expect(self.page.get_by_role('heading', name='已下架助手')).to_be_visible()
        self.assertEqual(history_requests, [])
