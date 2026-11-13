"""Complete navigation paths with isolated HTTP fixtures, without paid generation."""
import os
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from developer_regressions import DeveloperBoundary
from knowledge_folder_regressions import KnowledgeFolderRegressions
from message_actions_regressions import chat_message
from playwright.async_api import expect


class HistoryPaging(DeveloperBoundary):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.searches=[]
        self.fail_older=False

    async def respond(self,route):
        url=urlparse(route.request.url);params=parse_qs(url.query)
        if url.path=='/api/chat/conversations':
            search=params.get('search',[''])[0];self.searches.append(search)
            archived=params.get('archived',['false'])[0]=='true'
            if search:
                rows=[{'id':'old','title':'从未加载过的旧会话','created_at':'2020-01-01T00:00:00Z'}] if '旧' in search and not archived else []
                cursor=''
            elif archived:
                rows=[{'id':'archived','title':'归档会话','archived':True,'created_at':'2020-01-01T00:00:00Z'}];cursor=''
            elif params.get('cursor'):
                rows=[{'id':'old','title':'很早的会话','created_at':'2020-01-01T00:00:00Z'}];cursor=''
            else:
                rows=[{'id':'recent','title':'最近的会话','created_at':'2026-11-10T00:00:00Z'}];cursor='conversation-next'
            return await route.fulfill(json=rows,headers={'X-Next-Cursor':cursor})
        if url.path=='/api/chat/conversations/recent/messages':
            if params.get('cursor'):
                if self.fail_older:
                    return await route.fulfill(status=503,json={'error':{'code':'temporary','message':'早期消息暂不可用'}})
                rows=[chat_message('q-old','user','很早的问题',created_at='2020-01-01T00:00:00Z'),chat_message('a-old','assistant','旧回复第一版',reply_to_message_id='q-old',created_at='2020-01-01T00:00:01Z'),chat_message('a-old-2','assistant','旧回复第二版',reply_to_message_id='q-old',generation_index=2,created_at='2020-01-01T00:00:02Z')]
                cursor=''
            else:
                rows=[]
                for index in range(15):
                    rows.extend([chat_message(f'q-{index}','user',f'当前问题 {index}',created_at=f'2026-11-10T00:00:{index*2:02}Z'),chat_message(f'a-{index}','assistant',f'当前回复 {index}。'+'已保存的内容。'*30,reply_to_message_id=f'q-{index}',created_at=f'2026-11-10T00:00:{index*2+1:02}Z')])
                cursor='message-next'
            return await route.fulfill(json=rows,headers={'X-Next-Cursor':cursor})
        if url.path.endswith('/context'):
            return await route.fulfill(json={'status':'full','summary_version':0,'covered_rounds':0,'retained_rounds':0,'dirty':False,'truncated':False,'dropped_rounds':0,'estimated_input_tokens':None,'input_budget':None,'reason':None})
        return await super().respond(route)

    async def test_load_more_and_full_search_respect_archive_filter(self):
        await self.page.goto(self.origin+'/chat')
        dialog=await self.open_history()
        await dialog.get_by_role('button',name='加载更多会话',exact=True).click()
        await expect(dialog.get_by_role('button',name='很早的会话',exact=True)).to_be_visible()
        search=dialog.get_by_role('textbox',name='搜索最近会话',exact=True)
        await search.fill('旧会话')
        await expect(dialog.get_by_role('button',name='从未加载过的旧会话',exact=True)).to_be_visible()
        self.assertIn('旧会话',self.searches)
        await dialog.get_by_text('已归档',exact=True).click()
        await expect(dialog.get_by_text('没有匹配会话',exact=True)).to_be_visible()
        await search.fill('')
        await expect(dialog.get_by_role('button',name='归档会话',exact=True)).to_be_visible()
        self.assertEqual(self.posts,[])

    async def test_older_messages_keep_viewport_draft_and_reply_versions(self):
        await self.page.goto(self.origin+'/chat')
        await self.select_conversation('最近的会话')
        await expect(self.page.locator('#message-q-0')).to_be_attached()
        await self.page.get_by_role('textbox',name='消息内容',exact=True).fill('保留我的草稿')
        await self.page.locator('.message-history').evaluate('(node)=>node.scrollTo({top:0,behavior:"instant"})')
        await self.page.wait_for_timeout(300)
        anchor=self.page.locator('#message-q-0')
        before=await anchor.bounding_box()
        await self.page.get_by_role('button',name='加载更早消息',exact=True).click()
        await expect(self.page.locator('#message-a-old-2')).to_be_attached()
        after=await anchor.bounding_box()
        self.assertAlmostEqual(before['y'],after['y'],delta=3)
        await self.page.locator('.message-history').evaluate('(node)=>node.scrollTo({top:0,behavior:"instant"})')
        await expect(self.page.get_by_text('旧回复第二版',exact=True)).to_be_visible()
        await self.page.get_by_role('button',name='上一个回复版本',exact=True).click()
        await expect(self.page.get_by_text('旧回复第一版',exact=True)).to_be_visible()
        await expect(self.page.get_by_role('textbox',name='消息内容',exact=True)).to_have_value('保留我的草稿')
        self.assertEqual(self.posts,[])
        directory=os.environ.get('BATCH2_EVIDENCE')
        if directory:
            Path(directory).mkdir(parents=True,exist_ok=True)
            await self.page.screenshot(path=str(Path(directory)/'history-older-messages.png'))

    async def test_failed_older_page_keeps_current_messages_and_can_retry(self):
        self.fail_older=True
        await self.page.goto(self.origin+'/chat');await self.select_conversation('最近的会话')
        await self.page.get_by_role('button',name='加载更早消息',exact=True).click()
        await expect(self.page.get_by_text('早期消息暂不可用',exact=True)).to_be_visible()
        await expect(self.page.locator('#message-q-0')).to_be_attached()
        self.fail_older=False
        await self.page.get_by_role('button',name='加载更早消息',exact=True).click()
        await expect(self.page.get_by_text('早期消息暂不可用',exact=True)).to_have_count(0)
        await expect(self.page.locator('#message-a-old-2')).to_be_attached()


class FolderLocations(KnowledgeFolderRegressions):
    async def test_directory_link_reload_back_forward_and_view_switch(self):
        await self.page.goto(self.origin+'/knowledge/base-1/documents?folder=folder-b')
        await expect(self.page.get_by_role('button',name='快速上手.txt',exact=True)).to_be_visible()
        await self.page.reload()
        await expect(self.page.locator('.document-path')).to_contain_text('使用指南')
        await self.page.get_by_role('button',name='产品资料',exact=True).click()
        await expect(self.page).to_have_url(self.origin+'/knowledge/base-1/documents?folder=folder-a')
        await self.page.get_by_role('button',name='根目录',exact=True).click()
        await expect(self.page).to_have_url(self.origin+'/knowledge/base-1/documents')
        await self.page.go_back()
        await expect(self.page.get_by_role('button',name='产品介绍.md',exact=True)).to_be_visible()
        await self.page.go_forward()
        await expect(self.page.get_by_role('button',name='根目录说明.txt',exact=True)).to_be_visible()
        await self.page.go_back()
        await self.page.get_by_role('tab',name='检索测试',exact=True).click()
        self.assertEqual(parse_qs(urlparse(self.page.url).query),{'folder':['folder-a']})
        await self.page.get_by_role('tab',name='资料',exact=True).click()
        await expect(self.page.get_by_role('button',name='产品介绍.md',exact=True)).to_be_visible()

    async def test_invalid_and_foreign_folder_fall_back_once_after_authorization(self):
        for suffix in ('folder=foreign-folder','folder=folder-a&folder=folder-b','folder='):
            await self.page.goto(self.origin+'/knowledge/base-1/documents?'+suffix)
            await expect(self.page).to_have_url(self.origin+'/knowledge/base-1/documents')
            await expect(self.page.get_by_role('button',name='根目录说明.txt',exact=True)).to_be_visible()
            await expect(self.page.get_by_text('目录已失效或不属于当前知识库，已返回根目录',exact=True)).to_have_count(1)

    async def test_folder_link_does_not_grant_library_access(self):
        reads=[]
        async def forbidden(route):
            reads.append(urlparse(route.request.url).path)
            return await route.fulfill(status=404,json={'error':{'code':'knowledge_not_found','message':'知识库不存在'}})
        await self.page.route('**/api/knowledge/bases/blocked**',forbidden)
        await self.page.goto(self.origin+'/knowledge/blocked/documents?folder=folder-b')
        await expect(self.page.get_by_text('知识库不存在',exact=True)).to_be_visible()
        self.assertEqual(reads,['/api/knowledge/bases/blocked'])
        await expect(self.page.get_by_role('button',name='快速上手.txt',exact=True)).to_have_count(0)

    async def test_accepted_upload_updates_location_and_survives_reload(self):
        self.confirm_import=True
        await self.open_documents();modal=await self.open_upload()
        await self.choose_folder(modal,'根目录 / 产品资料 / 使用指南')
        await modal.locator('input[type=file]').set_input_files({'name':'a.txt','mimeType':'text/plain','buffer':b'Folder content'})
        await modal.get_by_role('button',name='添加并自动处理',exact=True).click()
        await expect(modal).to_be_hidden()
        await expect(self.page).to_have_url(self.origin+'/knowledge/base-1/documents?folder=folder-b')
        await self.page.reload()
        await expect(self.page.get_by_role('button',name='a.txt',exact=True)).to_be_visible()


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(cls(name) for cls in (HistoryPaging,FolderLocations) for name in cls.__dict__ if name.startswith('test_'))
