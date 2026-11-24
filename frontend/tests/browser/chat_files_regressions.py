"""Attachment admission and private conversation lifecycle through original UI."""
import unittest
import asyncio
import contextlib
import json
import re
from urllib.parse import parse_qs, urlparse
from playwright.async_api import expect
from developer_regressions import DeveloperBoundary
from files_regressions import FILE

class ChatFilesUIRegressions(DeveloperBoundary):
    async def test_partial_pdf_history_displays_unread_page_warning(self):
        warning = '第 1 页为图片页，未识别其中内容；已提取 304 页可读正文。'
        async def messages(route):
            await route.fulfill(json=[{'id':'pdf-message','role':'user','content':'分析正文','reasoning':'',
                'run_id':None,'created_at':'2026-11-20T00:00:00Z','attachments':[{
                    'file_id':'pdf-file','original_filename':'book.pdf','mime':'application/pdf','size':5833220,
                    'sha256':'a'*64,'artifact_file_id':'pdf-artifact','parser_version':'native-3-attachments',
                    'parse_warning':warning}]}])
        await self.page.route(self.origin + '/api/chat/conversations/c1/messages*', messages)
        await self.page.goto(self.origin + '/chat')
        await self.select_conversation('附件会话')
        await expect(self.page.locator('.attachment-parse-warnings')).to_contain_text(warning)
        await expect(self.page.locator('.attachment-parse-warnings')).to_contain_text('book.pdf')

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
        elif path=='/api/chat/attachments':
            await route.fulfill(status=201,json={**FILE,'id':'upload-1','original_filename':'note.txt'})
        else:
            await super().respond(route)

    async def test_pdf_binary_download_is_typed_for_inline_preview(self):
        message={'id':'pdf-message','role':'user','content':'请阅读附件',
            'run_id':None,'is_complete':True,'created_at':'2026-11-19T00:00:00Z',
            'attachments':[{'file_id':'pdf-1','original_filename':'report.pdf',
                            'mime':'application/pdf','size':100,'sha256':'a'*64,'artifact_file_id':None,'parser_version':None}]}
        await self.page.route(self.origin+'/api/chat/conversations/c1/messages*',
                              lambda route: route.fulfill(json=[message]))
        await self.page.route(self.origin+'/api/files/pdf-1/download',
                              lambda route: route.fulfill(body=b'%PDF-1.4\n%%EOF',content_type='application/octet-stream'))
        await self.page.goto(self.origin+'/chat')
        await self.select_conversation('附件会话')
        await self.page.get_by_role('button',name='查看附件：report.pdf',exact=True).click()
        frame=self.page.get_by_role('region',name='预览 report.pdf',exact=True).locator('iframe')
        await expect(frame).to_be_visible()
        mime=await frame.evaluate("async e => (await fetch(e.src)).headers.get('content-type')")
        self.assertEqual(mime,'application/pdf')

    async def test_attachment_only_message_freezes_ids_for_retry(self):
        await self.page.goto(self.origin+'/chat')
        await self.select_conversation('附件会话')
        composer = self.page.get_by_role('textbox', name='消息内容', exact=True)
        async with self.page.expect_file_chooser() as chooser:
            await composer.press('@')
        await (await chooser.value).set_files({'name':'note.txt','mimeType':'text/plain','buffer':b'test fact'})
        await expect(composer).to_have_value('')
        await expect(self.page.locator('.queued-file-status')).to_contain_text('TXT')
        await expect(self.page.get_by_role('button',name='发送消息',exact=True)).to_be_enabled()
        await self.page.get_by_role('button',name='发送消息',exact=True).click()
        await expect(self.page.get_by_text('受控提交失败',exact=True)).to_be_visible()
        self.assertEqual(self.changes[0]['file_ids'],['upload-1'])
        self.assertEqual(self.changes[0]['message'],'请分析上传的文件。')
        await self.page.get_by_role('button',name='使用原请求重试',exact=True).click()
        await self.page.wait_for_timeout(200)
        self.assertEqual(self.changes[0],self.changes[1])

    async def test_attachments_scroll_in_one_row_and_keep_draft_when_removed(self):
        uploads = []
        async def upload(route):
            name = parse_qs(urlparse(route.request.url).query)['filename'][0]
            uploads.append(name)
            await route.fulfill(status=201, json={**FILE, 'id': f'upload-{len(uploads)}', 'original_filename': name})
        await self.page.route(self.origin + '/api/chat/attachments?filename=*', upload)
        await self.page.goto(self.origin + '/chat')
        await self.select_conversation('附件会话')
        draft = self.page.get_by_role('textbox', name='消息内容', exact=True)
        await draft.fill('请核对这些资料')
        names = [f'研究资料-{index}.txt' for index in range(5)]
        files = [{'name': name, 'mimeType': 'text/plain', 'buffer': b'fixture'} for name in names]
        picker = self.page.locator('input[type=file]')
        await picker.set_input_files(files[:1])
        await expect(self.page.locator('.queued-file-status')).to_contain_text('TXT')
        await expect(self.page.get_by_role('button', name='向右查看附件', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button', name='向左查看附件', exact=True)).to_have_count(0)
        await picker.set_input_files(files[1:])
        cards = self.page.locator('.queued-file')
        await expect(cards).to_have_count(5)
        await expect(self.page.get_by_role('button', name='发送消息', exact=True)).to_be_enabled()
        tops = await cards.evaluate_all('nodes => nodes.map(node => node.getBoundingClientRect().top)')
        self.assertLessEqual(max(tops) - min(tops), 1)
        queue = self.page.get_by_role('region', name='待发送附件', exact=True)
        self.assertTrue(await queue.evaluate('node => node.scrollWidth > node.clientWidth'))
        right = self.page.get_by_role('button', name='向右查看附件', exact=True)
        left = self.page.get_by_role('button', name='向左查看附件', exact=True)
        await right.click()
        await expect(left).to_be_visible()
        await expect(right).to_have_count(0)
        await left.click()
        await expect(left).to_have_count(0)
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        await right.click()
        await expect(left).to_be_visible()
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        await expect(draft).to_have_value('请核对这些资料')
        for name in reversed(names):
            await self.page.get_by_role('button', name='移除附件：' + name, exact=True).click()
        await expect(queue).to_have_count(0)
        await expect(right).to_have_count(0)
        await expect(left).to_have_count(0)
        await expect(draft).to_have_value('请核对这些资料')
        self.assertEqual(len(uploads), 5)
        self.assertEqual(self.changes, [])

    async def test_capacity_error_stays_by_composer_and_preserves_attachment_and_draft(self):
        submissions = []
        received, release = asyncio.Event(), asyncio.Event()
        async def reject(route):
            submissions.append(route.request.post_data_json)
            received.set()
            await release.wait()
            await route.fulfill(status=422, json={'error': {
                'code': 'context_input_too_long', 'message': '本次内容超过已配置的模型输入容量',
                'details': {'estimated_input_tokens': 12000, 'input_budget': 8192}}})
        await self.page.route(self.origin + '/api/chat/conversations/c1/runs', reject)
        await self.page.goto(self.origin + '/chat')
        await self.select_conversation('附件会话')
        await self.page.locator('input[type=file]').set_input_files({'name': 'note.txt', 'mimeType': 'text/plain', 'buffer': b'fact'})
        draft = self.page.get_by_role('textbox', name='消息内容', exact=True)
        await draft.fill('阅读这份附件')
        await self.page.get_by_role('button', name='发送消息', exact=True).click()
        try:
            await asyncio.wait_for(received.wait(), timeout=5)
            await expect(draft).to_have_value('')
            await expect(self.page.locator('.composer .attachment-card')).to_have_count(0)
            await expect(self.page.locator('.message-row.user')).to_contain_text('note.txt')
        finally:
            release.set()
        notice = self.page.get_by_role('alert', name='发送提示', exact=True)
        await expect(notice).to_contain_text('附件和草稿已保留')
        await expect(notice).to_contain_text('12,000')
        await expect(notice).to_contain_text('8,192')
        await expect(self.page.locator('.composer .composer-notice')).to_be_visible()
        await expect(self.page.locator('.composer .attachment-card')).to_have_count(1)
        await expect(self.page.locator('.message-row.user')).to_have_count(0)
        await expect(draft).to_have_value('阅读这份附件')
        await expect(self.page.get_by_role('button', name='使用原请求重试', exact=True)).to_have_count(0)
        await expect(self.page.locator('.chat-alert')).to_have_count(0)
        await self.page.set_viewport_size({'width': 390, 'height': 844})
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        await draft.fill('精简后的问题')
        await expect(notice).to_have_count(0)
        await self.page.get_by_role('button', name='发送消息', exact=True).click()
        await expect(notice).to_be_visible()
        self.assertNotEqual(submissions[0]['client_request_id'], submissions[1]['client_request_id'])
        self.assertEqual(submissions[0]['file_ids'], submissions[1]['file_ids'])

    async def test_small_documents_have_no_count_limit(self):
        uploads = []
        async def upload(route):
            name = parse_qs(urlparse(route.request.url).query)['filename'][0]
            uploads.append(name)
            await route.fulfill(status=201, json={**FILE, 'id': f'upload-{len(uploads)}', 'original_filename': name})
        await self.page.route(self.origin + '/api/chat/attachments?filename=*', upload)
        await self.page.goto(self.origin + '/chat')
        await self.select_conversation('附件会话')
        picker = self.page.locator('input[type=file]')
        await picker.set_input_files([{'name': f'研究报告-{index}.txt', 'mimeType': 'text/plain', 'buffer': b'fixture'} for index in range(7)])
        await expect(self.page.locator('.queued-file')).to_have_count(7)
        await expect(self.page.locator('.ant-message-notice')).to_have_count(0)
        await expect(self.page.locator('.chat-alert')).to_have_count(0)
        self.assertEqual(len(uploads), 7)
        await self.page.get_by_role('button', name='移除附件：研究报告-0.txt', exact=True).click()
        await picker.set_input_files([{'name': '补充资料.txt', 'mimeType': 'text/plain', 'buffer': b'fixture'}])
        await expect(self.page.locator('.queued-file')).to_have_count(7)
        await expect(self.page.get_by_role('button', name='发送消息', exact=True)).to_be_enabled()
        self.assertEqual(len(uploads), 8)

    async def test_active_reply_uses_dots_and_survives_capture_focus_changes(self):
        started, release = asyncio.Event(), asyncio.Event()
        submissions, cancellations = [], []
        run = {'id': 'capture-run', 'resource_type': 'chat', 'resource_id': 'c1', 'status': 'running',
               'attempt_id': 'attempt-1', 'attempt_no': 1, 'revision': 1, 'projection_seq': 0,
               'text': '', 'reasoning': '', 'error': None, 'created_at': '2026-11-06T00:00:00Z',
               'usage': {'input_tokens': None, 'output_tokens': None, 'status': 'pending'}}
        async def submit(route):
            submissions.append(route.request.post_data_json)
            await route.fulfill(status=202, json=run)
        async def stream(route):
            started.set()
            await release.wait()
            with contextlib.suppress(Exception):
                complete = {**run, 'status': 'completed', 'revision': 2, 'text': '截图之后仍然完整生成',
                            'usage': {'input_tokens': 5, 'output_tokens': 8, 'status': 'known'}}
                await route.fulfill(content_type='text/event-stream', body='event: snapshot\ndata: ' + json.dumps(complete) + '\n\n')
        async def cancel(route):
            cancellations.append(route.request.url)
            await route.fulfill(json={**run, 'status': 'cancelled', 'revision': 2})
        await self.page.route(self.origin + '/api/chat/conversations/c1/runs', submit)
        await self.page.route(self.origin + '/api/runs/capture-run/events', stream)
        await self.page.route(self.origin + '/api/runs/capture-run/cancel', cancel)
        try:
            await self.page.goto(self.origin + '/chat')
            await self.select_conversation('附件会话')
            await self.page.get_by_role('textbox', name='消息内容').fill('请持续生成')
            await self.page.get_by_role('button', name='发送消息', exact=True).click()
            await asyncio.wait_for(started.wait(), timeout=5)
            await expect(self.page.locator('.typing-indicator')).to_be_visible()
            await expect(self.page.get_by_text('生成中', exact=True)).to_have_count(0)
            await self.page.evaluate("""() => {
                window.dispatchEvent(new KeyboardEvent('keydown', {key:'s', metaKey:true, shiftKey:true}));
                window.dispatchEvent(new KeyboardEvent('keydown', {key:'a', altKey:true}));
                window.dispatchEvent(new Event('blur'));
                Object.defineProperty(document, 'hidden', {configurable:true, get:() => true});
                Object.defineProperty(document, 'visibilityState', {configurable:true, get:() => 'hidden'});
                document.dispatchEvent(new Event('visibilitychange'));
            }""")
            await expect(self.page.get_by_role('button', name='停止生成', exact=True)).to_be_visible()
            await self.page.evaluate("""() => {
                Object.defineProperty(document, 'hidden', {configurable:true, get:() => false});
                Object.defineProperty(document, 'visibilityState', {configurable:true, get:() => 'visible'});
                document.dispatchEvent(new Event('visibilitychange'));
                window.dispatchEvent(new Event('focus'));
            }""")
            release.set()
            await expect(self.page.get_by_text('截图之后仍然完整生成', exact=True)).to_be_visible()
            await expect(self.page.locator('.typing-indicator')).to_have_count(0)
            self.assertEqual(len(submissions), 1)
            self.assertEqual(cancellations, [])
        finally:
            release.set()

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
        await self.page.route(self.origin + '/api/chat/attachments?filename=*',failed)
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
