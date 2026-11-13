"""Repair behavior in a real browser with isolated HTTP fixtures only."""
import json
import os
from pathlib import Path
import re
import unittest
from urllib.parse import urlparse, parse_qs
from playwright.async_api import expect
from knowledge_v2_regressions import KnowledgeV2Regressions
from files_regressions import FILE

class KnowledgeRepairRegressions(KnowledgeV2Regressions):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.settings_writes = []
        self.upload_keys = []
        self.drop_stage_response = False
        self.expired_stage = False
        self.base.update(rerank_model_id=None, use_default_rerank=True)
        self.defaults = {'default_embed_model_id': 'embed-1', 'default_rerank_model_id': 'rank-1',
            'chunk_size': 1000, 'chunk_overlap': 200, 'version': 1, 'updated_at': self.base['updated_at']}

    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path == '/api/models':
            rows = []
            for mid, name, kind in [('embed-1', '原向量模型', 'embedding'), ('embed-2', '向量模型 B', 'embedding'), ('rank-1', '库内重排模型', 'rerank')]:
                rows.append({'id': mid, 'name': name, 'connection_id': 'fixture', 'upstream_model': mid,
                    'max_output_tokens': 512, 'model_type': kind, 'published': True, 'enabled': True, 'capabilities': [kind]})
            return await route.fulfill(json=rows)
        if path == '/api/knowledge/settings':
            if route.request.method == 'PATCH': self.defaults.update(route.request.post_data_json)
            return await route.fulfill(json=self.defaults)
        if path == '/api/knowledge/bases/base-1/settings':
            body = route.request.post_data_json
            self.settings_writes.append(body)
            self.base.update({k: v for k, v in body.items() if k in self.base})
            self.base['version'] += 1
            return await route.fulfill(json={'base': self.base, 'job': None})
        if path.startswith('/api/knowledge/bases/base-1/uploads/requests/') and self.expired_stage:
            return await route.fulfill(status=409,json={'error':{'code':'knowledge_upload_expired','message':'上次上传已过期'}})
        if path == '/api/knowledge/bases/base-1/uploads' and route.request.method == 'POST':
            self.upload_keys.append(parse_qs(urlparse(route.request.url).query).get('client_request_id', [None])[0])
            if self.drop_stage_response:
                self.drop_stage_response = False
                return await route.abort('failed')
            return await route.fulfill(status=201, json={'file': FILE, 'upload_id': 'upload-1', 'folder_id': None})
        if path == '/api/knowledge/bases/base-1/documents' and route.request.method == 'POST':
            self.posts.append(route.request.post_data_json)
            return await route.fulfill(status=202, json={
                'id':'job-upload','base_id':'base-1','operation':'import','status':'completed','phase':'completed',
                'version':1,'attempt_id':'attempt-upload','index_revision':'revision-1','total_documents':1,
                'completed_documents':1,'failed_documents':0,'chunk_count':1,'document_results':[],
                'error':None,'created_at':self.base['created_at'],'updated_at':self.base['updated_at'],
                'finished_at':self.base['updated_at'],'cancel_allowed':False})
        if path == '/api/knowledge/bases/base-1/migration':
            return await route.fulfill(json={'base_id':'base-1','document_policy':'legacy','expected_version':1,
                'acl_version':1,'sharing':{'access':'private','entries':[]},'documents':[],
                'requires_acl_cleanup':False,'can_migrate':True})
        await super().respond(route)

    async def test_library_settings_are_inside_knowledge_and_confirm_reprocessing(self):
        await self.page.goto(self.origin + '/knowledge/base-1/documents')
        await self.page.get_by_role('button', name='知识库设置', exact=True).click()
        modal = self.page.get_by_role('dialog', name='知识库设置 · 业务知识', exact=True)
        await expect(modal).to_be_visible()
        destination = Path(os.environ.get('KNOWLEDGE_REPAIR_EVIDENCE', '/tmp/knowledge-repair-ui'))
        destination.mkdir(parents=True, exist_ok=True)
        await expect(modal.get_by_label('知识库名称', exact=True)).to_be_editable()
        await self.page.screenshot(path=str(destination/'knowledge-settings.png'), full_page=True)
        await modal.get_by_label('知识库名称', exact=True).fill('业务资料库')
        await modal.get_by_label('知识库向量模型', exact=True).click()
        await self.page.get_by_title('向量模型 B', exact=True).click()
        await modal.get_by_label('知识库重排模型', exact=True).click()
        await self.page.get_by_title('库内重排模型', exact=True).click()
        await modal.get_by_role('spinbutton', name='切片大小', exact=True).fill('800')
        save = modal.get_by_role('button', name='保存并重新处理', exact=True)
        await expect(save).to_be_disabled()
        await modal.get_by_role('checkbox', name='确认重新处理现有资料并产生模型用量', exact=True).check()
        await expect(save).to_be_enabled()
        await save.click()
        await expect(modal).to_be_hidden()
        self.assertEqual(len(self.settings_writes), 1)
        body = self.settings_writes[0]
        self.assertEqual((body['embed_model_id'], body['rerank_model_id'], body['chunk_size']), ('embed-2','rank-1',800))
        self.assertEqual(body['name'], '业务资料库')
        self.assertTrue(body['confirm_rebuild'])

    async def test_stale_index_opens_repair_before_any_upload(self):
        self.base['index_status'] = 'reindex_required'
        await self.page.goto(self.origin + '/knowledge/base-1/documents')
        await expect(self.page.get_by_role('button', name='修复索引', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='添加资料', exact=True).click()
        await expect(self.page.get_by_role('dialog', name='知识库设置 · 业务知识', exact=True)).to_be_visible()
        self.assertEqual(self.upload_keys, [])

    async def test_legacy_library_has_direct_migration_and_upload_preflight(self):
        self.base['document_policy'] = 'legacy'
        await self.page.goto(self.origin + '/knowledge/base-1/documents')
        await expect(self.page.get_by_role('button', name='预览并迁移', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='添加资料', exact=True).click()
        await expect(self.page.get_by_role('dialog', name='迁移资料与共享', exact=True)).to_be_visible()
        self.assertEqual(self.upload_keys, [])

    async def test_defaults_are_in_knowledge_and_old_admin_url_redirects(self):
        await self.page.goto(self.origin + '/knowledge')
        await self.page.get_by_role('button', name='知识库更多操作', exact=True).click()
        await self.page.get_by_role('menuitem', name='默认配置', exact=True).click()
        await expect(self.page).to_have_url(re.compile(r'/knowledge/settings$'))
        await expect(self.page.get_by_role('heading', name='知识库默认配置', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('默认向量模型', exact=True)).to_be_visible()
        destination = Path(os.environ.get('KNOWLEDGE_REPAIR_EVIDENCE', '/tmp/knowledge-repair-ui'))
        destination.mkdir(parents=True, exist_ok=True)
        await expect(self.page.get_by_role('button', name='保存配置', exact=True)).to_be_enabled()
        await self.page.screenshot(path=str(destination/'knowledge-defaults.png'), full_page=True)
        await self.page.goto(self.origin + '/admin/knowledge-settings')
        await expect(self.page).to_have_url(re.compile(r'/knowledge/settings$'))

    async def test_lost_upload_response_reuses_same_staging_identity(self):
        self.drop_stage_response = True
        await self.page.goto(self.origin + '/knowledge/base-1/documents')
        await self.page.get_by_role('button', name='添加资料', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='添加资料', exact=True)
        await dialog.locator('input[type=file]').set_input_files({'name':'same.txt','mimeType':'text/plain','buffer':b'only one source'})
        await dialog.get_by_role('button', name='添加并自动处理', exact=True).click()
        await expect(dialog.locator('.inline-error')).to_be_visible()
        await dialog.get_by_role('button', name='添加并自动处理', exact=True).click()
        await expect(dialog).to_be_hidden()
        self.assertEqual(len(self.upload_keys), 2)
        self.assertTrue(self.upload_keys[0])
        self.assertEqual(self.upload_keys[0], self.upload_keys[1])

    async def test_expired_unacknowledged_upload_can_be_reselected_without_reusing_expired_key(self):
        self.expired_stage = True
        draft = {'version':1,'requestId':'import-original','folderId':None,'size':1000,'overlap':200,
            'uncertain':False,'discarding':False,'complete':False,
            'items':[{'id':'expired-request','name':'expired.txt','fileId':None,'uploadId':None}]}
        await self.page.add_init_script('sessionStorage.setItem("zhiheng:knowledge-upload:user-1:base-1:root", '+json.dumps(json.dumps(draft))+');')
        await self.page.goto(self.origin+'/knowledge/base-1/documents')
        await self.page.get_by_role('button',name='添加资料',exact=True).click()
        modal=self.page.get_by_role('dialog',name='添加资料',exact=True)
        await modal.get_by_role('button',name='添加并自动处理',exact=True).click()
        await expect(modal.get_by_text('上次上传已过期，请重新选择原文件',exact=True)).to_be_visible()
        await modal.locator('input[type=file]').set_input_files({'name':'expired.txt','mimeType':'text/plain','buffer':b'reselected source'})
        await modal.get_by_role('button',name='添加并自动处理',exact=True).click()
        await expect(modal).to_be_hidden()
        self.assertEqual(len(self.upload_keys),1)
        self.assertNotEqual(self.upload_keys[0],'expired-request')

if __name__ == '__main__': unittest.main()
