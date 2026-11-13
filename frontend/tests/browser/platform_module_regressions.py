"""Module-level role switches and navigation have no implicit ordinary-user grants."""

import os
import re
import unittest
from urllib.parse import urlparse

from playwright.async_api import expect
from admin_regressions import AdminRegressions

MODULES = [
    ('overview', '工作台', '平台应用'), ('chat', '对话体验', '平台应用'),
    ('agents', '智能助手', '平台应用'), ('work', '工作空间', '平台应用'),
    ('models', '模型目录', '资源能力'), ('knowledge', '知识库', '资源能力'),
    ('skills', '技能', '资源能力'), ('developer', '开发接入', '开发与用量'),
    ('usage', '用量与调用', '开发与用量'),
]

class PlatformModuleRegressions(AdminRegressions):
    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path == '/api/admin/users/user-2/effective-permissions':
            return await route.fulfill(json={'items': [{'code':'platform:chat:access', 'label':'对话体验', 'group':'对话体验', 'roles':['研发人员','自定义角色']} ]})
        if path == '/api/admin/permissions':
            items = [
                {'code': f'platform:{module}:access', 'label': {'chat':'使用对话体验','knowledge':'使用知识库'}.get(module,title), 'group': title, 'section': '功能使用', 'description': '使用功能，不包含维护与凭据管理'}
                for module, title, section in MODULES
            ]
            items += [{'code':'platform:knowledge:manage','label':'维护本人知识库','group':'知识库','section':'资源维护'},
                      {'code':'platform:knowledge:share','label':'共享知识库','group':'知识库','section':'发布与共享'},
                      {'code':'ai:models:write','label':'管理平台模型','group':'模型配置','section':'平台管理'},
                      {'code':'ai:credentials:read','label':'查看凭据连接信息','group':'凭据管理','section':'平台管理'},
                      {'code':'ai:credentials:write','label':'管理模型凭据','group':'凭据管理','section':'平台管理'}]
            return await route.fulfill(json={'items': items})
        if path == '/api/chat/conversations':
            return await route.fulfill(json=[])
        if path == '/api/models':
            return await route.fulfill(json=[])
        return await super().respond(route)

    async def test_new_role_switches_start_off_and_save_exactly_selected_modules(self):
        await self.page.goto(self.origin + '/admin/roles')
        await self.page.get_by_role('button', name='新建角色', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='新建角色', exact=True)
        await expect(dialog.get_by_role('switch', name='工作台', exact=True)).not_to_be_checked()
        await expect(dialog.get_by_role('switch', name='使用对话体验', exact=True)).not_to_be_checked()
        await dialog.get_by_role('textbox', name='角色名称', exact=True).fill('按需开启')
        await dialog.get_by_role('switch', name='使用对话体验', exact=True).click()
        await dialog.get_by_role('switch', name='使用知识库', exact=True).click()
        await expect(dialog.get_by_role('switch', name='使用对话体验', exact=True)).to_be_checked()
        await expect(dialog.get_by_role('switch', name='智能助手', exact=True)).not_to_be_checked()
        await expect(dialog.get_by_role('switch', name='维护本人知识库', exact=True)).not_to_be_checked()
        await expect(dialog.get_by_role('switch', name='共享知识库', exact=True)).not_to_be_checked()
        await expect(dialog.get_by_role('switch', name='管理模型凭据', exact=True)).not_to_be_checked()
        await expect(dialog.get_by_role('switch', name='查看凭据连接信息', exact=True)).not_to_be_checked()
        await dialog.get_by_role('button', name=re.compile(r'保\s*存')).click()
        await expect(dialog).to_be_hidden()
        self.assertEqual(set(self.roles[-1]['permissions']), {'platform:chat:access', 'platform:knowledge:access'})
        self.assertNotIn('platform:overview:access', self.roles[-1]['permissions'])

    async def test_ordinary_navigation_and_denied_direct_route(self):
        self.user = {'id': 'member', 'username': 'member', 'is_admin': False, 'permissions': ['platform:chat:access']}
        await self.page.goto(self.origin + '/chat')
        nav = self.page.get_by_role('navigation', name='主导航', exact=True)
        await expect(nav.get_by_role('link', name='对话体验', exact=True)).to_be_visible()
        for label in ('工作台', '智能助手', '工作空间', '模型目录', '知识库', '技能', '开发接入', '用量与调用'):
            await expect(nav.get_by_role('link', name=label, exact=True)).to_have_count(0)
        await self.page.goto(self.origin + '/knowledge')
        await expect(self.page).to_have_url(re.compile(r'/access\?denied=knowledge'))
        await expect(self.page.get_by_role('heading', name='当前账号未获准使用此模块')).to_be_visible()
        await expect(self.page.get_by_role('navigation', name='可用功能').get_by_role('link', name='对话体验', exact=True)).to_be_visible()

    async def test_no_modules_has_recovery_page_and_profile_entry(self):
        self.user = {'id': 'member', 'username': 'member', 'is_admin': False, 'permissions': []}
        await self.page.goto(self.origin + '/')
        await expect(self.page).to_have_url(re.compile('/access$'))
        await expect(self.page.get_by_role('heading', name='暂未分配功能权限')).to_be_visible()
        await expect(self.page.get_by_role('link', name='个人资料', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('navigation', name='主导航').get_by_role('link')).to_have_count(0)

    async def test_user_permission_sources_are_visible_without_changing_grants(self):
        await self.page.goto(self.origin + '/admin/users')
        row = self.page.get_by_role('row').filter(has=self.page.get_by_text('小艾', exact=True))
        await row.get_by_role('button', name='更多操作', exact=True).click()
        await self.page.get_by_role('menuitem', name='有效权限', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='有效权限与来源角色', exact=True)
        await expect(dialog).to_contain_text('对话体验')
        await expect(dialog).to_contain_text('研发人员、自定义角色')
        self.assertFalse(any(method in ('PATCH','POST','DELETE') for method,path,query,body in self.requests if '/admin/' in path))

    async def test_maintenance_and_sharing_do_not_auto_select_use(self):
        await self.page.goto(self.origin + '/admin/roles')
        await self.page.get_by_role('button',name='新建角色',exact=True).click()
        dialog=self.page.get_by_role('dialog',name='新建角色',exact=True)
        await dialog.get_by_role('textbox',name='角色名称',exact=True).fill('知识库维护人员')
        await dialog.get_by_role('switch',name='维护本人知识库',exact=True).click()
        await dialog.get_by_role('switch',name='共享知识库',exact=True).click()
        await expect(dialog.get_by_role('switch',name='使用知识库',exact=True)).not_to_be_checked()
        await dialog.get_by_role('button',name=re.compile(r'保\s*存')).click()
        await expect(dialog).to_be_hidden()
        self.assertEqual(set(self.roles[-1]['permissions']),{'platform:knowledge:manage','platform:knowledge:share'})

if __name__ == '__main__':
    os.environ.setdefault('PLATFORM_BROWSER_URL', 'http://127.0.0.1:19080')
    unittest.main()
