"""Isolated skill upload boundaries using the real Vue component and mocked HTTP."""

import asyncio
import contextlib
import unittest
from urllib.parse import urlparse

from developer_regressions import DeveloperBoundary
from playwright.async_api import expect


class SkillUploadModalRegressions(DeveloperBoundary):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.confirmations = 0
        self.skill = {
            'id': 'skill-1', 'slug': 'example', 'name': '示例技能',
            'description': '用于验收', 'package_version': '1.0.0', 'version': 1,
            'status': 'enabled', 'source': 'upload', 'revision_id': 'revision-1',
            'content_hash': 'hash-1', 'file_count': 1, 'total_bytes': 123,
        }

    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path == '/api/skills':
            await route.fulfill(json={'items': [], 'total': 0, 'page': 1, 'page_size': 12})
        elif path == '/api/skills/import/prepare':
            await route.fulfill(status=201, json=self.prepared())
        elif path == '/api/skills/import/draft-1/confirm':
            self.confirmations += 1
            await route.fulfill(status=201, json=self.skill)
        else:
            await super().respond(route)

    def prepared(self):
        return {
            **self.skill, 'draft_id': 'draft-1', 'expires_at': '2026-11-05T01:00:00Z',
            'action': 'update', 'target_skill_id': 'skill-1', 'expected_version': 1,
            'current_package_version': '0.9.0',
        }

    async def mount_upload(self, slug='example'):
        await self.page.goto(self.origin + '/skills')
        await expect(self.page.get_by_role('heading', name='我的技能', exact=True)).to_be_visible()
        await self.page.evaluate(r"""async (slug) => {
            const source = await (await fetch('/src/modules/skills/components/SkillUploadModal.vue')).text()
            const dependencies = [...source.matchAll(/from\s+["']([^"']+)["']/g)].map(match => match[1])
            const { createApp, h, reactive } = await import(dependencies.find(path => /\/vue\.js(?:\?|$)/.test(path)))
            const { default: SkillUploadModal } = await import('/src/modules/skills/components/SkillUploadModal.vue')
            const state = reactive({
                modelValue: true, canManage: true,
                target: { skillId: 'skill-1', skillSlug: slug, skillName: '示例技能' },
            })
            const host = document.createElement('div')
            document.body.append(host)
            const app = createApp({ render: () => h(SkillUploadModal, {
                ...state, 'onUpdate:modelValue': value => { state.modelValue = value },
            }) })
            app.mount(host)
            window.skillUploadHarness = { state }
        }""", slug)
        dialog = self.page.get_by_role('dialog')
        await expect(dialog).to_be_visible()
        return dialog

    async def select_and_prepare(self, dialog):
        await dialog.locator('input[type=file]').set_input_files({
            'name': 'SKILL.md', 'mimeType': 'text/markdown', 'buffer': b'fixture skill',
        })
        await dialog.get_by_role('button', name='解析技能', exact=True).click()

    async def test_target_import_uses_replace_wording(self):
        dialog = await self.mount_upload()
        await expect(dialog).to_have_accessible_name('替换技能')
        await self.select_and_prepare(dialog)
        await expect(dialog.get_by_text('替换现有内容', exact=True)).to_be_visible()
        await expect(dialog.get_by_role('button', name='确认替换', exact=True)).to_be_enabled()
        await expect(dialog).not_to_contain_text('版本')
        await expect(dialog).not_to_contain_text('历史')

    async def test_target_first_step_names_the_skill_and_confirms_replacement(self):
        dialog = await self.mount_upload()
        await expect(dialog.get_by_text('替换「示例技能」的内容', exact=True)).to_be_visible()
        await expect(dialog.locator('.ant-steps')).to_contain_text('确认替换')
        await expect(dialog).not_to_contain_text('确认安装')
        await expect(dialog).not_to_contain_text('example')
        await expect(dialog.get_by_role('button', name='解析技能', exact=True)).to_be_disabled()

    async def test_update_rejects_a_package_with_another_slug(self):
        dialog = await self.mount_upload('other-skill')
        await self.select_and_prepare(dialog)
        await expect(dialog.get_by_text('请选择对应技能包', exact=True)).to_be_visible()
        await expect(dialog.get_by_role('button', name='确认替换', exact=True)).to_be_disabled()
        self.assertEqual(self.confirmations, 0)

    async def test_target_change_discards_a_prepared_package(self):
        dialog = await self.mount_upload()
        await self.select_and_prepare(dialog)
        await expect(dialog.get_by_role('button', name='确认替换', exact=True)).to_be_enabled()
        await self.page.evaluate("window.skillUploadHarness.state.target = { skillId: 'skill-2', skillSlug: 'second', skillName: '第二技能' }")
        await expect(dialog.get_by_role('button', name='确认替换', exact=True)).to_have_count(0)
        await expect(dialog.get_by_role('button', name='解析技能', exact=True)).to_be_disabled()
        self.assertEqual(self.confirmations, 0)

    async def test_target_change_discards_a_late_preflight_response(self):
        requested, release = asyncio.Event(), asyncio.Event()

        async def delayed(route):
            requested.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await route.fulfill(status=201, json=self.prepared())

        await self.page.route('**/api/skills/import/prepare?*', delayed)
        try:
            dialog = await self.mount_upload()
            await self.select_and_prepare(dialog)
            await asyncio.wait_for(requested.wait(), timeout=5)
            await self.page.evaluate("window.skillUploadHarness.state.target = { skillId: 'skill-2', skillSlug: 'second', skillName: '第二技能' }")
            release.set()
            await self.page.wait_for_timeout(200)
            await expect(dialog.get_by_role('button', name='确认替换', exact=True)).to_have_count(0)
            await expect(dialog.get_by_role('button', name='解析技能', exact=True)).to_be_disabled()
            self.assertEqual(self.confirmations, 0)
        finally:
            release.set()


class SkillDetailModalRegressions(DeveloperBoundary):
    async def mount_detail(self, description='用于验收的提示词'):
        await self.page.goto(self.origin + '/models')
        await self.page.evaluate(r"""async (description) => {
            const source = await (await fetch('/src/modules/skills/components/SkillDetailModal.vue')).text()
            const dependencies = [...source.matchAll(/from\s+["']([^"']+)["']/g)].map(match => match[1])
            const { createApp, h } = await import(dependencies.find(path => /\/vue\.js(?:\?|$)/.test(path)))
            const { default: SkillDetailModal } = await import('/src/modules/skills/components/SkillDetailModal.vue')
            const host = document.createElement('div')
            document.body.append(host)
            const app = createApp({ render: () => h(SkillDetailModal, {
                modelValue: true, loading: false, saving: false, deleting: false,
                error: '', actionError: '', canManage: true,
                skill: { skillId: 'skill-1', skillSlug: 'example', skillName: '示例技能',
                    description, status: 'enabled', version: '1.0.0',
                    sourceType: 'upload', fileCount: 1, totalBytes: 123 },
                content: '---\nname: example\nversion: 1.0.0\n---\n# 技能正文\n\n' + '正文内容\n\n'.repeat(60),
            }) })
            app.mount(host)
        }""", description)
        detail = self.page.get_by_role('dialog').filter(
            has=self.page.get_by_role('heading', name='示例技能', exact=True)
        )
        await expect(detail).to_be_visible()
        await expect(detail.locator(':focus')).to_have_count(1)
        return detail

    async def test_detail_focuses_reading_area_with_actions_below(self):
        detail = await self.mount_detail()
        await expect(detail.locator('.detail-meta')).to_have_count(0)
        await expect(detail.get_by_text('个人上传', exact=True)).to_have_count(0)
        await expect(detail.get_by_text('SKILL.md', exact=True)).to_have_count(0)
        await expect(detail).not_to_contain_text('1.0.0')
        reading = detail.locator('.skill-markdown')
        await expect(reading.get_by_role('heading', name='技能正文', exact=True)).to_be_visible()
        scroll = detail.locator('.ant-modal-body')
        layout = await scroll.evaluate("el => ({ height: el.getBoundingClientRect().height, scrollHeight: el.scrollHeight, overflow: getComputedStyle(el).overflowY })")
        surface = await reading.evaluate("el => ({ background: getComputedStyle(el).backgroundColor, radius: getComputedStyle(el).borderRadius })")
        self.assertGreaterEqual(layout['height'], 420)
        self.assertLessEqual(layout['height'], 460)
        self.assertGreater(layout['scrollHeight'], layout['height'])
        self.assertEqual(layout['overflow'], 'auto')
        self.assertNotEqual(surface['background'], 'rgba(0, 0, 0, 0)')
        self.assertNotEqual(surface['radius'], '0px')
        icon = await detail.locator('.skill-identity-icon').bounding_box()
        title = await detail.get_by_role('heading', name='示例技能', exact=True).bounding_box()
        self.assertLessEqual(icon['y'] + icon['height'], title['y'])
        footer = detail.locator('.detail-footer')
        await footer.get_by_role('button', name='更多技能操作', exact=True).click()
        await expect(self.page.get_by_role('menuitem', name='替换技能', exact=True)).to_be_enabled()
        await expect(self.page.get_by_role('menuitem', name='删除技能', exact=True)).to_be_enabled()
        await expect(detail.get_by_role('button', name='在对话中使用', exact=True)).to_have_count(0)

    async def test_long_description_preserves_the_reading_area_and_full_title(self):
        description = ('这是一段用于验证技能说明在详情页中保持清晰紧凑阅读布局的长文本。' * 20)[:500]
        detail = await self.mount_detail(description)
        summary = detail.locator('.detail-description')
        await expect(summary).to_have_attribute('title', description)
        for height in [900, 720]:
            await self.page.set_viewport_size({'width': 1440, 'height': height})
            layout = await summary.evaluate("el => ({ height: el.getBoundingClientRect().height, lineHeight: parseFloat(getComputedStyle(el).lineHeight) })")
            self.assertLessEqual(layout['height'], layout['lineHeight'] * 3 + 1)
            reading = await detail.locator('.skill-markdown').bounding_box()
            modal = await detail.locator('.ant-modal').bounding_box()
            self.assertGreaterEqual(reading['height'], 400)
            self.assertGreaterEqual(modal['y'], 0)
            self.assertLessEqual(modal['y'] + modal['height'], height)
            await expect(detail.get_by_role('button', name='更多技能操作', exact=True)).to_be_visible()


if __name__ == '__main__':
    unittest.main(verbosity=2)
