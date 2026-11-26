"""Market and personal skill behavior through the actual Vue UI."""
import asyncio
import contextlib
import re
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
        self.published = {**self.personal, 'id': 'published-1', 'slug': 'platform.published-1',
            'name': '深度研究', 'description': '整理研究目标、资料依据与结论', 'source': 'platform'}
        self.items = [self.published, self.personal]
        self.member_permissions = None
        self.reads, self.uploads = [], []
        self.publication_requests = []
        self.confirms = 0

    async def respond(self, route):
        request = route.request
        path = urlparse(request.url).path
        if path == '/api/auth/me' and self.member_permissions is not None:
            await route.fulfill(json={
                'id': 'user-1', 'username': 'member', 'is_admin': False, 'permissions': self.member_permissions,
            })
        elif path == '/api/skills':
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
            self.items.append(self.personal)
            await route.fulfill(status=201, json=self.personal)
        elif path == '/api/skills/personal-1/platform':
            self.publication_requests.append((request.method, request.post_data_json))
            self.items = [
                item for item in self.items
                if (item['source'], item['id']) != ('platform', self.personal['id'])
            ]
            self.personal['version'] += 1
            if request.method == 'PUT':
                revision = self.personal['revision_id']
                self.personal['platform_revision_id'] = revision
                self.items.append({**self.personal, 'source': 'platform', 'slug': 'platform.personal-1'})
                await route.fulfill(json=self.personal)
            else:
                self.personal['platform_revision_id'] = None
                await route.fulfill(status=204)
        elif path.startswith('/api/skills/platform/'):
            identifier = path.rsplit('/', 1)[1]
            published = next(item for item in self.items if item['source'] == 'platform' and item['id'] == identifier)
            await route.fulfill(json={
                'skill': published,
                'content': '# 已发布的操作说明\n\n整理资料并验证结论。\n\n## 交付检查\n\n逐项核对引用。',
            })
        elif path.startswith('/api/skills/') and path.endswith('/content'):
            await route.fulfill(json={'content': '# 技能说明\n\n按目标梳理资料，保留事实与行动事项。'})
        elif path.startswith('/api/skills/'):
            item = self.published if 'published-1' in path else self.personal
            if path.endswith('/status'):
                item.update(status=request.post_data_json['status'], version=item['version']+1)
            await route.fulfill(json=item)
        else:
            await super().respond(route)

    async def test_market_is_default_and_published_skills_only_offer_withdrawal(self):
        await self.page.goto(self.origin + '/skills')
        await expect(self.page.get_by_role('link', name='技能', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('heading', name='技能', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('tab', name='平台技能', exact=True)).to_have_attribute('aria-selected', 'true')
        await expect(self.page.locator('.skill-item')).to_have_count(1)
        await expect(self.page.locator('.skill-item')).to_contain_text('深度研究')
        await expect(self.page.get_by_role('button', name='上传技能', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='深度研究', exact=True).click()
        dialog = self.page.locator('.skill-detail-modal[role="dialog"]')
        await expect(dialog.get_by_role('heading', name='已发布的操作说明', exact=True)).to_be_visible()
        await expect(dialog.get_by_role('region', name='技能内容')).to_contain_text('逐项核对引用。')
        await expect(dialog.get_by_role('button', name='下架平台技能', exact=True)).to_be_visible()
        await expect(dialog.get_by_role('button', name='更多技能操作')).to_have_count(0)
        await expect(dialog.get_by_role('switch')).to_have_count(0)
        self.assertEqual(self.reads[0], 'platform')

    async def test_my_tab_upload_installs_only_in_my_list(self):
        self.items = [self.published]
        await self.page.goto(self.origin + '/skills')
        await self.page.get_by_role('tab', name='我的技能', exact=True).click()
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
        await self.page.get_by_role('tab', name='平台技能', exact=True).click()
        await expect(self.page.locator('.skill-item')).to_contain_text('深度研究')
        await expect(self.page.locator('.skill-item')).not_to_contain_text('团队笔记')

    async def test_skill_card_publication_and_withdrawal_with_newer_draft(self):
        self.items = [self.personal]
        await self.page.goto(self.origin + '/skills')
        await expect(self.page.get_by_text('暂无平台技能', exact=True)).to_be_visible()
        await self.page.goto(self.origin + '/skills?tab=mine')
        market = self.page.get_by_role('tab', name='平台技能', exact=True)
        mine = self.page.get_by_role('tab', name='我的技能', exact=True)
        card = self.page.locator('.skill-item').filter(has_text='团队笔记')
        actions = card.get_by_role('button', name='更多操作', exact=True)
        confirmation = self.page.locator('.ant-modal-confirm')
        confirm = confirmation.get_by_role('button', name=re.compile(r'^确\s*定$'))

        await actions.click()
        await self.page.get_by_role('menuitem', name='上架平台技能', exact=True).click()
        await expect(confirmation).to_contain_text('确认上架平台技能「团队笔记」？')
        await confirm.click()
        await expect(card).to_contain_text('已上架')
        await market.click()
        await actions.click()
        await expect(self.page.get_by_role('menuitem', name='下架平台技能', exact=True)).to_be_visible()

        self.personal.update(revision_id='revision-2', version=3)
        await mine.click()
        await actions.click()
        await expect(self.page.get_by_role('menuitem', name='更新上架版本', exact=True)).to_be_visible()
        await self.page.get_by_role('menuitem', name='下架平台技能', exact=True).click()
        await expect(confirmation).to_contain_text('确认下架平台技能「团队笔记」？')
        await confirm.click()
        await expect(card).not_to_contain_text('已上架')
        self.assertEqual(self.publication_requests, [
            ('PUT', {'expected_version': 1}),
            ('DELETE', {'expected_revision_id': 'revision-1'}),
        ])
        await market.click()
        await expect(self.page.get_by_text('暂无平台技能', exact=True)).to_be_visible()
        await expect(card).to_have_count(0)

    async def test_multifile_skill_still_shows_publication_entry_and_reason(self):
        self.personal['file_count'] = 2
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        await self.page.goto(self.origin + '/skills?tab=mine')
        await self.page.get_by_role('button', name='更多操作', exact=True).click()
        publish = self.page.get_by_role('menuitem', name=re.compile('^上架平台技能'))
        await expect(publish).to_be_visible()
        await expect(publish).to_be_disabled()
        await expect(publish).to_contain_text('技能包须仅包含 SKILL.md')
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        await self.page.get_by_role('button', name='更多操作', exact=True).click()

        await self.page.get_by_role('button', name='团队笔记', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        await expect(dialog.get_by_role('button', name='上架平台技能', exact=True)).to_be_disabled()
        await expect(dialog.get_by_text('技能包须仅包含 SKILL.md', exact=True)).to_be_visible()
        self.assertEqual(self.publication_requests, [])

    async def test_role_publish_controls_are_available_only_on_personal_cards(self):
        self.member_permissions = ['platform:skills:enabled', 'platform:skills:read', 'platform:skills:use']
        await self.page.goto(self.origin + '/skills?tab=mine')
        card = self.page.locator('.skill-item').filter(has_text='团队笔记')
        actions = card.get_by_role('button', name='更多操作', exact=True)
        await expect(card).to_be_visible()
        await expect(actions).to_have_count(0)

        self.member_permissions.append('platform:skills:platform_publish')
        await self.page.reload()
        await actions.click()
        await self.page.get_by_role('menuitem', name='上架平台技能', exact=True).click()
        await self.page.locator('.ant-modal-confirm').get_by_role('button', name=re.compile(r'^确\s*定$')).click()
        await expect(card).to_contain_text('已上架')
        await actions.click()
        await expect(self.page.get_by_role('menuitem', name='下架平台技能', exact=True)).to_be_visible()

        await self.page.get_by_role('tab', name='平台技能', exact=True).click()
        await expect(self.page.get_by_role('button', name='深度研究', exact=True)).to_be_visible()
        await expect(self.page.locator('.skill-item').get_by_role('button', name='更多操作', exact=True)).to_have_count(0)

    async def test_late_market_refresh_cannot_replace_my_tab(self):
        started, release = asyncio.Event(), asyncio.Event()
        await self.page.goto(self.origin + '/skills')
        await expect(self.page.locator('.skill-item')).to_contain_text('深度研究')
        async def held(route):
            if parse_qs(urlparse(route.request.url).query).get('source') == ['platform']:
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
            await self.page.get_by_role('tab', name='我的技能', exact=True).click()
            await expect(self.page.locator('.skill-item')).to_contain_text('团队笔记')
            release.set()
            await self.page.wait_for_timeout(100)
            await expect(self.page.get_by_role('tab', name='我的技能', exact=True)).to_have_attribute('aria-selected', 'true')
            await expect(self.page.locator('.skill-item')).not_to_contain_text('深度研究')
        finally:
            release.set()

    async def test_upload_shortcut_selects_my_tab(self):
        await self.page.goto(self.origin + '/skills?action=upload')
        await expect(self.page.get_by_role('tab', name='我的技能', exact=True)).to_have_attribute('aria-selected', 'true')
        await expect(self.page.get_by_role('dialog', name='上传技能', exact=True)).to_be_visible()
        await expect(self.page).to_have_url(self.origin + '/skills?tab=mine')

    async def test_tabs_and_upload_fit_mobile(self):
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        await self.page.goto(self.origin + '/skills')
        await expect(self.page.locator('.skill-item')).to_contain_text('深度研究')
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        await self.page.get_by_role('tab', name='我的技能', exact=True).click()
        await expect(self.page.locator('.skill-item')).to_contain_text('团队笔记')
        await expect(self.page.get_by_role('button', name='上传技能', exact=True)).to_be_visible()
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
