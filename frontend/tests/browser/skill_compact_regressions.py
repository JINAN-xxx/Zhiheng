"""Compact personal skills share one state owner across list and modal."""

import asyncio
import contextlib
import re
import unittest
from urllib.parse import parse_qs, urlparse

from developer_regressions import DeveloperBoundary
from playwright.async_api import expect


class CompactSkills(DeveloperBoundary):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.skill = {'id': 'skill-1', 'slug': 'example', 'name': '示例技能', 'description': '将文档内容整理为清晰的总结', 'package_version': '8.7.6', 'version': 1, 'status': 'enabled', 'source': 'upload', 'revision_id': 'revision-1', 'content_hash': 'hash-1', 'file_count': 3, 'total_bytes': 5120}
        self.items = [self.skill, {**self.skill, 'id': 'skill-2', 'slug': 'second', 'name': '第二技能', 'status': 'disabled'}]
        self.list_reads = 0
        self.updates = []
        self.content = '---\nname: example\nversion: 8.7.6\n---\n# 技能说明\n\n- 保留完整说明\n- 安全显示内容\n\n<script>window.skillExecuted=true</script>\n\n[危险链接](javascript:alert(1))'

    async def respond(self, route):
        path, method = urlparse(route.request.url).path, route.request.method
        if path == '/api/skills':
            self.list_reads += 1
            query = parse_qs(urlparse(route.request.url).query)
            status = query.get('status', [''])[0]
            keyword = query.get('search', [''])[0]
            items = [s for s in self.items if (not status or s['status'] == status) and (not keyword or keyword in s['name'])]
            page = int(query.get('page', ['1'])[0])
            size = int(query.get('page_size', ['24'])[0])
            await route.fulfill(json={'items': items[(page - 1) * size:page * size], 'total': len(items), 'page': page, 'page_size': size})
        elif re.fullmatch(r'/api/skills/skill-\d+/status', path):
            body = route.request.post_data_json
            self.updates.append(body)
            skill = next(s for s in self.items if s['id'] == path.split('/')[3])
            self.assertEqual(body['expected_version'], skill['version'])
            skill.update(status=body['status'], version=skill['version'] + 1)
            await route.fulfill(json=skill)
        elif re.fullmatch(r'/api/skills/skill-\d+/content', path):
            await route.fulfill(json={'content': self.content})
        elif re.fullmatch(r'/api/skills/skill-\d+', path):
            skill = next(s for s in self.items if s['id'] == path.split('/')[3])
            if method == 'DELETE':
                self.items = [s for s in self.items if s['id'] != skill['id']]
            await route.fulfill(json=skill)
        else:
            await super().respond(route)

    async def open_skills(self):
        await self.page.goto(self.origin + '/skills?tab=mine')
        await expect(self.page.locator('.skill-item')).to_have_count(min(24, len(self.items)), timeout=10000)

    def row(self, name='示例技能'):
        return self.page.locator('.skill-item').filter(has=self.page.get_by_role('button', name=name, exact=True))

    async def detail(self, name='示例技能'):
        await self.row(name).get_by_role('button', name=name, exact=True).click()
        dialog = self.page.locator('.skill-detail-modal[role="dialog"]')
        await expect(dialog.get_by_role('switch')).to_be_visible()
        return dialog

    async def test_compact_desktop_layout_and_safe_centered_markdown(self):
        await self.open_skills()
        await expect(self.page.locator('.skill-card')).to_have_count(0)
        await expect(self.page.locator('.skill-grid')).not_to_contain_text('8.7.6')
        await expect(self.page.locator('.skill-grid')).not_to_contain_text('KB')
        for width, columns in [(1440, 3), (1920, 4)]:
            await self.page.set_viewport_size({'width': width, 'height': 1000})
            actual = await self.page.locator('.skill-grid').evaluate('(el) => getComputedStyle(el).gridTemplateColumns.split(" ").length')
            self.assertEqual(actual, columns)
            bounds = await self.row().bounding_box()
            self.assertLess(bounds['height'], 130)
        dialog = await self.detail()
        await expect(dialog.locator('.skill-markdown h1')).to_have_text('技能说明')
        await expect(dialog.locator('.skill-markdown li')).to_have_count(2)
        await expect(dialog.locator('.skill-markdown')).not_to_contain_text('8.7.6')
        self.assertIsNone(await self.page.evaluate('window.skillExecuted'))
        await expect(dialog.locator('script, a[href^="javascript:"]')).to_have_count(0)
        await expect(dialog.locator('.ant-modal')).to_have_css('transform', 'none')
        bounds = await dialog.locator('.ant-modal').bounding_box()
        self.assertLess(abs(bounds['x'] + bounds['width'] / 2 - 960), 20)
        self.assertLessEqual(bounds['width'], 720)

    async def test_switch_updates_only_one_item_and_detail_uses_current_version(self):
        await self.open_skills()
        reads = self.list_reads
        await self.row().get_by_role('switch').click()
        await expect(self.row().get_by_role('switch')).not_to_be_checked()
        self.assertEqual(self.updates, [{'status': 'disabled', 'expected_version': 1}])
        self.assertEqual(self.list_reads, reads)
        await expect(self.page.get_by_role('dialog')).to_have_count(0)
        dialog = await self.detail()
        await dialog.get_by_role('switch').click()
        await expect(dialog.get_by_role('switch')).to_be_checked()
        await expect(self.row().get_by_role('switch')).to_be_checked()
        self.assertEqual(self.updates[-1], {'status': 'enabled', 'expected_version': 2})
        self.assertEqual(self.list_reads, reads)

    async def test_failed_switch_keeps_state_and_does_not_reload_list(self):
        async def reject(route):
            self.updates.append(route.request.post_data_json)
            await route.fulfill(status=409, json={'error': {'code': 'skill_conflict', 'message': '技能已变化，请刷新后重试'}})
        await self.page.route('**/api/skills/skill-1/status', reject)
        await self.open_skills()
        reads = self.list_reads
        await self.row().get_by_role('switch').click()
        await expect(self.row()).to_contain_text('技能已变化，请刷新后重试')
        await expect(self.row().get_by_role('switch')).to_be_checked()
        self.assertEqual(len(self.updates), 1)
        self.assertEqual(self.list_reads, reads)

    async def test_repeated_switch_uses_one_request_and_preserves_other_items(self):
        requested, release = asyncio.Event(), asyncio.Event()
        async def delayed(route):
            requested.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await self.respond(route)
        await self.page.route('**/api/skills/skill-1/status', delayed)
        try:
            await self.open_skills()
            await self.row().get_by_role('switch').evaluate('(el) => {el.click();el.click()}')
            await asyncio.wait_for(requested.wait(), timeout=10)
            await expect(self.row().get_by_role('switch')).to_be_disabled()
            await expect(self.row('第二技能').get_by_role('switch')).to_be_enabled()
            release.set()
            await expect(self.row().get_by_role('switch')).not_to_be_checked()
            self.assertEqual(len(self.updates), 1)
        finally:
            release.set()

    async def test_stale_detail_cannot_revert_completed_switch(self):
        requested, release = asyncio.Event(), asyncio.Event()
        stale = dict(self.skill)
        async def delayed(route):
            requested.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await route.fulfill(json=stale)
        await self.page.route('**/api/skills/skill-1', delayed)
        status_requested, status_release = asyncio.Event(), asyncio.Event()
        async def status(route):
            status_requested.set()
            await status_release.wait()
            with contextlib.suppress(Exception):
                await self.respond(route)
        await self.page.route('**/api/skills/skill-1/status', status)
        try:
            await self.open_skills()
            await self.row().get_by_role('switch').click()
            await asyncio.wait_for(status_requested.wait(), timeout=10)
            await self.row().get_by_role('button', name='示例技能', exact=True).click()
            await asyncio.wait_for(requested.wait(), timeout=10)
            status_release.set()
            await expect(self.row().get_by_role('switch')).not_to_be_checked()
            release.set()
            dialog = self.page.locator('.skill-detail-modal[role="dialog"]')
            await expect(dialog.get_by_role('switch')).not_to_be_checked()
            self.assertEqual(self.updates, [{'status': 'disabled', 'expected_version': 1}])
        finally:
            release.set()
            status_release.set()

    async def test_navigation_discards_late_status_and_modal(self):
        requested, release = asyncio.Event(), asyncio.Event()
        async def delayed(route):
            requested.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await self.respond(route)
        await self.page.route('**/api/skills/skill-1/status', delayed)
        try:
            await self.page.goto(self.origin + '/models')
            await self.page.get_by_role('link', name='技能', exact=True).click()
            await self.page.get_by_role('tab', name='我的', exact=True).click()
            await expect(self.page.locator('.skill-item')).to_have_count(2)
            await self.row().get_by_role('switch').click()
            await asyncio.wait_for(requested.wait(), timeout=10)
            await self.page.go_back()
            await expect(self.page.get_by_role('heading', name='模型目录', exact=True)).to_be_visible()
            release.set()
            await self.page.wait_for_timeout(150)
            await expect(self.page.get_by_role('dialog')).to_have_count(0)
            await expect(self.page.locator('.skill-item')).to_have_count(0)
        finally:
            release.set()

    async def test_filtered_status_update_adjusts_count_without_page_refresh(self):
        await self.open_skills()
        await self.page.locator('.skill-controls .ant-select-selector').click()
        await self.page.locator('.ant-select-dropdown').get_by_text('已启用', exact=True).click()
        await expect(self.page.locator('.skill-item')).to_have_count(1)
        await expect(self.page.locator('.skill-total')).to_have_text('共 1 个')
        reads = self.list_reads
        await self.row().get_by_role('switch').click()
        await expect(self.page.locator('.skill-item')).to_have_count(0)
        await expect(self.page.locator('.skill-total')).to_have_text('共 0 个')
        await expect(self.page.get_by_text('没有符合条件的技能', exact=True)).to_be_visible()
        self.assertEqual(self.list_reads, reads)

    async def test_late_list_read_cannot_restore_a_stale_switch(self):
        await self.open_skills()
        requested, release = asyncio.Event(), asyncio.Event()
        stale = [dict(s) for s in self.items]
        calls = 0
        async def delayed(route):
            nonlocal calls
            calls += 1
            if calls == 1:
                requested.set()
                await release.wait()
                with contextlib.suppress(Exception):
                    await route.fulfill(json={'items': stale, 'total': 2, 'page': 1, 'page_size': 24})
            else:
                await self.respond(route)
        await self.page.route('**/api/skills?*', delayed)
        try:
            await self.page.get_by_role('button', name='刷新技能', exact=True).click()
            await asyncio.wait_for(requested.wait(), timeout=10)
            await self.row().get_by_role('switch').click()
            await expect(self.row().get_by_role('switch')).not_to_be_checked()
            release.set()
            await expect(self.page.get_by_role('button', name='刷新技能', exact=True)).not_to_have_class(re.compile('ant-btn-loading'))
            await self.row().get_by_role('switch').click()
            await expect(self.row().get_by_role('switch')).to_be_checked()
            self.assertEqual(self.updates[-1], {'status': 'enabled', 'expected_version': 2})
        finally:
            release.set()

    async def test_filtering_out_last_item_on_last_page_returns_to_existing_items(self):
        self.items = [{**self.skill, 'id': f'skill-{i}', 'slug': f'example-{i}', 'name': f'技能{i}'} for i in range(1, 26)]
        await self.open_skills()
        await self.page.locator('.skill-controls .ant-select-selector').click()
        await self.page.locator('.ant-select-dropdown').get_by_text('已启用', exact=True).click()
        await self.page.locator('.ant-pagination-item-2').click()
        await expect(self.page.locator('.skill-item')).to_have_count(1)
        await self.row('技能25').get_by_role('switch').click()
        await expect(self.page.locator('.skill-item')).to_have_count(24)
        await expect(self.page.locator('.skill-total')).to_have_text('共 24 个')
        await expect(self.page.get_by_text('没有符合条件的技能', exact=True)).to_have_count(0)

    async def test_long_name_and_description_keep_document_and_actions_in_view(self):
        self.skill['name'] = '长名称' * 33 + '字'
        self.skill['description'] = '长说明' * 166 + '文字'
        await self.page.set_viewport_size({'width': 1440, 'height': 720})
        await self.open_skills()
        dialog = await self.detail(self.skill['name'])
        await expect(dialog.locator('.ant-modal')).to_have_css('transform', 'none')
        bounds = await dialog.locator('.ant-modal-content').bounding_box()
        self.assertGreaterEqual(bounds['y'], 0)
        self.assertLessEqual(bounds['y'] + bounds['height'], 720)
        body = await dialog.locator('.skill-markdown').bounding_box()
        self.assertGreaterEqual(body['height'], 180)
        await expect(dialog.get_by_role('button', name='更多技能操作', exact=True)).to_be_in_viewport()

    async def test_replace_from_detail_opens_targeted_upload_without_versions(self):
        await self.open_skills()
        dialog = await self.detail()
        await dialog.get_by_role('button', name='更多技能操作', exact=True).click()
        await self.page.get_by_role('menuitem', name='替换技能', exact=True).click()
        upload = self.page.get_by_role('dialog', name='替换技能', exact=True)
        await expect(upload).to_be_visible()
        await expect(self.page.locator('.skill-detail-modal')).to_have_count(0)
        await expect(upload).to_contain_text('示例技能')
        await expect(upload.get_by_role('textbox')).to_have_count(0)


if __name__ == '__main__':
    unittest.main()
