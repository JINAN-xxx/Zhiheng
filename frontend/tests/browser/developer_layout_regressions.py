"""Developer details stay navigable and scrollable on narrow screens."""

import unittest

from playwright.async_api import expect

from developer_resources_regressions import DeveloperResourceRegressions


class DeveloperLayoutRegressions(DeveloperResourceRegressions):
    async def test_usage_without_currency_distinguishes_statistics_from_points(self):
        rows = [{
            'id': 'usage-' + str(index), 'model_id': 'model-1', 'operation': 'chat',
            'run_status': 'completed', 'status': 'unknown' if index == 0 else 'known',
            'points_enforced': index == 1, 'created_at': '2026-11-10T00:00:00Z',
            'input_tokens': None if index == 0 else 8, 'output_tokens': None if index == 0 else 2,
            'search_units': None, 'billable_cost': None, 'currency': None,
        } for index in range(2)]
        await self.page.route('**/api/developer/applications/app-1/usage?*',
                              lambda route: route.fulfill(json={'items': rows, 'total': 2}))
        for width in (1440, 390):
            await self.page.set_viewport_size({'width': width, 'height': 900})
            await self.page.goto(self.origin + '/developer?applicationId=app-1&tab=usage')
            scope = self.page.locator('.usage-table' if width > 680 else '.usage-mobile-list')
            await expect(scope).to_contain_text('仅统计用量')
            await expect(scope).to_contain_text('按积分计费')
            await expect(scope).to_contain_text('待核对')
        self.assertEqual(self.posts, [])

    async def test_overview_shortcuts_open_existing_tabs_without_writes(self):
        await self.open_details()
        for label, tab in [('前往访问密钥', '访问密钥'),
                           ('前往资源授权', '资源授权'),
                           ('前往调用记录', '调用记录')]:
            await self.page.get_by_role('tab', name='概览', exact=True).click()
            await self.page.get_by_role('button', name=label, exact=True).click()
            await expect(self.page.get_by_role('tab', name=tab, exact=True)).to_have_attribute(
                'aria-selected', 'true'
            )
        self.assertEqual(self.posts, [])
        self.assertEqual(self.resource_posts, [])

    async def test_all_detail_tabs_can_be_clicked_on_a_small_phone(self):
        await self.page.set_viewport_size({'width': 320, 'height': 844})
        await self.open_details()
        for label in ['访问密钥', '模型授权', '资源授权', '调用记录', '概览']:
            tab = self.page.get_by_role('tab', name=label, exact=True)
            await expect(tab).to_be_in_viewport()
            await tab.click()
            await expect(tab).to_have_attribute('aria-selected', 'true')
        self.assertTrue(await self.page.evaluate(
            'document.documentElement.scrollWidth <= innerWidth'
        ))

    async def test_last_authorized_resource_is_reachable_without_moving_navigation(self):
        self.bindings = [{
            'kind': 'knowledge_base', 'resource_id': f'resource-{index}',
            'display_name': f'资料 {index}', 'status': 'ready', 'owned': True,
            'allowed_actions': ['read', 'write', 'query', 'delete'],
            'actions': ['read', 'query'], 'available': True,
        } for index in range(1, 13)]
        await self.page.set_viewport_size({'width': 320, 'height': 844})
        await self.page.goto(self.origin + '/developer?applicationId=app-1&tab=resources')
        last = self.page.get_by_text('资料 12', exact=True)
        await expect(last).to_have_count(1)
        tabs = self.page.locator('.detail-tabs')
        before = await tabs.bounding_box()
        await last.scroll_into_view_if_needed()
        await expect(last).to_be_in_viewport()
        after = await tabs.bounding_box()
        self.assertAlmostEqual(before['y'], after['y'], delta=.5)
        self.assertGreater(await self.page.locator('.resources-panel').evaluate(
            '(panel) => panel.scrollTop'
        ), 0)


def load_tests(loader, _tests, _pattern):
    return unittest.TestSuite(DeveloperLayoutRegressions(name)
                              for name in loader.getTestCaseNames(DeveloperLayoutRegressions)
                              if name in DeveloperLayoutRegressions.__dict__)


if __name__ == '__main__':
    unittest.main(verbosity=2)
