"""Market and personal skill behavior through the actual Vue UI."""
import asyncio
import contextlib
import unittest
from urllib.parse import parse_qs, urlparse

from playwright.async_api import expect
from developer_regressions import DeveloperBoundary


class SkillMarketRegressions(DeveloperBoundary):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.personal = {'id': 'personal-1', 'slug': 'team-notes', 'name': '团队笔记',
            'description': '整理团队资料和行动事项', 'package_version': '1.0.0', 'version': 1,
            'status': 'enabled', 'source': 'upload', 'revision_id': 'revision-1',
            'content_hash': 'hash-1', 'file_count': 1, 'total_bytes': 120}
        self.preset = {**self.personal, 'id': 'preset-1', 'slug': 'deep-research',
            'name': '深度研究', 'description': '整理研究目标、资料依据与结论', 'source': 'builtin'}
        self.items = [self.preset, self.personal]
        self.reads, self.uploads, self.deletes = [], [], []
        self.confirms = 0

    async def respond(self, route):
        request = route.request
        path = urlparse(request.url).path
        if path == '/api/skills':
            query = parse_qs(urlparse(request.url).query)
            source = query.get('source', [''])[0]
            self.reads.append(source)
            rows = [item for item in self.items if not source or item['source'] == source]
            await route.fulfill(json={'items': rows, 'total': len(rows), 'page': 1, 'page_size': 24})
        elif path == '/api/skills/import/prepare':
            self.uploads.append(request.post_data_buffer)
            await route.fulfill(status=201, json={**self.personal, 'draft_id': 'draft-1',
                'expires_at': '2099-01-01T00:00:00Z', 'action': 'install',
                'target_skill_id': None, 'expected_version': None, 'current_package_version': None})
        elif path == '/api/skills/import/draft-1/confirm':
            self.confirms += 1
            self.items = [self.preset, self.personal]
            await route.fulfill(status=201, json=self.personal)
        elif path.startswith('/api/skills/') and path.endswith('/content'):
            await route.fulfill(json={'content': '# 技能说明\n\n按目标梳理资料，保留事实与行动事项。'})
        elif path.startswith('/api/skills/'):
            item = self.preset if 'preset-1' in path else self.personal
            if request.method == 'DELETE':
                self.deletes.append(item['id'])
            elif path.endswith('/status'):
                item.update(status=request.post_data_json['status'], version=item['version']+1)
            await route.fulfill(json=item)
        else:
            await super().respond(route)

    async def test_market_is_default_and_presets_have_no_delete_or_replace_actions(self):
        await self.page.goto(self.origin + '/skills')
        await expect(self.page.get_by_role('link', name='技能', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('heading', name='技能', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('tab', name='市场', exact=True)).to_have_attribute('aria-selected', 'true')
        await expect(self.page.locator('.skill-item')).to_have_count(1)
        await expect(self.page.locator('.skill-item')).to_contain_text('深度研究')
        await expect(self.page.get_by_role('button', name='上传技能', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='深度研究', exact=True).click()
        dialog = self.page.locator('.skill-detail-modal[role="dialog"]')
        await expect(dialog.locator('.skill-markdown')).to_contain_text('技能说明')
        await expect(dialog.get_by_role('button', name='更多技能操作')).to_have_count(0)
        await dialog.get_by_role('switch').click()
        await expect(dialog.get_by_role('switch')).not_to_be_checked()
        self.assertEqual(self.deletes, [])
        self.assertEqual(self.reads[0], 'builtin')

    async def test_my_tab_upload_installs_only_in_my_list(self):
        self.items = [self.preset]
        await self.page.goto(self.origin + '/skills')
        await self.page.get_by_role('tab', name='我的', exact=True).click()
        await expect(self.page.get_by_text('暂无个人技能', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='上传技能', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        await dialog.locator('input[type=file]').set_input_files({
            'name': 'team-notes.zip', 'mimeType': 'application/zip', 'buffer': b'fixture package bytes'})
        await dialog.get_by_role('button', name='解析技能', exact=True).click()
        await expect(dialog.get_by_text('团队笔记', exact=True)).to_be_visible()
        await dialog.get_by_role('button', name='确认安装', exact=True).click()
        await expect(dialog).not_to_be_visible()
        await expect(self.page.locator('.skill-item')).to_contain_text('团队笔记')
        self.assertEqual(self.uploads, [b'fixture package bytes'])
        self.assertEqual(self.confirms, 1)
        await self.page.get_by_role('tab', name='市场', exact=True).click()
        await expect(self.page.locator('.skill-item')).to_contain_text('深度研究')
        await expect(self.page.locator('.skill-item')).not_to_contain_text('团队笔记')

    async def test_late_market_refresh_cannot_replace_my_tab(self):
        started, release = asyncio.Event(), asyncio.Event()
        await self.page.goto(self.origin + '/skills')
        await expect(self.page.locator('.skill-item')).to_contain_text('深度研究')
        async def held(route):
            if parse_qs(urlparse(route.request.url).query).get('source') == ['builtin']:
                started.set()
                await release.wait()
                with contextlib.suppress(Exception):
                    await self.respond(route)
            else:
                await self.respond(route)
        await self.page.route(self.origin + '/api/skills?*', held)
        try:
            await self.page.get_by_role('button', name='刷新技能', exact=True).click()
            await asyncio.wait_for(started.wait(), timeout=5)
            await self.page.get_by_role('tab', name='我的', exact=True).click()
            await expect(self.page.locator('.skill-item')).to_contain_text('团队笔记')
            release.set()
            await self.page.wait_for_timeout(100)
            await expect(self.page.get_by_role('tab', name='我的', exact=True)).to_have_attribute('aria-selected', 'true')
            await expect(self.page.locator('.skill-item')).not_to_contain_text('深度研究')
        finally:
            release.set()

    async def test_upload_shortcut_selects_my_tab(self):
        await self.page.goto(self.origin + '/skills?action=upload')
        await expect(self.page.get_by_role('tab', name='我的', exact=True)).to_have_attribute('aria-selected', 'true')
        await expect(self.page.get_by_role('dialog', name='上传技能', exact=True)).to_be_visible()
        await expect(self.page).to_have_url(self.origin + '/skills?tab=mine')

    async def test_tabs_and_upload_fit_mobile(self):
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        await self.page.goto(self.origin + '/skills')
        await expect(self.page.locator('.skill-item')).to_contain_text('深度研究')
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        await self.page.get_by_role('tab', name='我的', exact=True).click()
        await expect(self.page.locator('.skill-item')).to_contain_text('团队笔记')
        await expect(self.page.get_by_role('button', name='上传技能', exact=True)).to_be_visible()
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
