"""Built-in role presentation and user assignment through same-origin API fixtures."""
import re
import unittest
from urllib.parse import urlparse

from playwright.async_api import expect

import admin_regressions


class BuiltinRoleRegressions(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = admin_regressions.AdminRegressions.asyncTearDown
    collection = admin_regressions.AdminRegressions.collection
    open = admin_regressions.AdminRegressions.open

    async def asyncSetUp(self):
        await admin_regressions.AdminRegressions.asyncSetUp(self)
        self.roles.append(dict(id='system-role', version=1, code='super_admin', name='超级管理员', system_key='super_admin', member_count=2, permissions=[], is_active=True, remark='由系统维护'))
        self.roles[0]['member_count'] = 1
        self.users[0]['role_ids'] = ['system-role']
        self.users.append(dict(id='admin-2', version=4, username='second-admin', display_name='第二位管理员', email='', phone='', department_id=None, post_ids=[], role_ids=['system-role', 'role-1'], is_active=True, is_admin=True, remark=''))

    async def respond(self, route):
        request = route.request
        path = urlparse(request.url).path
        if path == '/api/admin/users/options':
            await route.fulfill(json={
                'departments': [], 'posts': [],
                'roles': [{key: role.get(key) for key in ['id', 'name', 'system_key']} for role in self.roles]
            })
        elif path == '/api/admin/users/admin-2' and request.method == 'PATCH':
            payload = request.post_data_json
            self.requests.append(('PATCH', path, {}, payload))
            user = self.users[2]
            user.update({key: value for key, value in payload.items() if key != 'expected_version'})
            user['role_ids'] = [role_id for role_id in user['role_ids'] if role_id != 'system-role']
            if user['is_admin']:
                user['role_ids'].append('system-role')
            user['version'] += 1
            await route.fulfill(json=user)
        else:
            await admin_regressions.AdminRegressions.respond(self, route)

    async def test_builtin_role_has_server_member_count_and_read_only_details(self):
        await self.open('roles')
        row = self.page.get_by_role('row').filter(has_text='super_admin')
        await expect(row.get_by_text('系统内置', exact=True)).to_be_visible()
        await expect(row.get_by_text('全部管理权限', exact=True)).to_be_visible()
        await expect(row.get_by_text('2', exact=True)).to_be_visible()
        await expect(row.get_by_role('button', name='编辑', exact=True)).to_have_count(0)
        await expect(row.get_by_role('button', name='删除', exact=True)).to_have_count(0)
        await row.get_by_role('button', name='查看', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        await expect(dialog.get_by_text('全部管理权限', exact=True)).to_be_visible()
        await expect(dialog.get_by_text('由系统维护', exact=True)).to_be_visible()
        await expect(dialog.locator('input, textarea, select')).to_have_count(0)
        await expect(dialog.get_by_role('button', name=re.compile(r'^保\s*存$'))).to_have_count(0)
        self.assertFalse(any(method in ['PATCH', 'DELETE'] for method, _, _, _ in self.requests))

    async def test_role_reader_can_view_builtin_details_without_write_permission(self):
        self.user = dict(id='reader', username='reader', is_admin=False, permissions=['system:roles:read'])
        await self.open('roles')
        await self.page.get_by_role('row').filter(has_text='super_admin').get_by_role('button', name='查看', exact=True).click()
        await expect(self.page.get_by_role('dialog').get_by_text('全部管理权限', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='新建角色', exact=True)).to_have_count(0)

    async def test_admin_user_displays_linked_disabled_system_role_and_can_be_demoted(self):
        await self.open('users')
        row = self.page.get_by_role('row').filter(has_text='第二位管理员')
        await expect(row.get_by_text('超级管理员', exact=True)).to_be_visible()
        await row.get_by_role('button', name='编辑', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        roles = dialog.get_by_label('角色', exact=True)
        builtin = roles.locator('option[value="system-role"]')
        await expect(builtin).to_have_text('超级管理员（系统内置）')
        # Playwright's is_disabled retargets options inside a label to the select.
        await expect(builtin).to_have_js_property('disabled', True)
        self.assertTrue(await builtin.evaluate('(option) => option.selected'))
        await expect(roles.locator('option[value="role-1"]')).to_have_js_property('disabled', False)
        await expect(dialog.get_by_text('系统内置角色由“超级管理员”开关管理。', exact=True)).to_be_visible()
        await dialog.get_by_label('显示名称', exact=True).fill('更新后的管理员')
        await dialog.get_by_role('button', name=re.compile(r'^保\s*存$')).click()
        await expect(dialog).not_to_be_visible()
        payload = next(body for method, path, _, body in self.requests if method == 'PATCH' and path.endswith('/admin-2'))
        self.assertEqual(payload['is_admin'], True)
        self.assertIn('system-role', payload['role_ids'])
        await self.page.get_by_role('row').filter(has_text='更新后的管理员').get_by_role('button', name='编辑', exact=True).click()
        await dialog.get_by_label('超级管理员', exact=True).uncheck()
        await dialog.get_by_role('button', name=re.compile(r'^保\s*存$')).click()
        await expect(dialog).not_to_be_visible()
        payload = [body for method, path, _, body in self.requests if method == 'PATCH' and path.endswith('/admin-2')][-1]
        self.assertEqual(payload['is_admin'], False)
        await expect(self.page.get_by_role('row').filter(has_text='更新后的管理员').get_by_text('超级管理员', exact=True)).to_have_count(0)

    async def test_ordinary_user_cannot_select_system_role_but_can_select_regular_role(self):
        await self.open('users')
        await self.page.get_by_role('row').filter(has_text='小艾').get_by_role('button', name='编辑', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        roles = dialog.get_by_label('角色', exact=True)
        builtin = roles.locator('option[value="system-role"]')
        await expect(builtin).to_have_js_property('disabled', True)
        self.assertFalse(await builtin.evaluate('(option) => option.selected'))
        await roles.select_option('role-1')
        await dialog.get_by_role('button', name=re.compile(r'^保\s*存$')).click()
        await expect(dialog).not_to_be_visible()
        payload = next(body for method, path, _, body in self.requests if method == 'PATCH' and path.endswith('/user-2'))
        self.assertEqual(payload['role_ids'], ['role-1'])
        self.assertEqual(payload['is_admin'], False)


if __name__ == '__main__':
    unittest.main(verbosity=2)
