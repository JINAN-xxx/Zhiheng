"""Focused lifecycle interactions against the real Vue pages and strict HTTP fixtures."""

import re
import unittest

from agents_regressions import AgentsUIRegressions
from developer_regressions import DeveloperBoundary
from model_management_regressions import ModelManagementRegressions
from playwright.async_api import expect
from skills_regressions import SkillsUIRegressions


async def accept(page):
    dialog = page.get_by_role('dialog').filter(has=page.get_by_role('button', name=re.compile(r'^确\s*定$')))
    await dialog.get_by_role('button', name=re.compile(r'^确\s*定$')).click()


REFERENCES = [
    {'kind': 'agent', 'id': 'agent-1', 'name': '研究助手', 'status': 'published', 'path': '/agents/agent-1'},
    {'kind': 'work', 'id': None, 'name': '其他用户的工作空间', 'status': 'referenced', 'path': None},
    {'kind': 'future', 'id': 'bad', 'name': '外部路径', 'status': 'active', 'path': '//example.com/private'},
    {'kind': 'future', 'id': 'script', 'name': '无效路径', 'status': 'active', 'path': 'javascript:alert(1)'},
    {'kind': 'future', 'id': 'slashes', 'name': '反斜杠路径', 'status': 'active', 'path': '/\\example.com/private'},
    {'kind': 'future', 'id': 'invalid', 'name': ['无效引用'], 'status': 'active', 'path': '/models'},
]


class LifecycleSkills(SkillsUIRegressions):
    async def test_member_cannot_delete_builtin_skill(self):
        self.skill['source'] = 'builtin'
        await self.test_member_reads_without_administration_controls()

    async def test_builtin_enabled_skill_deletes_with_one_confirmation(self):
        self.skill['source'] = 'builtin'
        await self.open_skills()
        await self.page.get_by_role('button', name='查看详情', exact=True).click()
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await expect(drawer.get_by_role('button', name='删除技能', exact=True)).to_be_enabled()
        await drawer.get_by_role('button', name='删除技能', exact=True).click()
        await expect(self.page.get_by_role('dialog')).to_contain_text('历史')
        await accept(self.page)
        await expect(self.page.locator('.skill-card')).to_have_count(0)
        self.assertEqual(self.updates, [])

    async def test_skill_dependency_lists_actionable_and_private_references(self):
        async def blocked(route):
            if route.request.method == 'DELETE':
                await route.fulfill(
                    status=409,
                    json={
                        'error': {
                            'code': 'skill_referenced',
                            'message': '请先处理当前使用中的资源',
                            'details': {'references': REFERENCES},
                        }
                    },
                )
            else:
                await route.fulfill(json=self.skill)

        await self.page.route('**/api/skills/skill-1', blocked)
        await self.open_skills()
        await self.page.get_by_role('button', name='查看详情', exact=True).click()
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await expect(drawer.get_by_role('button', name='删除技能', exact=True)).to_be_enabled()
        await drawer.get_by_role('button', name='删除技能', exact=True).click()
        await accept(self.page)
        references = drawer.get_by_role('region', name='使用中的资源', exact=True)
        await expect(references).to_be_visible()
        await expect(references.get_by_role('link', name='研究助手', exact=True)).to_have_attribute(
            'href', '/agents/agent-1'
        )
        await expect(references.get_by_text('其他用户的工作空间', exact=True)).to_be_visible()
        await expect(references.locator('li').filter(has_text='其他用户的工作空间')).to_contain_text('使用中')
        await expect(references.get_by_role('link')).to_have_count(1)
        rendering = await self.page.evaluate(r"""async () => {
            const source = await (await fetch('/src/shared/components/ResourceReferences.vue')).text()
            const dependencies = [...source.matchAll(/from\s+["']([^"']+)["']/g)].map(match => match[1])
            const { createApp, h, nextTick, ref } = await import(dependencies.find(path => /\/vue\.js(?:\?|$)/.test(path)))
            const { createRouter, createMemoryHistory } = await import(dependencies.find(path => /\/vue-router\.js(?:\?|$)/.test(path)))
            const { default: ResourceReferences } = await import('/src/shared/components/ResourceReferences.vue')
            const draft = { kind: 'agent', id: 'agent-1', name: '研究助手', status: 'draft', path: '/agents/agent-1' }
            const published = { ...draft, status: 'published' }
            const model = { kind: 'model', id: 'model-1', name: '日常助手', status: 'enabled', path: '/models' }
            const details = ref({ references: [draft, published, model] })
            const warnings = []
            const host = document.createElement('div')
            document.body.append(host)
            const app = createApp({ render: () => h(ResourceReferences, { details: details.value }) })
            app.config.warnHandler = (message) => warnings.push(message)
            const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:pathMatch(.*)*', component: { render: () => null } }] })
            app.use(router)
            app.mount(host)
            await nextTick()
            details.value = { references: [model, published, draft] }
            await nextTick()
            const text = host.textContent
            app.unmount()
            host.remove()
            return { warnings, text }
        }""")
        self.assertIn('草稿', rendering['text'])
        self.assertIn('已发布', rendering['text'])
        self.assertFalse(any('Duplicate keys' in warning for warning in rendering['warnings']), rendering['warnings'])
        await references.get_by_role('link', name='研究助手', exact=True).click()
        await expect(self.page).to_have_url(re.compile(r'/agents/agent-1$'))

    async def check_import_action(self, action, label):
        await self.page.route(
            '**/api/skills/import/prepare?*',
            lambda route: route.fulfill(
                status=201,
                json={
                    **self.skill,
                    'draft_id': 'draft-1',
                    'expires_at': '2026-10-30T10:00:00Z',
                    'action': action,
                    'target_skill_id': 'skill-1',
                    'expected_version': 1,
                    'current_package_version': '0.9.0',
                },
            ),
        )
        await self.open_skills()
        dialog = await self.prepare_upload()
        await expect(dialog.get_by_text('0.9.0', exact=True)).to_be_visible()
        await expect(dialog.get_by_role('button', name=label, exact=True)).to_be_visible()
        await expect(dialog).to_contain_text('历史')
        await dialog.get_by_role('button', name=label, exact=True).click()
        await expect(dialog).not_to_be_visible()
        self.assertEqual(self.confirmations, 1)

    async def test_existing_slug_preflight_explicitly_confirms_update(self):
        await self.check_import_action('update', '确认更新')

    async def test_deleted_slug_preflight_explicitly_confirms_restore(self):
        await self.check_import_action('restore', '确认恢复')

    async def test_unchanged_preflight_does_not_offer_another_install(self):
        await self.page.route(
            '**/api/skills/import/prepare?*',
            lambda route: route.fulfill(
                status=201,
                json={
                    **self.skill,
                    'draft_id': 'draft-1',
                    'expires_at': '2026-10-30T10:00:00Z',
                    'action': 'unchanged',
                    'target_skill_id': 'skill-1',
                    'expected_version': 1,
                    'current_package_version': '1.0.0',
                },
            ),
        )
        await self.open_skills()
        dialog = await self.prepare_upload()
        await expect(dialog.get_by_role('button', name='已安装', exact=True)).to_be_disabled()
        self.assertEqual(self.confirmations, 0)


class LifecycleModels(ModelManagementRegressions):
    async def test_model_dependency_message_is_not_replaced_by_version_conflict(self):
        await self.page.route(
            '**/api/models/model-1',
            lambda route: route.fulfill(
                status=409,
                json={
                    'error': {
                        'code': 'model_referenced',
                        'message': '请先处理当前模型依赖',
                        'details': {'references': REFERENCES},
                    }
                },
            ),
        )
        await self.page.goto(self.origin + '/models')
        await self.page.get_by_role('button', name='更多操作', exact=True).click()
        await expect(self.page.get_by_role('menuitem', name='删除模型', exact=True)).to_be_visible()
        await self.page.get_by_role('menuitem', name='删除模型', exact=True).click()
        await accept(self.page)
        await expect(self.page.get_by_text('请先处理当前模型依赖', exact=True)).to_be_visible()
        await expect(
            self.page.get_by_role('region', name='使用中的资源', exact=True).get_by_role(
                'link', name='研究助手', exact=True
            )
        ).to_have_attribute('href', '/agents/agent-1')

    async def test_enabled_model_deletes_directly_with_history_retained(self):
        deleted = []
        await self.page.route(
            '**/api/models/model-1', lambda route: (deleted.append(route.request.method), route.fulfill(status=204))[1]
        )
        await self.page.goto(self.origin + '/models')
        await self.page.get_by_role('button', name='更多操作', exact=True).click()
        await expect(self.page.get_by_role('menuitem', name='删除模型', exact=True)).to_be_visible()
        await self.page.get_by_role('menuitem', name='删除模型', exact=True).click()
        await expect(self.page.get_by_role('dialog')).to_contain_text('历史')
        await expect(self.page.get_by_role('dialog')).not_to_contain_text('有历史或业务引用时将拒绝')
        await accept(self.page)
        await expect(self.page.get_by_role('dialog')).to_have_count(0)
        self.assertEqual(deleted, ['DELETE'])

    async def test_credential_deletion_lists_current_models(self):
        await self.page.route(
            '**/api/models/connections/connection-1',
            lambda route: route.fulfill(
                status=409,
                json={
                    'error': {
                        'code': 'connection_referenced',
                        'message': '当前模型仍在使用此凭据',
                        'details': {
                            'references': [
                                {
                                    'kind': 'model',
                                    'id': 'model-1',
                                    'name': '日常助手',
                                    'status': 'enabled',
                                    'path': '/models',
                                }
                            ]
                        },
                    }
                },
            ),
        )
        await self.page.goto(self.origin + '/models')
        await self.page.get_by_role('tab', name='凭据管理', exact=True).click()
        await self.page.get_by_role('button', name='更多凭据操作', exact=True).click()
        await expect(self.page.get_by_role('menuitem', name='删除凭据', exact=True)).to_be_visible()
        await self.page.get_by_role('menuitem', name='删除凭据', exact=True).click()
        await accept(self.page)
        references = self.page.get_by_role('region', name='使用中的资源', exact=True)
        await expect(references.get_by_role('link', name='日常助手', exact=True)).to_have_attribute('href', '/models')


class LifecycleAgents(AgentsUIRegressions):
    async def test_successful_save_clears_previous_deletion_dependencies(self):
        async def block_delete(route):
            if route.request.method == 'DELETE':
                await route.fulfill(
                    status=409,
                    json={
                        'error': {
                            'code': 'agent_referenced',
                            'message': '请先移除工作成员',
                            'details': {'references': REFERENCES},
                        }
                    },
                )
            else:
                await self.respond(route)

        await self.page.route('**/api/agents/agent-1', block_delete)
        await self.open_agents()
        await self.choose_agent_action('配置')
        await self.page.get_by_role('button', name='删除智能体', exact=True).click()
        await accept(self.page)
        references = self.page.get_by_role('region', name='使用中的资源', exact=True)
        await expect(references).to_be_visible()
        await self.page.get_by_label('智能体名称', exact=True).fill('新的名称')
        await self.page.get_by_role('button', name='保存', exact=True).click()
        await expect(references).to_have_count(0)

    async def test_agent_deletion_displays_current_work_dependencies(self):
        await self.page.route(
            '**/api/agents/agent-1',
            lambda route: (
                route.fulfill(
                    status=409,
                    json={
                        'error': {
                            'code': 'agent_referenced',
                            'message': '请先移除工作成员',
                            'details': {
                                'references': [
                                    {
                                        'kind': 'work',
                                        'id': 'work-1',
                                        'name': '当前工作空间',
                                        'status': 'active',
                                        'path': '/work/work-1',
                                    }
                                ]
                            },
                        }
                    },
                )
                if route.request.method == 'DELETE'
                else route.fulfill(json=self.agent)
            ),
        )
        await self.open_agents()
        await self.choose_agent_action('配置')
        await self.page.get_by_role('button', name='删除智能体', exact=True).click()
        await accept(self.page)
        await expect(
            self.page.get_by_role('region', name='使用中的资源', exact=True).get_by_role(
                'link', name='当前工作空间', exact=True
            )
        ).to_have_attribute('href', '/work/work-1')

    async def test_published_agent_deletes_directly(self):
        self.agent.update(status='published', published_version=1, published_config=self.config.copy())
        await self.open_agents()
        await self.choose_agent_action('配置')
        editor = self.page.locator('.agent-editor--page')
        await expect(editor.get_by_role('button', name='删除智能体', exact=True)).to_be_enabled()
        await editor.get_by_role('button', name='删除智能体', exact=True).click()
        await expect(self.page.get_by_role('dialog')).to_contain_text('下线')
        await expect(self.page.get_by_role('dialog')).to_contain_text('历史')
        await accept(self.page)
        await expect(self.page.get_by_role('heading', name='智能体', exact=True)).to_be_visible()
        self.assertEqual(self.agents, [])


class LifecycleApplications(DeveloperBoundary):
    async def test_reenable_application_displays_missing_model_dependencies(self):
        self.app['status'] = 'disabled'
        await self.page.route(
            '**/api/developer/applications/app-1/enable',
            lambda route: route.fulfill(
                status=409,
                json={
                    'error': {
                        'code': 'model_unavailable',
                        'message': '有效密钥引用的模型已移除，请先更新授权',
                        'details': {
                            'references': [
                                {
                                    'kind': 'model',
                                    'id': 'model-1',
                                    'name': '日常助手',
                                    'status': 'deleted',
                                    'path': '/models',
                                }
                            ]
                        },
                    }
                },
            ),
        )
        await self.open_details()
        await self.page.get_by_role('button', name=re.compile('重新启用')).click()
        await accept(self.page)
        await expect(self.page.get_by_text('有效密钥引用的模型已移除，请先更新授权', exact=True)).to_be_visible()
        await expect(
            self.page.get_by_role('region', name='使用中的资源', exact=True).get_by_role(
                'link', name='日常助手', exact=True
            )
        ).to_have_attribute('href', '/models')
        self.assertEqual(self.app['status'], 'disabled')

    async def test_disabled_application_can_rename_without_enabling(self):
        self.app['status'] = 'disabled'
        await self.open_details()
        edit = self.page.locator('.detail-actions').get_by_role('button', name=re.compile('编辑'))
        await expect(edit).to_be_visible()
        await edit.click()
        dialog = self.page.get_by_role('dialog')
        await dialog.get_by_label('应用名称', exact=True).fill('停用应用的新名称')
        await dialog.get_by_role('button', name=re.compile(r'^保\s*存$')).click()
        await expect(self.page.get_by_role('heading', name='停用应用的新名称', exact=True)).to_be_visible()
        self.assertEqual(self.app['status'], 'disabled')
        self.assertEqual(self.posts[-1], {'name': '停用应用的新名称', 'expected_version': 1})

    async def test_active_application_deletes_and_explains_keys_and_audit(self):
        await self.open_details()
        remove = self.page.get_by_role('button', name=re.compile('删除应用'))
        await expect(remove).to_be_visible()
        await remove.click()
        await expect(self.page.get_by_role('dialog')).to_contain_text('密钥')
        await expect(self.page.get_by_role('dialog')).to_contain_text('审计')
        await accept(self.page)
        await expect(self.page.get_by_role('heading', name='内部工具', exact=True)).to_have_count(0)
        self.assertIsNotNone(self.app['deleted_at'])


def load_tests(loader, tests, pattern):
    # Inherited fixtures stay reusable; run only the focused lifecycle cases in this module.
    suite = unittest.TestSuite()
    for fixture in (LifecycleSkills, LifecycleModels, LifecycleAgents, LifecycleApplications):
        suite.addTests(fixture(name) for name in fixture.__dict__ if name.startswith('test_'))
    return suite


if __name__ == '__main__':
    unittest.main(verbosity=2)
