"""Managed storage behavior through the real Vue UI and isolated HTTP fixtures."""
import asyncio
import contextlib
import os
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse
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
        self.file_queries = []
        self.state_writes = []
        self.file_jobs = []
        self.reconcile_issues = []
        await super().asyncSetUp()
        self.console_errors, self.request_failures = [], []
        self.page.on('console', lambda entry: self.console_errors.append(entry.text) if entry.type == 'error' else None)
        self.page.on('requestfailed', lambda request: self.request_failures.append({'url': request.url, 'failure': request.failure}))

    async def asyncTearDown(self):
        evidence = os.environ.get('STORAGE_UI_EVIDENCE')
        if evidence:
            destination = Path(evidence)
            destination.mkdir(parents=True, exist_ok=True)
            (destination / (self._testMethodName + '.json')).write_text(json.dumps({'requests': self.file_requests, 'queries': self.file_queries, 'writes': self.state_writes, 'console_errors': self.console_errors, 'request_failures': self.request_failures}, ensure_ascii=False, indent=2))
            with contextlib.suppress(Exception):
                await self.page.screenshot(path=str(destination / (self._testMethodName + '.png')), full_page=True)
        await super().asyncTearDown()

    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path.startswith('/api/files'):
            self.file_requests.append(path)
        if path == '/api/files':
            query = parse_qs(urlparse(route.request.url).query)
            self.file_queries.append(query)
            rows = self.matching_rows(query)
            page, size = int(query.get('page', ['1'])[0]), int(query.get('page_size', ['20'])[0])
            await route.fulfill(json={'items': rows[(page - 1) * size:page * size], 'total': len(rows), 'page': page, 'page_size': size})
        elif path == '/api/files/stats':
            rows = self.matching_rows(parse_qs(urlparse(route.request.url).query))
            await route.fulfill(json={'total_count': len(rows), 'total_bytes': sum(row['size'] for row in rows), 'active_count': sum(row['status'] == 'active' for row in rows), 'deleted_count': sum(row['status'] == 'deleted' for row in rows), 'public_count': 0, 'public_bytes': 0, 'private_bytes': sum(row['size'] for row in rows), 'expired_count': 0, 'retention_expiring_count': 0, 'acl_expiring_count': 0})
        elif path == '/api/files/users':
            await route.fulfill(json=[{'id': 'user-1', 'username': 'admin'}])
        elif path == '/api/files/file-1':
            if route.request.method == 'PATCH':
                self.file_writes.append(json.loads(route.request.post_data))
                self.file_rows[0].update(json.loads(route.request.post_data))
            elif route.request.method == 'DELETE':
                self.state_writes.append({'path': path, **route.request.post_data_json})
                self.file_rows[0]['status'] = 'deleted'
                self.file_rows[0]['version'] += 1
            await route.fulfill(json=self.file_rows[0])
        elif path == '/api/files/file-1/acl':
            if route.request.method == 'PATCH':
                self.file_writes.append(json.loads(route.request.post_data))
            await route.fulfill(json={'file': self.file_rows[0], 'entries': [], 'builtin_permissions': [{'source': 'owner', 'enabled': True, 'deny_overridable': False}]})
        elif path == '/api/files/file-1/download':
            await route.fulfill(body='fixture report', content_type='text/plain')
        elif path == '/api/files/file-1/references':
            await route.fulfill(json=[])
        elif path == '/api/files/file-1/audit/page':
            await route.fulfill(json={'items': [], 'total': 0})
        elif re.fullmatch('/api/files/[^/]+/(delete|restore|purge)', path):
            payload = route.request.post_data_json
            self.state_writes.append({'path': path, **payload})
            row = next(row for row in self.file_rows if row['id'] == path.split('/')[-2])
            row['status'] = {'delete': 'deleted', 'restore': 'active', 'purge': 'purged'}[path.split('/')[-1]]
            row['version'] += 1
            await route.fulfill(json=row)
        elif path == '/api/files/policies':
            await route.fulfill(json=[])
        elif path == '/api/files/reminders':
            await route.fulfill(json=[])
        elif path == '/api/files/jobs':
            await route.fulfill(json=self.file_jobs)
        elif path == '/api/files/reconcile/issues':
            await route.fulfill(json=self.reconcile_issues)
        elif path == '/api/files/reconcile/stats':
            await route.fulfill(json={'open': 0, 'resolved': 0, 'ignored': 0, 'quarantined': 0})
        elif path == '/api/knowledge/bases':
            await route.fulfill(json=[])
        elif path == '/api/knowledge/capabilities':
            await route.fulfill(json={'milvus_configured': True, 'milvus_available': True, 'native_formats': ['txt', 'md', 'pdf', 'docx', 'xlsx'], 'graph_available': False, 'mineru_available': False})
        else:
            await super().respond(route)

    def matching_rows(self, query):
        status = query.get('status', ['active'])[0]
        search = query.get('search', [''])[0]
        mime = query.get('mime', [''])[0]
        return [row for row in self.file_rows if (status == 'all' or row['status'] == status) and search in row['original_filename'] and (not mime or mime == row['mime'])]

    async def advanced(self, label):
        await self.page.get_by_role('button', name='高级管理', exact=True).click()
        await self.page.get_by_role('menuitem', name=label, exact=True).click()

    async def more(self, label, filename='业务报告'):
        await self.page.get_by_role('button', name=f'更多操作：{filename}', exact=True).click()
        return self.page.get_by_role('menuitem', name=f'{label}：{filename}', exact=True)

    async def test_storage_is_reached_from_admin_and_returns_to_knowledge(self):
        await self.page.route(self.origin + '/api/admin/users?*', lambda route:route.fulfill(json={'items': [], 'total': 0, 'page': 1, 'page_size': 20}))
        await self.page.goto(self.origin + '/admin')
        await self.page.get_by_role('navigation', name='后台管理导航', exact=True).get_by_role('link', name='存储管理', exact=True).click()
        await expect(self.page).to_have_url(self.origin + '/admin/storage')
        await expect(self.page.get_by_role('navigation', name='后台管理导航', exact=True).get_by_role('link', name='存储管理', exact=True)).to_have_attribute('aria-current', 'page')
        await expect(self.page.get_by_role('heading', name='存储管理', exact=True)).to_be_visible()
        navigation = self.page.get_by_role('navigation', name='主导航', exact=True)
        await expect(navigation.get_by_role('link', name='文件管理', exact=True)).to_have_count(0)
        await expect(self.page.get_by_text('管理可访问文件与平台保留规则', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('region', name='当前查询概览', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='高级管理', exact=True)).to_be_visible()
        await expect(self.page.locator('input[type="file"]')).to_have_count(0)
        await expect(self.page.get_by_role('button', name='上传', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='返回知识库', exact=True).click()
        await expect(self.page).to_have_url(self.origin + '/knowledge')
        await expect(self.page.get_by_role('button', name='新建知识库', exact=True)).to_be_visible()

    async def test_old_file_links_keep_storage_tools_and_knowledge_selection(self):
        for path in ['/files', '/system/file']:
            with self.subTest(path=path):
                await self.page.goto(self.origin + path)
                await expect(self.page.get_by_role('heading', name='存储管理', exact=True)).to_be_visible()
                await expect(self.page).to_have_url(re.compile(r'/(?:admin/storage|system/file|files)$'))
                navigation = self.page.get_by_role('navigation', name='后台管理导航', exact=True)
                await expect(navigation.get_by_role('link', name='存储管理', exact=True)).to_have_attribute('aria-current', 'page')
                await expect(self.page.get_by_role('button', name='查看文件：业务报告', exact=True)).to_be_visible()

    async def test_members_cannot_enter_storage_using_new_or_old_links(self):
        await self.page.route(self.origin + '/api/auth/me', lambda route: route.fulfill(json={'id': 'member-1', 'username': 'member', 'is_admin': False}))
        await self.page.goto(self.origin + '/knowledge')
        await expect(self.page.get_by_role('heading', name='知识库', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='存储管理', exact=True)).to_have_count(0)
        for path in ['/knowledge/storage', '/files', '/system/file']:
            with self.subTest(path=path):
                await self.page.goto(self.origin + path)
                await expect(self.page).to_have_url(re.compile(r'/(?:knowledge|chat)$'))
                await expect(self.page.get_by_role('heading', name='存储管理', exact=True)).to_have_count(0)
        self.assertEqual(self.file_requests, [])

    async def test_mobile_knowledge_storage_round_trip_stays_on_screen(self):
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        await self.page.goto(self.origin + '/knowledge')
        await self.page.get_by_role('button', name='打开主菜单', exact=True).click()
        await self.page.get_by_role('button', name='用户菜单', exact=True).click()
        await self.page.get_by_role('link', name='后台管理', exact=True).click()
        await self.page.get_by_role('navigation', name='后台管理导航', exact=True).get_by_role('link', name='存储管理', exact=True).click()
        await expect(self.page.get_by_role('heading', name='存储管理', exact=True)).to_be_visible()
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        await self.page.get_by_role('button', name='返回知识库', exact=True).click()
        await expect(self.page.get_by_role('button', name='新建知识库', exact=True)).to_be_visible()

    async def test_original_acl_has_deny_expiry_and_real_builtin_rules(self):
        await self.page.goto(self.origin + '/files')
        await (await self.more('访问权限')).click()
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
        await expect(await self.more('访问权限')).to_have_attribute('aria-disabled', 'true')
        await self.page.keyboard.press('Escape')
        await expect(self.page.get_by_role('button', name='删除文件：业务报告')).to_be_disabled()

    async def test_policies_reminders_and_reconcile_are_live_drawers(self):
        await self.page.goto(self.origin + '/files')
        await self.advanced('保留策略')
        await expect(self.page.get_by_text('文件保留策略', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='Close', exact=True).click()
        await self.advanced('保留提醒')
        await expect(self.page.get_by_text('文件保留提醒', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='Close', exact=True).click()
        await self.advanced('存储对账')
        await expect(self.page.get_by_text('任务记录', exact=True)).to_be_visible()

    async def test_tablet_numeric_drawer_width_keeps_close_control_on_screen(self):
        await self.page.set_viewport_size({'width':768,'height':900})
        await self.page.goto(self.origin+'/files')
        await self.advanced('保留策略')
        close=self.page.get_by_role('button',name='Close',exact=True)
        await expect(close).to_be_visible()
        await self.page.wait_for_timeout(400)
        box=await close.bounding_box()
        self.assertGreaterEqual(box['x'],0)
        self.assertLessEqual(box['x']+box['width'],768)
        await close.click()
        await expect(self.page.get_by_text('文件保留策略',exact=True)).not_to_be_visible()

    async def test_default_storage_focuses_on_search_files_and_common_actions(self):
        await self.page.goto(self.origin + '/files')
        await expect(self.page.get_by_role('textbox', name='搜索文件名', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('combobox', name='文件类型', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('textbox', name='筛选 MIME 类型', exact=True)).to_be_hidden()
        await expect(self.page.locator('tr.ant-table-row').get_by_text('12 B · TXT', exact=True)).to_be_visible()
        await expect(self.page.locator('tr.ant-table-row').get_by_text('text/plain', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('tab', name='文件', exact=True)).to_have_attribute('aria-selected', 'true')
        await expect(self.page.get_by_role('tab', name='回收站', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='高级筛选', exact=True)).to_have_attribute('aria-expanded', 'false')
        await expect(self.page.get_by_placeholder('请输入所有者')).to_be_hidden()
        await expect(self.page.get_by_text('文件概览', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('region', name='批量文件操作', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('columnheader')).to_have_count(6)
        for label in ['所有者', 'ACL', '存储状态', '文件有效期']:
            await expect(self.page.get_by_role('columnheader', name=label, exact=True)).to_have_count(0)
        for label in ['查看文件：业务报告', '下载文件：业务报告', '删除文件：业务报告', '更多操作：业务报告']:
            await expect(self.page.get_by_role('button', name=label, exact=True)).to_be_enabled()
        for label in ['保留策略', '保留提醒', '存储对账', '授权', '转移', '恢复', '清理']:
            await expect(self.page.get_by_role('button', name=label, exact=True)).to_have_count(0)
        async with self.page.expect_download() as pending:
            await self.page.get_by_role('button', name='下载文件：业务报告', exact=True).click()
        self.assertEqual((await pending.value).suggested_filename, 'report.txt')

    async def test_friendly_text_type_sends_exact_mime_and_keeps_refresh_selection(self):
        self.file_rows.append({**FILE, 'id': 'pdf-1', 'display_name': 'PDF报告', 'original_filename': 'report.pdf', 'mime': 'application/pdf'})
        await self.page.goto(self.origin + '/files')
        types = self.page.get_by_role('combobox', name='文件类型', exact=True)
        await expect(types).to_be_visible()
        await self.page.locator('.file-type-filter .ant-select-selector').click()
        await self.page.locator('.ant-select-item-option[title="文本"]').click()
        await self.page.get_by_role('button', name='搜索文件', exact=True).click()
        await expect(self.page.get_by_role('button', name='查看文件：PDF报告', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button', name='查看文件：业务报告', exact=True)).to_be_visible()
        self.assertEqual(self.file_queries[-1]['mime'], ['text/plain'])
        checkbox = self.page.get_by_role('checkbox', name='选择文件：业务报告', exact=True)
        await checkbox.check()
        await self.page.get_by_role('button', name='刷新文件', exact=True).click()
        await expect(self.page.locator('.ant-spin-spinning')).to_have_count(0)
        await expect(checkbox).to_be_checked()
        self.assertEqual(self.file_queries[-1]['mime'], ['text/plain'])
        await self.page.locator('.file-type-filter .ant-select-selector').click()
        await types.fill('全部')
        await self.page.locator('.ant-select-item-option[title="全部类型"]').click()
        await self.page.get_by_role('button', name='搜索文件', exact=True).click()
        await expect(self.page.get_by_role('button', name='查看文件：PDF报告', exact=True)).to_be_visible()
        self.assertNotIn('mime', self.file_queries[-1])
        await expect(self.page.get_by_role('region', name='批量文件操作', exact=True)).to_have_count(0)

    async def test_advanced_custom_mime_stays_exact_and_details_keep_original_type(self):
        custom_mime = 'application/vnd.example+json'
        self.file_rows[0].update(mime=custom_mime, original_filename='report.custom')
        await self.page.goto(self.origin + '/files')
        await self.page.get_by_role('button', name='高级筛选', exact=True).click()
        await self.page.get_by_role('textbox', name='筛选 MIME 类型', exact=True).fill(custom_mime)
        await expect(self.page.locator('.file-type-filter .ant-select-selection-item')).to_have_text('自定义类型')
        await self.page.get_by_role('button', name='搜索文件', exact=True).click()
        await expect(self.page.locator('.ant-spin-spinning')).to_have_count(0)
        self.assertEqual(self.file_queries[-1]['mime'], [custom_mime])
        await self.page.get_by_role('button', name='高级筛选', exact=True).click()
        await expect(self.page.get_by_role('textbox', name='筛选 MIME 类型', exact=True)).to_be_hidden()
        await expect(self.page.locator('tr.ant-table-row').get_by_text('12 B · CUSTOM', exact=True)).to_be_visible()
        await expect(self.page.locator('tr.ant-table-row')).not_to_contain_text(custom_mime)
        await self.page.get_by_role('checkbox', name='选择文件：业务报告', exact=True).check()
        await self.page.get_by_role('button', name='刷新文件', exact=True).click()
        await expect(self.page.locator('.ant-spin-spinning')).to_have_count(0)
        await expect(self.page.get_by_role('checkbox', name='选择文件：业务报告', exact=True)).to_be_checked()
        self.assertEqual(self.file_queries[-1]['mime'], [custom_mime])
        await self.page.get_by_role('button', name='查看文件：业务报告', exact=True).click()
        await expect(self.page.get_by_text(custom_mime, exact=True)).to_be_visible()

    async def test_advanced_statistics_filters_and_each_file_tool_remain_discoverable(self):
        await self.page.goto(self.origin + '/files')
        await self.advanced('详细统计')
        await expect(self.page.get_by_text('文件概览', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('统计数据随当前查询条件实时更新', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='高级筛选', exact=True).click()
        for placeholder in ['请输入上传用户', '请输入所有者']:
            await expect(self.page.get_by_placeholder(placeholder)).to_be_visible()
        for label in ['访问类型', '文件状态', '过期状态', '上传时间']:
            await expect(self.page.locator('.file-advanced-search').get_by_text(label, exact=True)).to_be_visible()
        for label in ['访问权限', '转移文件', '业务引用', '文件审计']:
            item = await self.more(label)
            await expect(item).to_be_visible()
            await self.page.keyboard.press('Escape')
        for label, title in [('转移文件', '转移文件 - 业务报告'), ('业务引用', '业务引用 - 业务报告'), ('文件审计', '文件审计 - 业务报告')]:
            await (await self.more(label)).click()
            await expect(self.page.get_by_text(title, exact=True)).to_be_visible()
            await self.page.get_by_role('button', name='Close', exact=True).click()
        self.assertIn('/api/files/file-1/references', self.file_requests)
        self.assertIn('/api/files/file-1/audit/page', self.file_requests)

    async def test_selection_survives_refresh_and_resets_on_query_or_page_change(self):
        self.file_rows.extend([{**FILE, 'id': f'file-{index}', 'display_name': f'文档{index}', 'original_filename': f'note-{index}.txt'} for index in range(2, 23)])
        await self.page.goto(self.origin + '/files')
        selection = self.page.get_by_role('checkbox', name='选择文件：业务报告', exact=True)
        await selection.check()
        toolbar = self.page.get_by_role('region', name='批量文件操作', exact=True)
        await expect(toolbar.get_by_text('已选 1 项', exact=True)).to_be_visible()
        await expect(toolbar.get_by_role('button', name='移入回收站', exact=True)).to_be_enabled()
        await expect(toolbar.get_by_role('button', name='恢复', exact=True)).to_have_count(0)
        before = len(self.file_queries)
        await self.page.get_by_role('button', name='刷新文件', exact=True).click()
        await expect(selection).to_be_checked()
        await expect(toolbar).to_be_visible()
        await expect(self.page.locator('.ant-spin-spinning')).to_have_count(0)
        self.assertGreater(len(self.file_queries), before)
        await self.page.get_by_title('2', exact=True).click()
        await expect(toolbar).to_have_count(0)
        await self.page.get_by_title('1', exact=True).click()
        await selection.check()
        await self.page.get_by_role('textbox', name='搜索文件名', exact=True).fill('report')
        await self.page.get_by_role('button', name='搜索文件', exact=True).click()
        await expect(toolbar).to_have_count(0)
        self.assertEqual(self.file_queries[-1]['search'], ['report'])
        self.assertEqual(self.file_queries[-1]['page'], ['1'])

    async def test_recycle_view_restores_and_permanently_cleans_with_versions(self):
        self.file_rows[0]['status'] = 'deleted'
        await self.page.goto(self.origin + '/files')
        await expect(self.page.get_by_role('button', name='查看文件：业务报告', exact=True)).to_have_count(0)
        await self.page.get_by_role('tab', name='回收站', exact=True).click()
        await expect(self.page.get_by_role('tab', name='回收站', exact=True)).to_have_attribute('aria-selected', 'true')
        await expect(self.page.get_by_role('button', name='删除文件：业务报告', exact=True)).to_have_count(0)
        await self.page.get_by_role('checkbox', name='选择文件：业务报告', exact=True).check()
        toolbar = self.page.get_by_role('region', name='批量文件操作', exact=True)
        await expect(toolbar.get_by_role('button', name='恢复', exact=True)).to_be_enabled()
        await expect(toolbar.get_by_role('button', name='永久清理', exact=True)).to_be_enabled()
        await self.page.get_by_role('button', name='恢复文件：业务报告', exact=True).click()
        await self.page.get_by_role('button', name=re.compile(r'^确\s*定$')).click()
        await expect(self.page.get_by_role('button', name='恢复文件：业务报告', exact=True)).to_have_count(0)
        self.assertEqual(self.state_writes, [{'path': '/api/files/file-1/restore', 'expected_version': 1}])
        await self.page.get_by_role('tab', name='文件', exact=True).click()
        await self.page.get_by_role('button', name='删除文件：业务报告', exact=True).click()
        await self.page.get_by_role('button', name=re.compile(r'^确\s*定$')).click()
        await expect(self.page.get_by_role('button', name='删除文件：业务报告', exact=True)).to_have_count(0)
        await self.page.get_by_role('tab', name='回收站', exact=True).click()
        await self.page.get_by_role('button', name='永久清理：业务报告', exact=True).click()
        await self.page.get_by_role('button', name=re.compile(r'^确\s*定$')).click()
        await expect(self.page.get_by_role('button', name='永久清理：业务报告', exact=True)).to_have_count(0)
        self.assertEqual([item['expected_version'] for item in self.state_writes], [1, 2, 3])
        self.assertEqual(self.file_queries[-1]['status'], ['deleted'])

    async def test_referenced_and_derived_files_keep_delete_and_purge_protection(self):
        self.file_rows[0]['reference_count'] = 1
        await self.page.goto(self.origin + '/files')
        await expect(self.page.get_by_role('button', name='删除文件：业务报告', exact=True)).to_be_disabled()
        await self.page.get_by_role('checkbox', name='选择文件：业务报告', exact=True).check()
        await expect(self.page.get_by_role('button', name='移入回收站', exact=True)).to_be_disabled()
        self.file_rows[0]['status'] = 'deleted'
        await self.page.get_by_role('tab', name='回收站', exact=True).click()
        await expect(self.page.get_by_role('button', name='永久清理：业务报告', exact=True)).to_be_disabled()
        await expect(self.page.get_by_role('button', name='恢复文件：业务报告', exact=True)).to_be_enabled()
        self.file_rows[0]['source_file_id'] = 'source-1'
        await self.page.get_by_role('button', name='刷新文件', exact=True).click()
        await expect(self.page.get_by_role('button', name='恢复文件：业务报告', exact=True)).to_be_disabled()
        await expect(self.page.get_by_role('checkbox', name='选择文件：业务报告', exact=True)).to_be_disabled()
        self.assertEqual(self.state_writes, [])

    async def test_mobile_default_and_advanced_controls_stay_in_view(self):
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        await self.page.goto(self.origin + '/files')
        for label in ['高级管理', '高级筛选', '搜索文件']:
            button = self.page.get_by_role('button', name=label, exact=True)
            await expect(button).to_be_visible()
            box = await button.bounding_box()
            self.assertGreaterEqual(box['x'], 0)
            self.assertLessEqual(box['x'] + box['width'], 390)
        more = self.page.get_by_role('button', name='更多操作：业务报告', exact=True)
        await expect(more).to_be_in_viewport(ratio=1)
        await more.click()
        await expect(self.page.get_by_role('menuitem', name='访问权限：业务报告', exact=True)).to_be_visible()
        await more.click()
        await expect(self.page.get_by_role('menuitem', name='访问权限：业务报告', exact=True)).to_be_hidden()
        if evidence := os.environ.get('STORAGE_UI_EVIDENCE'):
            await self.page.screenshot(path=str(Path(evidence) / 'storage-mobile-default.png'))
        await self.page.get_by_role('button', name='高级筛选', exact=True).click()
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        await self.advanced('详细统计')
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))

    async def test_failed_recycle_write_keeps_selection_and_can_retry_once(self):
        requested, release = asyncio.Event(), asyncio.Event()
        attempts = []

        async def fail_once(route):
            if route.request.method != 'DELETE':
                await self.respond(route)
                return
            attempts.append(route.request.post_data_json)
            requested.set()
            await release.wait()
            await route.fulfill(status=409, json={'error': {'code': 'conflict', 'message': '文件状态已变化'}})

        await self.page.route(self.origin + '/api/files/file-1', fail_once)
        try:
            await self.page.goto(self.origin + '/files')
            checkbox = self.page.get_by_role('checkbox', name='选择文件：业务报告', exact=True)
            await checkbox.check()
            button = self.page.get_by_role('button', name='移入回收站', exact=True)
            await button.click()
            await self.page.get_by_role('button', name=re.compile(r'^确\s*定$')).click()
            await asyncio.wait_for(requested.wait(), 10)
            await expect(button).to_be_disabled()
            await expect(self.page.get_by_role('button', name='删除文件：业务报告', exact=True)).to_be_disabled()
            release.set()
            await expect(self.page.get_by_text(re.compile('已处理0项；文件状态已变化'))).to_be_visible()
            await expect(checkbox).to_be_checked()
            await expect(button).to_be_enabled()
            self.assertEqual(attempts, [{'expected_version': 1}])
            await self.page.unroute(self.origin + '/api/files/file-1', fail_once)
            await button.click()
            await self.page.get_by_role('button', name=re.compile(r'^确\s*定$')).click()
            await expect(checkbox).to_have_count(0)
            self.assertEqual(self.state_writes, [{'path': '/api/files/file-1', 'expected_version': 1}])
        finally:
            release.set()

    async def test_reconcile_observation_pauses_when_hidden_without_refreshing_file_workspace(self):
        self.file_jobs = [{'id': 'reconcile-1', 'kind': 'reconcile', 'status': 'running', 'started_at': FILE['created_at'], 'finished_at': None, 'scanned': 2, 'changed': 0, 'errors': []}]
        await self.page.goto(self.origin + '/files')
        await self.page.get_by_role('checkbox', name='选择文件：业务报告', exact=True).check()
        await self.advanced('存储对账')
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await expect(drawer.get_by_text('执行中', exact=True)).to_be_visible()
        reads = self.file_requests.count('/api/files/jobs')
        await self.page.evaluate("Object.defineProperty(document, 'hidden', { configurable: true, value: true }); document.dispatchEvent(new Event('visibilitychange'))")
        await self.page.wait_for_timeout(2300)
        self.assertEqual(self.file_requests.count('/api/files/jobs'), reads)
        await self.page.evaluate("Object.defineProperty(document, 'hidden', { configurable: true, value: false }); document.dispatchEvent(new Event('visibilitychange'))")
        async def observed():
            while self.file_requests.count('/api/files/jobs') <= reads:
                await asyncio.sleep(.02)
        await asyncio.wait_for(observed(), 1)
        await expect(drawer.locator('.ant-spin-spinning')).to_have_count(0)
        self.assertEqual(self.file_requests.count('/api/files'), 1)
        self.file_jobs[0].update(status='succeeded', finished_at=FILE['created_at'])
        await expect(drawer.get_by_text('已完成', exact=True)).to_be_visible(timeout=4000)
        reads = self.file_requests.count('/api/files/jobs')
        await self.page.wait_for_timeout(2200)
        self.assertEqual(self.file_requests.count('/api/files/jobs'), reads)
        await drawer.get_by_role('button', name='Close', exact=True).click()
        await expect(self.page.get_by_role('checkbox', name='选择文件：业务报告', exact=True)).to_be_checked()
        self.assertEqual(self.state_writes, [])

    async def test_reconcile_write_failure_survives_status_recovery(self):
        async def failed_scan(route):
            await route.fulfill(status=409, json={'error': {'code': 'conflict', 'message': '已有对账任务，请稍后重试'}})
        await self.page.route(self.origin + '/api/files/jobs/reconcile', failed_scan)
        await self.page.goto(self.origin + '/files')
        await self.advanced('存储对账')
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await drawer.get_by_role('button', name='开始对账', exact=True).click()
        async def recovered():
            while self.file_requests.count('/api/files/reconcile/stats') < 2:
                await asyncio.sleep(.02)
        await asyncio.wait_for(recovered(), 3)
        await expect(drawer.get_by_role('button', name='开始对账', exact=True)).not_to_have_class(re.compile('ant-btn-loading'))
        await expect(drawer.get_by_text('已有对账任务，请稍后重试', exact=True)).to_be_visible()
        await expect(drawer.get_by_role('button', name='开始对账', exact=True)).to_be_enabled()
        self.assertEqual(self.file_requests.count('/api/files'), 1)

    async def test_reconcile_observation_recovers_after_transient_read_failure(self):
        self.file_jobs = [{'id': 'reconcile-1', 'kind': 'reconcile', 'status': 'running', 'started_at': FILE['created_at'], 'finished_at': None, 'scanned': 2, 'changed': 0, 'errors': []}]
        fail_next = False
        reads = []

        async def jobs(route):
            nonlocal fail_next
            reads.append(route.request.method)
            if fail_next:
                fail_next = False
                self.file_requests.append('/api/files/jobs')
                await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '任务读取暂时失败'}})
            else:
                await self.respond(route)

        await self.page.route(self.origin + '/api/files/jobs', jobs)
        await self.page.goto(self.origin + '/files')
        await self.advanced('存储对账')
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await expect(drawer.get_by_text('执行中', exact=True)).to_be_visible()
        fail_next = True
        await expect(drawer.get_by_text('任务读取暂时失败', exact=True)).to_be_visible(timeout=4000)
        await expect(drawer.get_by_text('执行中', exact=True)).to_be_visible()
        self.file_jobs[0].update(status='succeeded', finished_at=FILE['created_at'])
        await expect(drawer.get_by_text('已完成', exact=True)).to_be_visible(timeout=7000)
        await expect(drawer.get_by_text('任务读取暂时失败', exact=True)).to_have_count(0)
        self.assertEqual(self.file_requests.count('/api/files'), 1)
        self.assertTrue(all(method == 'GET' for method in reads))

    async def test_reconcile_close_and_navigation_stop_reads_without_cancelling_backend(self):
        self.file_jobs = [{'id': 'reconcile-1', 'kind': 'reconcile', 'status': 'running', 'started_at': FILE['created_at'], 'finished_at': None, 'scanned': 2, 'changed': 0, 'errors': []}]
        traffic = []
        self.page.on('request', lambda request: traffic.append((request.method, urlparse(request.url).path)) if '/api/files' in request.url else None)
        await self.page.goto(self.origin + '/files')
        await self.advanced('存储对账')
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await expect(drawer.get_by_text('执行中', exact=True)).to_be_visible()
        await drawer.get_by_role('button', name='Close', exact=True).click()
        reads = self.file_requests.count('/api/files/jobs')
        await self.page.wait_for_timeout(2300)
        self.assertEqual(self.file_requests.count('/api/files/jobs'), reads)
        await self.advanced('存储对账')
        await expect(drawer.get_by_text('执行中', exact=True)).to_be_visible()
        reads = self.file_requests.count('/api/files/jobs')
        await self.page.goto(self.origin + '/knowledge')
        await self.page.wait_for_timeout(2300)
        self.assertEqual(self.file_requests.count('/api/files/jobs'), reads)
        self.assertEqual(self.file_jobs[0]['status'], 'running')
        self.assertTrue(all(method == 'GET' for method, _ in traffic))

    async def test_reconcile_revoked_access_clears_protected_content_and_stops_observing(self):
        self.file_jobs = [{'id': 'reconcile-1', 'kind': 'reconcile', 'status': 'running', 'started_at': FILE['created_at'], 'finished_at': None, 'scanned': 2, 'changed': 0, 'errors': []}]
        self.reconcile_issues = [{'id': 'issue-1', 'job_id': 'reconcile-1', 'file_id': 'file-1', 'kind': 'missing', 'status': 'open', 'version': 1, 'size': 12, 'detail': '敏感对账异常', 'expected_size': 12, 'actual_size': None, 'created_at': FILE['created_at'], 'updated_at': FILE['created_at']}]
        denied = False
        async def fixtures(route):
            path = urlparse(route.request.url).path
            if denied and path in ['/api/files/jobs', '/api/files/reconcile/issues', '/api/files/reconcile/stats']:
                self.file_requests.append(path)
                await route.fulfill(status=403, json={'error': {'code': 'forbidden', 'message': '对账读取权限已移除'}})
            else:
                await self.respond(route)
        await self.page.route(self.origin + '/api/files/**', fixtures)
        await self.page.goto(self.origin + '/files')
        await self.advanced('存储对账')
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await expect(drawer.get_by_text('敏感对账异常', exact=True)).to_be_visible()
        denied = True
        await expect(drawer.get_by_text('对账读取权限已移除', exact=True)).to_be_visible(timeout=4000)
        await expect(drawer.get_by_text('敏感对账异常', exact=True)).to_have_count(0)
        await expect(drawer.get_by_text('执行中', exact=True)).to_have_count(0)
        await expect(drawer.get_by_role('button', name='开始对账', exact=True)).to_be_disabled()
        reads = self.file_requests.count('/api/files/jobs')
        await self.page.wait_for_timeout(4300)
        self.assertEqual(self.file_requests.count('/api/files/jobs'), reads)

    async def test_file_refresh_keeps_content_on_temporary_failure_and_clears_authoritative_denials(self):
        status = 200
        async def fixtures(route):
            path = urlparse(route.request.url).path
            if status != 200 and path in ['/api/files', '/api/files/stats']:
                self.file_requests.append(path)
                await route.fulfill(status=status, json={'error': {'code': 'unavailable' if status == 503 else 'forbidden', 'message': '存储暂时不可用' if status == 503 else '存储读取已拒绝'}})
            else:
                await self.respond(route)
        await self.page.route(self.origin + '/api/files**', fixtures)
        for denied_status in [403, 404]:
            with self.subTest(status=denied_status):
                status = 200
                await self.page.goto(self.origin + '/files')
                checkbox = self.page.get_by_role('checkbox', name='选择文件：业务报告', exact=True)
                await checkbox.check()
                status = 503
                await self.page.get_by_role('button', name='刷新文件', exact=True).click()
                await expect(self.page.get_by_text('存储暂时不可用', exact=True)).to_be_visible()
                await expect(checkbox).to_be_checked()
                await expect(self.page.get_by_role('button', name='查看文件：业务报告', exact=True)).to_be_visible()
                status = denied_status
                await self.page.get_by_role('button', name='刷新文件', exact=True).click()
                await expect(self.page.get_by_text('存储读取已拒绝', exact=True)).to_be_visible()
                await expect(self.page.get_by_role('button', name='查看文件：业务报告', exact=True)).to_have_count(0)
                await expect(self.page.get_by_role('region', name='当前查询概览', exact=True)).to_have_count(0)
                await expect(self.page.get_by_role('region', name='批量文件操作', exact=True)).to_have_count(0)
        self.assertEqual(self.state_writes, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
