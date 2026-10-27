"""Original graph workspace with explicit core graph contracts."""
import asyncio
import contextlib
import unittest
from urllib.parse import urlparse

from playwright.async_api import expect
from knowledge_regressions import KnowledgeUIRegressions, STAMP


def job(status='completed', operation='build', version=2):
    return {'id':'graph-job-1','base_id':'base-1','operation':operation,'status':status,'phase':status,'version':version,'attempt_id':'graph-attempt-1','graph_revision':'graph-revision-1','processed_documents':1,'total_documents':1,'progress':{'extracted_chunks':1,'total_chunks':1,'entities':2,'relations':1},'error':None,'result':{'entities':2,'relations':1,'chunks':1,'seconds':1.25} if operation=='preview' else None,'cancel_allowed':status not in ['completed','failed','cancelled','interrupted'],'created_at':STAMP,'updated_at':STAMP,'finished_at':STAMP if status=='completed' else None}


def quality_query_job(status='completed', version=2):
    row = job(status, 'query', version)
    if status == 'completed':
        row['result'] = {'context': '质量探针私有聚合上下文', 'entities': [], 'relationships': [], 'sources': [{'document_id': 'doc-1', 'file_id': 'file-1', 'chunk_id': 'chunk-1', 'ordinal': 0, 'content_hash': 'a' * 64, 'content': '质量探针私有来源正文'}]}
    return row

class GraphRegressions(KnowledgeUIRegressions):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.base.update(enable_knowledge_graph=True,llm_model_id='chat-1',graph_status='ready')
        self.config={'version':1,'llm_model_id':'chat-1','reasoning_strategy':'auto','timeout_seconds':240,'max_output_tokens':16384,'concurrency':2}
        self.graph_posts=[]
        self.graph_job=job()
        self.graph={'revision':'graph-revision-1','nodes':[{'id':'甲','entity_name':'甲','entity_type':'组织','description':'<img src=x onerror=alert(1)>'},{'id':'乙','entity_name':'乙','entity_type':'人','description':'研发成员'}],'edges':[{'id':'rel-1','source':'甲','target':'乙','description':'合作'}],'is_truncated':False}
        self.slow_graph=None

    async def respond(self,route):
        path,method=urlparse(route.request.url).path,route.request.method
        if path=='/api/knowledge/capabilities':
            await route.fulfill(json={'milvus_configured':True,'milvus_available':True,'native_formats':['txt'],'graph_available':True,'mineru_available':False});return
        prefix='/api/knowledge/bases/base-1/graph'
        if not path.startswith(prefix):
            await super().respond(route);return
        suffix=path[len(prefix):]
        if suffix=='/config':
            if method=='PATCH':
                body=route.request.post_data_json;self.graph_posts.append((suffix,body));self.config={**body,'version':self.config['version']+1};self.config.pop('expected_version',None)
            await route.fulfill(json={'config':self.config,'available':True,'active_revision':'graph-revision-1','requires_rebuild':False,'can_manage':True,'supported_reasoning_strategies':['auto','default']});return
        if suffix=='/jobs':await route.fulfill(json=[]);return
        if suffix=='/labels':await route.fulfill(json={'items':['甲','乙'],'truncated':False});return
        if suffix=='/subgraph':
            if self.slow_graph:await self.slow_graph.wait()
            await route.fulfill(json=self.graph);return
        if suffix in ['/build','/preview']:
            self.graph_posts.append((suffix,route.request.post_data_json))
            self.graph_job=job('completed',suffix[1:])
            await route.fulfill(status=202,json=self.graph_job);return
        if suffix=='/jobs/graph-job-1/cancel':
            self.graph_posts.append((suffix,route.request.post_data_json));self.graph_job=job('cancelled',self.graph_job['operation'],3)
            await route.fulfill(json=self.graph_job);return
        if suffix=='/jobs/graph-job-1':await route.fulfill(json=self.graph_job);return
        await route.fulfill(status=404,json={'error':{'code':'fixture_missing','message':path}})

    async def test_graph_route_restores_original_setup_and_canvas(self):
        await self.page.goto(self.origin+'/knowledge/base-1/graph')
        await expect(self.page.get_by_role('heading',name='图谱配置',exact=True)).to_be_visible()
        await expect(self.page.get_by_role('heading',name='图谱试构建',exact=True)).to_be_visible()
        await expect(self.page.locator('.knowledge-graph__canvas canvas')).to_be_visible()
        await expect(self.page.get_by_role('img',name='知识图谱，2 个节点，1 个关系',exact=True)).to_be_visible()
        self.assertEqual(await self.page.locator('img[src="x"]').count(),0)

    async def test_build_uses_frozen_config_version_and_stable_request(self):
        await self.page.goto(self.origin+'/knowledge/base-1/graph')
        await self.page.get_by_role('button',name='构建图谱',exact=True).click()
        await expect(self.page.get_by_text('图谱构建完成',exact=True)).to_be_visible()
        bodies=[body for path,body in self.graph_posts if path=='/build']
        self.assertEqual(len(bodies),1)
        self.assertEqual(bodies[0]['expected_version'],1)
        self.assertFalse(bodies[0]['rebuild'])
        self.assertTrue(bodies[0]['client_request_id'])


    async def test_lost_build_response_restores_same_request_after_reload(self):
        accepted=[]
        async def submit(route):
            accepted.append(route.request.post_data_json)
            if len(accepted)==1:
                await route.fulfill(status=503,json={'error':{'code':'unavailable','message':'提交结果待确认'}})
            else:await route.fulfill(status=202,json=job())
        await self.page.route('**/api/knowledge/bases/base-1/graph/build',submit)
        await self.page.goto(self.origin+'/knowledge/base-1/graph')
        await self.page.get_by_role('button',name='构建图谱',exact=True).click()
        await expect(self.page.get_by_role('button',name='确认原请求或恢复查看',exact=True)).to_be_visible()
        await self.page.reload()
        await expect(self.page.get_by_role('button',name='确认原请求或恢复查看',exact=True)).to_be_visible()
        self.assertEqual(len(accepted),1)
        await self.page.get_by_role('button',name='确认原请求或恢复查看',exact=True).click()
        await expect(self.page.get_by_text('图谱构建完成',exact=True)).to_be_visible()
        self.assertEqual(accepted[0],accepted[1])

    async def test_revoked_graph_read_clears_previously_visible_canvas(self):
        await self.page.goto(self.origin+'/knowledge/base-1/graph')
        await expect(self.page.locator('.knowledge-graph__canvas canvas')).to_be_visible()
        async def deny(route):
            await route.fulfill(status=403,json={'error':{'code':'graph_sources_unavailable','message':'无权读取图谱全部原文'}})
        await self.page.route('**/api/knowledge/bases/base-1/graph/subgraph?*',deny)
        await self.page.get_by_role('button',name='刷新知识图谱',exact=True).click()
        await expect(self.page.locator('.knowledge-graph__canvas')).to_have_count(0)
        await expect(self.page.get_by_text('无权读取图谱全部原文',exact=True).first).to_be_visible()

    async def test_graph_query_mode_reaches_paid_endpoint_and_restores_sources(self):
        requests=[]
        async def submit(route):
            requests.append(route.request.post_data_json)
            result=job(operation='query')
            result['result']={'context':'图谱证据是业务九点开门。','entities':self.graph['nodes'],'relationships':self.graph['edges'],'sources':[{'document_id':'doc-1','file_id':'file-1','chunk_id':'chunk-1','ordinal':0,'content_hash':'a'*64,'content':'业务九点开门。'}]}
            await route.fulfill(status=202,json=result)
        await self.page.route('**/api/knowledge/bases/base-1/graph/query',submit)
        await self.page.goto(self.origin+'/knowledge/base-1/query')
        await self.page.locator('.ant-select[aria-label="检索目标"]').click()
        await self.page.get_by_text('知识图谱',exact=True).last.click()
        await self.page.locator('.ant-select[aria-label="检索模式"]').click()
        await self.page.get_by_text('关联检索',exact=True).click()
        await self.page.get_by_role('textbox',name='检索问题',exact=True).fill('几点开门？')
        await self.page.get_by_role('button',name='检索',exact=True).click()
        await expect(self.page.get_by_text('图谱证据是业务九点开门。',exact=True)).to_be_visible()
        self.assertEqual(len(requests),1)
        self.assertEqual(requests[0]['mode'],'global')
        self.assertTrue(requests[0]['client_request_id'])
        self.assertEqual(self.queries,[])


    async def test_cancel_acknowledgement_cannot_be_overwritten_by_late_poll(self):
        entered,release=asyncio.Event(),asyncio.Event()
        self.graph_job=job('running')
        async def list_jobs(route):await route.fulfill(json=[self.graph_job])
        async def read_job(route):
            old=job('running',version=2);entered.set();await release.wait()
            with contextlib.suppress(Exception):await route.fulfill(json=old)
        await self.page.route('**/api/knowledge/bases/base-1/graph/jobs',list_jobs)
        await self.page.route('**/api/knowledge/bases/base-1/graph/jobs/graph-job-1',read_job)
        try:
            await self.page.goto(self.origin+'/knowledge/base-1/graph')
            await asyncio.wait_for(entered.wait(),4)
            await self.page.get_by_role('button',name='取消图谱任务',exact=True).click()
            await expect(self.page.get_by_text('图谱任务已取消',exact=True)).to_be_visible()
            release.set();await self.page.wait_for_timeout(900)
            await expect(self.page.get_by_text('图谱任务已取消',exact=True)).to_be_visible()
            await expect(self.page.get_by_role('button',name='取消图谱任务',exact=True)).to_have_count(0)
        finally:release.set()

    async def test_busy_build_blocks_new_paid_preview_after_graph_load_error(self):
        self.graph_job=job('running')
        await self.page.route('**/api/knowledge/bases/base-1/graph/jobs',lambda route:route.fulfill(json=[self.graph_job]))
        await self.page.route('**/api/knowledge/bases/base-1/graph/subgraph?*',lambda route:route.fulfill(status=503,json={'error':{'code':'unavailable','message':'读取失败'}}))
        await self.page.goto(self.origin+'/knowledge/base-1/graph')
        await expect(self.page.get_by_role('button',name='试构建',exact=True)).to_be_disabled()
        await expect(self.page.get_by_role('button',name='构建中',exact=True)).to_be_disabled()
        self.assertEqual([row for row in self.graph_posts if row[0] in ['/preview','/build']],[])


    async def test_graph_query_unknown_response_reload_keeps_original_mode_and_key(self):
        requests=[]
        async def submit(route):
            requests.append(route.request.post_data_json)
            if len(requests)==1:
                await route.fulfill(status=503,json={'error':{'code':'unavailable','message':'检索结果待确认'}})
            else:
                result=job(operation='query');result['result']={'context':'恢复图谱上下文','entities':[],'relationships':[],'sources':[]}
                await route.fulfill(status=202,json=result)
        await self.page.route('**/api/knowledge/bases/base-1/graph/query',submit)
        await self.page.goto(self.origin+'/knowledge/base-1/query')
        await self.page.locator('.ant-select[aria-label="检索目标"]').click()
        await self.page.get_by_text('知识图谱',exact=True).last.click()
        await self.page.locator('.ant-select[aria-label="检索模式"]').click()
        await self.page.get_by_text('关联检索',exact=True).click()
        await self.page.get_by_role('textbox',name='检索问题',exact=True).fill('关系是什么？')
        await self.page.get_by_role('button',name='检索',exact=True).click()
        await expect(self.page.get_by_role('button',name='恢复查看',exact=True)).to_be_visible()
        await self.page.reload()
        await expect(self.page.get_by_role('button',name='恢复查看',exact=True)).to_be_visible()
        await expect(self.page.locator('.ant-select[aria-label="检索目标"]')).to_contain_text('知识图谱')
        await expect(self.page.locator('.ant-select[aria-label="检索模式"]')).to_contain_text('关联检索')
        self.assertEqual(len(requests),1)
        await self.page.get_by_role('button',name='恢复查看',exact=True).click()
        await expect(self.page.get_by_text('恢复图谱上下文',exact=True)).to_be_visible()
        self.assertEqual(requests[0],requests[1])

    async def test_known_query_restore_failure_only_polls_original_job(self):
        posts=[];gets=[];ready=False
        async def query(route):
            posts.append(route.request.post_data_json)
            await route.fulfill(status=202,json=job('running','query'))
        async def observe(route):
            gets.append(route.request.url)
            if not ready:await route.fulfill(status=503,json={'error':{'code':'unavailable','message':'观察中断'}})
            else:
                result=job(operation='query');result['result']={'context':'原任务恢复','entities':[],'relationships':[],'sources':[]}
                await route.fulfill(json=result)
        await self.page.route('**/api/knowledge/bases/base-1/graph/query',query)
        await self.page.route('**/api/knowledge/bases/base-1/graph/jobs/graph-job-1',observe)
        await self.page.goto(self.origin+'/knowledge/base-1/query')
        await self.page.locator('.ant-select[aria-label="检索目标"]').click();await self.page.get_by_text('知识图谱',exact=True).last.click()
        await self.page.get_by_role('textbox',name='检索问题',exact=True).fill('查询')
        await self.page.get_by_role('button',name='检索',exact=True).click()
        await expect(self.page.get_by_role('button',name='恢复查看',exact=True)).to_be_visible()
        await self.page.reload()
        await expect(self.page.get_by_role('button',name='恢复查看',exact=True)).to_be_visible()
        ready=True
        await self.page.get_by_role('button',name='恢复查看',exact=True).click()
        await expect(self.page.get_by_text('原任务恢复',exact=True)).to_be_visible()
        self.assertEqual(len(posts),1)
        self.assertGreaterEqual(len(gets),3)


    async def test_job_list_shows_newest_build_not_older_completed_job(self):
        latest=job('failed',version=3);latest['id']='graph-new';latest['created_at']='2026-10-24T00:00:00Z';latest['error']={'code':'failed','message':'最新构建失败'}
        old=job();old['id']='graph-old'
        await self.page.route('**/api/knowledge/bases/base-1/graph/jobs',lambda route:route.fulfill(json=[latest,old]))
        await self.page.goto(self.origin+'/knowledge/base-1/graph')
        await expect(self.page.get_by_text('图谱构建失败',exact=True)).to_be_visible()
        await expect(self.page.get_by_text('最新构建失败',exact=True)).to_be_visible()


    async def test_owner_can_open_graph_setup_before_first_build(self):
        self.base.update(enable_knowledge_graph=False,llm_model_id=None,graph_status='disabled')
        self.config=None
        await self.page.goto(self.origin+'/knowledge')
        await self.page.get_by_role('button',name='查看图谱',exact=True).click()
        await expect(self.page.get_by_role('heading',name='图谱配置',exact=True)).to_be_visible()
        await expect(self.page.get_by_role('heading',name='图谱试构建',exact=True)).to_be_visible()

    async def test_unpublished_graph_shows_configuration_failure_without_canvas(self):
        async def state(route):
            await route.fulfill(json={'config':self.config,'available':True,'active_revision':None,'requires_rebuild':False,'can_manage':True,'supported_reasoning_strategies':['auto','default']})
        async def fail(route):
            await route.fulfill(status=503,json={'error':{'code':'unavailable','message':'图谱接纳结果未确认'}})
        await self.page.route('**/api/knowledge/bases/base-1/graph/config',state)
        await self.page.route('**/api/knowledge/bases/base-1/graph/build',fail)
        await self.page.goto(self.origin+'/knowledge/base-1/graph')
        await self.page.get_by_role('button',name='构建图谱',exact=True).click()
        await expect(self.page.get_by_text('图谱接纳结果未确认',exact=True)).to_be_visible()
        await expect(self.page.locator('.knowledge-graph__canvas')).to_have_count(0)


    async def test_original_graph_layout_survives_mobile_and_dark_theme(self):
        await self.page.goto(self.origin+'/knowledge/base-1/graph')
        await expect(self.page.locator('.knowledge-graph__canvas canvas')).to_be_visible()
        for width in [390,768,1440]:
            await self.page.set_viewport_size({'width':width,'height':960})
            await self.page.wait_for_timeout(120)
            geometry=await self.page.locator('.knowledge-graph-page__content').evaluate('(node)=>({scroll:node.scrollWidth,width:node.clientWidth})')
            self.assertLessEqual(geometry['scroll'],geometry['width']+1)
            await expect(self.page.get_by_role('button',name='构建图谱',exact=True)).to_be_visible()
        await self.page.get_by_role('button',name='切换主题',exact=True).click()
        await expect(self.page.locator('html')).to_have_class(__import__('re').compile('dark'))
        color=await self.page.locator('.graph-panel h2').first.evaluate('(node)=>getComputedStyle(node).color')
        self.assertNotEqual(color,'rgb(0, 0, 0)')

    async def test_readonly_graph_viewer_cannot_submit_model_operations(self):
        self.base['can_manage']=False
        async def state(route):await route.fulfill(json={'config':self.config,'available':True,'active_revision':'graph-revision-1','requires_rebuild':False,'can_manage':False,'supported_reasoning_strategies':['auto','default']})
        await self.page.route('**/api/knowledge/bases/base-1/graph/config',state)
        await self.page.goto(self.origin+'/knowledge/base-1/graph')
        await expect(self.page.locator('.knowledge-graph__canvas canvas')).to_be_visible()
        await expect(self.page.get_by_role('button',name='构建图谱',exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button',name='试构建',exact=True)).to_have_count(0)
        self.assertEqual(self.graph_posts,[])


    async def test_known_graph_query_restore_blocks_new_submission_while_first_get_waits(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def prepare(route):await route.fulfill(status=202,json=job('running','query'))
        async def fail_read(route):await route.fulfill(status=503,json={'error':{'code':'unavailable','message':'观察中断'}})
        await self.page.route('**/api/knowledge/bases/base-1/graph/query',prepare)
        await self.page.route('**/api/knowledge/bases/base-1/graph/jobs/graph-job-1',fail_read)
        await self.page.goto(self.origin+'/knowledge/base-1/query')
        await self.page.locator('.ant-select[aria-label="检索目标"]').click();await self.page.get_by_text('知识图谱',exact=True).last.click()
        await self.page.get_by_role('textbox',name='检索问题',exact=True).fill('原查询')
        await self.page.get_by_role('button',name='检索',exact=True).click()
        await expect(self.page.get_by_role('button',name='恢复查看',exact=True)).to_be_visible()
        async def slow(route):
            entered.set();await release.wait()
            with contextlib.suppress(Exception):await route.fulfill(json=job('running','query'))
        await self.page.route('**/api/knowledge/bases/base-1/graph/jobs/graph-job-1',slow)
        try:
            await self.page.reload();await asyncio.wait_for(entered.wait(),4)
            await expect(self.page.get_by_role('textbox',name='检索问题',exact=True)).to_be_disabled()
            await expect(self.page.get_by_role('button',name='检索中',exact=True)).to_be_disabled()
            await expect(self.page.locator('.ant-select[aria-label="检索目标"]')).to_have_class(__import__('re').compile('ant-select-disabled'))
        finally:release.set()


    async def test_permission_error_clears_previous_preview_statistics(self):
        self.graph_job=job(operation='preview')
        await self.page.route('**/api/knowledge/bases/base-1/graph/jobs',lambda route:route.fulfill(json=[self.graph_job]))
        await self.page.goto(self.origin+'/knowledge/base-1/graph')
        await expect(self.page.locator('.graph-preview-result')).to_be_visible()
        async def deny(route):await route.fulfill(status=403,json={'error':{'code':'forbidden','message':'来源权限已变化'}})
        await self.page.route('**/api/knowledge/bases/base-1/graph/config',deny)
        await self.page.get_by_role('button',name='刷新知识图谱',exact=True).click()
        await expect(self.page.get_by_text('来源权限已变化',exact=True)).to_be_visible()
        await expect(self.page.locator('.graph-preview-result')).to_have_count(0)


    async def test_01_poll_acl_denial_invalidates_inflight_subgraph(self):
        entered, release = (asyncio.Event(), asyncio.Event())
        self.graph_job = job('running')
        denied = False
        await self.page.route('**/api/knowledge/bases/base-1/graph/jobs', lambda route: route.fulfill(json=[self.graph_job]))

        async def observe(route):
            if denied:
                await route.fulfill(status=403, json={'error': {'code': 'graph_sources_unavailable', 'message': '独立探针：图谱全集原文已撤权'}})
            else:
                await route.fulfill(json=self.graph_job)
        await self.page.route('**/api/knowledge/bases/base-1/graph/jobs/graph-job-1', observe)

        async def delayed(route):
            entered.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await route.fulfill(json=self.graph)
        try:
            await self.page.goto(self.origin + '/knowledge/base-1/graph')
            await expect(self.page.locator('.knowledge-graph__canvas canvas')).to_be_visible()
            await self.page.route('**/api/knowledge/bases/base-1/graph/subgraph?*', delayed)
            await self.page.get_by_role('button', name='加载图谱', exact=True).click()
            await asyncio.wait_for(entered.wait(), 4)
            denied = True
            await expect(self.page.get_by_text('独立探针：图谱全集原文已撤权', exact=True)).to_be_visible()
            await expect(self.page.locator('.knowledge-graph__canvas')).to_have_count(0)
            release.set()
            await self.page.wait_for_timeout(450)
            await expect(self.page.locator('.knowledge-graph__canvas')).to_have_count(0, timeout=1000)
        finally:
            release.set()

    async def test_02_authoritative_config_readonly_prevents_model_post(self):

        async def state(route):
            await route.fulfill(json={'config': self.config, 'available': True, 'active_revision': 'graph-revision-1', 'requires_rebuild': False, 'can_manage': False, 'supported_reasoning_strategies': ['auto', 'default']})
        submitted = []

        async def reject(route):
            submitted.append(route.request.post_data_json)
            await route.fulfill(status=403, json={'error': {'code': 'knowledge_forbidden', 'message': '已撤销编辑权限'}})
        await self.page.route('**/api/knowledge/bases/base-1/graph/config', state)
        await self.page.route('**/api/knowledge/bases/base-1/graph/build', reject)
        await self.page.goto(self.origin + '/knowledge/base-1/graph')
        await expect(self.page.locator('.knowledge-graph__canvas canvas')).to_be_visible()
        build = self.page.get_by_role('button', name='构建图谱', exact=True)
        if await build.count() and await build.is_enabled():
            await build.click()
            await expect(self.page.get_by_text('已撤销编辑权限', exact=True)).to_be_visible()
        self.assertEqual(submitted, [], 'Latest graph config is read-only: no model-operation POST should leave the UI')

    async def test_03_null_progress_does_not_render_fabricated_zero(self):
        self.graph_job = job('running')
        self.graph_job.update(processed_documents=None, total_documents=None, progress={'extracted_chunks': None, 'total_chunks': None, 'entities': None, 'relations': None})
        await self.page.route('**/api/knowledge/bases/base-1/graph/jobs', lambda route: route.fulfill(json=[self.graph_job]))
        await self.page.goto(self.origin + '/knowledge/base-1/graph')
        await expect(self.page.get_by_role('heading', name='正在构建图谱', exact=True)).to_be_visible()
        await expect(self.page.locator('.knowledge-graph__canvas canvas')).to_be_visible()
        text = await self.page.locator('.graph-build-panel').inner_text()
        self.assertNotIn('0%', text, 'Unknown server progress must not be projected as 0%')

    async def graph_query(self, text='独立图谱查询'):
        await self.page.locator('.ant-select[aria-label="检索目标"]').click()
        await self.page.get_by_text('知识图谱', exact=True).last.click()
        await self.page.get_by_role('textbox', name='检索问题', exact=True).fill(text)
        await self.page.get_by_role('button', name='检索', exact=True).click()

    async def test_04_latest_vector_query_survives_prior_completed_graph_query(self):
        posted = []

        async def query(route):
            posted.append(route.request.post_data_json)
            row = job(operation='query')
            row['result'] = {'context': '旧图谱上下文', 'entities': [], 'relationships': [], 'sources': []}
            await route.fulfill(status=202, json=row)

        async def read(route):
            row = job(operation='query')
            row['result'] = {'context': '旧图谱上下文', 'entities': [], 'relationships': [], 'sources': []}
            await route.fulfill(json=row)
        await self.page.route('**/api/knowledge/bases/base-1/graph/query', query)
        await self.page.route('**/api/knowledge/bases/base-1/graph/jobs/graph-job-1', read)
        await self.page.goto(self.origin + '/knowledge/base-1/query')
        await self.graph_query()
        await expect(self.page.get_by_text('旧图谱上下文', exact=True)).to_be_visible()
        await self.page.locator('.ant-select[aria-label="检索目标"]').click()
        await self.page.locator('.ant-select-item-option-content').get_by_text('知识库', exact=True).click()
        await self.page.get_by_role('textbox', name='检索问题', exact=True).fill('最新向量问题')
        await self.page.get_by_role('button', name='检索', exact=True).click()
        await expect(self.page.locator('.query-passage')).to_contain_text('业务工作时间是九点到十七点。')
        self.assertEqual(len(self.queries), 1)
        await self.page.reload()
        await expect(self.page.get_by_role('textbox', name='检索问题', exact=True)).to_be_visible()
        await self.page.wait_for_timeout(300)
        await expect(self.page.locator('.query-passage')).to_contain_text('业务工作时间是九点到十七点。', timeout=1500)
        await expect(self.page.locator('.ant-select[aria-label="检索目标"]')).to_contain_text('知识库')


    async def test_quality_01_subgraph_denial_must_invalidate_delayed_preview_poll(self):
        entered, release = (asyncio.Event(), asyncio.Event())
        followup_config_release = asyncio.Event()
        self.graph_job = job('running', 'preview')
        await self.page.route('**/api/knowledge/bases/base-1/graph/jobs', lambda route: route.fulfill(json=[self.graph_job]))

        async def poll(route):
            entered.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await route.fulfill(json=job('completed', 'preview', 3))
        await self.page.route('**/api/knowledge/bases/base-1/graph/jobs/graph-job-1', poll)
        try:
            await self.page.goto(self.origin + '/knowledge/base-1/graph')
            await expect(self.page.locator('.knowledge-graph__canvas canvas')).to_be_visible()
            await asyncio.wait_for(entered.wait(), 4)
            await self.page.route('**/api/knowledge/bases/base-1/graph/subgraph?*', lambda route: route.fulfill(status=403, json={'error': {'code': 'graph_sources_unavailable', 'message': '质量探针：全集来源撤权'}}))
            await self.page.get_by_role('button', name='加载图谱', exact=True).click()
            await expect(self.page.get_by_text('质量探针：全集来源撤权', exact=True)).to_be_visible()
            await expect(self.page.locator('.knowledge-graph__canvas')).to_have_count(0)

            async def pending_config(route):
                await followup_config_release.wait()
                with contextlib.suppress(Exception):
                    await route.fulfill(json={'config': self.config, 'available': True, 'active_revision': 'graph-revision-1', 'requires_rebuild': False, 'can_manage': True, 'supported_reasoning_strategies': ['auto', 'default']})
            await self.page.route('**/api/knowledge/bases/base-1/graph/config', pending_config)
            release.set()
            await self.page.wait_for_timeout(400)
            await expect(self.page.locator('.graph-preview-result')).to_have_count(0, timeout=1000)
        finally:
            release.set()
            followup_config_release.set()

    async def test_quality_02_source_acl_denial_must_clear_aggregate_query_result(self):
        await self.page.route('**/api/knowledge/bases/base-1/graph/query', lambda route: route.fulfill(status=202, json=quality_query_job()))
        await self.page.route('**/api/knowledge/bases/base-1/documents/doc-1/preview', lambda route: route.fulfill(status=403, json={'error': {'code': 'file_forbidden', 'message': '质量探针：此原文已撤权'}}))
        await self.page.goto(self.origin + '/knowledge/base-1/query')
        await self.graph_query()
        await expect(self.page.get_by_text('质量探针私有聚合上下文', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='查看图谱来源：doc-1', exact=True).click()
        await expect(self.page.get_by_text('质量探针：此原文已撤权', exact=True)).to_be_visible()
        await expect(self.page.locator('.query-answer')).to_have_count(0, timeout=1000)
        await expect(self.page.locator('.query-passage')).to_have_count(0)

    async def test_quality_04_query_cancel_unknown_reload_requires_explicit_original_cancel(self):
        cancels, posts = ([], [])

        async def query(route):
            posts.append(route.request.post_data_json)
            await route.fulfill(status=202, json=quality_query_job('running'))

        async def cancel(route):
            cancels.append({'url': route.request.url, 'body': route.request.post_data_json})
            await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '质量探针：取消接纳未知'}})
        await self.page.route('**/api/knowledge/bases/base-1/graph/query', query)
        await self.page.route('**/api/knowledge/bases/base-1/graph/jobs/graph-job-1', lambda route: route.fulfill(json=quality_query_job('running')))
        await self.page.route('**/api/knowledge/bases/base-1/graph/jobs/graph-job-1/cancel', cancel)
        await self.page.goto(self.origin + '/knowledge/base-1/query')
        await self.graph_query()
        await self.page.get_by_role('button', name='取消检索', exact=True).click()
        await expect(self.page.get_by_role('button', name='恢复查看', exact=True)).to_be_visible()
        self.assertEqual(len(cancels), 1)
        await self.page.reload()
        await expect(self.page.get_by_role('textbox', name='检索问题', exact=True)).to_be_visible()
        await self.page.wait_for_timeout(300)
        self.assertEqual(len(posts), 1)
        self.assertEqual(len(cancels), 1, 'Reload must not automatically repeat an uncertain cancel POST')
        await self.page.get_by_role('button', name='恢复查看', exact=True).click()
        await expect(self.page.get_by_text('质量探针：取消接纳未知', exact=True)).to_be_visible()
        self.assertEqual(len(cancels), 2)
        self.assertEqual(cancels[0], cancels[1])
        self.assertEqual(len(posts), 1)


    async def test_denied_completed_graph_result_allows_vector_fallback(self):
        posts=[]
        async def query(route):
            posts.append(route.request.post_data_json);await route.fulfill(status=202,json=quality_query_job())
        await self.page.route('**/api/knowledge/bases/base-1/graph/query',query)
        await self.page.route('**/api/knowledge/bases/base-1/documents/doc-1/preview',lambda route:route.fulfill(status=403,json={'error':{'code':'forbidden','message':'此来源已撤权'}}))
        await self.page.goto(self.origin+'/knowledge/base-1/query');await self.graph_query()
        await self.page.get_by_role('button',name='查看图谱来源：doc-1',exact=True).click()
        await expect(self.page.get_by_text('此来源已撤权',exact=True)).to_be_visible()
        await expect(self.page.locator('.query-answer')).to_have_count(0)
        await self.page.get_by_role('button',name='Close',exact=True).click()
        await expect(self.page.locator('.ant-select[aria-label="检索目标"]')).not_to_have_class(__import__('re').compile('ant-select-disabled'))
        await self.page.reload()
        await expect(self.page.locator('.ant-select[aria-label="检索目标"]')).to_contain_text('知识库')
        self.assertEqual(len(posts),1)


if __name__=='__main__':unittest.main()
