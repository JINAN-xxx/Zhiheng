"""Storage read grants cannot reveal write controls or request the user directory."""
import re
from urllib.parse import urlparse
from playwright.async_api import expect
from files_regressions import FilesUIRegressions


class StoragePermissionRegressions(FilesUIRegressions):
    async def asyncSetUp(self):
        self.user = {'id': 'user-1', 'username': 'storage-reader', 'is_admin': False,
                     'permissions': ['ai:storage:enabled', 'ai:storage:read']}
        self.user_directory_reads = 0
        self.fail_identity = False
        await super().asyncSetUp()

    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path == '/api/auth/me':
            if self.fail_identity:
                return await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '身份确认暂不可用'}})
            return await route.fulfill(json=self.user)
        if path == '/api/files/users':
            self.user_directory_reads += 1
            if 'system:users:read' not in self.user['permissions'] and not self.user['is_admin']:
                return await route.fulfill(status=403, json={'error': {'code': 'forbidden', 'message': '用户目录无权限'}})
        return await super().respond(route)

    async def open_storage(self):
        await self.page.goto(self.origin + '/admin/storage')
        await expect(self.page.get_by_role('button', name='查看文件：业务报告', exact=True)).to_be_visible()

    async def test_reader_has_no_bulk_writes_or_user_directory_request(self):
        await self.open_storage()
        await expect(self.page.get_by_role('checkbox', name='选择文件：业务报告', exact=True)).to_be_disabled()
        await expect(self.page.get_by_role('button', name='更多批量操作', exact=True)).to_have_count(0)
        await expect(self.page.get_by_text('用户目录无权限', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='查看文件：业务报告', exact=True).click()
        await expect(self.page.get_by_role('button', name='编辑文件信息', exact=True)).to_have_count(0)
        self.assertEqual(self.user_directory_reads, 0)
        self.assertEqual(self.state_writes, [])

    async def test_delete_grant_does_not_reveal_acl_or_transfer_and_revocation_blocks_confirmation(self):
        self.user['permissions'].append('ai:storage:delete')
        await self.open_storage()
        await self.page.get_by_role('checkbox', name='选择文件：业务报告', exact=True).check()
        await expect(self.page.get_by_role('button', name='更多批量操作', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='移入回收站', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        await expect(dialog).to_be_visible()
        self.user['permissions'].remove('ai:storage:delete')
        await dialog.get_by_role('button', name=re.compile(r'确\s*定')).click()
        await expect(self.page.get_by_role('checkbox', name='选择文件：业务报告', exact=True)).to_be_disabled()
        self.assertEqual(self.state_writes, [])
        self.assertEqual(self.user_directory_reads, 0)

    async def test_acl_grant_is_independent_and_revocation_blocks_save(self):
        self.user['permissions'].append('ai:storage:acl')
        await self.open_storage()
        await self.page.get_by_role('checkbox', name='选择文件：业务报告', exact=True).check()
        await expect(self.page.get_by_role('button', name='移入回收站', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='更多批量操作', exact=True).click()
        await expect(self.page.get_by_role('menuitem').filter(has_text='转移')).to_have_count(0)
        await self.page.get_by_role('menuitem').filter(has_text='授权').click()
        drawer = self.page.locator('.ant-drawer-open')
        await expect(drawer.get_by_role('button', name=re.compile(r'保\s*存'))).to_be_enabled()
        self.user['permissions'].remove('ai:storage:acl')
        await drawer.get_by_role('button', name=re.compile(r'保\s*存')).click()
        await expect(drawer).to_have_count(0)
        self.assertEqual(self.file_writes, [])
        self.assertEqual(self.user_directory_reads, 0)

    async def test_transfer_does_not_request_user_directory_without_permission(self):
        self.user['permissions'].append('ai:storage:transfer')
        await self.open_storage()
        await self.page.get_by_role('checkbox', name='选择文件：业务报告', exact=True).check()
        await self.page.get_by_role('button', name='更多批量操作', exact=True).click()
        await self.page.get_by_role('menuitem').filter(has_text='转移').click()
        await expect(self.page.locator('.ant-drawer-open')).to_be_visible()
        await expect(self.page.get_by_text('当前账号没有用户目录查看权限，无法搜索接收人。', exact=True)).to_be_visible()
        self.assertEqual(self.user_directory_reads, 0)

    async def test_admin_does_not_gain_ownership_of_public_foreign_file(self):
        self.user['is_admin'] = True
        self.file_rows[0].update(owner_id='another-user', access='public')
        await self.open_storage()
        await expect(self.page.get_by_role('checkbox', name='选择文件：业务报告', exact=True)).to_be_disabled()
        await expect(self.page.get_by_role('button', name='删除文件：业务报告', exact=True)).to_have_count(0)

    async def test_identity_failure_blocks_transfer_with_a_visible_message(self):
        self.user['permissions'].append('ai:storage:transfer')
        await self.open_storage()
        await self.page.get_by_role('checkbox', name='选择文件：业务报告', exact=True).check()
        self.fail_identity = True
        await self.page.get_by_role('button', name='更多批量操作', exact=True).click()
        await self.page.get_by_role('menuitem').filter(has_text='转移').click()
        await expect(self.page.get_by_text('身份确认暂不可用', exact=True)).to_be_visible()
        await expect(self.page.locator('.ant-drawer-open')).to_have_count(0)
        self.assertEqual(self.file_writes, [])

    async def test_file_type_and_business_source_use_existing_metadata(self):
        self.file_rows[0].update(purpose='work_artifact', original_filename='chart.png', mime='image/png')
        await self.open_storage()
        await expect(self.page.locator('.file-source')).to_have_text('工作空间成果')
        await expect(self.page.locator('.file-name .attachment-file-icon [data-icon="picture"]')).to_be_visible()
        await expect(self.page.get_by_role('button', name='返回知识库', exact=True)).to_have_count(0)
