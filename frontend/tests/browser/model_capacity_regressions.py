"""Model capacity evidence through the real Vue form and controlled HTTP boundaries."""
from urllib.parse import urlparse
from playwright.async_api import expect
from model_management_regressions import ModelManagementRegressions

class ModelCapacityRegressions(ModelManagementRegressions):
    async def asyncSetUp(self):
        self.model={'id':'model-1','name':'日常助手','connection_id':'connection-1','upstream_model':'upstream-private',
                    'max_output_tokens':512,'context_window_tokens':None,'capacity_note':'','capacity_status':'unknown',
                    'model_type':'chat','enabled':True,'vision_enabled':False,'capabilities':['chat','stream'],
                    'availability':{'available':True,'reason':None}}
        self.capacity_patches=[]
        await super().asyncSetUp()

    async def respond(self,route):
        path=urlparse(route.request.url).path
        if path=='/api/models' and route.request.method=='GET':
            return await route.fulfill(json=[self.model])
        if path=='/api/models/model-1' and route.request.method=='PATCH':
            body=route.request.post_data_json
            self.capacity_patches.append(body)
            self.model.update(body)
            self.model['capacity_status']='unknown' if self.model['context_window_tokens'] is None else 'documented' if self.model['capacity_note'].strip() else 'unverified'
            return await route.fulfill(json=self.model)
        return await super().respond(route)

    async def edit_capacity(self):
        await self.page.goto(self.origin+'/models')
        await self.open_model_more()
        await self.page.get_by_role('menuitem',name='编辑模型',exact=True).click()
        return self.page.locator('.ant-drawer-open .ant-drawer-content')

    async def test_evidence_and_window_survive_save_and_cannot_forge_read_status(self):
        drawer=await self.edit_capacity()
        await drawer.locator('.ant-form-item').filter(has_text='上下文窗口 tokens（可选）').get_by_role('spinbutton').fill('8192')
        await drawer.get_by_label('容量依据',exact=True).fill('  https://provider.example/spec v2  ')
        await drawer.get_by_role('button',name='保存模型',exact=True).click()
        await expect(drawer).to_be_hidden()
        detail=await self.open_model_details()
        await expect(detail).to_contain_text('已记录依据')
        await expect(detail.locator('.capacity-note')).to_have_text('https://provider.example/spec v2')
        self.assertEqual(self.capacity_patches[0]['context_window_tokens'],8192)
        self.assertNotIn('capacity_status',self.capacity_patches[0])
        self.assertNotIn('availability',self.capacity_patches[0])

    async def test_reference_alone_keeps_unknown_window_and_capacity_conflict_retains_values(self):
        drawer=await self.edit_capacity()
        await drawer.get_by_label('容量依据',exact=True).fill('Original model card; deployment limit unknown')
        await drawer.get_by_role('button',name='保存模型',exact=True).click()
        await expect(drawer).to_be_hidden()
        detail=await self.open_model_details()
        await expect(detail).to_contain_text('窗口未知')
        await self.open_model_more()
        await self.page.get_by_role('menuitem',name='编辑模型',exact=True).click()
        drawer=self.page.locator('.ant-drawer-open .ant-drawer-content')
        await drawer.locator('.ant-form-item').filter(has_text='上下文窗口 tokens（可选）').get_by_role('spinbutton').fill('512')
        await drawer.get_by_role('button',name='保存模型',exact=True).click()
        await expect(drawer.get_by_role('alert')).to_contain_text('占满上下文窗口')
        await expect(drawer.get_by_label('容量依据',exact=True)).to_have_value('Original model card; deployment limit unknown')
        self.assertEqual(len(self.capacity_patches),1)

    async def test_platform_connection_reason_blocks_trial_without_claiming_supplier_health(self):
        self.model['availability']={'available':False,'reason':'connection_disabled'}
        await self.page.goto(self.origin+'/models')
        detail=await self.open_model_details()
        await expect(detail).to_contain_text('模型连接已停用')
        await expect(detail.get_by_role('button',name='测试连接',exact=True)).to_be_disabled()
        self.assertEqual(self.capacity_patches,[])
