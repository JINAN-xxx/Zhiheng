"""Desktop graph build workflow against real Vue pages and intercepted API fixtures."""
import asyncio
import re
from playwright.async_api import async_playwright, expect
from knowledge_check import BASE_URL, KnowledgeFixture, button, choose, modal


async def graph_build(browser):
    fixture = KnowledgeFixture()
    context = await browser.new_context(viewport={'width': 1440, 'height': 1000})
    await fixture.install(context)
    page = await context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        await page.goto(BASE_URL + '/ai/knowledge?dbId=kb-second&view=graph', wait_until='networkidle')
        await expect(page).to_have_url(re.compile(r'kb-second.*view=graph'))
        await expect(button(page, '启用并构建图谱')).to_be_disabled()
        assert not any(method == 'POST' and path.endswith('/graph-build') for path, method, _, _ in fixture.calls)
        await choose(page, '图谱对话模型', 'chat 测试模型')
        await button(page, '启用并构建图谱').click()
        await expect(page.get_by_text('正在构建图谱', exact=True)).to_be_visible()
        await expect(page.get_by_text('已处理 1 / 3 个文档 · 失败 0 个', exact=True)).to_be_visible()
        await expect(button(page, '构建中')).to_be_disabled()
        await expect(page.get_by_text('正在处理：待构建文档.md', exact=True)).to_be_visible()
        await button(page, '文档管理').click()
        await expect(button(page, '上传文档')).to_be_disabled()
        await expect(page.get_by_text('图谱正在构建，完成后可继续上传、切片或删除文档。', exact=True)).to_be_visible()
        await button(page, '知识图谱').click()
        fixture.graph_jobs['kb-second'].update(status='completed', processed_documents=3, current_document=None)
        await expect(page.get_by_text('图谱构建完成', exact=True)).to_be_visible()
        await expect(page.locator('.knowledge-graph canvas')).to_be_visible()
        await expect(page.get_by_role('img', name='知识图谱，2 个节点，1 个关系')).to_be_visible()
        await expect(button(page, '补建图谱')).to_be_enabled()
        await page.screenshot(path='/tmp/knowledge-graph-build-completed.png', full_page=True)
        polls = len([1 for path, method, _, _ in fixture.calls if path.endswith('/graph-build') and method == 'GET'])
        await page.wait_for_timeout(2300)
        assert polls == len([1 for path, method, _, _ in fixture.calls if path.endswith('/graph-build') and method == 'GET']), 'terminal jobs must stop polling'

        fixture.graph_jobs['kb-second'].update(status='failed', processed_documents=2, failed_documents=1, error={'code': 'model_unavailable', 'message': '测试模型暂不可用'})
        await button(page, '刷新知识图谱').click()
        await expect(page.get_by_text('测试模型暂不可用', exact=True)).to_be_visible()
        await button(page, '重试构建').click()
        await expect(page.get_by_text('正在构建图谱', exact=True)).to_be_visible()
        posts = [body for path, method, body, _ in fixture.calls if path.endswith('/graph-build') and method == 'POST']
        assert posts == [{'llm_model_id': 'chat-model'}, {}], posts

        # Begin a delayed old-KB status request, then navigate without a page reload.
        fixture.graph_status_delay = 1
        await button(page, '刷新知识图谱').click()
        await page.wait_for_timeout(150)
        await button(page, '返回知识库').click()
        await button(page.locator('.knowledge-card').filter(has_text='资料库'), '查看图谱').click()
        await expect(page.get_by_role('heading', name='资料库', exact=True)).to_be_visible()
        await expect(page.get_by_text('图谱构建完成', exact=True)).to_be_visible()
        await page.wait_for_timeout(2200)
        await expect(page.get_by_text('正在构建图谱', exact=True)).to_have_count(0)
        calls_before = len([1 for path, _, _, _ in fixture.calls if path == '/databases/kb-second/graph-build'])
        await page.wait_for_timeout(2300)
        assert calls_before == len([1 for path, _, _, _ in fixture.calls if path == '/databases/kb-second/graph-build']), 'old KB polling must stop after route switch'

        fixture.graph_status_delay = 0
        fixture.graph_jobs['kb-main'] = {'status': 'interrupted', 'total_documents': 2, 'processed_documents': 1, 'failed_documents': 0, 'error': {'code': 'interrupted', 'message': '服务重启中断'}}
        await button(page, '刷新知识图谱').click()
        await expect(page.get_by_text('图谱构建已中断', exact=True)).to_be_visible()
        await button(page, '重试构建').click()
        await expect(page.get_by_text('正在构建图谱', exact=True)).to_be_visible()
        fixture.graph_jobs['kb-main'].update(status='idle', current_document=None, error=None)
        await button(page, '刷新知识图谱').click()
        await expect(page.get_by_text('图谱待构建', exact=True)).to_be_visible()
        await button(page, '补建图谱').click()
        await expect(page.get_by_text('正在构建图谱', exact=True)).to_be_visible()
        assert not fixture.graph_content_while_building, fixture.graph_content_while_building
        assert not errors, errors
        print('desktop graph: disabled deep link, enable, progress, mutation lock, completion nodes, failed retry and stale KB switch passed')
    finally:
        await context.close()


async def graph_permissions_and_unavailable(browser):
    for unavailable in (False, True):
        fixture = KnowledgeFixture(['ai:knowledge:query'] if not unavailable else None)
        fixture.graph_available = not unavailable
        context = await browser.new_context(viewport={'width': 1280, 'height': 900})
        await fixture.install(context)
        page = await context.new_page()
        try:
            await page.goto(BASE_URL + '/ai/knowledge?dbId=kb-second&view=graph', wait_until='networkidle')
            await expect(page).to_have_url(re.compile(r'kb-second.*view=graph'))
            if unavailable:
                await expect(page.get_by_text('知识图谱服务尚未就绪，请联系管理员完成配置。', exact=True)).to_be_visible()
                await expect(button(page, '启用并构建图谱')).to_be_disabled()
            else:
                await expect(button(page, '启用并构建图谱')).to_have_count(0)
                await expect(page.get_by_text('此知识库尚未启用图谱，请联系有编辑权限的成员启用。', exact=True)).to_be_visible()
                await page.goto(BASE_URL + '/ai/knowledge?dbId=kb-main&view=graph', wait_until='networkidle')
                await expect(page.locator('.knowledge-graph canvas')).to_be_visible()
                await expect(button(page, '补建图谱')).to_have_count(0)
            assert not any(method == 'POST' and path.endswith('/graph-build') for path, method, _, _ in fixture.calls)
        finally:
            await context.close()
    print('desktop graph: query-only cannot start and unavailable service is explicit passed')


async def independent_document_status(browser):
    fixture = KnowledgeFixture()
    fixture.documents[0].update(graph_status='failed', graph_error={'code': 'model_error', 'message': '图谱提取失败，向量仍可检索'})
    context = await browser.new_context(viewport={'width': 1440, 'height': 960})
    await fixture.install(context)
    page = await context.new_page()
    try:
        await page.goto(BASE_URL + '/ai/knowledge?dbId=kb-main&view=documents', wait_until='networkidle')
        row = page.locator('tr').filter(has_text=fixture.documents[0]['file_name'])
        await expect(row.get_by_text('已索引', exact=True)).to_be_visible()
        await expect(row.get_by_text('构建失败', exact=True)).to_be_visible()
        await row.get_by_text('构建失败', exact=True).hover()
        await expect(page.get_by_role('tooltip')).to_contain_text('图谱提取失败，向量仍可检索')
        await button(row, fixture.documents[0]['file_name']).click()
        await expect(modal(page).locator('pre')).to_contain_text('文档内容')
        print('desktop graph: graph failures preserve vector status and document preview passed')
    finally:
        await context.close()


async def create_graph(browser):
    fixture = KnowledgeFixture()
    fixture.fail_create = False
    context = await browser.new_context(viewport={'width': 1280, 'height': 900})
    await fixture.install(context)
    page = await context.new_page()
    try:
        await page.goto(BASE_URL + '/ai/knowledge', wait_until='networkidle')
        await button(page, '创建知识库').click()
        await modal(page).get_by_label('知识库名称', exact=True).fill('创建图谱测试')
        await choose(page, 'Embedding 模型', 'embedding 测试模型', modal(page))
        await modal(page).get_by_role('checkbox', name='同时构建知识图谱').check()
        await choose(page, '图谱对话模型', 'chat 测试模型', modal(page))
        await button(modal(page), '确定').click()
        await expect(modal(page)).to_have_count(0)
        payload = next(body for path, method, body, _ in fixture.calls if path == '/databases' and method == 'POST')
        assert payload['kb_type'] == 'milvus'
        assert payload['enable_knowledge_graph'] is True
        assert payload['llm_model_id'] == 'chat-model'
        assert payload['embed_model_id'] == 'embedding-model'
        print('desktop graph: create payload retains fixed Milvus and explicit graph/chat model passed')
    finally:
        await context.close()


async def main():
    expect.set_options(timeout=20000)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            await graph_build(browser)
            await graph_permissions_and_unavailable(browser)
            await create_graph(browser)
            await independent_document_status(browser)
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
