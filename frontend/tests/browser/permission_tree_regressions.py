"""Single-column module switches and independent operation checkboxes."""
import os
import re
import runpy
import unittest
from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import expect
from admin_regressions import AdminRegressions
from platform_model_authority_regressions import ModelAuthorityRegressions

POLICY=runpy.run_path(str(Path(__file__).resolve().parents[3]/'backend/src/zhiheng/shared/permission_policy.py'))
CATALOG=POLICY['catalog']()

def rights(module,*actions):
    return [module+':enabled',module+':read',*(module+':'+action for action in actions if action!='read')]

class PermissionTreeRegressions(AdminRegressions):
    async def respond(self,route):
        if urlparse(route.request.url).path=='/api/admin/permissions':
            return await route.fulfill(json={'items':CATALOG})
        return await super().respond(route)

    async def new_role(self):
        await self.page.goto(self.origin+'/admin/roles')
        await self.page.get_by_role('button',name='新建角色',exact=True).click()
        dialog=self.page.get_by_role('dialog',name='新建角色',exact=True)
        await dialog.get_by_role('textbox',name='角色名称').fill('按操作授权')
        return dialog

    async def test_create_selects_read_but_never_delete_and_saves_exact_operations(self):
        dialog=await self.new_role()
        module=dialog.get_by_role('region',name='知识库',exact=True)
        await expect(module.get_by_role('switch',name='开启知识库')).not_to_be_checked()
        await module.get_by_role('switch',name='开启知识库').click()
        await module.get_by_role('checkbox',name='知识库：新增',exact=True).check()
        await expect(module.get_by_role('checkbox',name='知识库：查看',exact=True)).to_be_checked()
        await expect(module.get_by_role('checkbox',name='知识库：删除',exact=True)).not_to_be_checked()
        await dialog.get_by_role('button',name=re.compile(r'保\s*存')).click()
        await expect(dialog).to_be_hidden()
        self.assertEqual(set(self.roles[-1]['permissions']),set(rights('platform:knowledge','create')))

    async def test_module_all_does_not_enable_credentials_or_other_modules(self):
        dialog=await self.new_role()
        models=dialog.get_by_role('region',name='模型目录',exact=True)
        await models.get_by_role('switch',name='开启模型目录').click()
        await models.get_by_role('checkbox',name='全选模型目录',exact=True).check()
        await expect(dialog.get_by_role('switch',name='开启凭据管理',exact=True)).not_to_be_checked()
        await expect(dialog.get_by_role('switch',name='开启用户管理',exact=True)).not_to_be_checked()
        await dialog.get_by_role('button',name=re.compile(r'保\s*存')).click()
        await expect(dialog).to_be_hidden()
        expected={item['code'] for item in CATALOG if item['module']=='platform:models'}
        self.assertEqual(set(self.roles[-1]['permissions']),expected)

    async def test_off_retains_children_and_reenable_restores_without_extra_actions(self):
        self.roles[0]['permissions']=rights('platform:knowledge','create')
        await self.page.goto(self.origin+'/admin/roles')
        await self.page.get_by_role('row').filter(has=self.page.get_by_text('运营员',exact=True)).get_by_role('button',name='编辑',exact=True).click()
        dialog=self.page.get_by_role('dialog',name='编辑角色',exact=True)
        await dialog.get_by_role('switch',name='开启知识库',exact=True).click()
        await dialog.get_by_role('button',name=re.compile(r'保\s*存')).click()
        await expect(dialog).to_be_hidden()
        self.assertEqual(set(self.roles[0]['permissions']),{'platform:knowledge:read','platform:knowledge:create'})
        await self.page.get_by_role('row').filter(has=self.page.get_by_text('运营员',exact=True)).get_by_role('button',name='编辑',exact=True).click()
        await dialog.get_by_role('switch',name='开启知识库',exact=True).click()
        await expect(dialog.get_by_role('checkbox',name='知识库：新增',exact=True)).to_be_checked()
        await expect(dialog.get_by_role('checkbox',name='知识库：删除',exact=True)).not_to_be_checked()
        await dialog.get_by_role('checkbox',name='知识库：查看',exact=True).uncheck()
        await expect(dialog.get_by_role('checkbox',name='知识库：新增',exact=True)).not_to_be_checked()

    async def test_system_modules_use_the_same_tree_and_mobile_layout(self):
        dialog=await self.new_role()
        module=dialog.get_by_role('region',name='用户管理',exact=True)
        await module.get_by_role('switch',name='开启用户管理').click()
        await module.get_by_role('checkbox',name='用户管理：新增',exact=True).check()
        await expect(module.get_by_role('checkbox',name='用户管理：查看',exact=True)).to_be_checked()
        await expect(module.get_by_role('checkbox',name='用户管理：删除',exact=True)).not_to_be_checked()
        await expect(module.get_by_role('checkbox',name='用户管理：分配角色',exact=True)).not_to_be_checked()
        await self.page.set_viewport_size({'width':390,'height':844})
        await module.get_by_role('checkbox',name='用户管理：新增',exact=True).scroll_into_view_if_needed()
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))

    async def test_user_edit_never_inherits_password_reset_or_account_toggle(self):
        self.user.update(is_admin=False, permissions=rights('system:users','update'))
        await self.page.goto(self.origin+'/admin/users')
        row=self.page.get_by_role('row').filter(has=self.page.get_by_text('小艾',exact=True))
        await row.get_by_role('button',name='更多操作',exact=True).click()
        await expect(self.page.get_by_role('menuitem',name='重置密码',exact=True)).to_be_disabled()
        await self.page.keyboard.press('Escape')
        await row.get_by_role('button',name='编辑',exact=True).click()
        await expect(self.page.get_by_role('switch',name='启用账号',exact=True)).to_be_disabled()

    async def test_password_reset_is_available_without_user_edit(self):
        self.user.update(is_admin=False, permissions=rights('system:users','reset_password'))
        await self.page.goto(self.origin+'/admin/users')
        row=self.page.get_by_role('row').filter(has=self.page.get_by_text('小艾',exact=True))
        await expect(row.get_by_role('button',name='编辑',exact=True)).to_be_disabled()
        await row.get_by_role('button',name='更多操作',exact=True).click()
        await self.page.get_by_role('menuitem',name='重置密码',exact=True).click()
        await expect(self.page.get_by_role('dialog',name='重置密码：alice',exact=True)).to_be_visible()

    async def test_role_delete_is_available_without_role_edit(self):
        self.user.update(is_admin=False, permissions=rights('system:roles','delete'))
        self.roles[0]['permissions']=[]
        await self.page.goto(self.origin+'/admin/roles')
        row=self.page.get_by_role('row').filter(has=self.page.get_by_text('运营员',exact=True))
        await expect(row.get_by_role('button',name='编辑',exact=True)).to_have_count(0)
        await row.get_by_role('button',name='更多操作',exact=True).click()
        await expect(self.page.get_by_role('menuitem',name='删除',exact=True)).to_be_enabled()

class OperationModelRegressions(ModelAuthorityRegressions):
    async def test_create_only_has_no_toggle_edit_or_delete_controls(self):
        self.permissions=rights('platform:models','create')
        await self.page.goto(self.origin+'/models')
        await expect(self.page.get_by_role('button',name='接入模型',exact=True)).to_be_enabled()
        await expect(self.page.get_by_role('switch',name='启用模型“日常助手”',exact=True)).to_have_count(0)
        await self.page.get_by_role('button',name='查看模型详情：日常助手',exact=True).click()
        await expect(self.page.get_by_role('button',name='编辑模型',exact=True)).to_have_count(0)
        self.assertNotIn('/api/models/connections',self.authority_requests)

    async def test_credentials_have_independent_route_and_never_enable_model_directory(self):
        self.permissions=rights('ai:credentials','read')
        await self.page.goto(self.origin+'/credentials')
        await expect(self.page.get_by_role('heading',name='凭据管理',exact=True)).to_be_visible()
        await expect(self.page.get_by_text('Company credential',exact=True)).to_be_visible()
        await expect(self.page.get_by_role('navigation',name='主导航').get_by_role('link',name='模型目录',exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button',name='新增凭据',exact=True)).to_have_count(0)
        self.assertNotIn('/api/models',self.authority_requests)

if __name__=='__main__':
    os.environ.setdefault('PLATFORM_BROWSER_URL','http://127.0.0.1:19080')
    unittest.main()
