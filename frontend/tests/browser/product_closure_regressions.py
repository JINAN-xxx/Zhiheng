import asyncio
import re
import json
import unittest
from urllib.parse import urlparse,parse_qs
from playwright.async_api import expect
from billing_regressions import BillingRegressions
from agents_regressions import AgentsUIRegressions
from work_regressions import WorkUIRegressions
from knowledge_v2_regressions import KnowledgeV2Regressions

class TokenClosureUI(BillingRegressions):
    async def asyncSetUp(self):
        self.token_calls=[];self.token_writes=[];self.commands={};self.fail_once=False;self.deny=False
        self.tokens={'limit_tokens':100000,'quota_version':0,'points_enabled':False,'used_tokens':0,'reserved_tokens':99,
                     'records':[{'id':'u1','run_id':'unknown1','model_id':'model-1','source':'knowledge_retrieval','status':'unknown',
                                 'version':2,'points_enforced':False,'input_tokens':None,'output_tokens':None,'charged_tokens':None,'created_at':'2026-11-09T01:00:00Z'}]}
        await super().asyncSetUp()
    async def respond(self,route):
        parsed=urlparse(route.request.url);path=parsed.path
        if path=='/api/usage':
            query=parse_qs(parsed.query);self.token_calls.append(query)
            await route.fulfill(json={**self.tokens,'total':125,'page':int(query.get('page',['1'])[0]),'page_size':20})
        elif path.startswith('/api/usage/'):
            body=route.request.post_data_json;self.token_writes.append(body)
            if self.deny:
                return await route.fulfill(status=403,json={'error':{'code':'permission_denied','message':'权限已撤销'}})
            key=body['client_request_id']
            if key not in self.commands:
                if path.endswith('/quota'):
                    self.tokens.update(limit_tokens=body['limit_tokens'],quota_version=1)
                    result=dict(self.tokens,total=125,page=1,page_size=20)
                else:
                    self.tokens.update(used_tokens=10,reserved_tokens=0)
                    self.tokens['records'][0].update(status='known',input_tokens=8,output_tokens=2,charged_tokens=10,version=3)
                    result=dict(self.tokens['records'][0])
                self.commands[key]=result
            if self.fail_once:
                self.fail_once=False
                return await route.fulfill(status=503,json={'error':{'code':'unavailable','message':'响应待确认'}})
            await route.fulfill(json=self.commands[key])
        else: await super().respond(route)
    async def open_tokens(self):
        await self.page.goto(self.origin+'/usage?tab=history')
        await expect(self.page.get_by_role('heading',name='我的 Token 用量')).to_be_visible()
        await expect(self.page.get_by_text('共 125 条',exact=True)).to_be_visible()
    async def test_disabled_billing_shows_usage_without_any_token_quota_controls(self):
        self.tokens.update(used_tokens=200000)
        await self.open_tokens()
        await expect(self.page.locator('.token-total strong')).to_have_text('200,000')
        await expect(self.page.get_by_role('button',name='调整 Token 额度',exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('region',name='Token 额度')).to_have_count(0)
        await expect(self.page.get_by_role('region',name='Token 用量统计',exact=True)).to_be_visible()
        await expect(self.page.locator('.token-ledger > .ant-alert')).to_have_count(0)
        self.assertEqual(self.token_writes,[])
    async def test_unknown_tokens_release_and_pagination_requests_server_page(self):
        await self.open_tokens()
        await self.page.get_by_role('button',name='核对用量',exact=True).click()
        modal=self.page.get_by_role('dialog',name='核对未知 Token 用量')
        await modal.get_by_label('实际输入 Token',exact=True).fill('8')
        await modal.get_by_label('实际输出 Token',exact=True).fill('2')
        await modal.get_by_label('核对依据',exact=True).fill('供应商已确认')
        await modal.locator('.ant-modal-footer .ant-btn-primary').click()
        await expect(modal).to_be_hidden()
        await expect(self.page.locator('.token-desktop-records').get_by_text('已核算',exact=True)).to_be_visible()
        await self.page.locator('.ant-pagination-item-2').click()
        await expect(self.page.locator('.ant-pagination-item-2')).to_have_class('ant-pagination-item ant-pagination-item-2 ant-pagination-item-active')
        self.assertEqual(self.token_calls[-1]['page'],['2'])
    async def test_revoked_permission_refreshes_identity_and_closes_protected_view(self):
        await self.open_tokens()
        await self.page.get_by_role('button',name='核对用量',exact=True).click()
        modal=self.page.get_by_role('dialog',name='核对未知 Token 用量')
        await modal.get_by_role('checkbox').check()
        await modal.get_by_label('核对依据',exact=True).fill('检查权限变化')
        self.user['permissions']=[];self.deny=True
        await modal.locator('.ant-modal-footer .ant-btn-primary').click()
        await expect(self.page).to_have_url(self.origin+'/chat')
        self.assertEqual(self.tokens['quota_version'],0)
    async def test_user_account_token_entry_opens_requested_account(self):
        await self.page.goto(self.origin+'/usage')
        await self.page.get_by_role('tab',name='用户积分',exact=True).click()
        await self.page.locator('.accounts-table').get_by_role('button',name='Token 用量',exact=True).click()
        await expect(self.page.get_by_role('heading',name='积分管理员的 Token 用量',exact=True)).to_be_visible()
        self.assertEqual(self.token_calls[-1]['user_id'],['user-1'])
    async def test_readonly_operator_cannot_adjust_or_reconcile(self):
        self.user['permissions']=['ai:credits:read']
        await self.open_tokens()
        await expect(self.page.get_by_role('button',name='调整 Token 额度',exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button',name='核对用量',exact=True)).to_have_count(0)
        self.assertEqual(self.token_writes,[])

class WorkClosureUI(WorkUIRegressions):
    async def test_archived_task_exposes_reopen_and_member_repair(self):
        self.task['status']='archived'
        await self.open_task()
        await expect(self.page.get_by_role('button',name='重新打开',exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button',name='添加智能体',exact=True)).to_be_visible()
        await self.page.get_by_role('button',name='重新打开',exact=True).click()
        await expect(self.page.get_by_role('button',name='完成工作',exact=True)).to_be_visible()



class AgentClosureUI(AgentsUIRegressions):
    async def test_pending_creation_survives_reload_and_reuses_original_body(self):
        original={**self.config,'history_rounds':20,'client_request_id':'11111111-1111-4111-8111-111111111111'}
        pending=json.dumps({'owner':'user-1','body':original})
        await self.page.add_init_script('sessionStorage.setItem("zhiheng:agent-create:user-1",'+json.dumps(pending)+')')
        calls=[]
        async def create(route):
            if route.request.method!='POST':return await self.respond(route)
            body=route.request.post_data_json;calls.append(body)
            self.agent.update({key:value for key,value in body.items() if key!='client_request_id'})
            if len(calls)==1:
                return await route.fulfill(status=503,json={'error':{'code':'unavailable','message':'创建响应待确认'}})
            await route.fulfill(status=201,json=self.agent)
        await self.page.route('**/api/agents',create)
        await self.page.goto(self.origin+'/agents/new')
        await expect(self.page.get_by_label('系统提示词',exact=True)).to_have_value(original['system_prompt'])
        confirm=self.page.get_by_role('button',name='确认原提交',exact=True)
        await confirm.click()
        await expect(self.page.get_by_text('创建响应待确认',exact=True)).to_be_visible()
        await confirm.click()
        await expect(self.page).to_have_url(self.origin+'/agents/agent-1')
        self.assertEqual(calls,[original,original])
    async def test_api_drawer_creates_key_with_required_ranker(self):
        self.agent.update(status='published',published_version=1,published_config={**self.config,'model_id':'published-model','application_id':'app-1','knowledge_base_ids':['base-1']})
        await self.page.route('**/api/agents/agent-1/api-access',lambda route:route.fulfill(json={'application_id':'app-1','published_version':1,'model_ids':['published-model','embed-1','rank-1']}))
        await self.open_agents();await self.choose_agent_action('API')
        drawer=self.page.locator('.ant-drawer-open .ant-drawer-content')
        await drawer.get_by_role('button',name='创建密钥',exact=True).click()
        await expect(drawer.locator('.issued-key code')).to_have_text(self.secret)
        self.assertEqual(self.posts[-1]['model_ids'],['published-model','embed-1','rank-1'])

class UploadClosureUI(KnowledgeV2Regressions):
    async def test_server_pending_upload_can_import_without_session_draft_or_reupload(self):
        from knowledge_regressions import FILE,STAMP
        pending={**FILE,'id':'pending-file','original_filename':'恢复原文.txt'}
        imported=[]
        await self.page.route('**/api/knowledge/bases/base-1/uploads',lambda route:route.fulfill(json={'items':[{'request_id':'upload-request','upload_id':'pending-upload','file':pending,'folder_id':None,'created_at':STAMP}],'total':1}))
        async def documents(route):
            if route.request.method!='POST':return await self.respond(route)
            imported.append(route.request.post_data_json)
            await route.fulfill(status=202,json={'id':'job-1','base_id':'base-1','operation':'import','status':'completed','phase':'completed','version':3,'attempt_id':'attempt-1','index_revision':'revision-2','total_documents':1,'completed_documents':1,'failed_documents':0,'chunk_count':1,'document_results':[],'error':None,'created_at':STAMP,'updated_at':STAMP,'finished_at':STAMP,'cancel_allowed':False})
        await self.page.route('**/api/knowledge/bases/base-1/documents',documents)
        await self.page.goto(self.origin+'/knowledge/base-1/documents')
        await self.page.evaluate('sessionStorage.clear()')
        await self.page.get_by_role('button',name='添加资料',exact=True).click()
        modal=self.page.get_by_role('dialog')
        await expect(modal).to_contain_text('恢复原文.txt')
        await expect(modal).to_contain_text('发现 1 个待导入原文')
        await modal.get_by_role('button',name='添加并自动处理',exact=True).click()
        await expect(modal).to_be_hidden()
        self.assertEqual(imported[0]['file_ids'],['pending-file'])

class UploadDiscoveryCancellationUI(UploadClosureUI):
    async def test_pending_discovery_can_close_before_server_responds(self):
        import contextlib
        started,release=asyncio.Event(),asyncio.Event()
        async def pending(route):
            started.set()
            await release.wait()
            with contextlib.suppress(Exception): await route.fulfill(json={'items':[],'total':0})
        await self.page.route('**/api/knowledge/bases/base-1/uploads',pending)
        try:
            await self.page.goto(self.origin+'/knowledge/base-1/documents')
            await self.page.get_by_role('button',name='添加资料',exact=True).click()
            await asyncio.wait_for(started.wait(),5)
            modal=self.page.get_by_role('dialog',name='添加资料',exact=True)
            await modal.get_by_role('button',name=re.compile(r'^取\s*消$')).click()
            await expect(modal).to_be_hidden()
        finally: release.set()

if __name__=='__main__':unittest.main(verbosity=2)
