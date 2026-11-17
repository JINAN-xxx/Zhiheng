"""Permission revocation and server-owned capability projection through the real Vue UI."""
import os
import unittest
from urllib.parse import urlparse

from playwright.async_api import expect
from developer_regressions import DeveloperBoundary


class PlatformOptimizationRegressions(DeveloperBoundary):
    async def asyncSetUp(self):
        self.permissions = ['platform:models:enabled', 'platform:models:read', 'platform:models:config_read', 'platform:models:create', 'platform:models:update']
        self.scope_reads = 0
        await super().asyncSetUp()

    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path == '/api/auth/me':
            return await route.fulfill(json={'id': 'user-1', 'username': 'member', 'is_admin': False, 'permissions': self.permissions})
        if path == '/api/models/connection-options':
            return await route.fulfill(json=[{'id': 'connection-1', 'name': 'Test connection', 'enabled': True}])
        if path == '/api/models/connections':
            return await route.fulfill(json=[{'id': 'connection-1', 'name': 'Test credential', 'provider': 'openai_compatible', 'base_url': 'http://fixture.invalid', 'has_api_key': True, 'enabled': True}])
        if path == '/api/developer/applications/scope-catalog':
            self.scope_reads += 1
            definitions = [
                {'scope': 'knowledge:read', 'label': '读取知识库', 'description': '读取目录', 'requires_models': False, 'grantable': True, 'reason': None},
                {'scope': 'knowledge:query', 'label': '知识检索', 'description': '检索', 'requires_models': False, 'grantable': False, 'reason': '当前账号没有检索权限'},
                {'scope': 'skills:read', 'label': '读取技能', 'description': '读取修订', 'requires_models': False, 'grantable': True, 'reason': None},
            ]
            return await route.fulfill(json={'scopes': [x['scope'] for x in definitions], 'definitions': definitions, 'caps': {'resource_rpm_min': 1, 'resource_rpm_max': 120}})
        return await super().respond(route)

    async def refresh_permissions(self):
        await self.page.evaluate("async () => { const m = await import('/src/modules/identity/public.ts'); await m.restoreIdentity(true) }")

    async def test_revoking_create_closes_create_form_while_update_is_still_allowed(self):
        await self.page.goto(self.origin + '/models')
        await self.page.get_by_role('button', name='接入模型', exact=True).click()
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await expect(drawer).to_be_visible()
        self.permissions.remove('platform:models:create')
        await self.refresh_permissions()
        await expect(drawer).to_have_count(0)
        await expect(self.page.get_by_role('button', name='接入模型', exact=True)).to_have_count(0)

    async def test_revoking_update_closes_edit_form_while_create_is_still_allowed(self):
        await self.page.goto(self.origin + '/models')
        await self.page.get_by_role('button', name='查看模型详情：日常助手', exact=True).click()
        await self.page.get_by_role('button', name='更多模型操作', exact=True).click()
        await self.page.get_by_role('menuitem', name='编辑模型', exact=True).click()
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await expect(drawer).to_be_visible()
        self.permissions.remove('platform:models:update')
        await self.refresh_permissions()
        await expect(drawer).to_have_count(0)
        await expect(self.page.get_by_role('button', name='接入模型', exact=True)).to_be_enabled()

    async def test_credential_revocation_clears_typed_secret_with_other_actions_remaining(self):
        self.permissions = ['ai:credentials:enabled', 'ai:credentials:read', 'ai:credentials:create', 'ai:credentials:update']
        await self.page.goto(self.origin + '/credentials')
        await self.page.get_by_role('button', name='新增凭据', exact=True).click()
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        secret = drawer.locator('input[type=password]')
        await secret.fill('test-only-secret-never-submit')
        self.permissions.remove('ai:credentials:create')
        await self.refresh_permissions()
        await expect(drawer).to_have_count(0)
        self.permissions.append('ai:credentials:create')
        await self.refresh_permissions()
        await self.page.get_by_role('button', name='新增凭据', exact=True).click()
        await expect(drawer.locator('input[type=password]')).to_have_value('')

    async def test_read_scopes_do_not_require_query_or_skill_use_and_catalog_refreshes(self):
        self.permissions = ['platform:developer:enabled', 'platform:developer:read', 'platform:developer:key_create', 'platform:knowledge:enabled', 'platform:knowledge:read', 'platform:skills:enabled', 'platform:skills:read']
        await self.page.goto(self.origin + '/developer?applicationId=app-1&tab=keys')
        await self.page.get_by_role('button', name='创建密钥', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        await expect(dialog.get_by_role('checkbox', name='读取知识库')).to_be_enabled()
        await expect(dialog.get_by_role('checkbox', name='读取技能')).to_be_enabled()
        await expect(dialog.get_by_role('checkbox', name='知识检索')).to_be_disabled()
        self.permissions.append('platform:developer:invoke')
        before = self.scope_reads
        await self.refresh_permissions()
        await expect(dialog).to_be_hidden()
        await self.page.get_by_role('button', name='创建密钥', exact=True).click()
        await expect(self.page.get_by_role('checkbox', name='读取知识库')).to_be_enabled()
        self.assertGreater(self.scope_reads, before)

    async def test_unknown_window_is_explicit_in_model_details(self):
        await self.page.goto(self.origin + '/models')
        await self.page.get_by_role('button', name='查看模型详情：日常助手', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        await expect(dialog).to_contain_text('未知，请按接入服务文档或部署配置填写')
        await expect(dialog).to_contain_text('未配置，无法在本地确认输入上限')


if __name__ == '__main__':
    os.environ.setdefault('PLATFORM_BROWSER_URL', 'http://127.0.0.1:19080')
    unittest.main()
