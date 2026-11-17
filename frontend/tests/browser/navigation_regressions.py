"""Desktop primary-navigation grouping and preference behavior."""
import unittest
from urllib.parse import urlparse

from playwright.async_api import expect
from developer_regressions import DeveloperBoundary


class PrimaryNavigationRegressions(DeveloperBoundary):
    async def asyncSetUp(self):
        self.user = {'id': 'nav-user-1', 'username': '导航用户', 'is_admin': False, 'permissions': [f'platform:{module}:{action}' for module in ['overview','chat','agents','work','models','knowledge','skills','developer','usage'] for action in ['enabled','read']]}
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
            '应用': ['对话体验', '智能助手', '工作空间'],
            '资源': ['模型目录', '知识库', '技能'],
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
        await expect(resources).to_have_attribute('aria-expanded', 'false')
        await expect(resources).to_be_enabled()
        await expect(resources).to_have_class('platform-nav-group-title has-active-page')
        await resources.click()
        await expect(nav.get_by_role('link', name='模型目录', exact=True)).to_have_attribute('aria-current', 'page')
        await resources.click()
        await expect(resources).to_have_attribute('aria-expanded', 'false')
        await expect(nav.get_by_role('link', name='模型目录', exact=True)).to_be_hidden()
        await expect(self.page).to_have_url(self.origin + '/models')
        await self.page.reload()
        await expect(resources).to_have_attribute('aria-expanded', 'false')
        await self.page.goto(self.origin + '/developer')
        await expect(resources).to_have_attribute('aria-expanded', 'false')
        await expect(applications).to_have_attribute('aria-expanded', 'false')

    async def test_current_group_collapses_and_route_changes_only_open_the_new_group(self):
        await self.page.goto(self.origin + '/chat')
        nav = self.page.get_by_role('navigation', name='主导航', exact=True)
        applications = nav.get_by_role('button', name='应用', exact=True)
        chat = nav.get_by_role('link', name='对话体验', exact=True)
        await expect(chat).to_have_attribute('aria-current', 'page')
        await expect(applications).to_be_enabled()
        await nav.get_by_role('link', name='智能助手', exact=True).click()
        await expect(self.page).to_have_url(self.origin + '/agents')
        await applications.click()
        await expect(applications).to_have_attribute('aria-expanded', 'false')
        await expect(applications).to_have_class('platform-nav-group-title has-active-page')
        await expect(self.page).to_have_url(self.origin + '/agents')
        await self.page.go_back()
        await expect(self.page).to_have_url(self.origin + '/chat')
        await expect(applications).to_have_attribute('aria-expanded', 'false')
        await self.page.reload()
        await expect(applications).to_have_attribute('aria-expanded', 'false')
        await nav.get_by_role('link', name='工作台', exact=True).click()
        await expect(applications).not_to_have_class('platform-nav-group-title has-active-page')
        await self.page.go_back()
        await expect(self.page).to_have_url(self.origin + '/chat')
        await expect(applications).to_have_attribute('aria-expanded', 'true')
        await expect(chat).to_have_attribute('aria-current', 'page')
        await applications.focus()
        await self.page.keyboard.press('Enter')
        await expect(applications).to_have_attribute('aria-expanded', 'false')
        await self.page.keyboard.press('Space')
        await expect(applications).to_have_attribute('aria-expanded', 'true')

    async def test_mobile_current_group_can_collapse_without_closing_the_menu(self):
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        await self.page.goto(self.origin + '/chat')
        await self.page.get_by_role('button', name='打开主菜单', exact=True).click()
        drawer = self.page.locator('.platform-mobile-drawer')
        applications = drawer.get_by_role('button', name='应用', exact=True)
        await expect(applications).to_have_attribute('aria-expanded', 'true')
        await applications.click()
        await expect(applications).to_have_attribute('aria-expanded', 'false')
        await expect(drawer.get_by_role('link', name='对话体验', exact=True)).to_be_hidden()
        await expect(drawer).to_be_visible()
        await expect(self.page).to_have_url(self.origin + '/chat')
        await applications.click()
        await expect(drawer.get_by_role('link', name='对话体验', exact=True)).to_have_attribute('aria-current', 'page')

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
        await resources.focus()
        await self.page.keyboard.press('Enter')
        await expect(resources).to_have_attribute('aria-expanded', 'false')
        await self.page.keyboard.press('Space')
        await expect(resources).to_have_attribute('aria-expanded', 'true')


if __name__ == '__main__':
    unittest.main(verbosity=2)
