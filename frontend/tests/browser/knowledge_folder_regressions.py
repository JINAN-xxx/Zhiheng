"""Folder navigation and upload destinations use authorized HTTP fixtures only."""

import os
import re
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from knowledge_regressions import STAMP
from knowledge_upload_regressions import KnowledgeUploadLifecycle
from playwright.async_api import expect


class KnowledgeFolderRegressions(KnowledgeUploadLifecycle):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.folders = [
            self.folder('folder-a', '产品资料'),
            self.folder('folder-b', '使用指南', 'folder-a'),
            self.folder('folder-empty', '待整理'),
        ]
        template = self.documents[0]
        self.documents = [
            {**template, 'file_name': '根目录说明.txt'},
            {**template, 'id': 'doc-2', 'file_id': 'file-2',
             'file_name': '产品介绍.md', 'folder_id': 'folder-a'},
            {**template, 'id': 'doc-3', 'file_id': 'file-3',
             'file_name': '快速上手.txt', 'folder_id': 'folder-b'},
        ]
        self.base.update(document_count=3, sharing_scope='private')
        self.folder_creates = []
        self.pending_folders = []
        self.recover_pending = False
        self.mutations = []

    @staticmethod
    def folder(id, name, parent_id=None):
        return {'id': id, 'name': name, 'parent_id': parent_id, 'base_id': 'base-1',
                'version': 1, 'created_at': STAMP, 'updated_at': STAMP}

    async def respond(self, route):
        parsed = urlparse(route.request.url)
        path, method = parsed.path, route.request.method
        if method not in ('GET', 'HEAD'):
            self.mutations.append((method, path))
        if path == '/api/knowledge/bases/base-1/folders' and method == 'POST':
            body = route.request.post_data_json
            self.folder_creates.append(body)
            folder = self.folder('folder-new', body['name'], body['parent_id'])
            self.folders.append(folder)
            return await route.fulfill(status=201, json=folder)
        if path == '/api/knowledge/bases/base-1/uploads':
            folder_id = parse_qs(parsed.query).get('folder_id', [None])[0]
            if method == 'GET':
                self.pending_folders.append(folder_id)
                items = []
                if self.recover_pending and folder_id == 'folder-b':
                    self.uploaded = self.retained = True
                    items = [{'request_id': 'pending-request', 'upload_id': 'staged-acceptance',
                              'file': self.source()}]
                return await route.fulfill(json={'items': items, 'total': len(items)})
            if method == 'POST':
                self.upload_calls.append(route.request.url)
                self.uploaded = self.retained = True
                return await route.fulfill(status=201, json={
                    'file': self.source(), 'upload_id': 'staged-acceptance', 'folder_id': folder_id})
        if path == '/api/knowledge/bases/base-1/documents' and method == 'POST' and self.confirm_import:
            body = route.request.post_data_json
            if not any(doc['id'] == 'folder-uploaded-doc' for doc in self.documents):
                self.documents.append({**self.documents[0], 'id': 'folder-uploaded-doc',
                    'file_id': 'staged-file', 'file_name': 'a.txt', 'folder_id': body['folder_id']})
                self.base['document_count'] = len(self.documents)
        await super().respond(route)

    async def open_documents(self):
        await self.page.goto(self.origin + '/knowledge/base-1/documents')
        await expect(self.page.get_by_role('button', name='产品资料', exact=True)).to_be_visible()

    async def open_upload(self):
        await self.page.get_by_role('button', name='添加资料', exact=True).click()
        modal = self.page.get_by_role('dialog')
        await expect(modal.get_by_role('combobox', name='存放位置', exact=True)).to_be_enabled()
        return modal

    async def choose_folder(self, modal, label):
        await modal.locator('.upload-location .ant-select-selector').click()
        await self.page.locator('.ant-select-item-option').filter(
            has_text=re.compile('^' + re.escape(label) + '$')).click()
        await expect(modal.locator('.upload-location .ant-select-selection-item')).to_have_text(label)

    async def test_root_and_each_folder_show_only_their_direct_documents(self):
        await self.open_documents()
        await expect(self.page.get_by_text('2 个文件夹 · 1 份资料', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='待整理', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='使用指南', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button', name='产品介绍.md', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button', name='快速上手.txt', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='产品资料', exact=True).click()
        await expect(self.page.get_by_role('button', name='使用指南', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='产品介绍.md', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='根目录说明.txt', exact=True)).to_have_count(0)
        await self.page.get_by_role('textbox', name='搜索资料', exact=True).fill('使用指南')
        await self.page.get_by_role('button', name='使用指南', exact=True).click()
        await expect(self.page.get_by_role('textbox', name='搜索资料', exact=True)).to_have_value('')
        await expect(self.page.get_by_role('button', name='快速上手.txt', exact=True)).to_be_visible()
        await expect(self.page.locator('.document-path')).to_contain_text('产品资料')
        await expect(self.page.locator('.document-path')).to_contain_text('使用指南')
        await self.page.get_by_role('button', name='根目录', exact=True).click()
        await self.page.get_by_role('button', name='待整理', exact=True).click()
        await expect(self.page.get_by_text('此文件夹还没有资料', exact=True)).to_be_visible()
        self.assertEqual(self.mutations, [])

    async def test_shared_reader_browses_folders_without_management_actions(self):
        self.base.update(can_manage=False, owner_id='another-owner', sharing_scope='users')
        await self.page.route('**/api/auth/me', lambda route: route.fulfill(
            json={'id': 'user-1', 'username': 'reader', 'is_admin': False}))
        await self.open_documents()
        await expect(self.page.get_by_role('button', name='新建文件夹', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button', name='添加资料', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button', name='更多操作', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='产品资料', exact=True).click()
        await self.page.get_by_role('button', name='使用指南', exact=True).click()
        await expect(self.page.get_by_role('button', name='快速上手.txt', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='更多：快速上手.txt', exact=True)).to_have_count(0)
        self.assertEqual(self.mutations, [])

    async def test_create_folder_uses_current_directory_and_displays_it(self):
        await self.open_documents()
        await self.page.get_by_role('button', name='更多操作', exact=True).click()
        await expect(self.page.get_by_role('menuitem', name='文件夹管理', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('menuitem', name='新建文件夹', exact=True)).to_have_count(0)
        await self.page.keyboard.press('Escape')
        await self.page.get_by_role('button', name='产品资料', exact=True).click()
        await self.page.get_by_role('textbox', name='搜索资料', exact=True).fill('不会匹配')
        await self.page.get_by_role('button', name='新建文件夹', exact=True).click()
        modal = self.page.get_by_role('dialog')
        await expect(modal).to_contain_text('创建位置：根目录 / 产品资料')
        await modal.get_by_role('textbox', name='文件夹名称', exact=True).fill('发布资料')
        await modal.get_by_role('button', name=re.compile(r'^保\s*存$')).click()
        await expect(modal).to_be_hidden()
        await expect(self.page.get_by_role('button', name='发布资料', exact=True)).to_be_visible()
        self.assertEqual(self.folder_creates, [{'name': '发布资料', 'parent_id': 'folder-a'}])

    async def test_chosen_upload_directory_and_request_survive_uncertain_retry(self):
        self.unknown = True
        await self.open_documents()
        modal = await self.open_upload()
        await self.choose_folder(modal, '根目录 / 产品资料 / 使用指南')
        await expect(modal.get_by_role('combobox', name='存放位置', exact=True)).to_be_enabled()
        await modal.locator('input[type=file]').set_input_files(
            {'name': 'a.txt', 'mimeType': 'text/plain', 'buffer': b'Folder content'})
        await expect(modal.get_by_role('combobox', name='存放位置', exact=True)).to_be_disabled()
        await modal.get_by_role('button', name='添加并自动处理', exact=True).click()
        await expect(modal.get_by_text('接纳结果未确认', exact=True)).to_be_visible()
        self.assertEqual(self.import_calls[0]['folder_id'], 'folder-b')
        self.assertEqual(parse_qs(urlparse(self.upload_calls[0]).query)['folder_id'], ['folder-b'])
        await self.page.reload()
        await expect(self.page.get_by_role('button', name='产品资料', exact=True)).to_be_visible()
        modal = await self.open_upload()
        await expect(modal.locator('.upload-result')).to_have_count(0)
        await self.choose_folder(modal, '根目录 / 产品资料 / 使用指南')
        await expect(modal.locator('.upload-result')).to_have_count(1)
        await expect(modal.get_by_role('combobox', name='存放位置', exact=True)).to_be_disabled()
        self.confirm_import = True
        await modal.get_by_role('button', name='确认原提交', exact=True).click()
        await expect(modal).to_be_hidden()
        self.assertEqual(len(self.upload_calls), 1)
        self.assertEqual(self.import_calls[0], self.import_calls[1])
        self.assertEqual(self.discard_calls, [])

    async def test_upload_defaults_to_current_folder_and_can_choose_root(self):
        self.confirm_import = True
        await self.open_documents()
        await self.page.get_by_role('button', name='产品资料', exact=True).click()
        modal = await self.open_upload()
        await expect(modal.locator('.upload-location .ant-select-selection-item')).to_have_text('根目录 / 产品资料')
        await self.choose_folder(modal, '根目录')
        await expect(modal.get_by_role('combobox', name='存放位置', exact=True)).to_be_enabled()
        await modal.locator('input[type=file]').set_input_files(
            {'name': 'a.txt', 'mimeType': 'text/plain', 'buffer': b'Root content'})
        await modal.get_by_role('button', name='添加并自动处理', exact=True).click()
        await expect(modal).to_be_hidden()
        self.assertIsNone(self.import_calls[0]['folder_id'])
        self.assertNotIn('folder_id', parse_qs(urlparse(self.upload_calls[0]).query))
        self.assertEqual(self.pending_folders, ['folder-a', None])

    async def test_changing_destination_discovers_only_that_folders_pending_uploads(self):
        self.recover_pending = True
        await self.open_documents()
        modal = await self.open_upload()
        await expect(modal.locator('.upload-result')).to_have_count(0)
        await self.choose_folder(modal, '根目录 / 产品资料 / 使用指南')
        await expect(modal.locator('.upload-result')).to_have_count(1)
        await expect(modal.get_by_role('combobox', name='存放位置', exact=True)).to_be_disabled()
        await modal.get_by_role('button', name='Close', exact=True).click()
        await expect(modal).to_be_hidden()
        self.assertFalse(self.retained)
        self.assertEqual(self.pending_folders, [None, 'folder-b'])
        self.assertEqual(len(self.discard_calls), 1)
        self.assertEqual(self.upload_calls, [])
        self.assertEqual(self.import_calls, [])

    async def test_refresh_preserves_directory_and_search_on_desktop_and_mobile(self):
        destination = Path(os.environ.get('KNOWLEDGE_FOLDER_EVIDENCE', '/tmp/knowledge-folders'))
        destination.mkdir(parents=True, exist_ok=True)
        for label, width, height in [('desktop', 1440, 900), ('mobile', 390, 844)]:
            await self.page.set_viewport_size({'width': width, 'height': height})
            await self.open_documents()
            await self.page.screenshot(path=str(destination / f'folders-{label}.png'), full_page=True, animations='disabled')
            await self.page.get_by_role('button', name='产品资料', exact=True).click()
            search = self.page.get_by_role('textbox', name='搜索资料', exact=True)
            await search.fill('产品介绍')
            await self.page.get_by_role('button', name='刷新资料', exact=True).click()
            await expect(search).to_have_value('产品介绍')
            await expect(self.page.locator('.document-path')).to_contain_text('产品资料')
            await expect(self.page.get_by_role('button', name='产品介绍.md', exact=True)).to_be_visible()
            modal = await self.open_upload()
            await self.page.screenshot(path=str(destination / f'upload-{label}.png'), full_page=True, animations='disabled')
            self.assertEqual(await self.page.evaluate('document.documentElement.scrollWidth'), width)
            await modal.get_by_role('button', name='Close', exact=True).click()
            await expect(modal).to_be_hidden()


    async def test_upload_inside_folder_stays_there_after_returning_to_root_and_refreshing(self):
        self.confirm_import = True
        await self.open_documents()
        await self.page.get_by_role('button', name='产品资料', exact=True).click()
        modal = await self.open_upload()
        await modal.locator('input[type=file]').set_input_files({'name':'a.txt','mimeType':'text/plain','buffer':b'Folder content'})
        await modal.get_by_role('button',name='添加并自动处理',exact=True).click()
        await expect(modal).to_be_hidden()
        await expect(self.page.get_by_role('button',name='a.txt',exact=True)).to_be_visible()
        self.assertEqual(self.import_calls[0]['folder_id'],'folder-a')
        self.assertEqual(parse_qs(urlparse(self.upload_calls[0]).query)['folder_id'],['folder-a'])
        await self.page.get_by_role('button',name='根目录',exact=True).click()
        await expect(self.page.get_by_role('button',name='a.txt',exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button',name='根目录说明.txt',exact=True)).to_be_visible()
        await self.page.get_by_role('button',name='刷新资料',exact=True).click()
        await expect(self.page.get_by_role('button',name='a.txt',exact=True)).to_have_count(0)
        await self.page.get_by_role('button',name='产品资料',exact=True).click()
        await expect(self.page.get_by_role('button',name='a.txt',exact=True)).to_be_visible()
        self.assertEqual(next(doc for doc in self.documents if doc['id']=='folder-uploaded-doc')['folder_id'],'folder-a')

    async def test_upload_from_root_into_selected_folder_reveals_that_destination(self):
        self.confirm_import = True
        await self.open_documents()
        modal = await self.open_upload()
        await self.choose_folder(modal,'根目录 / 产品资料 / 使用指南')
        await modal.locator('input[type=file]').set_input_files({'name':'a.txt','mimeType':'text/plain','buffer':b'Nested content'})
        await modal.get_by_role('button',name='添加并自动处理',exact=True).click()
        await expect(modal).to_be_hidden()
        await expect(self.page.locator('.document-path')).to_contain_text('使用指南')
        await expect(self.page.get_by_role('button',name='a.txt',exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button',name='产品介绍.md',exact=True)).to_have_count(0)
        self.assertEqual(self.import_calls[0]['folder_id'],'folder-b')
        await self.page.get_by_role('button',name='根目录',exact=True).click()
        await expect(self.page.get_by_role('button',name='a.txt',exact=True)).to_have_count(0)

    async def test_root_search_does_not_flatten_nested_documents(self):
        await self.open_documents()
        search=self.page.get_by_role('textbox',name='搜索资料',exact=True)
        await search.fill('产品介绍')
        await expect(self.page.get_by_text('没有找到匹配的文件夹或资料',exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button',name='产品介绍.md',exact=True)).to_have_count(0)
        await search.fill('')
        await self.page.get_by_role('button',name='产品资料',exact=True).click()
        await search.fill('产品介绍')
        await expect(self.page.get_by_role('button',name='产品介绍.md',exact=True)).to_be_visible()
        self.assertEqual(self.mutations,[])


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(KnowledgeFolderRegressions(name)
                              for name in KnowledgeFolderRegressions.__dict__
                              if name.startswith('test_'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
