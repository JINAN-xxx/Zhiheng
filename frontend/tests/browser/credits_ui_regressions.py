"""Shared billing controls, independent personal/account data and reliable setting writes."""
import unittest
from playwright.async_api import expect
import billing_regressions as base


class CreditsUiRegressions(base.BillingRegressions):
    async def test_one_global_control_persists_between_credit_tabs_without_duplicate_reads(self):
        self.user['is_admin']=True
        await self.page.goto(self.origin+'/usage')
        await expect(self.page.locator('.available-points')).to_have_text('99.2')
        await expect(self.page.get_by_text('积分计费已停用',exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button',name='计费设置',exact=True)).to_have_count(1)
        await expect(self.page.locator('.credit-mode-notice')).to_have_count(0)
        await expect(self.page.locator('.credit-summary article > p')).to_have_count(0)
        await self.page.locator('.credit-billing-control').evaluate('el=>window.initialBillingControl=el')
        state_reads=sum(method=='GET' and path=='/api/billing/state' for method,path in self.billing_requests)
        own_reads=sum(method=='GET' and path=='/api/billing/me' for method,path in self.billing_requests)
        await self.page.get_by_role('tab',name='用户积分',exact=True).click()
        await expect(self.page.locator('.accounts-table')).to_be_visible()
        await expect(self.page.get_by_text('共 1 个账户',exact=True)).to_be_visible()
        self.assertTrue(await self.page.locator('.credit-billing-control').evaluate('el=>el===window.initialBillingControl'))
        self.assertEqual(sum(method=='GET' and path=='/api/billing/state' for method,path in self.billing_requests),state_reads)
        self.assertEqual(sum(method=='GET' and path=='/api/billing/me' for method,path in self.billing_requests),own_reads)
        await expect(self.page.get_by_role('button',name='计费设置',exact=True)).to_have_count(1)
        await expect(self.page.get_by_text('手工发放长期有效；回收仅影响可用积分，API Key 共享应用所有者余额。',exact=True)).to_have_count(0)
        await self.page.get_by_role('tab',name='Token 用量',exact=True).click()
        await expect(self.page.get_by_role('region',name='Token 用量统计')).to_be_visible()
        await expect(self.page.locator('.credit-billing-control')).to_have_count(0)

    async def test_global_refresh_updates_the_active_account_list(self):
        self.user['is_admin']=True
        await self.page.goto(self.origin+'/usage')
        await self.page.get_by_role('tab',name='用户积分',exact=True).click()
        await expect(self.page.locator('.accounts-table')).to_be_visible()
        before=sum(method=='GET' and path=='/api/billing/accounts' for method,path in self.billing_requests)
        self.account['available_points']='80'
        async with self.page.expect_response(lambda response:'/api/billing/accounts?' in response.url):
            await self.page.get_by_role('button',name='刷新积分',exact=True).click()
        await expect(self.page.locator('.accounts-table .point-amount')).to_have_text('80')
        self.assertEqual(sum(method=='GET' and path=='/api/billing/accounts' for method,path in self.billing_requests),before+1)

    async def test_unknown_mode_response_retries_the_same_command(self):
        self.user['is_admin']=True
        writes=[]
        stored={}
        async def mode(route):
            if route.request.method!='PATCH': return await self.respond(route)
            body=route.request.post_data_json
            writes.append(body)
            key=body['client_request_id']
            if key not in stored:
                self.state={'enabled':body['enabled'],'version':1}
                stored[key]=dict(self.state)
                return await route.fulfill(status=503,json={'error':{'code':'unavailable','message':'响应丢失，请重试'}})
            await route.fulfill(json=stored[key])
        await self.page.route('**/api/billing/state',mode)
        await self.page.goto(self.origin+'/usage')
        await self.page.get_by_role('button',name='计费设置',exact=True).click()
        dialog=self.page.get_by_role('dialog',name='积分计费设置',exact=True)
        await dialog.get_by_role('switch',name='启用积分计费',exact=True).check()
        await dialog.get_by_label('备注',exact=True).fill('确认模型价格')
        await dialog.get_by_role('button',name='保存设置',exact=True).click()
        await expect(dialog.get_by_text('响应丢失，请重试',exact=True)).to_be_visible()
        await dialog.get_by_role('button',name='保存设置',exact=True).click()
        await expect(dialog).to_be_hidden()
        await expect(self.page.get_by_text('积分计费已启用',exact=True)).to_be_visible()
        self.assertEqual(len(writes),2)
        self.assertEqual(writes[0],writes[1])
        self.assertEqual(len(stored),1)


if __name__=='__main__':unittest.main(verbosity=2)
