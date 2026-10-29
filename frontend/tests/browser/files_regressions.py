"""Original managed-file UI at the new cookie/DTO boundaries."""
import unittest
from urllib.parse import urlparse
import json
import re

from playwright.async_api import expect
from developer_regressions import DeveloperBoundary


FILE = {
    'id': 'file-1', 'original_filename': 'report.txt', 'display_name': '业务报告',
    'size': 12, 'mime': 'text/plain', 'sha256': 'a' * 64,
    'owner_id': 'user-1', 'uploader_id': 'user-1', 'access': 'private',
    'uploader_access_enabled': True, 'status': 'active', 'acl_version': 1,
    'version': 1, 'expires_at': None, 'deleted_at': None, 'retention_until': None,
    'created_at': '2026-10-23T00:00:00Z', 'updated_at': '2026-10-23T00:00:00Z',
    'reference_count': 0, 'source_file_id': None, 'parser_version': None,
    'storage_status': 'normal', 'acl_entry_count': 0, 'acl_nearest_expire_at': None,
}


class FilesUIRegressions(DeveloperBoundary):
    async def asyncSetUp(self):
        self.file_rows = [dict(FILE)]
        self.file_writes = []
        self.file_requests = []
        await super().asyncSetUp()

    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path.startswith('/api/files'):
            self.file_requests.append(path)
        if path == '/api/files':
            await route.fulfill(json={'items': self.file_rows, 'total': len(self.file_rows), 'page': 1, 'page_size': 20})
        elif path == '/api/files/stats':
            await route.fulfill(json={'total_count': 0, 'total_bytes': 0, 'active_count': 0, 'deleted_count': 0, 'public_count': 0, 'public_bytes': 0, 'private_bytes': 0, 'expired_count': 0, 'retention_expiring_count': 0, 'acl_expiring_count': 0})
        elif path == '/api/files/users':
            await route.fulfill(json=[{'id': 'user-1', 'username': 'admin'}])
        elif path == '/api/files/file-1':
            if route.request.method == 'PATCH':
                self.file_writes.append(json.loads(route.request.post_data))
                self.file_rows[0].update(json.loads(route.request.post_data))
            await route.fulfill(json=self.file_rows[0])
        elif path == '/api/files/file-1/acl':
            if route.request.method == 'PATCH':
                self.file_writes.append(json.loads(route.request.post_data))
            await route.fulfill(json={'file': self.file_rows[0], 'entries': [], 'builtin_permissions': [{'source': 'owner', 'enabled': True, 'deny_overridable': False}]})
        elif path == '/api/files/policies':
            await route.fulfill(json=[])
        elif path == '/api/files/reminders':
            await route.fulfill(json=[])
        elif path == '/api/files/jobs' or path == '/api/files/reconcile/issues':
            await route.fulfill(json=[])
        elif path == '/api/files/reconcile/stats':
            await route.fulfill(json={'open': 0, 'resolved': 0, 'ignored': 0, 'quarantined': 0})
        elif path == '/api/knowledge/bases':
            await route.fulfill(json=[])
        elif path == '/api/knowledge/capabilities':
            await route.fulfill(json={'milvus_configured': True, 'milvus_available': True, 'native_formats': ['txt', 'md', 'pdf', 'docx', 'xlsx'], 'graph_available': False, 'mineru_available': False})
        else:
            await super().respond(route)

    async def test_storage_is_reached_from_knowledge_and_returns_there(self):
        await self.page.goto(self.origin + '/knowledge')
        await self.page.get_by_role('button', name='存储管理', exact=True).click()
        await expect(self.page).to_have_url(self.origin + '/knowledge/storage')
        await expect(self.page.get_by_role('heading', name='存储管理', exact=True)).to_be_visible()
        navigation = self.page.get_by_role('navigation', name='主导航', exact=True)
        await expect(navigation.get_by_role('link', name='知识库', exact=True)).to_have_attribute('aria-current', 'page')
        await expect(navigation.get_by_role('link', name='文件管理', exact=True)).to_have_count(0)
        await expect(self.page.get_by_text('管理知识文档与对话附件的存储、权限及保留规则', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('文件概览', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='存储对账', exact=True)).to_be_visible()
        await expect(self.page.locator('input[type="file"]')).to_have_count(0)
        await expect(self.page.get_by_role('button', name='上传', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='返回知识库', exact=True).click()
        await expect(self.page).to_have_url(self.origin + '/knowledge')
        await expect(self.page.get_by_role('button', name='创建知识库', exact=True)).to_be_visible()

    async def test_old_file_links_keep_storage_tools_and_knowledge_selection(self):
        for path in ['/files', '/system/file']:
            with self.subTest(path=path):
                await self.page.goto(self.origin + path)
                await expect(self.page.get_by_role('heading', name='存储管理', exact=True)).to_be_visible()
                navigation = self.page.get_by_role('navigation', name='主导航', exact=True)
                await expect(navigation.get_by_role('link', name='知识库', exact=True)).to_have_attribute('aria-current', 'page')
                await expect(self.page.get_by_role('button', name='查看文件：业务报告', exact=True)).to_be_visible()

    async def test_members_cannot_enter_storage_using_new_or_old_links(self):
        await self.page.route(self.origin + '/api/auth/me', lambda route: route.fulfill(json={'id': 'member-1', 'username': 'member', 'is_admin': False}))
        await self.page.goto(self.origin + '/knowledge')
        await expect(self.page.get_by_role('heading', name='知识库', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='存储管理', exact=True)).to_have_count(0)
        for path in ['/knowledge/storage', '/files', '/system/file']:
            with self.subTest(path=path):
                await self.page.goto(self.origin + path)
                await expect(self.page).to_have_url(self.origin + '/knowledge')
        self.assertEqual(self.file_requests, [])

    async def test_mobile_knowledge_storage_round_trip_stays_on_screen(self):
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        await self.page.goto(self.origin + '/knowledge')
        await self.page.get_by_role('button', name='存储管理', exact=True).click()
        await expect(self.page.get_by_role('heading', name='存储管理', exact=True)).to_be_visible()
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        await self.page.get_by_role('button', name='返回知识库', exact=True).click()
        await expect(self.page.get_by_role('button', name='创建知识库', exact=True)).to_be_visible()

    async def test_original_acl_has_deny_expiry_and_real_builtin_rules(self):
        await self.page.goto(self.origin + '/files')
        await self.page.get_by_role('button', name='访问权限：业务报告', exact=True).click()
        await expect(self.page.get_by_text('内置权限', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('不可覆盖', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='添加授权', exact=True).click()
        await expect(self.page.get_by_text('有效期至', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('包含下级部门', exact=True)).to_have_count(0)

    async def test_metadata_can_edit_display_name_without_changing_storage_name(self):
        await self.page.goto(self.origin + '/files')
        await self.page.get_by_role('button', name='查看文件：业务报告', exact=True).click()
        await self.page.get_by_role('button', name='编辑文件信息', exact=True).click()
        await self.page.get_by_label('显示名称', exact=True).fill('重命名报告')
        await self.page.get_by_role('button', name=re.compile(r'保\s*存'), exact=True).click()
        await expect(self.page.get_by_text('重命名报告', exact=True).first).to_be_visible()
        self.assertEqual(self.file_writes[0]['expected_version'], 1)
        self.assertEqual(self.file_writes[0]['display_name'], '重命名报告')
        self.assertNotIn('original_filename', self.file_writes[0])

    async def test_derived_files_allow_inspection_but_no_independent_mutation(self):
        self.file_rows[0]['source_file_id'] = 'source-1'
        await self.page.goto(self.origin + '/files')
        await expect(self.page.get_by_role('button', name='查看文件：业务报告')).to_be_enabled()
        await expect(self.page.get_by_role('button', name='访问权限：业务报告')).to_be_disabled()
        await expect(self.page.get_by_role('button', name='删除文件：业务报告')).to_be_disabled()

    async def test_policies_reminders_and_reconcile_are_live_drawers(self):
        await self.page.goto(self.origin + '/files')
        await self.page.get_by_role('button', name='保留策略', exact=True).click()
        await expect(self.page.get_by_text('文件保留策略', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='Close', exact=True).click()
        await self.page.get_by_role('button', name='保留提醒', exact=True).click()
        await expect(self.page.get_by_text('文件保留提醒', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='Close', exact=True).click()
        await self.page.get_by_role('button', name='存储对账', exact=True).click()
        await expect(self.page.get_by_text('任务记录', exact=True)).to_be_visible()

    async def test_tablet_numeric_drawer_width_keeps_close_control_on_screen(self):
        await self.page.set_viewport_size({'width':768,'height':900})
        await self.page.goto(self.origin+'/files')
        await self.page.get_by_role('button',name='保留策略',exact=True).click()
        close=self.page.get_by_role('button',name='Close',exact=True)
        await expect(close).to_be_visible()
        await self.page.wait_for_timeout(400)
        box=await close.bounding_box()
        self.assertGreaterEqual(box['x'],0)
        self.assertLessEqual(box['x']+box['width'],768)
        await close.click()
        await expect(self.page.get_by_text('文件保留策略',exact=True)).not_to_be_visible()


if __name__ == '__main__':
    unittest.main(verbosity=2)
