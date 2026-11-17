"""Assistant file inputs retain stable identities and scoped history downloads."""
import json
from base64 import b64decode
from urllib.parse import urlparse

from playwright.async_api import expect
from assistant_conversation_regressions import AssistantConversationRegressions


class AssistantAttachmentRegressions(AssistantConversationRegressions):
    async def asyncSetUp(self):
        self.uploads={}
        self.upload_contents={}
        self.upload_requests=[]
        self.reject_upload=False
        await super().asyncSetUp()

    async def respond(self,route):
        request=route.request
        path=urlparse(request.url).path
        root='/api/agents/assistant-1/conversations'
        if path==root+'/attachments':
            self.upload_requests.append(request.url)
            if self.reject_upload:
                return await route.fulfill(status=422,json={'error':{'code':'unsupported_capability','message':'当前助手模型不支持图片'}})
            from urllib.parse import parse_qs
            name=parse_qs(urlparse(request.url).query)['filename'][0]
            fid='file-'+str(len(self.uploads)+1)
            file={'id':fid,'original_filename':name,'display_name':name,'mime':'image/png' if name.endswith('.png') else 'text/plain','size':len(request.post_data_buffer or b''),
                'owner_id':'user-1','uploader_id':'user-1','access':'private','status':'active','version':1,'acl_version':1,
                'uploader_access_enabled':True,'sha256':'a'*64,'source_file_id':None,'parser_version':None,'expires_at':None,
                'deleted_at':None,'retention_until':None,'created_at':'2026-11-17T00:00:00Z','updated_at':'2026-11-17T00:00:00Z',
                'reference_count':0,'storage_status':'normal','acl_entry_count':0,'acl_nearest_expire_at':None,'purpose':'chat_attachment'}
            self.uploads[fid]=file
            self.upload_contents[fid]=request.post_data_buffer or b''
            return await route.fulfill(status=201,json=file)
        if path.endswith('/download') and '/attachments/' in path:
            fid=path.split('/')[-2]
            return await route.fulfill(body=self.upload_contents[fid],content_type='application/octet-stream')
        if path.startswith(root+'/') and path.endswith('/runs'):
            cid=path.split('/')[-2];body=request.post_data_json
            self.submissions.append(body)
            session=next(item for item in self.sessions if item['id']==cid)
            old=next((item for item in self.turns[cid] if item['request_id']==body['client_request_id']),None)
            if old is None:
                old={'id':'turn-'+str(len(self.turns[cid])+1),'sequence':len(self.turns[cid])+1,'content':body['message'],
                    'request_id':body['client_request_id'],'request':body['client_request_id'],'run_id':'run-'+cid,
                    'agent_name':'制度助手','sources':[], 'attachments':[{
                        'file_id':fid,'original_filename':self.uploads[fid]['original_filename'],'mime':self.uploads[fid]['mime'],'size':self.uploads[fid]['size']
                    } for fid in body.get('file_ids',[])]}
                self.turns[cid].append(old);session['version']+=1
            if self.fail_submit:
                self.fail_submit=False
                return await route.abort('failed')
            return await route.fulfill(status=202,json=self.snapshot(old['run_id'],cid))
        return await super().respond(route)

    async def attach(self,name='table.csv',content=b'Name,Value\nRevenue,42'):
        await self.page.locator('input[type=file]').set_input_files({'name':name,'mimeType':'text/csv','buffer':content})
        await expect(self.page.locator('.assistant-file-queue')).to_contain_text('已就绪')

    async def test_file_only_send_and_history_preview(self):
        await self.open_chat();await self.attach()
        await expect(self.page.get_by_role('button',name='发送',exact=True)).to_be_enabled()
        await self.page.get_by_role('button',name='发送',exact=True).click()
        file=self.page.get_by_role('button',name='查看附件：table.csv',exact=True)
        await expect(file).to_be_enabled()
        self.assertEqual(self.submissions[0]['file_ids'],['file-1'])
        self.assertEqual(self.submissions[0]['message'],'请分析附件。')
        await file.click()
        await expect(self.page.get_by_role('dialog')).to_contain_text('Revenue,42')

    async def test_lost_admission_refresh_reuses_file_ids_without_upload(self):
        self.fail_submit=True
        await self.open_chat();await self.attach();await self.page.get_by_role('button',name='发送',exact=True).click()
        await expect(self.page.locator('.assistant-chat__pending')).to_contain_text('无法连接服务')
        original=dict(self.submissions[0]);await self.page.reload()
        # The original admission already exists in restored history, so refresh resolves it without a second send.
        await expect(self.page.get_by_role('button',name='查看附件：table.csv',exact=True)).to_be_enabled()
        self.assertEqual(len(self.upload_requests),1)
        self.assertEqual(len(self.submissions),1)
        self.assertEqual(original['file_ids'],['file-1'])

    async def test_binary_image_download_renders_safe_preview(self):
        await self.open_chat()
        image=b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6ZxQAAAAASUVORK5CYII=')
        await self.attach(name='pixel.png',content=image)
        await self.page.get_by_role('button',name='发送',exact=True).click()
        await self.page.get_by_role('button',name='查看附件：pixel.png',exact=True).click()
        picture=self.page.get_by_role('dialog').locator('img')
        await expect(picture).to_be_visible()
        await expect(picture).to_have_js_property('naturalWidth',1)

    async def test_failed_upload_blocks_send_and_can_be_removed(self):
        self.reject_upload=True;await self.open_chat()
        await self.page.locator('input[type=file]').set_input_files({'name':'photo.png','mimeType':'image/png','buffer':b'fake'})
        await expect(self.page.locator('.assistant-file-queue')).to_contain_text('当前助手模型不支持图片')
        await expect(self.page.get_by_role('button',name='发送',exact=True)).to_be_disabled()
        await self.page.get_by_role('button',name='移除附件：photo.png',exact=True).click()
        await expect(self.page.locator('.assistant-file-queue')).to_have_count(0)
        self.assertEqual(self.submissions,[])

    async def test_multiple_small_files_stay_compact_on_phone(self):
        await self.page.set_viewport_size({'width':390,'height':844});await self.open_chat()
        await self.page.locator('input[type=file]').set_input_files([{'name':f'file-{i}.txt','mimeType':'text/plain','buffer':b'example'} for i in range(8)])
        await expect(self.page.locator('.queued-file')).to_have_count(8)
        await expect(self.page.get_by_role('button',name='发送',exact=True)).to_be_enabled()
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
