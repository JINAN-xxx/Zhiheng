"""AI capabilities preserve basic use and restrict platform management controls."""
import unittest
from urllib.parse import parse_qs, urlparse

from playwright.async_api import expect
from developer_regressions import DeveloperBoundary
from files_regressions import FILE


class AIPermissionsRegressions(DeveloperBoundary):
    async def asyncSetUp(self):
        self.permissions = []
        self.requests = []
        self.file_rows = []
        await super().asyncSetUp()

    async def respond(self, route):
        request = route.request
        path = urlparse(request.url).path
        self.requests.append((request.method, path, parse_qs(urlparse(request.url).query)))
        if path == '/api/auth/me':
            return await route.fulfill(json={'id': 'user-1', 'username': 'member', 'is_admin': False, 'permissions': self.permissions})
        if path == '/api/models/connections':
            if not any(code in self.permissions for code in ['ai:models:read', 'ai:models:write']):
                return await route.fulfill(status=403, json={'error': {'code': 'forbidden', 'message': '无模型管理权限'}})
            return await route.fulfill(json=[{'id': 'connection-1', 'name': '团队服务', 'provider': 'openai_compatible', 'base_url': 'http://provider.example/v1', 'has_api_key': True, 'enabled': True}])
        if path == '/api/billing/models/model-1/rates' and request.method == 'GET':
            return await route.fulfill(json=[])
        if path == '/api/models/model-1/rate-cards':
            return await route.fulfill(json=[])
        if path == '/api/models/model-1/test' and request.method == 'POST':
            return await route.fulfill(status=201, json={'id': 'test-run', 'resource_type': 'connection_test', 'resource_id': 'model-1', 'status': 'completed', 'attempt_id': 'attempt-1', 'attempt_no': 1, 'revision': 1, 'projection_seq': 1, 'text': '连接成功', 'error': None, 'created_at': '2026-10-23T00:00:00Z', 'usage': {'input_tokens': 1, 'output_tokens': 1, 'status': 'known'}})
        skill = {'id': 'skill-1', 'slug': 'example', 'name': '示例技能', 'description': '基础使用技能', 'package_version': '1.0.0', 'version': 1, 'status': 'enabled', 'source': 'upload', 'revision_id': 'revision-1', 'content_hash': 'hash-1', 'file_count': 1, 'total_bytes': 123, 'created_at': '2026-10-23T00:00:00Z', 'updated_at': '2026-10-23T00:00:00Z'}
        if path == '/api/skills':
            return await route.fulfill(json={'items': [skill], 'total': 1, 'page': 1, 'page_size': 12})
        if path in ['/api/skills/skill-1', '/api/skills/skill-1/status']:
            return await route.fulfill(json={**skill, 'status': 'disabled'} if request.method == 'PATCH' else skill)
        if path == '/api/skills/skill-1/content':
            return await route.fulfill(json={'content': '技能正文'})
        if path == '/api/knowledge/bases':
            return await route.fulfill(json=[])
        if path == '/api/knowledge/capabilities':
            return await route.fulfill(json={'milvus_configured': True, 'milvus_available': True, 'native_formats': ['txt'], 'graph_available': False, 'mineru_available': False})
        if path in ['/api/files/policies', '/api/files/reconcile/issues', '/api/files/jobs', '/api/files/users', '/api/files/reminders']:
            return await route.fulfill(json=[])
        if path == '/api/files/reconcile/stats':
            return await route.fulfill(json={'open': 0, 'resolved': 0, 'ignored': 0, 'quarantined': 0})
        if path == '/api/files':
            return await route.fulfill(json={'items': self.file_rows, 'total': len(self.file_rows), 'page': 1, 'page_size': 20})
        if path == '/api/files/file-1':
            return await route.fulfill(json=self.file_rows[0])
        if path == '/api/files/stats':
            return await route.fulfill(json={'total_count': 0, 'total_bytes': 0, 'active_count': 0, 'deleted_count': 0, 'public_count': 0, 'public_bytes': 0, 'private_bytes': 0, 'expired_count': 0, 'retention_expiring_count': 0, 'acl_expiring_count': 0})
        return await super().respond(route)

    async def assert_no_model_management(self):
        for label in ['接入模型', '新增凭据', '编辑模型', '费率配置', '测试连接', '更多操作']:
            await expect(self.page.get_by_role('button', name=label, exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('tab', name='凭据管理', exact=True)).to_have_count(0)

    async def open_models(self):
        await self.page.goto(self.origin + '/models')
        await expect(self.page).to_have_url(self.origin + '/models')
        await expect(self.page.get_by_role('heading', name='模型目录', exact=True)).to_be_visible()
        mobile_menu = self.page.get_by_role('button', name='打开主菜单', exact=True)
        is_mobile = await mobile_menu.count() > 0
        if is_mobile:
            await mobile_menu.click()
        link = self.page.get_by_role('navigation', name='主导航', exact=True).get_by_role('link', name='模型目录', exact=True)
        await expect(link).to_be_visible()
        await expect(link).to_have_attribute('href', '/models')
        if is_mobile:
            await link.click()
            await expect(link).not_to_be_visible()

    async def open_storage_tool(self, label):
        await self.page.get_by_role('button', name='高级管理', exact=True).click()
        await self.page.get_by_role('menuitem', name=label, exact=True).click()

    async def test_basic_user_sees_published_models_without_management_requests_on_desktop_and_mobile(self):
        for width in [1440, 390]:
            with self.subTest(width=width):
                await self.page.set_viewport_size({'width': width, 'height': 900})
                await self.open_models()
                await expect(self.page.get_by_text('日常助手', exact=True)).to_be_visible()
                await self.assert_no_model_management()
        self.assertFalse(any(path == '/api/models/connections' for _, path, _ in self.requests))
        self.assertFalse(any(query.get('include_disabled') == ['true'] for _, path, query in self.requests if path == '/api/models'))
        self.assertFalse(any('rate-cards' in path for _, path, _ in self.requests))

    async def test_model_reader_sees_credentials_without_mutation_or_test_controls(self):
        self.permissions = ['ai:models:read']
        await self.open_models()
        await expect(self.page.get_by_text('日常助手', exact=True)).to_be_visible()
        for label in ['接入模型', '新增凭据', '编辑模型', '费率配置', '测试连接', '更多操作']:
            await expect(self.page.get_by_role('button', name=label, exact=True)).to_have_count(0)
        await self.page.get_by_role('tab', name='凭据管理', exact=True).click()
        await expect(self.page.get_by_role('tabpanel', name='凭据管理', exact=True).get_by_text('团队服务', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='编辑凭据', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button', name='更多凭据操作', exact=True)).to_have_count(0)

    async def test_rates_reader_can_view_history_but_cannot_create_a_price(self):
        self.permissions = ['ai:rates:read']
        await self.open_models()
        await self.page.get_by_role('button', name='更多操作', exact=True).click()
        await self.page.get_by_role('menuitem', name='费率配置', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='费率配置', exact=True)
        await expect(dialog.get_by_role('tab', name='积分计价', exact=True)).to_have_attribute('aria-selected', 'true')
        await expect(dialog.get_by_role('button', name='创建积分价格版本', exact=True)).to_have_count(0)
        await expect(dialog.get_by_label('备注', exact=True)).to_have_count(0)
        await dialog.get_by_role('tab', name='历史金额费率', exact=True).click()
        await expect(dialog.get_by_text('历史费率版本', exact=True)).to_be_visible()
        await expect(dialog.get_by_role('button', name='创建价格版本', exact=True)).to_have_count(0)
        await expect(dialog.get_by_label('输入令牌单价', exact=True)).to_have_count(0)
        self.assertFalse(any(method != 'GET' for method, _, _ in self.requests))

    async def test_model_writer_can_onboard_without_receiving_test_or_rate_permissions(self):
        self.permissions = ['ai:models:write']
        await self.open_models()
        await expect(self.page.get_by_role('button', name='接入模型', exact=True)).to_be_enabled()
        await self.page.get_by_role('button', name='更多操作', exact=True).click()
        await expect(self.page.get_by_role('menuitem', name='编辑模型', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='测试连接', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button', name='费率配置', exact=True)).to_have_count(0)

    async def test_storage_reader_sees_policy_and_reconcile_without_write_controls(self):
        self.permissions = ['ai:storage:read']
        await self.page.goto(self.origin + '/knowledge')
        await self.page.get_by_role('button', name='存储管理', exact=True).click()
        await expect(self.page.get_by_role('heading', name='存储管理', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='用户菜单', exact=True).click()
        await expect(self.page.get_by_role('link', name='后台管理', exact=True)).to_have_count(0)
        await self.page.keyboard.press('Escape')
        await self.open_storage_tool('保留策略')
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content').filter(has_text='文件保留策略')
        await expect(drawer).to_be_visible()
        await expect(drawer.get_by_role('button', name='新增策略', exact=True)).to_have_count(0)
        await drawer.get_by_role('button', name='Close', exact=True).click()
        await self.open_storage_tool('存储对账')
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content').filter(has_text='文件存储对账')
        await expect(drawer).to_be_visible()
        await expect(drawer.get_by_role('button', name='开始对账', exact=True)).to_have_count(0)
        self.assertFalse(any(method != 'GET' for method, _, _ in self.requests))

    async def test_model_tester_can_test_without_model_or_rate_management(self):
        self.permissions = ['ai:models:test']
        await self.open_models()
        await self.page.get_by_role('button', name='测试连接', exact=True).click()
        await expect(self.page.locator('.test-result pre')).to_have_text('连接成功')
        for label in ['接入模型', '编辑模型', '费率配置']:
            await expect(self.page.get_by_role('button', name=label, exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('tab', name='凭据管理', exact=True)).to_have_count(0)
        self.assertTrue(any(method == 'POST' and path.endswith('/test') for method, path, _ in self.requests))
        self.assertFalse(any(path == '/api/models/connections' for _, path, _ in self.requests))

    async def test_skill_basic_user_can_read_but_has_no_upload_or_mutation_controls(self):
        # Keep the historical node ID while checking personal management without platform permissions.
        await self.page.goto(self.origin + '/skills?action=upload')
        await expect(self.page.get_by_text('示例技能', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='上传技能', exact=True)).to_be_enabled()
        dialog = self.page.get_by_role('dialog', name='上传技能', exact=True)
        await expect(dialog).to_be_visible()
        await dialog.get_by_role('button', name='Close', exact=True).click()
        await expect(dialog).not_to_be_visible()
        await self.page.get_by_role('button', name='查看详情', exact=True).click()
        await expect(self.page.get_by_text('技能正文', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='停用技能', exact=True)).to_be_enabled()
        await expect(self.page.get_by_role('button', name='删除技能', exact=True)).to_be_enabled()
        self.assertFalse(any(method != 'GET' for method, _, _ in self.requests))
        await self.page.get_by_role('button', name='停用技能', exact=True).click()
        await expect(self.page.get_by_role('button', name='重新启用', exact=True)).to_be_visible()
        self.assertTrue(any(method == 'PATCH' and path.endswith('/status') for method, path, _ in self.requests))

    async def test_skill_writer_has_upload_and_status_controls_without_admin_identity(self):
        self.permissions = ['ai:skills:write']
        await self.page.goto(self.origin + '/skills')
        await expect(self.page.get_by_role('button', name='上传技能', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='查看详情', exact=True).click()
        await expect(self.page.get_by_text('技能正文', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='停用技能', exact=True).click()
        await expect(self.page.get_by_role('button', name='重新启用', exact=True)).to_be_visible()
        self.assertTrue(any(method == 'PATCH' and path.endswith('/status') for method, path, _ in self.requests))

    async def test_storage_writer_can_open_new_and_legacy_paths_without_system_admin_access(self):
        self.permissions = ['ai:storage:write']
        for path in ['/knowledge/storage', '/files', '/system/file']:
            with self.subTest(path=path):
                await self.page.goto(self.origin + path)
                await expect(self.page.get_by_role('heading', name='存储管理', exact=True)).to_be_visible()
                await self.page.get_by_role('button', name='用户菜单', exact=True).click()
                await expect(self.page.get_by_role('link', name='后台管理', exact=True)).to_have_count(0)
                await self.page.keyboard.press('Escape')
        await self.open_storage_tool('存储对账')
        await expect(self.page.get_by_role('button', name='开始对账', exact=True)).to_be_visible()

    async def test_storage_writer_cannot_manage_another_owners_visible_file(self):
        self.permissions = ['ai:storage:write']
        self.file_rows = [{**FILE, 'owner_id': 'another-user', 'access': 'public'}]
        await self.page.goto(self.origin + '/knowledge/storage')
        await expect(self.page.get_by_role('button', name='查看文件：业务报告', exact=True)).to_be_visible()
        for label in ['访问权限：业务报告', '转移文件：业务报告', '删除文件：业务报告']:
            await expect(self.page.get_by_role('button', name=label, exact=True)).to_have_count(0)
            await expect(self.page.get_by_role('menuitem', name=label, exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button', name='更多操作：业务报告', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('checkbox', name='选择文件：业务报告', exact=True)).to_be_disabled()
        await self.page.get_by_role('button', name='查看文件：业务报告', exact=True).click()
        await expect(self.page.get_by_role('button', name='编辑文件信息', exact=True)).to_have_count(0)

    async def test_storage_reader_keeps_owner_management_for_own_file(self):
        self.permissions = ['ai:storage:read']
        self.file_rows = [dict(FILE)]
        await self.page.goto(self.origin + '/knowledge/storage')
        await self.page.get_by_role('button', name='更多操作：业务报告', exact=True).click()
        await expect(self.page.get_by_role('menuitem', name='访问权限：业务报告', exact=True)).to_be_enabled()
        await expect(self.page.get_by_role('menuitem', name='转移文件：业务报告', exact=True)).to_be_enabled()
        await self.page.keyboard.press('Escape')
        await expect(self.page.get_by_role('button', name='删除文件：业务报告', exact=True)).to_be_enabled()
        await self.page.get_by_role('button', name='查看文件：业务报告', exact=True).click()
        await expect(self.page.get_by_role('button', name='编辑文件信息', exact=True)).to_be_visible()


if __name__ == '__main__':
    unittest.main(verbosity=2)
