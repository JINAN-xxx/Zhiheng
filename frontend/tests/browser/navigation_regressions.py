"""Desktop primary-navigation grouping and preference behavior."""
import unittest
from urllib.parse import urlparse

from playwright.async_api import expect
from developer_regressions import DeveloperBoundary


class PrimaryNavigationRegressions(DeveloperBoundary):
    async def asyncSetUp(self):
        self.user = {'id': 'nav-user-1', 'username': '导航用户', 'is_admin': False, 'permissions': []}
        await super().asyncSetUp()

    async def respond(self, route):
        if urlparse(route.request.url).path == '/api/auth/me':
            await route.fulfill(json=self.user)
        else:
            await super().respond(route)

    async def open_navigation(self):
        await self.page.goto(self.origin + '/developer')
        nav = self.page.get_by_role('navigation', name='主导航', exact=True)
        await expect(nav.get_by_role('button', name='资源', exact=True)).to_be_visible()
        return nav

    async def test_groups_remember_collapse_and_keep_current_and_icon_routes_accessible(self):
        nav = await self.open_navigation()
        expected = {
            '应用': ['对话体验', '智能体', '工作空间'],
            '资源': ['模型目录', '知识库', '技能管理'],
            '开发与用量': ['开发接入', '用量与调用'],
        }
        for group, labels in expected.items():
            panel = nav.get_by_role('group', name=group, exact=True)
            self.assertEqual(await panel.get_by_role('link').all_text_contents(), labels)
        await expect(nav.get_by_role('link').first).to_have_attribute('href', '/overview')
        await expect(nav.get_by_role('link')).to_have_count(9)
        resources = nav.get_by_role('button', name='资源', exact=True)
        applications = nav.get_by_role('button', name='应用', exact=True)
        original_url = self.page.url
        await resources.click()
        await applications.click()
        await expect(resources).to_have_attribute('aria-expanded', 'false')
        await expect(nav.get_by_role('link', name='模型目录', exact=True)).to_be_hidden()
        self.assertEqual(self.page.url, original_url)
        await self.page.reload()
        await expect(resources).to_have_attribute('aria-expanded', 'false')
        await expect(applications).to_have_attribute('aria-expanded', 'false')
        await expect(nav.get_by_role('link', name='开发接入', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='收起侧栏', exact=True).click()
        for label in ['工作台', *sum(expected.values(), [])]:
            await expect(nav.get_by_role('link', name=label, exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='展开侧栏', exact=True).click()
        await expect(resources).to_have_attribute('aria-expanded', 'false')
        await expect(applications).to_have_attribute('aria-expanded', 'false')
        await self.page.goto(self.origin + '/models')
        await expect(resources).to_have_attribute('aria-expanded', 'true')
        await expect(nav.get_by_role('link', name='模型目录', exact=True)).to_have_attribute('aria-current', 'page')
        await expect(resources).to_be_disabled()
        await self.page.reload()
        await expect(resources).to_have_attribute('aria-expanded', 'true')
        await self.page.goto(self.origin + '/developer')
        await expect(resources).to_have_attribute('aria-expanded', 'false')
        await expect(applications).to_have_attribute('aria-expanded', 'false')

    async def test_preferences_are_separate_for_each_account(self):
        nav = await self.open_navigation()
        applications = nav.get_by_role('button', name='应用', exact=True)
        resources = nav.get_by_role('button', name='资源', exact=True)
        await applications.click()
        self.user = {**self.user, 'id': 'nav-user-2', 'username': '另一个用户'}
        await self.page.reload()
        await expect(applications).to_have_attribute('aria-expanded', 'true')
        await resources.click()
        self.user = {**self.user, 'id': 'nav-user-1', 'username': '导航用户'}
        await self.page.reload()
        await expect(applications).to_have_attribute('aria-expanded', 'false')
        await expect(resources).to_have_attribute('aria-expanded', 'true')

    async def test_unavailable_preference_storage_does_not_break_navigation(self):
        await self.page.add_init_script("""(() => {
            for (const method of ['getItem', 'setItem']) {
                const original = Storage.prototype[method];
                Storage.prototype[method] = function(key, ...args) {
                    if (key.startsWith('zhiheng:primary-navigation:'))
                        throw new DOMException('Storage unavailable', 'SecurityError');
                    return original.call(this, key, ...args);
                };
            }
        })();""")
        nav = await self.open_navigation()
        resources = nav.get_by_role('button', name='资源', exact=True)
        await resources.focus()
        await self.page.keyboard.press('Enter')
        await expect(resources).to_have_attribute('aria-expanded', 'false')
        await self.page.keyboard.press('Space')
        await expect(resources).to_have_attribute('aria-expanded', 'true')
        await nav.get_by_role('link', name='模型目录', exact=True).click()
        await expect(self.page).to_have_url(self.origin + '/models')
        await expect(nav.get_by_role('link', name='模型目录', exact=True)).to_have_attribute('aria-current', 'page')


if __name__ == '__main__':
    unittest.main(verbosity=2)
