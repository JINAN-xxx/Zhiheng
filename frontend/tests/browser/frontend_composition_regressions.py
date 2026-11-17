"""Application composition reads only currently authorized resource previews."""
import asyncio
import unittest
from urllib.parse import urlparse

from playwright.async_api import expect
from console_regressions import ConsoleUIRegressions


def rights(*modules):
    return [f'{module}:{action}' for module in modules for action in ['enabled', 'read']]


class FrontendCompositionRegressions(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = ConsoleUIRegressions.asyncTearDown

    async def asyncSetUp(self):
        await ConsoleUIRegressions.asyncSetUp(self)
        self.user = {'id': 'u1', 'username': 'preview-reader', 'is_admin': False,
            'permissions': rights('platform:overview', 'platform:models', 'platform:agents', 'platform:knowledge', 'platform:skills', 'platform:developer', 'platform:usage', 'platform:chat', 'platform:work')}
        self.reads = []

    async def respond(self, route):
        path = urlparse(route.request.url).path
        self.reads.append(path)
        if path == '/api/auth/me':
            return await route.fulfill(json=self.user)
        return await ConsoleUIRegressions.respond(self, route)

    async def refresh_identity(self):
        await self.page.evaluate("async () => { const m = await import('/src/modules/identity/public.ts'); await m.restoreIdentity(true) }")

    async def test_existing_counts_grouping_and_links_remain(self):
        await self.page.goto(self.origin + '/overview')
        for label, value in [('模型', '1'), ('智能助手', '2'), ('知识库', '3'), ('技能', '4')]:
            group = self.page.get_by_role('region', name=label + '资源', exact=True)
            await expect(group.locator('.console-resource-count')).to_have_text(value)
            await expect(group.get_by_role('link', name='查看全部' + label, exact=True)).to_be_visible()
        navigation = self.page.get_by_role('navigation', name='主导航', exact=True)
        for label in ['工作台', '对话体验', '模型目录', '智能助手', '技能', '工作空间', '知识库', '开发接入', '用量与调用']:
            await expect(navigation.get_by_role('link', name=label, exact=True)).to_be_visible()
        await expect(navigation.get_by_role('link', name='凭据管理', exact=True)).to_have_count(0)
        self.assertEqual(self.writes, [])
        self.assertEqual(self.errors, [])
        self.assertEqual(self.unexpected, [])

    async def test_revocation_clears_already_visible_resource_names_and_stops_new_reads(self):
        await self.page.goto(self.origin + '/overview')
        knowledge = self.page.get_by_role('region', name='知识库资源', exact=True)
        await expect(knowledge.get_by_role('link', name='核对知识库1', exact=True)).to_be_visible()
        self.user['permissions'].remove('platform:knowledge:enabled')
        before = self.reads.count('/api/knowledge/bases')
        await self.refresh_identity()
        await expect(knowledge).to_have_count(0)
        await expect(self.page.get_by_role('link', name='核对知识库1', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='刷新工作台', exact=True).click()
        await expect(self.page.locator('.console-resource-panel')).to_have_attribute('aria-busy', 'false')
        self.assertEqual(self.reads.count('/api/knowledge/bases'), before)
        await expect(self.page.get_by_role('region', name='模型资源', exact=True)).to_be_visible()

    async def test_late_old_resource_read_cannot_restore_a_revoked_section(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def delay(route):
            started.set()
            await release.wait()
            return await ConsoleUIRegressions.respond(self, route)
        await self.page.route(self.origin + '/api/knowledge/bases', delay)
        await self.page.goto(self.origin + '/overview')
        await asyncio.wait_for(started.wait(), 5)
        self.user['permissions'].remove('platform:knowledge:enabled')
        await self.refresh_identity()
        await expect(self.page.get_by_role('region', name='模型资源', exact=True)).to_be_visible()
        release.set()
        await expect(self.page.get_by_role('region', name='知识库资源', exact=True)).to_have_count(0)
        self.assertEqual(self.errors, [])

    async def test_partial_failure_keeps_unavailable_count_separate_from_zero(self):
        self.fail_knowledge = True
        await self.page.goto(self.origin + '/overview')
        knowledge = self.page.get_by_role('region', name='知识库资源', exact=True)
        await expect(knowledge.locator('.console-resource-count')).to_have_text('暂不可用')
        await expect(knowledge.get_by_role('alert')).to_contain_text('知识服务暂不可用')
        await expect(self.page.get_by_role('region', name='智能助手资源', exact=True).locator('.console-resource-count')).to_have_text('2')
        self.assertEqual(self.errors, [])

    async def test_navigation_group_preference_and_mobile_links_remain_accessible(self):
        await self.page.goto(self.origin + '/overview')
        nav = self.page.get_by_role('navigation', name='主导航', exact=True)
        resources = nav.get_by_role('button', name='资源', exact=True)
        await resources.click()
        await expect(resources).to_have_attribute('aria-expanded', 'false')
        await self.page.reload()
        await expect(resources).to_have_attribute('aria-expanded', 'false')
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        await self.page.get_by_role('button', name='打开主菜单', exact=True).click()
        drawer = self.page.locator('.platform-mobile-drawer')
        await drawer.get_by_role('button', name='资源', exact=True).click()
        await expect(drawer.get_by_role('link', name='知识库', exact=True)).to_be_visible()
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
