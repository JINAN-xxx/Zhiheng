"""Release checks for canonical permissions, legacy URLs and current admin submenus."""
import unittest
import re
from urllib.parse import urlparse

from playwright.async_api import expect
from knowledge_admin_v2_regressions import KnowledgeAdminV2Regressions
from permission_tree_regressions import PermissionTreeRegressions, OperationModelRegressions, rights


class ReleaseKnowledgeNavigationRegressions(KnowledgeAdminV2Regressions):
    async def respond(self, route):
        path=urlparse(route.request.url).path
        if path in ['/api/knowledge/settings/parsing','/api/knowledge/settings/parsing/status']:
            self.requests.append({'method':route.request.method,'path':path,'body':None})
            if path.endswith('/status'):
                return await route.fulfill(json={'local':{'configured':False,'available':False},'cloud':None,'cloud_enabled':False})
            return await route.fulfill(json={
                'version':1,'local_url':None,'has_local_key':False,'cloud_enabled':False,
                'cloud_url':None,'has_cloud_key':False,'tier':'standard','timeout_seconds':900,'queue_timeout_seconds':120,
            })
        return await super().respond(route)

    async def test_storage_only_role_can_use_all_storage_aliases(self):
        self.user.update(is_admin=False, permissions=rights('ai:storage','read'))
        for path in ['/admin/storage', '/files', '/knowledge/storage', '/system/file']:
            with self.subTest(path=path):
                await self.page.goto(self.origin+path)
                await self.assert_storage()
                await expect(self.page.locator('.admin-navigation a.is-active')).to_have_text('存储管理')
        self.assertFalse(any(item['path'].startswith('/api/knowledge/') for item in self.requests))

    async def test_config_create_cannot_edit_knowledge_defaults_or_parsing(self):
        self.user.update(is_admin=False, permissions=rights('system:configs','create'))
        await self.page.goto(self.origin+'/knowledge/settings')
        await expect(self.page.get_by_role('heading',name='知识库默认配置',exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button',name='保存配置',exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('spinbutton',name='默认切片大小',exact=True)).to_be_disabled()
        self.assertFalse(self.patches())

    async def test_config_update_can_edit_defaults_without_knowledge_or_credential_access(self):
        self.user.update(is_admin=False, permissions=rights('system:configs','update'))
        await self.page.goto(self.origin+'/admin/knowledge-settings')
        await expect(self.page).to_have_url(self.origin+'/knowledge/settings')
        await expect(self.page.get_by_role('button',name='保存配置',exact=True)).to_be_enabled()
        await expect(self.page.get_by_role('spinbutton',name='默认切片大小',exact=True)).to_be_enabled()
        self.assertFalse(any(item['path'] in ['/api/models/connections','/api/knowledge/bases'] for item in self.requests))


class ReleaseAdminNavigationRegressions(PermissionTreeRegressions):
    async def test_legacy_product_aliases_cannot_bypass_module_gate(self):
        self.user.update(is_admin=False, permissions=rights('platform:chat','use'))
        for path in ['/ai/model','/ai/application','/ai/usage','/agent_extension/skill','/agent_extension/agent','/ai/chat/work']:
            with self.subTest(path=path):
                await self.page.goto(self.origin+path)
                await expect(self.page).to_have_url(re.compile(re.escape(self.origin) + r'/access\?denied='))
        self.assertFalse(any(path.startswith(('/api/models','/api/developer','/api/skills','/api/agents','/api/work')) for _,path,_,_ in self.requests))

    async def test_notice_setting_uses_exact_create_update_and_toggle_permissions(self):
        original=dict(self.configs[0])
        for actions,exists,active,allowed in [
            (['update'],False,True,False),
            (['create'],False,True,True),
            (['create'],True,True,False),
            (['update'],True,True,True),
            (['update'],True,False,False),
            (['update','toggle'],True,False,True),
        ]:
            with self.subTest(actions=actions,exists=exists,active=active):
                self.user.update(is_admin=False,permissions=rights('system:configs',*actions))
                self.configs=[{**original,'is_active':active}] if exists else []
                await self.page.goto(self.origin+'/admin/configs')
                switch=self.page.get_by_role('switch',name='在平台展示公告',exact=True)
                await expect(switch).to_be_visible()
                await expect(self.page.locator('.notice-visibility .ant-switch-loading')).to_have_count(0)
                if allowed: await expect(switch).to_be_enabled()
                else: await expect(switch).to_be_disabled()

    async def test_retired_admin_links_land_on_authorized_role_submenu(self):
        self.user.update(is_admin=False, permissions=rights('system:roles','read'))
        for path in ['/admin/menus','/admin/posts','/admin/dictionaries']:
            with self.subTest(path=path):
                await self.page.goto(self.origin+path)
                await expect(self.page).to_have_url(self.origin+'/admin/roles')
                await expect(self.page.get_by_text('运营员',exact=True)).to_be_visible()
                await expect(self.page.locator('.admin-navigation a')).to_have_count(1)
        self.assertFalse(any(path.startswith(('/api/admin/menus','/api/admin/posts','/api/admin/dictionaries','/api/admin/users')) for _,path,_,_ in self.requests))


class ReleaseModelNavigationRegressions(OperationModelRegressions):
    async def test_model_alias_preserves_the_active_menu(self):
        self.permissions=rights('platform:models','read')
        await self.page.goto(self.origin+'/ai/model')
        await expect(self.page.get_by_role('heading',name='模型目录',exact=True)).to_be_visible()
        await expect(self.page.get_by_role('navigation',name='主导航').get_by_role('link',name='模型目录',exact=True)).to_have_attribute('aria-current','page')
        self.assertNotIn('/api/models/connections',self.authority_requests)


if __name__=='__main__':
    unittest.main(verbosity=2)
