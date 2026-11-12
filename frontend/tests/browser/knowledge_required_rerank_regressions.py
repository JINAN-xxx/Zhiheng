"""Effective reranking cannot be disabled by leaving configuration empty."""
import json,os,re
from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import expect
from knowledge_repairs_regressions import KnowledgeRepairRegressions

class RequiredRerankRegressions(KnowledgeRepairRegressions):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.defaults_writes=[]
        self.ranker_available=True

    async def respond(self,route):
        path=urlparse(route.request.url).path
        if path=='/api/knowledge/capabilities':
            ready=bool(self.defaults['default_rerank_model_id']) and self.ranker_available
            return await route.fulfill(json={'milvus_configured':True,'milvus_available':True,'native_formats':['txt','pdf'],'graph_available':True,'mineru_available':False,
                'default_embedding_configured':self.defaults_ready,'default_rerank_configured':ready,
                'default_rerank_model_id':self.defaults['default_rerank_model_id'] if ready else None})
        if path=='/api/knowledge/settings' and route.request.method=='PATCH':
            self.defaults_writes.append(route.request.post_data_json)
        return await super().respond(route)

    async def settings(self):
        await self.page.goto(self.origin+'/knowledge/base-1/documents')
        await self.page.get_by_role('button',name='知识库设置',exact=True).click()
        modal=self.page.get_by_role('dialog',name='知识库设置 · 业务知识',exact=True)
        await expect(modal.get_by_label('知识库重排模型',exact=True)).to_be_enabled()
        return modal

    async def test_rerank_choice_has_a_named_default_and_no_disable_option(self):
        modal=await self.settings()
        await modal.get_by_label('知识库重排模型',exact=True).click()
        await expect(self.page.locator('.ant-select-dropdown').get_by_title('使用默认重排模型：库内重排模型',exact=True)).to_be_visible()
        await expect(self.page.get_by_text('不使用重排模型',exact=True)).to_have_count(0)
        directory=Path(os.environ.get('REQUIRED_RERANK_EVIDENCE','/tmp/required-rerank-ui'));directory.mkdir(parents=True,exist_ok=True)
        await self.page.screenshot(path=str(directory/'required-rerank-options.png'),full_page=True,animations='disabled')
        await self.page.get_by_title('库内重排模型',exact=True).click()
        await expect(modal.get_by_role('checkbox')).to_have_count(0)
        await modal.get_by_role('button',name='保存设置',exact=True).click()
        await expect(modal).to_be_hidden()
        self.assertEqual(len(self.settings_writes),1)
        self.assertEqual(self.settings_writes[0]['rerank_model_id'],'rank-1')
        self.assertFalse(self.settings_writes[0]['use_default_rerank'])
        self.assertFalse(self.settings_writes[0]['confirm_rebuild'])

    async def test_missing_default_blocks_save_until_a_concrete_ranker_is_selected(self):
        self.defaults['default_rerank_model_id']=None
        modal=await self.settings()
        await modal.get_by_role('button',name='保存设置',exact=True).click()
        await expect(modal).to_contain_text('必须选择有效的重排模型')
        self.assertEqual(self.settings_writes,[])
        await modal.get_by_label('知识库重排模型',exact=True).click()
        await expect(self.page.locator('.ant-select-dropdown').get_by_title('默认重排模型未配置或不可用',exact=True)).to_have_class(re.compile('ant-select-item-option-disabled'))
        await self.page.get_by_title('库内重排模型',exact=True).click()
        await modal.get_by_role('button',name='保存设置',exact=True).click()
        await expect(modal).to_be_hidden()
        self.assertEqual(self.settings_writes[0]['rerank_model_id'],'rank-1')

    async def test_default_configuration_requires_rerank_and_cannot_be_cleared(self):
        self.defaults['default_rerank_model_id']=None
        await self.page.goto(self.origin+'/knowledge/settings')
        await expect(self.page.get_by_role('heading',name='知识库默认配置',exact=True)).to_be_visible()
        await self.page.get_by_role('button',name='保存配置',exact=True).click()
        await expect(self.page.get_by_text('必须选择已启用的默认重排模型',exact=True)).to_be_visible()
        self.assertEqual(self.defaults_writes,[])
        field=self.page.locator('#knowledge-default-reranker')
        await field.click()
        await self.page.get_by_title('库内重排模型',exact=True).click()
        await self.page.locator('.ant-form-item').filter(has_text='默认重排模型').locator('.ant-select-selector').hover()
        await expect(self.page.locator('#knowledge-default-reranker').locator('xpath=ancestor::*[contains(@class,"ant-select")][1]').locator('.ant-select-clear')).to_have_count(0)
        await self.page.get_by_role('button',name='保存配置',exact=True).click()
        await expect(self.page.get_by_text('知识服务配置已保存',exact=True)).to_be_visible()
        self.assertEqual(self.defaults_writes[0]['default_rerank_model_id'],'rank-1')

    async def test_creation_is_blocked_when_only_embedding_is_configured(self):
        self.defaults['default_rerank_model_id']=None
        await self.page.goto(self.origin+'/knowledge')
        await self.page.get_by_role('button',name='新建知识库',exact=True).click()
        modal=self.page.get_by_role('dialog',name='创建知识库',exact=True)
        await modal.get_by_label('知识库名称',exact=True).fill('需要完整检索服务')
        await expect(modal).to_contain_text('默认向量模型和重排模型')
        await expect(modal.get_by_role('button',name=re.compile(r'^保\s*存$'))).to_be_disabled()
        self.assertEqual(self.posts,[])

    async def test_failed_reranking_preserves_input_and_shows_no_unranked_passages(self):
        self.preparation.update(status='failed',hits=[],error={'code':'knowledge_rerank_failed','message':'重排执行失败，本次未返回检索结果'},diagnostics={'rerank_status':'failed','candidate_count':1,'retrieval_modes':{'base-1':'hybrid'},'rerank_run_id':None,'rerank_milliseconds':0,'rerank_reason':'knowledge_rerank_failed'})
        await self.page.goto(self.origin+'/knowledge/base-1/query')
        await self.page.get_by_role('textbox',name='检索问题',exact=True).fill('保留这个检索问题')
        await self.page.get_by_role('button',name='检索',exact=True).click()
        await expect(self.page.get_by_text('重排执行失败，本次未返回检索结果',exact=True)).to_be_visible()
        await expect(self.page.locator('.query-passage')).to_have_count(0)
        await expect(self.page.get_by_role('textbox',name='检索问题',exact=True)).to_have_value('保留这个检索问题')

    async def test_mobile_settings_show_required_rerank_without_overflow(self):
        await self.page.set_viewport_size({'width':390,'height':844})
        modal=await self.settings()
        await expect(modal).to_contain_text('重排为必需步骤')
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        directory=Path(os.environ.get('REQUIRED_RERANK_EVIDENCE','/tmp/required-rerank-ui'));directory.mkdir(parents=True,exist_ok=True)
        await self.page.screenshot(path=str(directory/'required-rerank-mobile.png'),full_page=True,animations='disabled')
