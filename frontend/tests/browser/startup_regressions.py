"""Recover an actual failed entry download without needing the application bundle."""
import os
import re
import unittest

from playwright.async_api import async_playwright, expect


class StartupRegressions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True, args=['--no-sandbox'])
        self.context = await self.browser.new_context(viewport={'width': 1280, 'height': 800})
        self.page = await self.context.new_page()
        self.origin = os.environ.get('PLATFORM_BROWSER_URL', 'http://127.0.0.1:15191')
        await self.page.route(self.origin + '/api/**', lambda route: route.fulfill(
            status=401, json={'error': {'code': 'unauthorized', 'message': '请先登录'}}))

    async def asyncTearDown(self):
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()

    async def check_recovery(self, resource_pattern):
        unavailable = True
        requests = 0

        async def entry(route):
            nonlocal requests
            requests += 1
            if unavailable:
                await route.fulfill(status=503, content_type='application/javascript', body='')
            else:
                await route.continue_()

        pattern = re.compile(re.escape(self.origin) + resource_pattern)
        await self.page.route(pattern, entry)
        await self.page.goto(self.origin + '/login')
        await expect(self.page.get_by_text('页面加载失败，请检查网络后重试', exact=True)).to_be_visible()
        retry = self.page.get_by_role('button', name='重新加载', exact=True)
        await expect(retry).to_be_enabled()
        self.assertGreater(requests, 0)
        unavailable = False
        await retry.click()
        await expect(self.page.get_by_role('button', name='登录', exact=True)).to_be_visible()
        await expect(self.page.locator('.startup-loading')).to_have_count(0)
        self.assertGreater(requests, 1)

    async def test_failed_entry_explains_the_problem_and_reload_recovers(self):
        await self.check_recovery(r'/(?:src/app/main[.]ts|assets/platform-[^/]+[.]js)(?:[?].*)?$')

    async def test_failed_initial_route_can_reload_after_network_recovers(self):
        await self.check_recovery(r'/(?:src/modules/identity/views/LoginView[.]vue|assets/LoginView-[^/]+[.]js)(?:[?].*)?$')


if __name__ == '__main__':
    unittest.main(verbosity=2)
