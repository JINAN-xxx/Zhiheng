"""Gray entries and stale-content clearing against controlled HTTP fixtures."""
import os
import unittest
from pathlib import Path
from urllib.parse import urlparse

from knowledge_regressions import KnowledgeUIRegressions
from playwright.async_api import expect


class KnowledgeOwnerLifecycle(KnowledgeUIRegressions):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.unavailable = False
        self.gray = [
            {'id': 'disabled-base', 'name': '团队资料', 'owner_id': 'disabled-user', 'owner_name': '同事甲', 'availability': 'owner_disabled'},
            {'id': 'deleted-base', 'name': '历史项目资料', 'owner_id': 'deleted-user', 'owner_name': '同事乙', 'availability': 'owner_deleted'},
        ]

    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path == '/api/knowledge/bases':
            return await route.fulfill(json=self.gray if self.unavailable else [self.base, *self.gray])
        if self.unavailable and path.startswith('/api/knowledge/bases/base-1'):
            return await route.fulfill(status=404, json={'error': {'code': 'knowledge_owner_unavailable', 'message': '创建者已停用', 'details': {'availability': 'owner_disabled'}}})
        return await super().respond(route)

    async def test_minimal_gray_entries_have_no_open_or_management_actions(self):
        await self.page.goto(self.origin + '/knowledge')
        await expect(self.page.get_by_role('button', name='打开知识库：业务知识', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='与我共享', exact=True).click()
        entries = self.page.locator('.knowledge-entry--unavailable')
        await expect(entries).to_have_count(2)
        await expect(self.page.get_by_text('创建者已停用', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('创建者已删除', exact=True)).to_be_visible()
        await expect(entries.locator('button,a')).to_have_count(0)
        await expect(entries.locator('[aria-disabled="true"]')).to_have_count(2)
        self.assertEqual(await entries.first.evaluate('(node) => getComputedStyle(node).opacity'), '0.65')
        self.assertNotIn('份资料', await entries.inner_text() if await entries.count() == 1 else await entries.first.inner_text())
        directory = os.environ.get('KNOWLEDGE_BATCH1_EVIDENCE')
        if directory:
            target=Path(directory); target.mkdir(parents=True, exist_ok=True)
            await self.page.screenshot(path=str(target/'shared-unavailable-desktop.png'), full_page=True)
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        self.assertLessEqual(await self.page.evaluate('document.documentElement.scrollWidth'), 390)
        if directory:
            await self.page.screenshot(path=str(Path(directory)/'shared-unavailable-mobile.png'), full_page=True)

    async def test_returning_to_open_library_clears_preview_and_keeps_gray_entry(self):
        self.base.update(owner_id='disabled-user', owner_name='同事甲', can_manage=False)
        await self.page.goto(self.origin + '/knowledge/base-1/documents')
        await self.page.get_by_role('button', name='业务说明.txt', exact=True).click()
        await expect(self.page.get_by_text('业务工作时间是九点到十七点。', exact=True)).to_be_visible()
        self.gray = [{k: self.base[k] for k in ('id', 'name', 'owner_id', 'owner_name')} | {'availability': 'owner_disabled'}]
        self.unavailable = True
        await self.page.evaluate("window.dispatchEvent(new Event('focus'))")
        await expect(self.page.get_by_text('业务工作时间是九点到十七点。', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button', name='业务说明.txt', exact=True)).to_have_count(0)
        await expect(self.page.get_by_text('创建者已停用', exact=True)).to_be_visible()
        await self.page.goto(self.origin + '/knowledge')
        await self.page.get_by_role('button', name='与我共享', exact=True).click()
        await expect(self.page.locator('.knowledge-entry--unavailable')).to_have_count(1)
        await expect(self.page.get_by_role('button', name='打开知识库：业务知识', exact=True)).to_have_count(0)

    async def test_direct_link_is_denied_without_document_controls(self):
        self.unavailable = True
        await self.page.goto(self.origin + '/knowledge/base-1/documents')
        await expect(self.page.get_by_text('创建者已停用', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='添加资料', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button', name='业务说明.txt', exact=True)).to_have_count(0)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(loader.loadTestsFromName('KnowledgeOwnerLifecycle.'+name, module=__import__(__name__))
        for name in KnowledgeOwnerLifecycle.__dict__ if name.startswith('test_'))
