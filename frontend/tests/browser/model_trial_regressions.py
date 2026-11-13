"""Independent model trials preserve permissions, recovery and account boundaries."""
import asyncio
import contextlib
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs,urlparse
from playwright.async_api import expect
from model_compact_regressions import CompactModels
from model_card_regressions import LONG_NAME,MODEL_ID

RUN_ID='7265f4f1-107d-4fb4-afd9-824c60c7f290'

class ModelTrialRegressions(CompactModels):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.permissions=['ai:models:test']
        self.rows[0]['published']=False
        self.trial_bodies=[]
        self.model_queries=[]
        self.post_started=asyncio.Event()
        self.release_post=asyncio.Event()
        self.release_events=asyncio.Event()
        self.hold_post=False
        self.pending_result=False
        self.complete=False
        self.drop_once=False
        self.cancel_calls=0
        self.cancelled=False
        self.withdraw=False
        self.last_model=MODEL_ID
        self.last_body={}

    async def asyncTearDown(self):
        self.release_post.set();self.release_events.set()
        directory=Path(os.environ.get('MODEL_TRIAL_EVIDENCE','/tmp/model-trial-ui'))
        directory.mkdir(parents=True,exist_ok=True)
        (directory/(self._testMethodName+'.json')).write_text(json.dumps({'trials':self.trial_bodies,'queries':self.model_queries,'cancels':self.cancel_calls,'errors':self.errors},ensure_ascii=False,indent=2))
        await super().asyncTearDown()

    def snapshot(self):
        kind=next(row['model_type'] for row in self.rows if row['id']==self.last_model)
        terminal=self.complete or not self.pending_result or self.cancelled or self.withdraw
        value={'id':RUN_ID,'resource_type':'connection_test','resource_id':self.last_model,
            'status':'cancelled' if self.cancelled else 'failed' if self.withdraw else 'completed' if terminal else 'pending',
            'attempt_id':'attempt-1' if terminal else None,'attempt_no':1 if terminal else 0,
            'revision':2 if terminal else 1,'projection_seq':1 if terminal else 0,
            'text':'这是模型目录中的独立试用回答。' if kind=='chat' and terminal and not self.withdraw and not self.cancelled else '',
            'error':{'code':'forbidden','message':'试用权限已撤销'} if self.withdraw else None,
            'created_at':'2026-11-09T00:00:00Z',
            'usage':{'input_tokens':8 if terminal else None,'output_tokens':2 if terminal else None,'status':'known' if terminal else 'pending'}}
        if kind=='embedding' and terminal:value['result']={'data':[{'index':0,'embedding':[0.1,0.2,0.3,0.4]}]}
        if kind=='rerank' and terminal:value['result']={'results':[{'index':1,'relevance_score':0.91},{'index':0,'relevance_score':0.42}]}
        return value

    async def respond(self,route):
        request=route.request;parsed=urlparse(request.url);path=parsed.path
        if path=='/api/models' and request.method=='GET':
            query=parse_qs(parsed.query);self.model_queries.append(query)
            management=query.get('include_disabled')==['true']
            trial=query.get('trial')==['true']
            if trial and 'ai:models:test' not in self.permissions:
                return await route.fulfill(status=403,json={'error':{'code':'forbidden','message':'无试用权限'}})
            rows=self.rows if management else [row for row in self.rows if row['enabled'] and self.connection_enabled]
            return await route.fulfill(json=rows)
        if path.startswith('/api/models/') and path.endswith('/trial') and request.method=='POST':
            self.last_model=path.split('/')[3];self.last_body=request.post_data_json
            self.trial_bodies.append(self.last_body);self.post_started.set()
            if self.drop_once:
                self.drop_once=False
                return await route.abort('failed')
            if self.hold_post:await self.release_post.wait()
            return await route.fulfill(status=202,json=self.snapshot())
        if path=='/api/runs/'+RUN_ID:
            return await route.fulfill(json=self.snapshot())
        if path=='/api/runs/'+RUN_ID+'/events':
            if self.withdraw:
                self.permissions=[]
                return await route.fulfill(content_type='text/event-stream',body='data: '+json.dumps(self.snapshot(),ensure_ascii=False)+'\n\n')
            await self.release_events.wait()
            with contextlib.suppress(Exception):await route.fulfill(status=503,json={'error':{'code':'offline','message':'连接已关闭'}})
            return
        if path=='/api/runs/'+RUN_ID+'/cancel':
            self.cancel_calls+=1;self.cancelled=True
            return await route.fulfill(json=self.snapshot())
        return await super().respond(route)

    async def catalog(self):
        await self.page.goto(self.origin+'/models')
        await expect(self.page.get_by_role('heading',name='模型目录',exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button',name='查看模型详情：'+LONG_NAME,exact=True)).to_be_visible()

    async def trial(self,name=LONG_NAME):
        await self.page.get_by_role('button',name='查看模型详情：'+name,exact=True).click()
        detail=self.page.locator('.model-detail-modal[role="dialog"]')
        await expect(detail.get_by_role('button',name='试用模型',exact=True)).to_be_enabled()
        await detail.get_by_role('button',name='试用模型',exact=True).click()
        modal=self.page.locator('.model-trial-modal[role="dialog"]')
        await expect(modal).to_be_visible()
        return modal

    async def test_tester_inputs_a_question_without_publishing_or_credential_access(self):
        await self.catalog()
        modal=await self.trial()
        await expect(modal).not_to_contain_text('此模型尚未发布')
        self.assertEqual(self.trial_bodies,[])
        await modal.get_by_role('textbox',name='试用内容',exact=True).fill('请介绍这个模型适合的任务')
        await modal.get_by_role('button',name='提交试用',exact=True).click()
        await expect(modal).to_contain_text('这是模型目录中的独立试用回答。')
        self.assertEqual(len(self.trial_bodies),1)
        self.assertEqual(self.trial_bodies[0]['prompt'],'请介绍这个模型适合的任务')
        self.assertTrue(any(query.get('trial')==['true'] for query in self.model_queries))
        self.assertFalse(any(path=='/api/models/connections' for _,path,_ in self.requests))
        self.assertFalse(self.rows[0]['published'])
        directory=Path(os.environ.get('MODEL_TRIAL_EVIDENCE','/tmp/model-trial-ui'));directory.mkdir(parents=True,exist_ok=True)
        await self.page.screenshot(path=str(directory/'trial-chat.png'),full_page=True,animations='disabled')

    async def test_unknown_submission_reuses_the_original_input_after_reload(self):
        self.drop_once=True
        await self.catalog();modal=await self.trial()
        await modal.get_by_role('textbox',name='试用内容',exact=True).fill('原问题不可被重试改写')
        await modal.get_by_role('button',name='提交试用',exact=True).click()
        await expect(modal.get_by_role('button',name='提交试用',exact=True)).to_contain_text('确认原提交')
        await expect(modal.get_by_role('textbox',name='试用内容',exact=True)).to_be_disabled()
        self.assertEqual(len(self.trial_bodies),1)
        await self.page.reload()
        await expect(self.page.get_by_role('button',name='查看模型详情：'+LONG_NAME,exact=True)).to_be_visible()
        modal=await self.trial()
        self.assertEqual(len(self.trial_bodies),1)
        await expect(modal.get_by_role('textbox',name='试用内容',exact=True)).to_have_value('原问题不可被重试改写')
        await modal.get_by_role('button',name='提交试用',exact=True).click()
        await expect(modal).to_contain_text('独立试用回答')
        self.assertEqual(self.trial_bodies[0],self.trial_bodies[1])

    async def test_slow_submission_cannot_create_a_second_request(self):
        self.hold_post=True
        await self.catalog();modal=await self.trial()
        await modal.get_by_role('textbox',name='试用内容',exact=True).fill('只提交一次')
        button=modal.get_by_role('button',name='提交试用',exact=True)
        await button.click();await asyncio.wait_for(self.post_started.wait(),5)
        await button.dispatch_event('click')
        self.assertEqual(len(self.trial_bodies),1)
        self.release_post.set()
        await expect(modal).to_contain_text('独立试用回答')

    async def test_known_run_resumes_by_get_without_resubmitting(self):
        self.pending_result=True
        await self.catalog();modal=await self.trial()
        await modal.get_by_role('textbox',name='试用内容',exact=True).fill('恢复原试用')
        await modal.get_by_role('button',name='提交试用',exact=True).click()
        await expect(modal).to_contain_text('等待执行')
        await self.page.wait_for_function("Object.keys(sessionStorage).some(k=>k.startsWith('zhiheng:model-trial:') && JSON.parse(sessionStorage.getItem(k)).runId)")
        self.complete=True
        await self.page.reload()
        await expect(self.page.get_by_role('button',name='查看模型详情：'+LONG_NAME,exact=True)).to_be_visible()
        modal=await self.trial()
        await expect(modal).to_contain_text('独立试用回答')
        self.assertEqual(len(self.trial_bodies),1)

    async def test_pending_trial_can_be_stopped(self):
        self.pending_result=True
        await self.catalog();modal=await self.trial()
        await modal.get_by_role('textbox',name='试用内容',exact=True).fill('停止这次试用')
        await modal.get_by_role('button',name='提交试用',exact=True).click()
        await expect(modal.get_by_role('button',name='停止试用',exact=True)).to_be_visible()
        await modal.get_by_role('button',name='停止试用',exact=True).click()
        await expect(modal).to_contain_text('已停止')
        self.assertEqual(self.cancel_calls,1)
        self.assertEqual(len(self.trial_bodies),1)

    async def test_permission_withdrawal_closes_trial_but_keeps_enabled_catalog(self):
        self.pending_result=True;self.withdraw=True
        await self.catalog();modal=await self.trial()
        await modal.get_by_role('textbox',name='试用内容',exact=True).fill('权限变化')
        await modal.get_by_role('button',name='提交试用',exact=True).click()
        await expect(modal).to_be_hidden()
        await expect(self.page.get_by_role('button',name='查看模型详情：'+LONG_NAME,exact=True)).to_be_visible()
        self.assertEqual(len(self.trial_bodies),1)

    async def test_structured_trials_render_dimensions_and_original_candidate_order(self):
        self.rows[1]['published']=False;self.rows[2]['enabled']=True
        await self.catalog();modal=await self.trial('知识向量')
        await modal.get_by_role('textbox',name='试用内容',exact=True).fill('向量化这份内容')
        await modal.get_by_role('button',name='提交试用',exact=True).click()
        await expect(modal).to_contain_text('维度 4')
        await modal.get_by_role('button',name=re.compile(r'^关\s*闭$')).click()
        await expect(modal).to_be_hidden()
        modal=await self.trial('结果重排序')
        await modal.get_by_role('textbox',name='试用内容',exact=True).fill('相关度排序')
        await modal.get_by_role('textbox',name='候选资料',exact=True).fill('第一份候选\n第二份候选')
        await modal.get_by_role('button',name='提交试用',exact=True).click()
        await expect(modal.locator('.trial-ranking li').first).to_contain_text('第二份候选')
        self.assertEqual(self.trial_bodies[-1]['documents'],['第一份候选','第二份候选'])

    async def test_basic_users_see_enabled_models_but_cannot_trial_them(self):
        self.permissions=[]
        await self.page.goto(self.origin+'/models')
        await expect(self.page.get_by_role('heading',name='模型目录',exact=True)).to_be_visible()
        entry=self.page.get_by_role('button',name='查看模型详情：'+LONG_NAME,exact=True)
        await expect(entry).to_be_visible()
        await entry.click()
        detail=self.page.locator('.model-detail-modal[role="dialog"]')
        await expect(detail.get_by_role('button',name='试用模型',exact=True)).to_have_count(0)
        self.assertFalse(any(query.get('trial')==['true'] for query in self.model_queries))
        self.assertEqual(self.trial_bodies,[])

    async def test_enabled_chat_model_is_selectable_without_publication(self):
        self.permissions=[]
        self.rows[0].pop('published', None)
        await self.page.goto(self.origin+'/chat')
        selector=self.page.locator('.model-select .ant-select-selector')
        await expect(selector).to_be_visible()
        await selector.click()
        await expect(self.page.locator('.ant-select-item-option-content').filter(has_text=LONG_NAME)).to_be_visible()
        await expect(self.page.locator('.ant-select-item-option-content').filter(has_text='知识向量')).to_have_count(0)
        await expect(self.page.locator('.ant-select-item-option-content').filter(has_text='结果重排序')).to_have_count(0)

    async def test_storage_failure_does_not_start_a_billable_trial(self):
        await self.catalog();modal=await self.trial()
        await self.page.evaluate("() => { Storage.prototype.setItem=()=>{throw new Error('blocked')} }")
        await modal.get_by_role('textbox',name='试用内容',exact=True).fill('不应提交')
        await modal.get_by_role('button',name='提交试用',exact=True).click()
        await expect(modal).to_contain_text('无法保存试用进度')
        self.assertEqual(self.trial_bodies,[])

    async def test_mobile_trial_remains_inside_the_viewport(self):
        await self.page.set_viewport_size({'width':390,'height':844})
        await self.catalog();modal=await self.trial()
        await modal.get_by_role('textbox',name='试用内容',exact=True).fill('手机端试用')
        await modal.get_by_role('button',name='提交试用',exact=True).click()
        await expect(modal).to_contain_text('独立试用回答')
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        directory=Path(os.environ.get('MODEL_TRIAL_EVIDENCE','/tmp/model-trial-ui'));directory.mkdir(parents=True,exist_ok=True)
        await self.page.screenshot(path=str(directory/'trial-mobile.png'),full_page=True,animations='disabled')

if __name__=='__main__':
    import unittest
    unittest.main()
