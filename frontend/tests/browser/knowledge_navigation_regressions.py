"""Knowledge sections stay responsive and retain their authorized workspace."""
import asyncio
import contextlib
import copy
import json
import os
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse
import unittest

from playwright.async_api import expect
from knowledge_repairs_regressions import KnowledgeRepairRegressions


class KnowledgeNavigationRegressions(KnowledgeRepairRegressions):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.counts = Counter()
        self.held_access = None
        self.access_denied = False
        self.other_base = {**self.base, 'id':'base-2', 'name':'另一知识库'}
        self.bases.append(self.other_base)
        self.base.update(name='项目资料库',description='项目研究与技术文档',document_count=2)
        self.documents[0].update(file_name='基于视频联网技术的隧道分段车流实时检测研究与实现—项目研究报告初稿.docx', mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document', file_size=885054)
        self.documents.append({**self.documents[0],'id':'doc-2','file_id':'file-2',
            'file_name':'视频 AI 检测算法项目立项报告-v1.2.docx','file_size':74711})
        self.query_submissions = []

    async def respond(self, route):
        parsed = urlparse(route.request.url)
        path, method = parsed.path, route.request.method
        self.counts[(method,path)] += 1
        if path == '/api/knowledge/bases/base-1' and method == 'GET':
            if self.held_access:
                await self.held_access.wait()
            with contextlib.suppress(Exception):
                if self.access_denied:
                    return await route.fulfill(status=404,json={'error':{'code':'knowledge_not_found','message':'知识库访问已撤销'}})
                return await route.fulfill(json=copy.deepcopy(self.base))
            return
        if path == '/api/knowledge/bases/base-2': return await route.fulfill(json=self.other_base)
        if path == '/api/knowledge/bases/base-2/documents': return await route.fulfill(json={'items':[],'total':0})
        if path in ['/api/knowledge/bases/base-2/jobs','/api/knowledge/bases/base-2/folders']: return await route.fulfill(json=[])
        if path == '/api/knowledge/bases/base-1/query' and method == 'POST':
            self.query_submissions.append(route.request.post_data_json)
        await super().respond(route)

    async def asyncTearDown(self):
        if self.held_access:self.held_access.set()
        destination=Path(os.environ.get('KNOWLEDGE_UI_EVIDENCE','/tmp/knowledge-navigation'))
        destination.mkdir(parents=True,exist_ok=True)
        (destination/(self._testMethodName+'-trace.json')).write_text(json.dumps({'requests':{str(k):v for k,v in self.counts.items()},'query_submissions':self.query_submissions,'page_errors':self.errors},ensure_ascii=False,indent=2))
        await super().asyncTearDown()

    async def open_documents(self):
        await self.page.goto(self.origin+'/knowledge/base-1/documents')
        await expect(self.page.get_by_role('textbox',name='搜索资料',exact=True)).to_be_visible(timeout=20000)
        await expect(self.page.get_by_role('heading',name='项目资料库',exact=True)).to_be_visible()

    async def switch(self, name):
        await self.page.get_by_role('tab',name=name,exact=True).click()
        await expect(self.page.get_by_role('tab',name=name,exact=True)).to_have_attribute('aria-selected','true')

    async def test_switch_does_not_wait_for_network_or_remount_the_library(self):
        await self.open_documents()
        search=self.page.get_by_role('textbox',name='搜索资料',exact=True)
        await search.fill('视频联网')
        await search.evaluate('(element)=>window.keptKnowledgeSearch=element')
        await self.page.locator('.knowledge-header').evaluate('(element)=>window.keptKnowledgeHeader=element')
        counts=dict(self.counts)
        self.held_access=asyncio.Event()
        try:
            await self.switch('检索测试')
            question=self.page.get_by_role('textbox',name='检索问题',exact=True)
            await expect(question).to_be_visible(timeout=500)
            await question.fill('待提交的检索问题')
            await self.switch('资料')
            await expect(search).to_have_value('视频联网',timeout=500)
            self.assertTrue(await search.evaluate('(element)=>element===window.keptKnowledgeSearch'))
            self.assertTrue(await self.page.locator('.knowledge-header').evaluate('(element)=>element===window.keptKnowledgeHeader'))
            await self.switch('检索测试')
            await expect(question).to_have_value('待提交的检索问题',timeout=500)
            for path in ['/api/models','/api/knowledge/capabilities','/api/knowledge/bases',
                '/api/knowledge/bases/base-1/documents','/api/knowledge/bases/base-1/folders','/api/knowledge/bases/base-1/jobs']:
                self.assertEqual(self.counts[('GET',path)],counts.get(('GET',path),0),path)
            self.assertEqual(self.query_submissions,[])
        finally:self.held_access.set()

    async def test_result_and_question_survive_switching_without_another_query(self):
        await self.open_documents()
        await self.switch('检索测试')
        question=self.page.get_by_role('textbox',name='检索问题',exact=True)
        await question.fill('工作时间？')
        await self.page.get_by_role('button',name='检索',exact=True).click()
        passage=self.page.locator('.query-passage').first
        await expect(passage).to_be_visible()
        await passage.evaluate('(element)=>window.keptKnowledgePassage=element')
        for _ in range(3):
            await self.switch('资料')
            await self.switch('检索测试')
        await expect(question).to_have_value('工作时间？')
        self.assertTrue(await passage.evaluate('(element)=>element===window.keptKnowledgePassage'))
        self.assertEqual(len(self.query_submissions),1)

    async def test_pagination_scroll_and_keyboard_tabs_remain_stable(self):
        template=self.documents[0]
        self.documents=[{**template,'id':f'doc-{i}','file_id':f'file-{i}','file_name':f'项目研究资料 {i:02d}.docx'} for i in range(1,46)]
        self.base['document_count']=45
        await self.open_documents()
        await self.page.locator('.ant-pagination-item-2').click()
        body=self.page.locator('.knowledge-documents__content')
        await body.evaluate('(element)=>element.scrollTop=180')
        scroll=await body.evaluate('(element)=>element.scrollTop')
        self.assertGreater(scroll,0)
        await self.page.get_by_role('tab',name='资料',exact=True).press('ArrowRight')
        await expect(self.page.get_by_role('tab',name='检索测试',exact=True)).to_be_focused()
        await expect(self.page.get_by_role('textbox',name='检索问题',exact=True)).to_be_visible()
        await self.page.get_by_role('tab',name='检索测试',exact=True).press('ArrowLeft')
        await expect(self.page.locator('.ant-pagination-item-active')).to_have_text('2')
        self.assertEqual(await body.evaluate('(element)=>element.scrollTop'),scroll)

    async def test_browser_back_retains_state_but_another_library_starts_clean(self):
        await self.open_documents()
        await self.switch('检索测试')
        question=self.page.get_by_role('textbox',name='检索问题',exact=True)
        await question.fill('保留当前输入')
        await self.switch('资料')
        await self.page.go_back()
        await expect(question).to_have_value('保留当前输入')
        await self.page.get_by_role('button',name='返回知识库',exact=True).click()
        await self.page.get_by_role('button',name='打开知识库：另一知识库',exact=True).click()
        await self.switch('检索测试')
        await expect(self.page.get_by_role('heading',name='另一知识库',exact=True)).to_be_visible()
        await expect(self.page.get_by_role('textbox',name='检索问题',exact=True)).to_have_value('')

    async def test_access_revocation_clears_cached_panels(self):
        await self.open_documents()
        await self.switch('检索测试')
        question=self.page.get_by_role('textbox',name='检索问题',exact=True)
        await question.fill('工作时间？')
        await self.page.get_by_role('button',name='检索',exact=True).click()
        await expect(self.page.locator('.query-passage')).to_be_visible()
        self.access_denied=True
        await self.page.get_by_role('tab',name='资料',exact=True).click()
        await expect(self.page.get_by_text('知识库访问已撤销',exact=True)).to_be_visible()
        await expect(self.page.locator('.query-passage')).to_have_count(0)
        await expect(self.page.get_by_role('textbox',name='搜索资料',exact=True)).to_have_count(0)

    async def test_document_layout_and_both_sections_render_on_desktop_and_mobile(self):
        destination=Path(os.environ.get('KNOWLEDGE_UI_EVIDENCE','/tmp/knowledge-navigation'))
        destination.mkdir(parents=True,exist_ok=True)
        for label,width,height in [('desktop',1440,900),('mobile',390,844)]:
            await self.page.set_viewport_size({'width':width,'height':height})
            await self.open_documents()
            name=self.page.get_by_role('button',name=self.documents[0]['file_name'],exact=True)
            await expect(name).to_be_visible()
            await self.page.screenshot(path=str(destination/f'documents-{label}.png'),full_page=True,animations='disabled')
            self.assertEqual(await self.page.locator('.knowledge-header').count(),1)
            await self.switch('检索测试')
            await expect(self.page.get_by_role('textbox',name='检索问题',exact=True)).to_be_visible()
            await self.page.screenshot(path=str(destination/f'query-{label}.png'),full_page=True,animations='disabled')
            self.assertEqual(await self.page.locator('.knowledge-header').count(),1)
            self.assertEqual(await self.page.evaluate('document.documentElement.scrollWidth'),width)

if __name__=='__main__':unittest.main()
