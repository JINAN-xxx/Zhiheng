"""Knowledge v2 uses only isolated HTTP fixtures; never invokes a real model."""
import contextlib
import json
import os
from pathlib import Path
import re
import unittest
from urllib.parse import urlparse

from playwright.async_api import expect
from knowledge_regressions import KnowledgeUIRegressions


class KnowledgeV2Regressions(KnowledgeUIRegressions):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.base.update(document_policy='library', sharing_scope='private', owner_name='我')
        self.defaults_ready = True
        self.requests = []
        self.acl_writes = []
        self.acl_entries = []
        self.members = []
        self.invitations = []

    async def asyncTearDown(self):
        destination = Path(os.environ.get('KNOWLEDGE_V2_EVIDENCE', '/tmp/knowledge-v2-frontend'))
        destination.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(Exception):
            await self.page.screenshot(path=str(destination / (self._testMethodName + '.png')), full_page=True)
        (destination / (self._testMethodName + '.json')).write_text(json.dumps(self.requests, ensure_ascii=False, indent=2))
        await super().asyncTearDown()

    async def respond(self, route):
        path, method = urlparse(route.request.url).path, route.request.method
        self.requests.append({'path': path, 'method': method})
        if path == '/api/knowledge/capabilities':
            await route.fulfill(json={'milvus_configured': True, 'milvus_available': True,
                'native_formats': ['txt', 'md', 'pdf', 'docx', 'xlsx'], 'graph_available': True,
                'mineru_available': False, 'default_embedding_configured': self.defaults_ready, 'default_rerank_configured': True, 'default_rerank_model_id': 'rank-1'})
        elif path == '/api/knowledge/bases/base-1/members':
            await route.fulfill(json=self.members)
        elif path == '/api/knowledge/bases/base-1/invitations':
            if method=='POST':
                invitation={'id':'invite-1','status':'active','expires_at':'2026-11-10T03:00:00Z',
                    'created_at':'2026-11-09T03:00:00Z','accepted_at':None}
                self.invitations=[invitation]
                await route.fulfill(status=201,json={'invitation':invitation,'token':'A'*43})
            else:await route.fulfill(json=self.invitations)
        elif path == '/api/knowledge/bases/base-1/acl':
            if method == 'PATCH':
                payload = route.request.post_data_json
                self.acl_writes.append(payload)
                self.acl_entries = payload['entries']
                self.base.update(access=payload['access'], acl_version=2)
            await route.fulfill(json={'base': self.base, 'entries': self.acl_entries,
                'builtin_permissions': [{'source': 'owner', 'enabled': True, 'deny_overridable': False}]})
        elif path == '/api/knowledge/bases/base-1/users':
            await route.fulfill(json=[{'id': 'user-2', 'username': '同事'}])
        elif path == '/api/knowledge/bases/base-1/documents/doc-1/download':
            await route.fulfill(body='fixture content', content_type='text/plain')
        else:
            await super().respond(route)

    async def test_create_uses_platform_default_and_name_description_only(self):
        await self.page.goto(self.origin + '/knowledge')
        await self.page.get_by_role('button', name='新建知识库', exact=True).click()
        modal = self.page.get_by_role('dialog')
        await modal.get_by_label('知识库名称', exact=True).fill('团队资料')
        await expect(modal.get_by_role('combobox')).to_have_count(0)
        await expect(modal.get_by_role('spinbutton')).to_have_count(0)
        await modal.get_by_role('button', name=re.compile(r'^保\s*存$')).click()
        await expect(modal).to_be_hidden()
        self.assertEqual(self.posts, [{'name': '团队资料', 'description': ''}])

    async def test_missing_default_explicitly_blocks_create(self):
        self.defaults_ready = False
        await self.page.goto(self.origin + '/knowledge')
        await self.page.get_by_role('button', name='新建知识库', exact=True).click()
        modal = self.page.get_by_role('dialog')
        await expect(modal).to_contain_text('请联系管理员配置有效的默认向量模型和重排模型')
        await expect(modal.get_by_role('button', name=re.compile(r'^保\s*存$'))).to_be_disabled()
        self.assertEqual(self.posts, [])

    async def test_owned_shared_scope_and_no_storage_entry(self):
        self.bases.append({**self.base, 'id': 'shared-1', 'name': '同事资料', 'owner_id': 'user-2',
            'owner_name': '同事', 'can_manage': False, 'sharing_scope': 'users'})
        await self.page.goto(self.origin + '/knowledge')
        await expect(self.page.get_by_role('heading', name='知识库', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('同事资料', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='与我共享', exact=True).click()
        await expect(self.page.get_by_text('同事资料', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('所有者：同事', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='存储管理', exact=True)).to_have_count(0)

    async def test_add_material_has_no_acl_or_chunk_fields(self):
        await self.page.goto(self.origin + '/knowledge/base-1/documents')
        await expect(self.page.get_by_role('button', name='导入受管文件', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button', name='原文权限：业务说明.txt', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='添加资料', exact=True).click()
        modal = self.page.get_by_role('dialog')
        await expect(modal.get_by_role('spinbutton')).to_have_count(0)
        await expect(modal).to_contain_text('自动处理')
        await expect(modal).not_to_contain_text('原文权限')

    async def test_invalid_upload_files_never_enter_queue_and_warning_expires(self):
        await self.page.goto(self.origin+'/knowledge/base-1/documents')
        await self.page.get_by_role('button',name='添加资料',exact=True).click()
        modal=self.page.get_by_role('dialog')
        await modal.locator('input[type=file]').set_input_files([
            {'name':'scan.png','mimeType':'image/png','buffer':b'fixture'},
            {'name':'empty.txt','mimeType':'text/plain','buffer':b''}])
        await expect(modal.locator('.upload-result')).to_have_count(0)
        warning=self.page.locator('.ant-message-warning')
        await expect(warning).to_be_visible()
        await expect(warning).to_be_hidden(timeout=6500)
        self.assertFalse(any(r['method']=='POST' for r in self.requests))

    async def test_failed_processing_reuses_original_document_and_unknown_retry_key(self):
        self.documents[0].update(status='failed',error={'code':'model','message':'处理暂时失败'})
        from chat_knowledge_v2_regressions import JOB
        retries=[]
        async def retry(route):
            retries.append(route.request.post_data_json)
            if len(retries)==1:
                await route.fulfill(status=503,json={'error':{'code':'unknown','message':'处理接纳结果待确认'}})
            else:
                await route.fulfill(json=JOB)
        await self.page.route('**/api/knowledge/bases/base-1/documents/doc-1/rechunk',retry)
        await self.page.goto(self.origin+'/knowledge/base-1/documents')
        await self.page.get_by_role('button',name='重试处理',exact=True).click()
        await expect(self.page.get_by_text('处理接纳结果待确认',exact=False)).to_be_visible()
        await self.page.get_by_role('button',name='重试处理',exact=True).click()
        await expect(self.page.locator('.ant-message-success')).to_be_visible()
        self.assertEqual(retries[0],retries[1])
        self.assertFalse(any('/uploads' in r['path'] and r['method']=='POST' for r in self.requests))

    async def test_shared_reader_download_uses_document_boundary(self):
        self.base.update(can_manage=False, owner_id='user-2')
        await self.page.goto(self.origin + '/knowledge/base-1/documents')
        async with self.page.expect_download():
            await self.page.get_by_role('button', name='下载：业务说明.txt', exact=True).click()
        self.assertTrue(any(r['path'].endswith('/documents/doc-1/download') for r in self.requests))
        self.assertFalse(any(r['path'] == '/api/files/file-1/download' for r in self.requests))
        await expect(self.page.get_by_role('button', name='共享', exact=True)).to_have_count(0)

    async def test_sharing_uses_an_invitation_without_an_account_directory(self):
        await self.page.goto(self.origin+'/knowledge/base-1/documents')
        await self.page.get_by_role('button',name='共享',exact=True).click()
        modal=self.page.get_by_role('dialog')
        await expect(modal.get_by_role('combobox')).to_have_count(0)
        await modal.get_by_role('button',name='生成一次性邀请链接',exact=True).click()
        await expect(modal.get_by_role('textbox',name='邀请链接',exact=True)).to_have_value(self.origin+'/knowledge/join#'+'A'*43)
        await expect(modal).to_contain_text('24 小时')
        self.assertEqual(self.acl_writes,[])
        self.assertFalse(any(item['path'].endswith('/users') for item in self.requests))

    async def test_legacy_migration_shows_access_diff_before_owner_confirms(self):
        self.base.update(document_policy='legacy',access='private')
        submits=[]
        from chat_knowledge_v2_regressions import JOB
        async def migration(route):
            if route.request.method=='POST':
                submits.append(route.request.post_data_json)
                await route.fulfill(json=JOB)
            else:
                await route.fulfill(json={'base_id':'base-1','document_policy':'legacy',
                    'expected_version':1,'acl_version':1,'sharing':{'access':'private','entries':[{'user_id':'user-2','effect':'allow','expires_at':None}]},
                    'documents':[{'document_id':'doc-1','file_name':'业务说明.txt','can_migrate':True}],
                    'requires_acl_cleanup':False,'can_migrate':True})
        await self.page.route('**/api/knowledge/bases/base-1/migration',migration)
        await self.page.goto(self.origin+'/knowledge/base-1/documents')
        await self.page.get_by_role('button',name='更多操作',exact=True).click()
        await self.page.get_by_role('menuitem',name='迁移资料与共享',exact=True).click()
        modal=self.page.get_by_role('dialog',name='迁移资料与共享',exact=True)
        await expect(modal).to_contain_text('已有成员')
        await expect(modal).to_contain_text('业务说明.txt')
        await expect(modal.get_by_role('button',name='确认迁移',exact=True)).to_be_disabled()
        self.assertEqual(submits,[])
        await modal.get_by_role('checkbox').check()
        await modal.get_by_role('button',name='确认迁移',exact=True).click()
        await expect(modal).to_be_hidden()
        self.assertEqual(submits[0]['confirm_shared_access'],True)
        self.assertEqual(submits[0]['expected_acl_version'],1)

    async def test_delete_shows_visible_and_private_reference_impact(self):
        await self.page.route('**/api/knowledge/bases/base-1/references',lambda route:route.fulfill(json={
            'items':[{'name':'我的智能体','resource_type':'agent','path':'/agents/owned-agent'}],'hidden_count':2,'total':3}))
        await self.page.goto(self.origin+'/knowledge/base-1/documents')
        await self.page.get_by_role('button',name='更多操作',exact=True).click()
        await self.page.get_by_role('menuitem',name='删除知识库',exact=True).click()
        modal=self.page.get_by_role('dialog')
        await expect(modal).to_contain_text('我的智能体')
        await expect(modal.get_by_role('link',name='我的智能体',exact=True)).to_have_attribute('href','/agents/owned-agent')
        await expect(modal).to_contain_text('2 个无权查看详情的关联')
        await expect(modal.get_by_role('button',name=re.compile(r'^删\s*除$'))).to_be_disabled()
        await modal.get_by_role('checkbox').check()
        await expect(modal.get_by_role('button',name=re.compile(r'^删\s*除$'))).to_be_enabled()

    async def prepare_graph_result(self, diagnostics):
        from graph_regressions import job
        self.base.update(enable_knowledge_graph=True,graph_status='ready')
        graph_job=job(operation='query')
        graph_job['result']={'context':'图谱最终上下文','entities':[],'relationships':[],'sources':[],
            'diagnostics':diagnostics}
        await self.page.add_init_script("sessionStorage.setItem('zhiheng:graph-query:user-1:base-1',JSON.stringify({intent:null,jobId:'graph-job-1',cancelRequested:false,settled:true}))")
        await self.page.route('**/api/knowledge/bases/base-1/graph/jobs/graph-job-1',lambda route:route.fulfill(json=graph_job))

    async def test_graph_diagnostics_survive_restore_and_report_no_candidates(self):
        await self.prepare_graph_result({'candidate_count':0,'retrieval_modes':{'base-1':'hybrid'},
            'rerank_status':'no_candidates'})
        await self.page.goto(self.origin+'/knowledge/base-1/query')
        await expect(self.page.get_by_text('图谱最终上下文',exact=True)).to_be_visible()
        await self.page.get_by_role('button',name='高级诊断',exact=True).click()
        await expect(self.page.locator('.query-diagnostics')).to_contain_text('候选 0 条')
        await expect(self.page.locator('.query-diagnostics')).to_contain_text('无候选，未重排')

    async def test_partial_graph_diagnostics_do_not_invent_recall_counts(self):
        await self.prepare_graph_result({'rerank_status':'not_configured'})
        await self.page.goto(self.origin+'/knowledge/base-1/query')
        await expect(self.page.get_by_text('图谱最终上下文',exact=True)).to_be_visible()
        await self.page.get_by_role('button',name='高级诊断',exact=True).click()
        await expect(self.page.locator('.query-diagnostics')).to_contain_text('图谱检索')
        await expect(self.page.locator('.query-diagnostics')).to_contain_text('候选数未记录')
        await expect(self.page.locator('.query-diagnostics')).to_contain_text('未配置')
        await expect(self.page.locator('.query-diagnostics')).not_to_contain_text('候选 0 条')

    async def test_graph_reader_never_exposes_owner_diagnostics(self):
        self.base.update(owner_id='user-2',can_manage=False)
        await self.prepare_graph_result({'candidate_count':7,'retrieval_modes':{'base-1':'hybrid'},
            'rerank_status':'fallback','rerank_reason':'管理诊断原因'})
        await self.page.goto(self.origin+'/knowledge/base-1/query')
        await expect(self.page.get_by_text('图谱最终上下文',exact=True)).to_be_visible()
        await expect(self.page.locator('.query-diagnostics')).to_have_count(0)
        await expect(self.page.get_by_text('管理诊断原因',exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button',name='高级诊断',exact=True)).to_have_count(0)

    async def test_owner_diagnostics_identify_hybrid_fallback_without_fake_scores(self):
        self.preparation['diagnostics']={'candidate_count':17,'retrieval_modes':{'base-1':'hybrid'},
            'rerank_status':'fallback','rerank_reason':'重排服务暂时不可用'}
        self.preparation['hits'][0].update(fusion_score=0.7,rerank_score=None,vector_score=None)
        await self.page.goto(self.origin+'/knowledge/base-1/query')
        await self.page.get_by_role('textbox',name='检索问题',exact=True).fill('时间')
        await self.page.get_by_role('button',name='检索',exact=True).click()
        await expect(self.page.locator('.query-diagnostics')).to_have_count(0)
        await self.page.get_by_role('button',name='高级诊断',exact=True).click()
        await expect(self.page.locator('.query-diagnostics')).to_contain_text('向量与关键词混合检索')
        await expect(self.page.locator('.query-diagnostics')).to_contain_text('候选 17 条')
        await expect(self.page.locator('.query-diagnostics')).to_contain_text('临时降级')
        await expect(self.page.locator('.query-passage')).not_to_contain_text('重排分数')

    async def test_reader_has_no_management_diagnostics_or_graph_controls(self):
        self.base.update(owner_id='user-2',can_manage=False)
        self.preparation['diagnostics']={}
        await self.page.goto(self.origin+'/knowledge/base-1/query')
        await self.page.get_by_role('textbox',name='检索问题',exact=True).fill('时间')
        await self.page.get_by_role('button',name='检索',exact=True).click()
        await expect(self.page.locator('.query-passage')).to_have_count(1)
        await expect(self.page.get_by_role('button',name='高级诊断',exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('combobox')).to_have_count(0)

    async def test_retrieval_keeps_final_order_without_fake_percentages(self):
        self.preparation['hits'].append({**self.preparation['hits'][0], 'chunk_id': 'chunk-2',
            'content': '最终第二条', 'score': 0.99})
        await self.page.goto(self.origin + '/knowledge/base-1/query')
        await self.page.get_by_role('textbox', name='检索问题', exact=True).fill('时间')
        await self.page.get_by_role('button', name='检索', exact=True).click()
        await expect(self.page.locator('.query-passage').first).to_contain_text('九点')
        await expect(self.page.locator('.query-passage').nth(1)).to_contain_text('最终第二条')
        await expect(self.page.locator('.knowledge-query')).not_to_contain_text('%')


def load_tests(loader, _tests, _pattern):
    return unittest.TestSuite(KnowledgeV2Regressions(name)
        for name in loader.getTestCaseNames(KnowledgeV2Regressions)
        if name in KnowledgeV2Regressions.__dict__)


if __name__ == '__main__':
    unittest.main(verbosity=2)
