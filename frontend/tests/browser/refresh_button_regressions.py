"""Refresh controls keep one appearance and block duplicate in-flight actions."""

import unittest

from playwright.async_api import expect

from admin_regressions import AdminRegressions
from agents_regressions import AgentsUIRegressions
from billing_regressions import BillingRegressions
from console_regressions import ConsoleUIRegressions
from developer_regressions import DeveloperBoundary
from developer_resources_regressions import DeveloperResourceRegressions
from files_regressions import FilesUIRegressions
from graph_regressions import GraphRegressions
from knowledge_regressions import KnowledgeUIRegressions
from model_compact_regressions import CompactModels
from skill_compact_regressions import CompactSkills


class RefreshButtonRegressions(DeveloperBoundary):
    # This case creates and closes eleven independent page/browser fixtures.
    case_timeouts = {'test_refresh_controls_match_across_platform_pages': 180}

    async def assert_refresh_appearance(self, button):
        await expect(button).to_be_visible()
        await expect(button).to_be_enabled()
        await button.page.mouse.move(0, 0)
        await expect(button).to_have_css('background-color', 'rgb(255, 255, 255)')
        style = await button.evaluate('''(button) => {
            const rect = button.getBoundingClientRect(), style = getComputedStyle(button);
            return {width: rect.width, height: rect.height, radius: style.borderRadius,
                background: style.backgroundColor, border: style.borderTopWidth};
        }''')
        for dimension in ['width', 'height']:
            self.assertAlmostEqual(style.pop(dimension), 36, places=2)
        self.assertEqual(style, {'radius': '8px', 'background': 'rgb(255, 255, 255)', 'border': '1px'})

    async def test_refresh_controls_match_across_platform_pages(self):
        cases = [
            (ConsoleUIRegressions, '/overview', '刷新工作台'),
            (AgentsUIRegressions, '/agents', '刷新智能体'),
            (CompactModels, '/models', '刷新当前视图'),
            (CompactSkills, '/skills', '刷新技能'),
            (KnowledgeUIRegressions, '/knowledge', '刷新知识库'),
            (KnowledgeUIRegressions, '/knowledge/base-1/documents', '刷新资料'),
            (GraphRegressions, '/knowledge/base-1/graph', '刷新知识图谱'),
            (DeveloperBoundary, '/developer', '刷新应用'),
            (DeveloperResourceRegressions, '/developer?applicationId=app-1&tab=resources', '刷新资源授权'),
            (FilesUIRegressions, '/admin/storage', '刷新文件'),
            (BillingRegressions, '/usage', '刷新积分'),
        ]
        for case_type, path, label in cases:
            with self.subTest(path=path, label=label):
                case = case_type()
                await case.asyncSetUp()
                try:
                    await case.page.goto(case.origin + path)
                    button = case.page.get_by_role('button', name=label, exact=True)
                    await self.assert_refresh_appearance(button)
                    async with case.page.expect_request(
                        lambda request: request.method == 'GET' and '/api/' in request.url
                    ):
                        await button.click()
                    await expect(button).to_be_enabled()
                    await self.assert_refresh_appearance(button)
                finally:
                    await case.asyncTearDown()

    async def test_admin_subpages_share_the_same_refresh_control(self):
        case = AdminRegressions()
        await case.asyncSetUp()
        try:
            for path in ['users', 'roles', 'departments',
                         'notices', 'login-logs', 'operation-logs', 'sessions', 'monitor']:
                with self.subTest(path=path):
                    await case.page.goto(case.origin + '/admin/' + path)
                    button = case.page.locator('.workspace-refresh-button')
                    await self.assert_refresh_appearance(button)
                    self.assertTrue((await button.get_attribute('aria-label')).startswith('刷新'))
        finally:
            await case.asyncTearDown()

    async def test_loading_keeps_dimensions_blocks_duplicates_and_recovers(self):
        await self.page.goto(self.origin + '/developer')
        await self.page.get_by_role('heading', name='开发接入', exact=True).wait_for()
        await self.page.evaluate('''async () => {
            const source = await (await fetch('/src/shared/components/RefreshButton.vue')).text();
            const paths = [...source.matchAll(/from\\s+['"]([^'"]+)['"]/g)].map(match => match[1]);
            const {createApp, h, ref} = await import(paths.find(path => /\\/vue\\.js(?:\\?|$)/.test(path)));
            const {ConfigProvider} = await import(paths.find(path => path.includes('ant-design-vue')));
            const {lightTheme} = await import('/src/shared/theme.ts');
            const {default: RefreshButton} = await import('/src/shared/components/RefreshButton.vue');
            const state = {loading: ref(false), disabled: ref(false), calls: 0};
            const host = document.createElement('div');
            host.style.cssText = 'position:absolute;left:280px;top:100px;z-index:3000';
            document.body.append(host);
            const app = createApp({render: () => h(ConfigProvider, {theme: lightTheme}, {
                default: () => h(RefreshButton, {label: '刷新测试数据',
                    loading: state.loading.value, disabled: state.disabled.value,
                    onClick: () => { state.calls += 1; state.loading.value = true; }})
            })});
            app.mount(host);
            window.refreshControlFixture = {state, app, host};
        }''')
        button = self.page.get_by_role('button', name='刷新测试数据', exact=True)
        await self.assert_refresh_appearance(button)
        await button.evaluate('(button) => {button.click(); button.click()}')
        await expect(button).to_be_disabled()
        await expect(button).to_have_attribute('aria-busy', 'true')
        self.assertEqual(await self.page.evaluate('refreshControlFixture.state.calls'), 1)
        loading_size = await button.bounding_box()
        self.assertEqual((loading_size['width'], loading_size['height']), (36, 36))
        await self.page.evaluate('refreshControlFixture.state.loading.value = false')
        await self.assert_refresh_appearance(button)
        await button.focus()
        await button.press('Enter')
        await expect(button).to_be_disabled()
        self.assertEqual(await self.page.evaluate('refreshControlFixture.state.calls'), 2)
        await self.page.evaluate('''() => {
            refreshControlFixture.state.loading.value = false;
            refreshControlFixture.state.disabled.value = true;
        }''')
        await expect(button).to_be_disabled()
        await button.evaluate('(button) => button.click()')
        self.assertEqual(await self.page.evaluate('refreshControlFixture.state.calls'), 2)
        await self.page.evaluate('''() => {
            refreshControlFixture.app.unmount(); refreshControlFixture.host.remove();
        }''')


def load_tests(loader, _tests, _pattern):
    return unittest.TestSuite(RefreshButtonRegressions(name)
                              for name in loader.getTestCaseNames(RefreshButtonRegressions)
                              if name in RefreshButtonRegressions.__dict__)


if __name__ == '__main__':
    unittest.main(verbosity=2)
