"""Sent attachments enter knowledge only after explicit owner confirmation."""
import contextlib
import os
import re
from pathlib import Path
import unittest
from urllib.parse import urlparse
from playwright.async_api import expect
from chat_files_regressions import ChatFilesUIRegressions
from knowledge_regressions import STAMP

BASE={'id':'base-1','name':'共享资料库','description':'团队资料','owner_id':'user-1','owner_name':'我','access':'public','sharing_scope':'public','document_policy':'library','version':1,'acl_version':1,'embed_model_id':'embed-1','chunk_size':1000,'chunk_overlap':200,'active_revision':'r1','active_job_id':None,'index_status':'ready','document_count':0,'last_error':None,'enable_knowledge_graph':False,'llm_model_id':None,'graph_status':'disabled','created_at':STAMP,'updated_at':STAMP,'can_manage':True}
DOC={'id':'doc-copy','base_id':'base-1','file_id':'copied-file','file_name':'report.txt','file_size':12,'mime':'text/plain','folder_id':None,'version':1,'status':'ready','active_revision':'r2','job_id':None,'chunk_count':1,'chunk_size':1000,'chunk_overlap':200,'error':None,'graph_status':'disabled','graph_error':None,'created_at':STAMP,'updated_at':STAMP}
JOB={'id':'job-copy','base_id':'base-1','operation':'import','status':'completed','phase':'completed','version':1,'attempt_id':'a1','index_revision':'r2','total_documents':1,'completed_documents':1,'failed_documents':0,'chunk_count':1,'document_results':[],'error':None,'created_at':STAMP,'updated_at':STAMP,'finished_at':STAMP,'cancel_allowed':False}

class ChatKnowledgeV2Regressions(ChatFilesUIRegressions):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.owner='user-1'
        self.imports=[]
        self.metadata_reads=[]
        self.lost=False
        self.already=False
        self.sources=[]
        self.reference_downloads=[]
        self.targets=[BASE]
        self.join_error_status=0
        self.join_error_code='rejected'

    async def asyncTearDown(self):
        destination=Path(os.environ.get('KNOWLEDGE_V2_EVIDENCE','/tmp/knowledge-v2-frontend'))
        destination.mkdir(parents=True,exist_ok=True)
        with contextlib.suppress(Exception):
            await self.page.screenshot(path=str(destination/(self._testMethodName+'.png')),full_page=True)
        await super().asyncTearDown()

    async def respond(self,route):
        path=urlparse(route.request.url).path
        if path=='/api/chat/conversations/c1/messages':
            await route.fulfill(json=[{'id':'msg-1','role':'user','content':'分析资料','reasoning':'','run_id':None,'created_at':STAMP,'reply_to_message_id':None,'generation_index':1,'is_complete':True,'feedback':None,'knowledge_sources':self.sources,'skill_snapshots':[],'attachments':[{'file_id':'file-1','original_filename':'report.txt','mime':'text/plain','size':12,'sha256':'a'*64,'artifact_file_id':None,'parser_version':None,'owner_id':self.owner}]}])
        elif path=='/api/knowledge/bases':
            await route.fulfill(json=self.targets)
        elif path=='/api/knowledge/bases/base-1/attachments':
            self.imports.append(route.request.post_data_json)
            if self.join_error_status:
                await route.fulfill(status=self.join_error_status,json={'error':{'code':self.join_error_code,'message':'目标资料拒绝接纳'}})
            elif self.lost and len(self.imports)==1:
                await route.fulfill(status=503,json={'error':{'code':'unknown','message':'提交结果待确认'}})
            else:
                await route.fulfill(json={'document':DOC,'job':JOB,'already_exists':self.already})
        elif path=='/api/knowledge/bases/base-1/documents/doc-copy/preview':
            await route.fulfill(json={'document_id':'doc-copy','file_id':'copied-file','text':'共享资料副本','parser_version':'native-v1','chunk_count':1})
        elif path=='/api/knowledge/bases/base-1/documents/doc-copy/download':
            self.reference_downloads.append(path)
            await route.fulfill(body='shared copy',content_type='text/plain')
        elif path=='/api/files/file-1':
            self.metadata_reads.append(path)
            from files_regressions import FILE
            await route.fulfill(json={**FILE,'owner_id':self.owner or 'user-1'})
        elif path=='/api/files/file-1/download':
            await route.fulfill(body='private original',content_type='text/plain')
        else:
            await super().respond(route)

    async def open_attachment(self):
        await self.page.goto(self.origin+'/chat')
        await self.select_conversation('附件会话')
        await self.page.get_by_role('button',name='附件操作：report.txt',exact=True).click()

    async def test_explicit_copy_discloses_target_sharing_and_deduplicates(self):
        self.already=True
        await self.open_attachment()
        self.assertEqual(self.imports,[])
        await self.page.get_by_role('menuitem',name='加入知识库',exact=True).click()
        modal=self.page.get_by_role('dialog',name='加入知识库',exact=True)
        await modal.locator('.ant-select').click()
        await self.page.get_by_title('共享资料库',exact=True).click()
        await expect(modal).to_contain_text('共享用户可查看这份资料')
        await modal.get_by_role('button',name='加入并处理',exact=True).click()
        await expect(modal).to_contain_text('已在该知识库中')
        await expect(modal.get_by_role('link',name='查看资料',exact=True)).to_have_attribute('href','/knowledge/base-1/documents')
        self.assertEqual(self.imports[0]['file_id'],'file-1')
        self.assertEqual(len(self.imports),1)
        self.assertEqual(self.metadata_reads,[])

    async def test_definitive_rejection_clears_draft_and_reopen_can_choose_target(self):
        self.join_error_status=422
        await self.open_attachment()
        await self.page.get_by_role('menuitem',name='加入知识库',exact=True).click()
        modal=self.page.get_by_role('dialog',name='加入知识库',exact=True)
        await modal.locator('.ant-select').click()
        await self.page.get_by_title('共享资料库',exact=True).click()
        await modal.get_by_role('button',name='加入并处理',exact=True).click()
        await expect(modal).to_contain_text('目标资料拒绝接纳')
        self.assertIsNone(await self.page.evaluate("sessionStorage.getItem('zhiheng:knowledge-attachment:user-1:file-1')"))
        await modal.get_by_role('button',name=re.compile(r'^取\s*消$')).click()
        await self.page.get_by_role('button',name='附件操作：report.txt',exact=True).click()
        await self.page.get_by_role('menuitem',name='加入知识库',exact=True).click()
        await expect(modal.get_by_role('button',name='加入并处理',exact=True)).to_be_visible()
        await expect(modal.locator('.ant-select')).not_to_have_class(__import__('re').compile('.*ant-select-disabled.*'))

    async def test_pending_conflict_retains_identity_when_closed_and_reopened(self):
        self.join_error_status=409
        self.join_error_code='request_pending'
        await self.open_attachment()
        await self.page.get_by_role('menuitem',name='加入知识库',exact=True).click()
        modal=self.page.get_by_role('dialog',name='加入知识库',exact=True)
        await modal.locator('.ant-select').click()
        await self.page.get_by_title('共享资料库',exact=True).click()
        await modal.get_by_role('button',name='加入并处理',exact=True).click()
        await expect(modal.get_by_role('button',name='确认原提交',exact=True)).to_be_visible()
        await modal.get_by_role('button',name=re.compile(r'^取\s*消$')).click()
        await expect(modal).to_be_hidden()
        await self.page.get_by_role('button',name='附件操作：report.txt',exact=True).click()
        await self.page.get_by_role('menuitem',name='加入知识库',exact=True).click()
        self.join_error_status=0
        await modal.get_by_role('button',name='确认原提交',exact=True).click()
        await expect(modal).to_contain_text('已加入')
        self.assertEqual(self.imports[0],self.imports[1])

    async def test_unavailable_unknown_target_allows_close_without_losing_original_identity(self):
        self.targets=[]
        self.join_error_status=404
        await self.page.add_init_script("sessionStorage.setItem('zhiheng:knowledge-attachment:user-1:file-1',JSON.stringify({requestId:'original-copy-request',targetId:'base-1',targetName:'原资料库'}))")
        await self.open_attachment()
        await self.page.get_by_role('menuitem',name='加入知识库',exact=True).click()
        modal=self.page.get_by_role('dialog',name='加入知识库',exact=True)
        await expect(modal).to_contain_text('原目标知识库当前不可用')
        await modal.get_by_role('button',name='确认原提交',exact=True).click()
        await expect(modal).to_contain_text('目标资料拒绝接纳')
        await modal.get_by_role('button',name=re.compile(r'^取\s*消$')).click()
        await expect(modal).to_be_hidden()
        draft=await self.page.evaluate("JSON.parse(sessionStorage.getItem('zhiheng:knowledge-attachment:user-1:file-1'))")
        self.assertEqual(draft['requestId'],'original-copy-request')
        self.assertEqual(self.imports,[{'file_id':'file-1','client_request_id':'original-copy-request'}])

    async def test_other_owner_has_no_join_action(self):
        self.owner='user-2'
        await self.open_attachment()
        await expect(self.page.get_by_role('menuitem',name='加入知识库',exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('menuitem',name='下载',exact=True)).to_be_visible()
        self.assertEqual(self.imports,[])
        self.assertEqual(self.metadata_reads,[])

    async def test_shared_reference_download_rechecks_library_document_access(self):
        self.sources=[{'chunk_id':'chunk-copy','document_id':'doc-copy','file_id':'copied-file',
            'source_id':'doc-copy','knowledge_base_id':'base-1','index_revision':'r2','title':'共享资料.txt',
            'content':'共享资料副本','score':0.8}]
        await self.page.goto(self.origin+'/chat')
        await self.select_conversation('附件会话')
        await self.page.get_by_text('知识来源 · 1',exact=True).click()
        await self.page.get_by_role('button',name='查看来源：共享资料.txt',exact=True).click()
        modal=self.page.get_by_role('dialog')
        await expect(modal).to_contain_text('共享资料副本')
        async with self.page.expect_download():
            await modal.get_by_role('button',name='下载资料',exact=True).click()
        self.assertEqual(len(self.reference_downloads),1)

    async def test_unknown_acceptance_retries_same_copy_request(self):
        self.lost=True
        await self.open_attachment()
        await self.page.get_by_role('menuitem',name='加入知识库',exact=True).click()
        modal=self.page.get_by_role('dialog',name='加入知识库',exact=True)
        await modal.locator('.ant-select').click()
        await self.page.get_by_title('共享资料库',exact=True).click()
        await modal.get_by_role('button',name='加入并处理',exact=True).click()
        await expect(modal).to_contain_text('提交结果待确认')
        await modal.get_by_role('button',name='确认原提交',exact=True).click()
        await expect(modal).to_contain_text('已加入')
        self.assertEqual(self.imports[0],self.imports[1])


def load_tests(loader,_tests,_pattern):
    return unittest.TestSuite(ChatKnowledgeV2Regressions(name) for name in loader.getTestCaseNames(ChatKnowledgeV2Regressions) if name in ChatKnowledgeV2Regressions.__dict__)

if __name__=='__main__':unittest.main(verbosity=2)
