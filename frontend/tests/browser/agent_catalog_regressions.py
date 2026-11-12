"""Agent status filters use complete server results rather than the current page."""

import asyncio
import unittest
from urllib.parse import parse_qs, urlparse

from playwright.async_api import expect

from agents_regressions import AgentsUIRegressions


class AgentCatalogRegressions(AgentsUIRegressions):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.catalog = []
        self.list_requests = []
        self.fail_published_count = False
        for index in range(17):
            status = 'published' if index < 12 else 'draft'
            self.catalog.append({
                **self.agent,
                'id': f'catalog-{index}',
                'name': f'团队助手 {index + 1:02}',
                'status': status,
                'published_version': 1 if status == 'published' else None,
                'published_config': dict(self.config) if status == 'published' else None,
            })

    async def respond(self, route):
        if urlparse(route.request.url).path != '/api/agents' or route.request.method != 'GET':
            await super().respond(route)
            return
        query = parse_qs(urlparse(route.request.url).query)
        search = query.get('search', [''])[0]
        status = query.get('status', [''])[0]
        page = int(query.get('page', ['1'])[0])
        size = int(query.get('page_size', ['12'])[0])
        self.list_requests.append({'search': search, 'status': status, 'page': page, 'size': size})
        if self.fail_published_count and status == 'published' and size == 1:
            await route.fulfill(status=503, json={'error': {
                'code': 'count_unavailable', 'message': '状态统计暂不可用'
            }})
            return
        rows = [item for item in self.catalog
                if (not status or item['status'] == status)
                and search.casefold() in (item['name'] + item['description']).casefold()]
        offset = (page - 1) * size
        await route.fulfill(json={
            'items': rows[offset:offset + size], 'total': len(rows),
            'page': page, 'page_size': size,
        })

    def status_button(self, name):
        return self.page.get_by_role('group', name='按发布状态筛选智能体').get_by_role(
            'button', name=name, exact=True
        )

    async def test_status_filter_reads_beyond_first_page_and_resets_pagination(self):
        await self.open_agents()
        await expect(self.status_button('全部')).to_contain_text('17')
        await expect(self.status_button('已发布')).to_contain_text('12')
        await expect(self.status_button('草稿')).to_contain_text('5')
        await self.page.locator('.ant-pagination-next button').click()
        await expect(self.page.locator('.agent-card')).to_have_count(5)
        await self.status_button('草稿').click()
        await expect(self.status_button('草稿')).to_have_attribute('aria-pressed', 'true')
        await expect(self.page.locator('.agent-card')).to_have_count(5)
        await expect(self.page.locator('.agent-total')).to_have_text('共 5 个')
        await expect(self.page.locator('.ant-pagination')).to_have_count(0)
        await expect(self.page.locator('.create-agent-card')).to_have_count(0)
        self.assertTrue(any(item['status'] == 'draft' and item['page'] == 1
                            and item['size'] == 12 for item in self.list_requests))

    async def test_search_and_status_share_the_same_query_and_complete_counts(self):
        self.catalog[0]['name'] = '法务助手'
        self.catalog[12]['name'] = '法务草稿'
        await self.open_agents()
        await self.status_button('已发布').click()
        await expect(self.page.locator('.agent-total')).to_have_text('共 12 个')
        search = self.page.get_by_placeholder('搜索智能体', exact=True)
        await search.fill('法务')
        await search.press('Enter')
        await expect(self.page.locator('.agent-card')).to_have_count(1)
        await expect(self.page.locator('.agent-card')).to_contain_text('法务助手')
        await expect(self.status_button('全部')).to_contain_text('2')
        await expect(self.status_button('已发布')).to_contain_text('1')
        await expect(self.status_button('草稿')).to_contain_text('1')
        await self.status_button('草稿').click()
        await expect(self.page.locator('.agent-card')).to_contain_text('法务草稿')
        self.assertTrue(any(item['search'] == '法务' and item['status'] == 'draft'
                            and item['page'] == 1 for item in self.list_requests))

    async def test_unavailable_status_count_does_not_replace_the_agent_list(self):
        self.fail_published_count = True
        await self.open_agents()
        await expect(self.page.locator('.agent-card')).to_have_count(12)
        await expect(self.status_button('全部')).to_contain_text('17')
        await expect(self.status_button('已发布')).to_contain_text('—')
        await expect(self.status_button('草稿')).to_contain_text('5')
        await expect(self.page.get_by_text('状态统计暂不可用', exact=True)).to_have_count(0)
        await self.status_button('草稿').click()
        await expect(self.page.locator('.agent-card')).to_have_count(5)

    async def test_empty_filtered_result_preserves_search_and_creation(self):
        self.catalog = []
        await self.open_agents()
        await expect(self.page.get_by_text('暂无智能体', exact=True)).to_be_visible()
        await self.status_button('已发布').click()
        await expect(self.page.get_by_text('没有匹配的智能体', exact=True)).to_be_visible()
        await expect(self.page.get_by_placeholder('搜索智能体', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='新建智能体', exact=True).click()
        await expect(self.page).to_have_url(self.origin + '/agents/new')

    async def test_filter_controls_and_existing_results_stay_stable_during_refresh(self):
        await self.page.set_viewport_size({'width': 1600, 'height': 750})
        await self.open_agents()
        await expect(self.status_button('已发布')).to_contain_text('12')
        await expect(self.status_button('草稿')).to_contain_text('5')

        async def positions():
            return await self.page.evaluate('''() => {
                const selectors = ['.agent-status-filters', '.agent-total',
                    '.agent-controls .ant-input-search', '.agent-grid'];
                return selectors.map(selector => {
                    const rect = document.querySelector(selector).getBoundingClientRect();
                    return {x: rect.x, y: rect.y, width: rect.width};
                });
            }''')

        requested, release = asyncio.Event(), asyncio.Event()

        async def delayed_list(route):
            query = parse_qs(urlparse(route.request.url).query)
            if query.get('status') == ['draft'] and query.get('page_size') == ['12']:
                requested.set()
                await release.wait()
            await self.respond(route)

        before = await positions()
        await self.page.route(self.origin + '/api/agents?*', delayed_list)
        try:
            await self.status_button('草稿').click()
            await asyncio.wait_for(requested.wait(), timeout=5)
            await expect(self.status_button('全部')).to_contain_text('17')
            await expect(self.status_button('已发布')).to_contain_text('12')
            await expect(self.status_button('草稿')).to_contain_text('5')
            await expect(self.page.locator('.agent-card')).to_have_count(12)
            await expect(self.page.locator('.create-agent-card')).to_have_count(1)
            during = await positions()
        finally:
            release.set()
        await expect(self.page.locator('.agent-total')).to_have_text('共 5 个')
        await expect(self.page.locator('.agent-card')).to_have_count(5)
        await expect(self.page.locator('.create-agent-card')).to_have_count(0)
        after = await positions()
        for observed in (during, after):
            for original, current in zip(before, observed, strict=True):
                for dimension in ('x', 'y', 'width'):
                    self.assertAlmostEqual(original[dimension], current[dimension], delta=.5)


def load_tests(loader, _tests, _pattern):
    return unittest.TestSuite(AgentCatalogRegressions(name)
                              for name in loader.getTestCaseNames(AgentCatalogRegressions)
                              if name in AgentCatalogRegressions.__dict__)


if __name__ == '__main__':
    unittest.main(verbosity=2)
