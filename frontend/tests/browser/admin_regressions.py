"""Admin console HTTP boundaries; never intercept Vite source modules."""
import asyncio
import contextlib
import os
import re
import unittest
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright, expect


class AdminRegressions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.context = await self.browser.new_context(viewport={'width': 1440, 'height': 1000})
        self.page = await self.context.new_page()
        self.page.set_default_timeout(10000)
        self.origin = os.environ.get('PLATFORM_BROWSER_URL', 'http://127.0.0.1:15240')
        self.errors = []
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.user = {'id': 'self', 'username': 'admin', 'is_admin': True, 'permissions': []}
        self.users = [dict(id='self', version=3, username='admin', display_name='管理员', email='', phone='', department_id=None, post_ids=[], role_ids=[], is_active=True, is_admin=True, remark=''), dict(id='user-2', version=7, username='alice', display_name='小艾', email='', phone='', department_id=None, post_ids=[], role_ids=[], is_active=True, is_admin=False, remark='')]
        self.roles = [dict(id='role-1', version=3, code='operator', name='运营员', permissions=['system:notices:read'], is_active=True, remark='')]
        self.departments = [dict(id='department-1', version=1, parent_id=None, name='总部', sort_order=0, leader='', phone='', email='', is_active=True), dict(id='department-2', version=2, parent_id='department-1', name='研发部', sort_order=1, leader='张三', phone='', email='', is_active=True)]
        self.configs = [dict(id='config-1', version=6, key='console.notice_enabled', name='控制台公告显示', value='true', is_active=True, remark='')]
        self.menus = []
        self.posts = []
        self.requests = []
        self.dictionaries = [dict(id='dict-1', version=4, code='task_status', name='任务状态', is_active=True, remark='')]
        self.dictionary_items = [dict(id='item-1', version=2, label='已完成', value='completed', sort_order=1, is_active=True, remark='')]
        self.notices = [dict(id='notice-1', version=1, title='维护安排', content='<script>window.injected=true</script>原文', kind='notice', status='draft', remark='')]
        self.sessions = [dict(session_id='session-self', version=1, user_id='self', username='admin', created_at='2026-10-28T01:00:00Z', expires_at='2026-10-29T01:00:00Z', is_current=True), dict(session_id='session-other', version=5, user_id='user-2', username='alice', created_at='2026-10-28T01:00:00Z', expires_at='2026-10-29T01:00:00Z', is_current=False)]
        await self.page.route(self.origin + '/api/**', self.respond)

    async def asyncTearDown(self):
        await self.context.close()
        await self.browser.close()
        await self.playwright.stop()
        self.assertEqual(self.errors, [])

    def collection(self, rows, query):
        page = int(query.get('page', ['1'])[0])
        page_size = int(query.get('page_size', ['20'])[0])
        return {'items': rows[(page - 1) * page_size:page * page_size], 'total': len(rows), 'page': page, 'page_size': page_size}

    async def respond(self, route):
        request = route.request
        path = urlparse(request.url).path
        query = parse_qs(urlparse(request.url).query)
        method = request.method
        self.requests.append((method, path, query, request.post_data_json if request.post_data else None))
        status = 200
        if path == '/api/auth/me':
            body = self.user
        elif path == '/api/admin/users/options':
            body = {'departments': [], 'posts': [], 'roles': []}
        elif path == '/api/admin/users':
            if method == 'POST':
                self.users.append({**request.post_data_json, 'id': 'new-user', 'version': 1})
                body = {key: value for key, value in self.users[-1].items() if key != 'password'}
                status = 201
            else:
                body = self.collection([{key: value for key, value in user.items() if key != 'password'} for user in self.users], query)
        elif path == '/api/admin/permissions':
            body = {'items': [dict(code='system:notices:read', label='查看公告', group='系统管理 · 公告'), dict(code='system:notices:write', label='管理公告', group='系统管理 · 公告'), dict(code='ai:models:write', label='维护模型与凭据', group='AI平台 · 模型管理')]}
        elif path == '/api/admin/roles':
            if method == 'POST':
                self.roles.append({**request.post_data_json, 'id': 'role-new', 'code': 'role_0123456789abcdef', 'version': 1})
                body = self.roles[-1]
                status = 201
            else:
                body = self.collection(self.roles, query)
        elif path == '/api/admin/roles/role-1' and method == 'PATCH':
            self.roles[0].update({key: value for key, value in request.post_data_json.items() if key != 'expected_version'})
            self.roles[0]['version'] += 1
            body = self.roles[0]
        elif path == '/api/admin/departments':
            body = self.collection(self.departments, query)
        elif path == '/api/admin/configs':
            body = self.collection(self.configs, query)
        elif path == '/api/admin/configs/config-1' and method == 'PATCH':
            self.configs[0].update({key: value for key, value in request.post_data_json.items() if key != 'expected_version'})
            self.configs[0]['version'] += 1
            body = self.configs[0]
        elif path == '/api/admin/users/user-2' and method == 'PATCH':
            payload = request.post_data_json
            self.users[1].update({key: value for key, value in payload.items() if key != 'expected_version'})
            self.users[1]['version'] += 1
            body = self.users[1]
        elif path == '/api/admin/menus':
            if query.get('navigation') == ['true']:
                definitions = [('users', '用户管理', 'users'), ('roles', '角色权限', 'roles'), ('departments', '部门管理', 'departments'), ('posts', '岗位管理', 'posts'), ('menus', '菜单管理', 'menus'), ('dictionaries', '字典管理', 'dictionaries'), ('configs', '参数管理', 'configs'), ('storage', '存储管理', 'storage'), ('notices', '通知公告', 'notices'), ('login-logs', '登录日志', 'logs'), ('operation-logs', '操作日志', 'logs'), ('sessions', '在线会话', 'sessions'), ('monitor', '系统状态', 'monitor')]
                navigation = []
                for index, (slug, label, area) in enumerate(definitions):
                    prefix = 'ai' if area == 'storage' else 'system'
                    if not self.user['is_admin'] and f'{prefix}:{area}:read' not in self.user['permissions'] and not (area == 'storage' and 'ai:storage:write' in self.user['permissions']):
                        continue
                    row = next((menu for menu in self.menus if menu['path'] == '/admin/' + slug), None)
                    if row and not row['is_visible']:
                        continue
                    navigation.append({key: value for key, value in (row or dict(name=label, path='/admin/' + slug, permission=f'{prefix}:{area}:read', sort_order=index * 10, is_visible=True)).items() if key in ['name', 'path', 'permission', 'sort_order', 'is_visible']})
                body = self.collection(navigation, query)
            else:
                body = self.collection(self.menus, query)
        elif path == '/api/admin/menus/menu-1' and method == 'PATCH':
            self.menus[0].update({key: value for key, value in request.post_data_json.items() if key != 'expected_version'})
            self.menus[0]['version'] += 1
            body = self.menus[0]
        elif path == '/api/admin/posts':
            if method == 'POST':
                self.posts.append({**request.post_data_json, 'id': 'post-1', 'version': 1})
                body = self.posts[-1]
                status = 201
            else:
                body = self.collection(self.posts, query)
        elif path == '/api/admin/dictionaries':
            body = self.collection(self.dictionaries, query)
        elif path == '/api/admin/dictionaries/dict-1/items':
            body = self.collection(self.dictionary_items, query)
        elif path == '/api/admin/dictionaries/dict-1/items/item-1' and method == 'DELETE':
            self.dictionary_items = []
            status, body = 204, None
        elif path == '/api/admin/notices':
            body = self.collection(self.notices, query)
        elif path == '/api/admin/notices/notice-1' and method == 'PATCH':
            self.notices[0].update({key: value for key, value in request.post_data_json.items() if key != 'expected_version'})
            self.notices[0]['version'] += 1
            body = self.notices[0]
        elif path == '/api/admin/sessions':
            body = self.collection(self.sessions, query)
        elif path == '/api/admin/sessions/session-other' and method == 'DELETE':
            self.sessions.pop()
            status, body = 204, None
        elif path == '/api/admin/logs':
            body = self.collection([dict(id='log-1', kind=query.get('kind', ['operation'])[0], action='admin.user.update', username='admin', resource_type='user', resource_id='user-2', outcome='success', message='用户已更新', created_at='2026-10-28T01:00:00Z')], query)
        elif path == '/api/admin/monitor':
            body = {'platform': {'python_version': '3.13.0', 'system': 'Linux', 'cpu_count': 8, 'rss_bytes': 1048576, 'disk_total_bytes': 10737418240, 'disk_used_bytes': 1073741824}, 'database': {'status': 'ok', 'version': 'PostgreSQL 17', 'size_bytes': 10485760, 'connections': 3}, 'redis': {'status': 'ok', 'used_memory_bytes': 1048576, 'connected_clients': 2, 'uptime_seconds': 60, 'db_keys': 0}, 'worker': {'status': 'unavailable', 'heartbeat_age_seconds': None}, 'schema_version': '0010'}
        elif path in ['/api/models', '/api/chat/conversations', '/api/knowledge/available']:
            body = []
        elif path == '/api/admin/notices/published':
            body = self.collection([], query)
        else:
            status, body = 404, {'error': {'code': 'fixture_missing', 'message': path}}
        if status == 204:
            await route.fulfill(status=204)
        else:
            await route.fulfill(status=status, json=body)

    async def row_menu(self, row, command):
        await row.get_by_role('button', name='更多操作', exact=True).click()
        await self.page.get_by_role('menuitem', name=command, exact=True).click()

    async def select_value(self, field, label):
        await field.click()
        await self.page.locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden)').get_by_title(label, exact=True).click()

    async def open(self, page):
        await self.page.goto(self.origin + '/admin/' + page)
        await expect(self.page.locator('.admin-console')).to_be_visible(timeout=15000)

    async def test_permission_filter_allows_delegated_reader_without_write_controls(self):
        self.user = {'id': 'self', 'username': 'reader', 'is_admin': False, 'permissions': ['system:departments:read']}
        await self.page.goto(self.origin + '/admin')
        await expect(self.page).to_have_url(self.origin + '/admin/departments')
        await expect(self.page.get_by_role('heading', name='部门管理', exact=True)).to_be_visible()
        await expect(self.page.locator('.admin-navigation a')).to_have_count(1)
        await expect(self.page.get_by_role('button', name='新建部门', exact=True)).to_have_count(0)
        self.assertFalse(any(path == '/api/admin/users' for _, path, _, _ in self.requests))
        await self.page.goto(self.origin + '/admin/users')
        await expect(self.page.get_by_text('当前账号没有可用的管理入口。', exact=True)).to_be_visible()
        self.assertFalse(any(path == '/api/admin/users' for _, path, _, _ in self.requests))

    async def test_conflicting_user_edit_preserves_inputs_and_expected_version(self):
        async def conflict(route):
            self.requests.append(('PATCH', '/api/admin/users/user-2', {}, route.request.post_data_json))
            await route.fulfill(status=409, json={'error': {'code': 'version_conflict', 'message': '用户已被修改'}})
        await self.page.route(self.origin + '/api/admin/users/user-2', conflict)
        await self.open('users')
        await self.page.get_by_role('row').filter(has_text='小艾').get_by_role('button', name='编辑', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        await dialog.get_by_label('显示名称', exact=True).fill('未保存输入')
        await dialog.get_by_role('button', name=re.compile(r'^保\s*存$')).click()
        await expect(dialog.get_by_text(re.compile('当前输入已保留'))).to_be_visible()
        await expect(dialog.get_by_label('显示名称', exact=True)).to_have_value('未保存输入')
        payload = [body for method, path, _, body in self.requests if method == 'PATCH' and path.endswith('/user-2')][-1]
        self.assertEqual(payload['expected_version'], 7)
        self.assertNotIn('password', payload)
        self.assertNotIn('username', payload)

    async def test_password_requires_twelve_characters_and_is_cleared_when_closed(self):
        await self.open('users')
        row = self.page.get_by_role('row').filter(has_text='小艾')
        await self.row_menu(row, '重置密码')
        dialog = self.page.get_by_role('dialog')
        await dialog.get_by_label('新密码', exact=True).fill('short')
        await dialog.get_by_role('button', name='重置密码', exact=True).click()
        await expect(dialog.get_by_text('新密码至少需要 12 位', exact=True)).to_be_visible()
        self.assertFalse(any(path.endswith('/password') for _, path, _, _ in self.requests))
        await dialog.get_by_role('button', name=re.compile(r'^取\s*消$')).click()
        await self.row_menu(row, '重置密码')
        await expect(dialog.get_by_label('新密码', exact=True)).to_have_value('')
        await dialog.get_by_role('button',name=re.compile(r'^取\s*消$')).click()
        await expect(dialog).to_be_hidden()
        await self.page.get_by_role('row').filter(has_text='管理员').get_by_role('button', name='更多操作', exact=True).click()
        await expect(self.page.get_by_role('menuitem', name='删除', exact=True)).to_have_attribute('aria-disabled','true')

    async def test_duplicate_create_is_blocked_while_request_is_pending(self):
        requested, release = asyncio.Event(), asyncio.Event()
        async def delayed(route):
            if route.request.method != 'POST':
                return await self.respond(route)
            self.requests.append(('POST', '/api/admin/roles', {}, route.request.post_data_json))
            requested.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await route.fulfill(status=201, json={**route.request.post_data_json, 'id': 'role-new', 'code': 'role_duplicate', 'version': 1})
        await self.page.route(self.origin + '/api/admin/roles*', delayed)
        try:
            await self.open('roles')
            await self.page.get_by_role('button', name='新建角色', exact=True).click()
            dialog = self.page.get_by_role('dialog')
            await dialog.get_by_label('角色名称', exact=True).fill('工程团队')
            save = dialog.get_by_role('button', name=re.compile(r'^保\s*存$'))
            await save.click()
            await requested.wait()
            await dialog.locator('form').evaluate('(form) => form.dispatchEvent(new Event("submit", {cancelable: true, bubbles: true}))')
            self.assertEqual(sum(method == 'POST' and path == '/api/admin/roles' for method, path, _, _ in self.requests), 1)
            release.set()
            await expect(dialog).not_to_be_visible()
        finally:
            release.set()

    async def test_retired_dictionary_link_returns_to_default_without_reading_dictionary_data(self):
        await self.open('dictionaries')
        await expect(self.page).to_have_url(self.origin+'/admin/users')
        await expect(self.page.get_by_role('heading',name='用户管理',exact=True)).to_be_visible()
        nav=self.page.get_by_role('navigation',name='后台管理导航',exact=True)
        await expect(nav.get_by_role('link')).to_have_count(5)
        await expect(nav.get_by_role('link',name='字典管理',exact=True)).to_have_count(0)
        self.assertFalse(any(path.startswith('/api/admin/dictionaries') for _,path,_,_ in self.requests))

    async def test_notice_publish_uses_version_and_content_is_rendered_as_text(self):
        await self.open('notices')
        await self.page.get_by_role('button', name='查看', exact=True).click()
        await expect(self.page.get_by_role('dialog').locator('.notice-content')).to_contain_text('<script>')
        self.assertIsNone(await self.page.evaluate('window.injected'))
        await self.page.get_by_role('dialog').get_by_role('button', name='Close', exact=True).click()
        await self.row_menu(self.page.get_by_role('row').filter(has_text='维护安排'), '编辑')
        dialog = self.page.get_by_role('dialog')
        await self.select_value(dialog.get_by_label('发布状态', exact=True), '发布')
        await dialog.get_by_role('button', name=re.compile(r'^保\s*存$')).click()
        await expect(dialog).not_to_be_visible()
        payload = next(body for method, path, _, body in self.requests if method == 'PATCH' and path.endswith('/notice-1'))
        self.assertEqual((payload['status'], payload['expected_version']), ('published', 1))

    async def test_current_session_is_protected_and_other_session_can_be_revoked(self):
        await self.open('sessions')
        await expect(self.page.get_by_role('row').filter(has_text='session-self').get_by_role('button', name='强制退出', exact=True)).to_be_disabled()
        await self.page.get_by_role('row').filter(has_text='session-other').get_by_role('button', name='强制退出', exact=True).click()
        await self.page.get_by_role('dialog').get_by_role('button', name='强制退出', exact=True).click()
        await expect(self.page.get_by_text('session-other', exact=True)).to_have_count(0)
        self.assertTrue(any(method == 'DELETE' and path.endswith('/session-other') and query.get('expected_version') == ['5'] for method, path, query, _ in self.requests))

    async def test_fixed_navigation_ignores_legacy_overrides_and_does_not_fetch_menus(self):
        self.menus = [dict(id='menu-1', version=2, parent_id=None, name='旧导航名称', path='/admin/users', permission='system:users:read', sort_order=999, is_visible=False)]
        await self.open('users')
        await expect(self.page.get_by_role('heading', name='用户管理', exact=True)).to_be_visible()
        nav=self.page.get_by_role('navigation',name='后台管理导航',exact=True)
        await expect(nav.get_by_role('link',name='用户管理',exact=True)).to_be_visible()
        await expect(nav.get_by_role('link',name='导航配置',exact=True)).to_have_count(0)
        await expect(nav.get_by_role('link',name='旧导航名称',exact=True)).to_have_count(0)
        await nav.get_by_role('link',name='角色权限',exact=True).click()
        await expect(self.page.get_by_text('运营员',exact=True)).to_be_visible()
        self.assertFalse(any(path=='/api/admin/menus' for _,path,_,_ in self.requests))

    async def test_legacy_navigation_link_redirects_to_first_permitted_page(self):
        self.user={'id':'self','username':'department-reader','is_admin':False,'permissions':['system:departments:read']}
        await self.page.goto(self.origin+'/admin/menus')
        await expect(self.page).to_have_url(self.origin+'/admin/departments')
        await expect(self.page.get_by_role('heading',name='部门管理',exact=True)).to_be_visible()
        self.assertFalse(any(path=='/api/admin/menus' for _,path,_,_ in self.requests))

    async def test_role_editor_sends_real_permission_codes_and_version(self):
        await self.open('roles')
        await self.page.get_by_role('button', name='编辑', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        await expect(dialog.get_by_label('角色编码', exact=True)).to_be_disabled()
        await dialog.get_by_label('管理公告', exact=True).check()
        await dialog.get_by_role('button', name=re.compile(r'^保\s*存$')).click()
        await expect(dialog).not_to_be_visible()
        payload = next(body for method, path, _, body in self.requests if method == 'PATCH' and path.endswith('/role-1'))
        self.assertEqual(payload['permissions'], ['system:notices:read', 'system:notices:write'])
        self.assertEqual(payload['expected_version'], 3)
        self.assertNotIn('code', payload)

    async def test_new_role_generates_code_on_server_and_accepts_ai_permissions(self):
        await self.open('roles')
        await self.page.get_by_role('button', name='新建角色', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        await expect(dialog.get_by_label('角色编码', exact=True)).to_have_count(0)
        await expect(dialog.get_by_text('AI平台 · 模型管理', exact=True)).to_be_visible()
        await dialog.get_by_label('角色名称', exact=True).fill('模型接入员')
        await dialog.get_by_label('维护模型与凭据', exact=True).check()
        await dialog.get_by_role('button', name=re.compile(r'^保\s*存$')).click()
        await expect(dialog).not_to_be_visible()
        payload = next(body for method, path, _, body in self.requests if method == 'POST' and path == '/api/admin/roles')
        self.assertNotIn('code', payload)
        self.assertEqual(payload['permissions'], ['ai:models:write'])
        await expect(self.page.get_by_role('row').filter(has_text='模型接入员')).to_contain_text('role_0123456789abcdef')

    async def test_department_tree_expands_and_parent_cannot_be_self_or_descendant(self):
        await self.open('departments')
        row = self.page.get_by_role('row').filter(has_text='总部')
        await row.locator('.ant-table-row-expand-icon').click()
        await expect(self.page.get_by_text('研发部', exact=True)).to_be_visible()
        await row.get_by_role('button', name='编辑', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        parent = dialog.get_by_label('上级部门', exact=True)
        await parent.click()
        dropdown=self.page.locator('.ant-select-dropdown:not(.ant-select-dropdown-hidden)')
        await expect(dropdown.get_by_title('顶级部门',exact=True)).to_be_visible()
        await expect(dropdown.get_by_title('总部',exact=True)).to_have_count(0)
        await expect(dropdown.get_by_title('研发部',exact=True)).to_have_count(0)
        await expect(dialog.get_by_label('部门名称', exact=True)).to_have_value('总部')

    async def test_config_updates_connected_notice_switch(self):
        await self.open('configs')
        switch=self.page.get_by_role('switch',name='在平台展示公告',exact=True)
        await expect(switch).to_be_enabled()
        await expect(switch).to_be_checked()
        await switch.click()
        await expect(switch).not_to_be_checked()
        payload=next(body for method,path,_,body in self.requests if method=='PATCH' and path.endswith('/config-1'))
        self.assertEqual((payload['value'],payload['expected_version']),('false',6))

    async def test_new_user_password_is_only_in_create_request(self):
        await self.open('users')
        await self.page.get_by_role('button', name='新建用户', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        await dialog.get_by_label('用户名', exact=True).fill('new-alice')
        await dialog.get_by_label('初始密码', exact=True).fill('New-Password-12345')
        await dialog.get_by_role('button', name=re.compile(r'^保\s*存$')).click()
        await expect(dialog).not_to_be_visible()
        payload = next(body for method, path, _, body in self.requests if method == 'POST' and path == '/api/admin/users')
        self.assertEqual(payload['password'], 'New-Password-12345')
        await self.page.get_by_role('button', name='新建用户', exact=True).click()
        await expect(dialog.get_by_label('初始密码', exact=True)).to_have_value('')
        self.assertNotIn('New-Password-12345', await self.page.evaluate('JSON.stringify({local: {...localStorage}, session: {...sessionStorage}})'))

    async def test_logs_filter_and_real_monitor_nullable_status(self):
        await self.open('login-logs')
        await self.select_value(self.page.get_by_label('操作结果',exact=True),'失败')
        await expect(self.page.get_by_text('用户已更新', exact=True)).to_be_visible()
        self.assertTrue(any(query.get('kind') == ['login'] and query.get('outcome') == ['failed'] for _, path, query, _ in self.requests if path == '/api/admin/logs'))
        await self.open('monitor')
        await expect(self.page.get_by_text('PostgreSQL 17', exact=True)).to_be_visible()
        worker = self.page.locator('.workspace-panel').filter(has=self.page.get_by_role('heading', name='Worker', exact=True))
        await expect(worker.get_by_text('不可用', exact=True)).to_have_count(2)
        await self.page.set_viewport_size({'width': 375, 'height': 812})
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
