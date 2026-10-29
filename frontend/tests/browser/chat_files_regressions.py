"""Attachment admission and private conversation lifecycle through original UI."""
import unittest
import re
from urllib.parse import parse_qs, urlparse
from playwright.async_api import expect
from developer_regressions import DeveloperBoundary
from files_regressions import FILE

class ChatFilesUIRegressions(DeveloperBoundary):
    async def asyncSetUp(self):
        self.conversation={'id':'c1','title':'附件会话','created_at':'2026-10-23T00:00:00Z','version':3,'archived':False}
        self.deleted=False
        self.changes=[]
        self.vision=True
        await super().asyncSetUp()

    async def respond(self,route):
        path=urlparse(route.request.url).path
        method=route.request.method
        if path=='/api/models':
            await route.fulfill(json=[{'id':'model-1','name':'日常助手','connection_id':'connection-1','upstream_model':'private','max_output_tokens':512,'enabled':True,'published':True,'model_type':'chat','vision_enabled':self.vision,'capabilities':['chat','stream']+(['vision'] if self.vision else [])}])
        elif path=='/api/chat/conversations':
            archived=parse_qs(urlparse(route.request.url).query).get('archived',['false'])[0]=='true'
            await route.fulfill(json=[] if self.deleted or archived!=self.conversation['archived'] else [self.conversation])
        elif path=='/api/chat/conversations/c1':
            if method=='PATCH':
                body=route.request.post_data_json
                self.changes.append(body)
                self.conversation.update({k:v for k,v in body.items() if k!='expected_version'})
                self.conversation['version']+=1
            elif method=='DELETE':
                self.changes.append(route.request.post_data_json)
                self.deleted=True
                await route.fulfill(status=204)
                return
            await route.fulfill(json=self.conversation)
        elif path=='/api/chat/conversations/c1/messages':
            await route.fulfill(json=[])
        elif path=='/api/chat/conversations/c1/runs':
            self.changes.append(route.request.post_data_json)
            await route.fulfill(status=503,json={'error':{'code':'test','message':'受控提交失败'}})
        elif path=='/api/files':
            await route.fulfill(status=201,json={**FILE,'id':'upload-1','original_filename':'note.txt'})
        else:
            await super().respond(route)

    async def test_attachment_only_message_freezes_ids_for_retry(self):
        await self.page.goto(self.origin+'/chat')
        await self.select_conversation('附件会话')
        await self.page.locator('input[type=file]').set_input_files({'name':'note.txt','mimeType':'text/plain','buffer':b'test fact'})
        await expect(self.page.get_by_text('上传完成',exact=True)).to_be_visible()
        await self.page.get_by_role('button',name='发送消息',exact=True).click()
        await expect(self.page.get_by_text('受控提交失败',exact=True)).to_be_visible()
        self.assertEqual(self.changes[0]['file_ids'],['upload-1'])
        self.assertEqual(self.changes[0]['message'],'请分析上传的文件。')
        await self.page.get_by_role('button',name='使用原请求重试',exact=True).click()
        await self.page.wait_for_timeout(200)
        self.assertEqual(self.changes[0],self.changes[1])

    async def test_archive_restore_delete_use_fresh_conversation_version(self):
        await self.page.goto(self.origin+'/chat')
        await self.open_history()
        await self.page.locator('.recent-session').get_by_role('button',name='更多操作').click()
        await self.page.get_by_role('menuitem',name='归档',exact=True).click()
        await expect(self.page.get_by_role('button',name='附件会话',exact=True)).to_have_count(0)
        self.assertEqual(self.changes[0],{'expected_version':3,'archived':True})
        await self.page.get_by_text('已归档',exact=True).click()
        dialog = self.page.get_by_role('dialog', name='历史记录', exact=True)
        await dialog.get_by_role('button', name='附件会话', exact=True).click()
        await expect(dialog).to_be_hidden()
        await expect(self.page.get_by_role('button',name='发送消息')).to_be_disabled()
        await self.open_history()
        await self.page.locator('.recent-session').get_by_role('button',name='更多操作').click()
        await self.page.get_by_role('menuitem',name='取消归档',exact=True).click()
        await self.page.get_by_text('最近会话',exact=True).click()
        await self.page.locator('.recent-session').get_by_role('button',name='更多操作').click()
        await self.page.get_by_role('menuitem',name='删除',exact=True).click()
        await self.page.get_by_role('button',name=re.compile(r'确\s*定'),exact=True).click()
        await expect(self.page.get_by_role('button',name='附件会话',exact=True)).to_have_count(0)
        self.assertEqual(self.changes[-1],{'expected_version':5})

    async def test_archived_conversation_cannot_retry_failed_upload(self):
        uploads=[]
        async def failed(route):
            uploads.append(route.request.url)
            await route.fulfill(status=503,json={'error':{'code':'unavailable','message':'受控上传失败'}})
        await self.page.route(self.origin + '/api/files?filename=*',failed)
        await self.page.goto(self.origin+'/chat')
        await self.select_conversation('附件会话')
        await self.page.locator('input[type=file]').set_input_files({'name':'note.txt','mimeType':'text/plain','buffer':b'failed upload'})
        retry=self.page.get_by_role('button',name='重新上传',exact=True)
        await expect(retry).to_be_enabled()
        await self.open_history()
        await self.page.locator('.recent-session').get_by_role('button',name='更多操作').click()
        await self.page.get_by_role('menuitem',name='归档',exact=True).click()
        await self.close_history()
        await expect(self.page.get_by_text('会话已归档，取消归档后可继续对话。',exact=True)).to_be_visible()
        await expect(retry).to_be_disabled()
        await retry.dispatch_event('click')
        await self.page.wait_for_timeout(150)
        self.assertEqual(len(uploads),1)

if __name__=='__main__':
    unittest.main(verbosity=2)
