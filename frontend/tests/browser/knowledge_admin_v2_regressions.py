"""Knowledge defaults and storage navigation with fixture-only HTTP boundaries."""

import asyncio
import contextlib
import json
import os
from pathlib import Path
import re
import unittest
from urllib.parse import urlparse

from playwright.async_api import expect

from developer_regressions import DeveloperBoundary


class KnowledgeAdminV2Regressions(DeveloperBoundary):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.user = {'id': 'user-1', 'username': 'admin', 'is_admin': True, 'permissions': []}
        self.defaults = {'default_embed_model_id': 'embed-1', 'default_rerank_model_id': 'rank-1',
                         'chunk_size': 1000, 'chunk_overlap': 200, 'version': 7,
                         'updated_at': '2026-11-06T01:00:00Z'}
        self.models = [self.model('embed-1', '默认向量模型 A', 'embedding'),
                       self.model('embed-2', '向量模型 B', 'embedding'),
                       self.model('rank-1', '默认重排模型 A', 'rerank'),
                       self.model('rank-2', '重排模型 B', 'rerank'),
                       self.model('chat-1', '聊天模型', 'chat'),
                       self.model('embed-disabled', '已停用向量模型', 'embedding', enabled=False),
                       self.model('rank-disabled', '已停用重排模型', 'rerank', enabled=False)]
        self.requests = []
        self.menu_configs = []
        self.missing = []
        self.conflict = False
        self.save_requested, self.release_save = asyncio.Event(), asyncio.Event()
        self.hold_save = False
        self.console_errors = []
        self.page.on('console', lambda entry: self.console_errors.append(entry.text) if entry.type == 'error' else None)
        self.evidence = Path(os.environ.get('KNOWLEDGE_ADMIN_EVIDENCE', str(Path(__file__).resolve().parents[3] / '.test-artifacts' / 'knowledge-admin')))
        self.evidence.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def model(identifier, name, kind, **overrides):
        return {'id': identifier, 'name': name, 'connection_id': 'fixture-connection',
                'upstream_model': 'fixture-only', 'max_output_tokens': 512,
                'model_type': kind, 'published': True, 'enabled': True,
                'capabilities': [kind], **overrides}

    async def asyncTearDown(self):
        self.release_save.set()
        (self.evidence / (self._testMethodName + '.json')).write_text(json.dumps({
            'requests': self.requests, 'missing': self.missing, 'page_errors': self.errors,
            'console_errors': self.console_errors}, ensure_ascii=False, indent=2))
        with contextlib.suppress(Exception):
            await self.page.screenshot(path=str(self.evidence / (self._testMethodName + '.png')), full_page=True)
        await super().asyncTearDown()
        self.assertEqual(self.missing, [], 'Every API request must be handled by a fixture')

    async def respond(self, route):
        request = route.request
        path = urlparse(request.url).path
        body = request.post_data_json if request.post_data else None
        self.requests.append({'method': request.method, 'path': path, 'body': body})
        status = 200
        if path == '/api/auth/me':
            result = self.user
        elif path == '/api/admin/menus':
            if request.method == 'POST':
                if body['path'] == '/admin/storage' and body['permission'] != 'ai:storage:read':
                    status, result = 422, {'error': {'code': 'unknown_permission', 'message': '菜单权限代码无效'}}
                else:
                    result = {**body, 'id': 'menu-created', 'version': 1}
                    self.menu_configs.append(result)
                return await route.fulfill(status=status, json=result)
            if 'navigation=true' not in request.url:
                return await route.fulfill(json={'items': self.menu_configs, 'total': len(self.menu_configs),
                                                 'page': 1, 'page_size': 100})
            definitions = [('/admin/menus', '菜单管理', 'system:menus:read'),
                           ('/admin/knowledge-settings', '知识服务', 'system:configs:read'),
                           ('/admin/storage', '存储管理', 'ai:storage:read')]
            items = [{'path': page, 'name': label, 'permission': permission, 'sort_order': index,
                      'is_visible': True} for index, (page, label, permission) in enumerate(definitions)
                     if self.user['is_admin'] or permission in self.user['permissions']
                     or (page == '/admin/storage' and 'ai:storage:write' in self.user['permissions'])]
            for item in items:
                override = next((menu for menu in self.menu_configs if menu['path'] == item['path']), None)
                if override:
                    item.update({key: override[key] for key in ['name', 'is_visible', 'sort_order']})
            result = {'items': items, 'total': len(items), 'page': 1, 'page_size': 100}
        elif path == '/api/knowledge/settings':
            if request.method == 'PATCH':
                self.save_requested.set()
                if self.hold_save:
                    await self.release_save.wait()
                if self.conflict:
                    status, result = 409, {'error': {'code': 'knowledge_settings_conflict',
                                                   'message': '知识服务配置已变化，请刷新后重试'}}
                else:
                    self.defaults.update({key: value for key, value in body.items() if key != 'expected_version'})
                    self.defaults['version'] += 1
                    result = dict(self.defaults)
            else:
                result = dict(self.defaults)
        elif path == '/api/models':
            result = self.models
        elif path == '/api/files':
            result = {'items': [], 'total': 0, 'page': 1, 'page_size': 20}
        elif path == '/api/files/stats':
            result = {key: 0 for key in ['total_count', 'total_bytes', 'active_count', 'deleted_count',
                                        'public_count', 'public_bytes', 'private_bytes', 'expired_count',
                                        'retention_expiring_count', 'acl_expiring_count']}
        elif path == '/api/files/users':
            result = []
        elif path in ['/api/chat/conversations', '/api/knowledge/available']:
            result = []
        elif path == '/api/admin/notices/published':
            result = {'items': [], 'total': 0, 'page': 1, 'page_size': 20}
        else:
            self.missing.append(path)
            status, result = 404, {'error': {'code': 'fixture_missing', 'message': path}}
        with contextlib.suppress(Exception):
            await route.fulfill(status=status, json=result)

    def patches(self):
        return [item['body'] for item in self.requests
                if item['method'] == 'PATCH' and item['path'] == '/api/knowledge/settings']

    async def open_settings(self):
        await self.page.goto(self.origin + '/admin/knowledge-settings')
        await expect(self.page.get_by_role('heading', name='知识库默认配置', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('spinbutton', name='默认切片大小', exact=True)).to_have_value(str(self.defaults['chunk_size']))
        await expect(self.page.get_by_role('button', name='刷新知识服务', exact=True)).to_be_enabled()

    def select(self, label):
        return self.page.locator('.ant-form-item').filter(has=self.page.get_by_role('combobox', name=re.compile(r'^(?:\*\s*)?' + re.escape(label) + '$')))

    async def choose(self, label, value):
        # Ant's selected-value span covers the readonly input; keyboard interaction
        # uses the real, labelled combobox without bypassing pointer actionability.
        await self.page.get_by_role('combobox', name=re.compile(r'^(?:\*\s*)?' + re.escape(label) + '$')).press('ArrowDown')
        await self.page.locator('.ant-select-dropdown:visible').get_by_text(value, exact=True).click()

    async def save(self):
        await self.page.get_by_role('button', name='保存配置', exact=True).click()
        await expect(self.page.get_by_role('button', name='刷新知识服务', exact=True)).to_be_enabled()

    async def test_selected_models_are_typed_and_saved_version_is_used_next_time(self):
        await self.open_settings()
        await expect(self.select('默认向量模型')).to_contain_text('默认向量模型 A')
        await expect(self.select('默认重排模型')).to_contain_text('默认重排模型 A')
        await self.page.get_by_role('combobox', name='默认向量模型', exact=True).press('ArrowDown')
        dropdown = self.page.locator('.ant-select-dropdown:visible')
        await expect(dropdown.get_by_text('向量模型 B', exact=True)).to_be_visible()
        for hidden in ['默认重排模型 A', '聊天模型', '已停用向量模型']:
            await expect(dropdown.get_by_text(hidden, exact=True)).to_have_count(0)
        await dropdown.get_by_text('向量模型 B', exact=True).click()
        await self.choose('默认重排模型', '重排模型 B')
        await self.page.get_by_role('spinbutton', name='默认切片大小', exact=True).fill('1200')
        await self.save()
        self.assertEqual(self.patches()[0], {'expected_version': 7, 'default_embed_model_id': 'embed-2',
                                           'default_rerank_model_id': 'rank-2', 'chunk_size': 1200, 'chunk_overlap': 200})
        await self.page.get_by_role('spinbutton', name='默认重叠大小', exact=True).fill('250')
        await self.save()
        self.assertEqual(self.patches()[1]['expected_version'], 8)
        self.assertEqual(self.patches()[1]['chunk_overlap'], 250)

    async def test_required_ranker_cannot_be_cleared_and_preserves_embedding(self):
        await self.open_settings()
        ranker = self.select('默认重排模型')
        await ranker.locator('.ant-select').hover()
        await expect(ranker.locator('.ant-select-clear')).to_have_count(0)
        self.assertEqual(self.patches(), [])
        await self.choose('默认重排模型', '重排模型 B')
        await self.save()
        self.assertEqual(self.patches()[0]['default_rerank_model_id'], 'rank-2')
        self.assertEqual(self.patches()[0]['default_embed_model_id'], 'embed-1')

    async def test_empty_chunk_parameters_never_submit(self):
        await self.open_settings()
        size = self.page.get_by_role('spinbutton', name='默认切片大小', exact=True)
        overlap = self.page.get_by_role('spinbutton', name='默认重叠大小', exact=True)
        for empty, restore in [(size, '1000'), (overlap, '200')]:
            await empty.fill('')
            await self.save()
            await expect(self.page.get_by_role('alert')).to_contain_text('请填写有效的切片大小和重叠大小')
            self.assertEqual(self.patches(), [])
            await empty.fill(restore)

    async def test_overlap_at_least_chunk_size_never_submits(self):
        await self.open_settings()
        await self.page.get_by_role('spinbutton', name='默认切片大小', exact=True).fill('100')
        await self.save()
        await expect(self.page.get_by_role('alert')).to_contain_text('重叠大小必须小于切片大小')
        self.assertEqual(self.patches(), [])

    async def test_configs_reader_sees_values_but_no_write_controls(self):
        self.user.update(is_admin=False, permissions=['system:configs:read'])
        await self.open_settings()
        await expect(self.page.get_by_role('button', name='保存配置', exact=True)).to_have_count(0)
        for label in ['默认向量模型', '默认重排模型']:
            await expect(self.page.get_by_role('combobox', name=re.compile(r'^(?:\*\s*)?' + re.escape(label) + '$'))).to_be_disabled()
        for label in ['默认切片大小', '默认重叠大小']:
            await expect(self.page.get_by_role('spinbutton', name=label, exact=True)).to_be_disabled()
        self.assertEqual(self.patches(), [])

    async def test_unavailable_defaults_show_actionable_labels_and_allow_replacement(self):
        self.defaults.update(default_embed_model_id='missing-embedding', default_rerank_model_id='rank-private')
        await self.open_settings()
        for label in ['默认向量模型', '默认重排模型']:
            await expect(self.select(label)).to_contain_text('当前配置模型（不可用，请重新选择）')
        await self.choose('默认向量模型', '向量模型 B')
        await self.choose('默认重排模型', '重排模型 B')
        await self.save()
        self.assertEqual(self.patches()[0]['default_embed_model_id'], 'embed-2')
        self.assertEqual(self.patches()[0]['default_rerank_model_id'], 'rank-2')

    async def test_conflict_preserves_inputs_and_refresh_loads_new_version(self):
        self.conflict = True
        await self.open_settings()
        await self.choose('默认向量模型', '向量模型 B')
        await self.page.get_by_role('spinbutton', name='默认切片大小', exact=True).fill('1300')
        await self.save()
        await expect(self.page.get_by_role('alert')).to_contain_text('知识服务配置已变化，请刷新后重试')
        await expect(self.page.get_by_role('spinbutton', name='默认切片大小', exact=True)).to_have_value('1300')
        await expect(self.select('默认向量模型')).to_contain_text('向量模型 B')
        self.assertEqual(self.patches()[0]['expected_version'], 7)
        await self.page.screenshot(path=str(self.evidence / 'conflict-preserved-inputs.png'), full_page=True)
        self.conflict = False
        self.defaults.update(version=12, chunk_size=1400, chunk_overlap=300)
        await self.page.get_by_role('button', name='刷新知识服务', exact=True).click()
        await expect(self.page.get_by_role('spinbutton', name='默认切片大小', exact=True)).to_have_value('1400')
        await expect(self.select('默认向量模型')).to_contain_text('默认向量模型 A')
        await self.save()
        self.assertEqual(self.patches()[1]['expected_version'], 12)

    async def test_pending_save_disables_refresh_inputs_and_duplicate_submission(self):
        self.hold_save = True
        await self.open_settings()
        await self.page.get_by_role('button', name='保存配置', exact=True).click()
        await self.save_requested.wait()
        try:
            await expect(self.page.get_by_role('button', name='刷新知识服务', exact=True)).to_be_disabled()
            await expect(self.page.get_by_role('spinbutton', name='默认切片大小', exact=True)).to_be_disabled()
            await expect(self.select('默认重排模型').get_by_role('combobox')).to_be_disabled()
            # Ant includes its loading icon in the button's accessible name.
            await self.page.get_by_role('button', name=re.compile(r'保存配置$')).dispatch_event('click')
            self.assertEqual(len(self.patches()), 1)
        finally:
            self.release_save.set()
        await expect(self.page.get_by_role('button', name='刷新知识服务', exact=True)).to_be_enabled()

    async def assert_storage(self):
        await expect(self.page.locator('.files-page')).to_be_visible()
        await expect(self.page.get_by_role('heading', name='存储管理', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('region', name='当前查询概览', exact=True)).to_contain_text('0 个文件')
        await expect(self.page.get_by_role('tablist', name='文件视图', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('当前页面没有访问权限或已被隐藏。', exact=True)).to_have_count(0)

    async def test_storage_and_all_legacy_aliases_render_files_view(self):
        for index, path in enumerate(['/admin/storage', '/files', '/knowledge/storage', '/system/file']):
            await self.page.goto(self.origin + path)
            await self.assert_storage()
            self.assertEqual(sum(item['path'] == '/api/files' for item in self.requests), index + 1)
            self.assertEqual(sum(item['path'] == '/api/files/stats' for item in self.requests), index + 1)
            await expect(self.page.locator('.admin-navigation a.is-active')).to_have_text('存储管理')
            await self.page.screenshot(path=str(self.evidence / ('storage-' + path.strip('/').replace('/', '-') + '.png')), full_page=True)

    async def test_retired_menu_route_preserves_storage_reader_permissions(self):
        self.user.update(username='storage-operator', is_admin=False, permissions=['ai:storage:read'])
        await self.page.goto(self.origin + '/admin/menus')
        await expect(self.page).to_have_url(self.origin + '/admin/storage')
        await self.assert_storage()
        self.assertFalse(any(item['path']=='/api/admin/menus' for item in self.requests))

    async def test_storage_reader_gets_account_entry_navigation_and_no_knowledge_service(self):
        self.user.update(username='storage-operator', is_admin=False, permissions=['ai:storage:read'])
        await self.assert_storage_role_access()

    async def test_storage_writer_gets_account_entry_navigation_and_no_knowledge_service(self):
        self.user.update(username='storage-writer', is_admin=False, permissions=['ai:storage:write'])
        await self.assert_storage_role_access()
        for path in ['/files', '/knowledge/storage', '/system/file']:
            await self.page.goto(self.origin + path)
            await self.assert_storage()
            await expect(self.page.locator('.admin-navigation a.is-active')).to_have_text('存储管理')

    async def assert_storage_role_access(self):
        await self.page.goto(self.origin + '/chat')
        await self.page.get_by_role('button', name='用户菜单', exact=True).click()
        await self.page.get_by_role('menuitem').filter(has_text='后台管理').get_by_role('link', name='后台管理', exact=True).click()
        await expect(self.page).to_have_url(self.origin + '/admin/storage')
        await self.assert_storage()
        await expect(self.page.locator('.admin-navigation a')).to_have_count(1)
        await expect(self.page.locator('.admin-navigation a')).to_have_text('存储管理')
        await self.page.goto(self.origin + '/admin/knowledge-settings')
        await expect(self.page).to_have_url(self.origin + '/admin/storage')
        await self.assert_storage()
        self.assertFalse(any(item['path'] == '/api/knowledge/settings' for item in self.requests))
        self.assertEqual(self.patches(), [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
