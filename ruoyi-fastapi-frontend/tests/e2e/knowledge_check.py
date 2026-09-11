"""Knowledge workspace smoke check. All API calls are intercepted with local fixtures."""

import asyncio
import copy
import json
import os
import re
from urllib.parse import parse_qs, urlparse

from playwright.async_api import async_playwright, expect
from platform_shell_check import ai_route, response_for

BASE_URL = os.environ.get('KNOWLEDGE_CHECK_BASE_URL', 'http://127.0.0.1:12582')
UNSAFE = '<img src=x onerror=window.__kbInjected=1>'
LABEL = 'A&B/#中文'


def button(scope, label):
    return scope.get_by_role('button', name=re.compile(r'^(?:[A-Za-z][A-Za-z0-9_-]*\s+)*' + r'\s*'.join(map(re.escape, label)) + r'\s*$'))


def modal(page):
    return page.locator('.ant-modal:visible').last


def assert_chunk_params(params):
    assert 100 <= params['chunk_size'] <= 4000
    assert 0 <= params['chunk_overlap'] <= 1000
    assert params['chunk_overlap'] < params['chunk_size']


class KnowledgeFixture:
    def __init__(self, permissions=None):
        self.permissions = permissions or ['*:*:*']
        self.databases = [
            {'db_id': 'kb-main', 'name': '资料库', 'description': UNSAFE, 'kb_type': 'chroma', 'enable_knowledge_graph': True},
            {'db_id': 'kb-second', 'name': '第二知识库', 'description': '', 'kb_type': 'chroma', 'enable_knowledge_graph': False},
        ]
        self.folders = [{'id': 'folder-a', 'name': '第一目录', 'parent_id': None, 'db_id': 'kb-main'}]
        self.documents = [{'id': 'doc-one', 'file_name': UNSAFE + '.txt', 'folder_id': None, 'db_id': 'kb-main', 'status': 'done', 'chunk_size': 1000, 'chunk_overlap': 200, 'file_size': 40}, {'id': 'doc-child', 'file_name': '目录文档.md', 'folder_id': 'folder-a', 'db_id': 'kb-main', 'status': 'done'}]
        self.calls = []
        self.index_calls = 0
        self.fail_create = True
        self.query_delay = 0
        self.subgraph_delay = 0
        self.preview_delay = 0
        self.upload_delay = 0
        self.fail_document_refresh = False
        self.fail_database_refresh = False

    def respond(self, path, method, body, params):
        if path == '/getInfo':
            return {**response_for(path, method, 'all'), 'permissions': self.permissions}
        if path == '/getRouters':
            routes = response_for(path, method, 'all')['data']
            workspace = next(row for row in routes if row['path'] == '/ai')
            workspace['children'].append(ai_route('AiKnowledge', 'knowledge', 'plugin/ai/knowledge/index', '知识库'))
            return {'code': 200, 'data': routes}
        if not path.startswith('/ai/knowledge'):
            return response_for(path, method, 'all')
        suffix = path[len('/ai/knowledge'):]
        self.calls.append((suffix, method, copy.deepcopy(body), params))
        data = {}
        if suffix in ('/databases', '/available'):
            if method == 'POST':
                if self.fail_create:
                    self.fail_create = False
                    return {'code': 200, 'data': {'status': 'failed', 'message': '模拟模型配置失败'}}
                self.databases.append({**body, 'db_id': 'kb-new', 'name': body['database_name']})
                data = self.databases[-1]
            else:
                if self.fail_database_refresh:
                    self.fail_database_refresh = False
                    return {'code': 503, 'msg': '模拟知识库刷新失败'}
                data = {'databases': self.databases}
        elif suffix == '/models':
            model_type = params.get('model_type', ['embedding'])[0]
            data = {'models': [{'model_id': model_type + '-model', 'display_name': model_type + ' 测试模型'}]}
        elif suffix == '/types':
            data = {'kb_types': {'chroma': {'available': True}, 'milvus': {'available': False}, 'lightrag': {'available': True}}}
        elif suffix == '/folders':
            if method == 'POST':
                folder = {**body, 'id': 'folder-new', 'parent_id': body.get('parent_id')}
                self.folders.append(folder)
                data = {'folder': folder}
            else:
                data = {'folders': [row for row in self.folders if row['db_id'] == params['db_id'][0]]}
        elif suffix.startswith('/folders/'):
            folder_id = suffix.split('/')[-1]
            assert params.get('db_id') == ['kb-main']
            if method == 'DELETE':
                self.folders = [row for row in self.folders if row['id'] != folder_id]
            else:
                next(row for row in self.folders if row['id'] == folder_id)['name'] = body['name']
        elif suffix == '/files/upload':
            assert params['db_id'] == ['kb-main']
            assert 'file' in body['multipart']
            data = {'upload_id': 'opaque-upload', 'file_name': 'upload.txt', 'file_size': 20}
        elif suffix.endswith('/documents'):
            db_id = suffix.split('/')[2]
            if method == 'POST':
                assert body['items'] == ['opaque-upload']
                assert_chunk_params(body['params'])
                self.index_calls += 1
                if self.index_calls == 1:
                    data = {'status': 'success', 'items': [{'status': 'failed', 'error': '模拟文件解析失败'}]}
                else:
                    self.documents.append({'id': 'doc-upload', 'file_name': 'upload.txt', 'db_id': db_id, 'folder_id': None, 'status': 'done', 'chunk_size': body['params']['chunk_size'], 'chunk_overlap': body['params']['chunk_overlap']})
                    data = {'status': 'success', 'items': [{'status': 'done'}]}
            else:
                if self.fail_document_refresh:
                    self.fail_document_refresh = False
                    return {'code': 503, 'msg': '模拟文档刷新失败'}
                data = {'documents': [row for row in self.documents if row['db_id'] == db_id]}
        elif suffix.endswith('/preview'):
            data = {'content': UNSAFE + '\n文档内容'}
        elif suffix.endswith('/rechunk'):
            assert_chunk_params(body)
            data = {'status': 'success'}
        elif suffix.endswith('/query-graph'):
            assert 1 <= body['meta']['top_k'] <= 8
            data = {'status': 'success', 'result': UNSAFE + '\n图谱回答'}
        elif suffix.endswith('/query'):
            assert 1 <= body['meta']['top_k'] <= 8
            data = {'status': 'success', 'result': [{'content': UNSAFE + '\n检索片段', 'score': 0.8, 'metadata': {'source': UNSAFE}}]}
        elif suffix.endswith('/labels'):
            data = {'labels': [LABEL]}
        elif suffix.endswith('/subgraph'):
            data = {'nodes': [{'id': 'n1', 'entity_name': UNSAFE, 'entity_type': '测试', 'properties': {'description': UNSAFE}}, {'id': 'n2', 'entity_name': LABEL}], 'edges': [{'source': 'n1', 'target': 'n2', 'type': UNSAFE}], 'is_truncated': True}
        elif '/documents/' in suffix and method == 'DELETE':
            self.documents = [row for row in self.documents if row['id'] != suffix.split('/')[-1]]
        elif suffix.startswith('/databases/'):
            db_id = suffix.split('/')[2]
            if method == 'DELETE':
                self.databases = [row for row in self.databases if row['db_id'] != db_id]
            elif method == 'PUT':
                next(row for row in self.databases if row['db_id'] == db_id).update(body)
            else:
                data = next(row for row in self.databases if row['db_id'] == db_id)
        else:
            raise AssertionError((suffix, method, body))
        return {'code': 200, 'data': copy.deepcopy(data)}

    async def install(self, context):
        async def handler(route):
            request = route.request
            url = urlparse(request.url)
            path = re.sub(r'^/(dev-api|docker-api)', '', url.path)
            body = {}
            if request.post_data:
                body = {'multipart': request.post_data} if 'multipart/form-data' in request.headers.get('content-type', '') else request.post_data_json
            if path.startswith('/ai/knowledge'):
                assert request.headers.get('authorization') == 'Bearer knowledge-test-token'
            if path.endswith('/query') and self.query_delay:
                await asyncio.sleep(self.query_delay)
            if path.endswith('/subgraph') and self.subgraph_delay:
                await asyncio.sleep(self.subgraph_delay)
            if path.endswith('/preview') and self.preview_delay:
                await asyncio.sleep(self.preview_delay)
            if path.endswith('/files/upload') and self.upload_delay:
                await asyncio.sleep(self.upload_delay)
            payload = self.respond(path, request.method, body, parse_qs(url.query))
            await route.fulfill(content_type='application/json', body=json.dumps(payload, ensure_ascii=False))
        for pattern in ('**/dev-api/**', '**/docker-api/**', '**/transport/crypto/frontend-config', '**/captchaImage'):
            await context.route(pattern, handler)
        await context.add_cookies([{'name': 'Admin-Token', 'value': 'knowledge-test-token', 'url': BASE_URL}])


async def choose(page, label, option, scope=None):
    field = (scope or page).get_by_label(label, exact=True)
    await field.locator('.ant-select-selector').click()
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter(has_text=re.compile('^' + re.escape(option) + '$')).click()


async def desktop(browser):
    fixture = KnowledgeFixture()
    context = await browser.new_context(viewport={'width': 1440, 'height': 960})
    await fixture.install(context)
    page = await context.new_page()
    page.set_default_timeout(30000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        await page.goto(BASE_URL + '/ai/knowledge', wait_until='networkidle', timeout=120000)
        await expect(page.get_by_text(UNSAFE, exact=True)).to_be_visible()
        assert await page.locator('img[onerror]').count() == 0
        await page.screenshot(path='/tmp/knowledge-desktop-overview.png', full_page=True)
        fixture.fail_database_refresh = True
        await button(page, '刷新知识库').click()
        await expect(page.get_by_text('模拟知识库刷新失败', exact=True)).to_be_visible()
        await expect(page.get_by_text('资料库', exact=True)).to_be_visible()
        await page.locator('.ant-notification-notice-close').click()
        await button(page, '创建知识库').click()
        await modal(page).get_by_label('知识库名称', exact=True).fill('测试新库')
        await choose(page, 'Embedding 模型', 'embedding 测试模型', modal(page))
        await button(modal(page), '确定').click()
        await expect(modal(page).get_by_text('模拟模型配置失败', exact=True)).to_be_visible()
        await modal(page).get_by_label('知识库名称', exact=True).fill('测试新库修正')
        await button(modal(page), '确定').click()
        await expect(modal(page)).to_have_count(0)
        await expect(page).to_have_url(re.compile(r'dbId=kb-new.*view=documents'))
        await expect(button(page, '上传文档')).to_be_visible()
        await button(page, '返回知识库').click()
        row = page.locator('.knowledge-card').filter(has_text='测试新库修正')
        await expect(button(row, '管理文档')).to_be_visible()
        await expect(button(row, '测试检索')).to_be_visible()
        await button(row, '编辑知识库').click()
        await modal(page).get_by_label('名称', exact=True).fill('新库已改名')
        await button(modal(page), '确定').click()
        await expect(page.get_by_text('新库已改名', exact=True)).to_be_visible()

        row = page.locator('.knowledge-card').filter(has_text='资料库')
        await button(row, '管理文档').click()
        await expect(page).to_have_url(re.compile(r'dbId=kb-main.*view=documents'))
        await expect(button(page, '第一目录')).to_be_visible()
        await button(page, UNSAFE + '.txt').click()
        await expect(modal(page).locator('pre')).to_contain_text(UNSAFE)
        assert await modal(page).locator('img[onerror]').count() == 0
        await modal(page).get_by_role('button', name='Close', exact=True).click()
        await button(page, '新建文件夹').click()
        await modal(page).get_by_label('名称', exact=True).fill('新目录')
        await button(modal(page), '确定').click()
        row = page.locator('tr').filter(has_text='新目录')
        await button(row, '重命名').click()
        await modal(page).get_by_label('名称', exact=True).fill('目录已改名')
        await button(modal(page), '确定').click()
        row = page.locator('tr').filter(has_text='目录已改名')
        await button(row, '删除').click()
        await button(modal(page), '删除').click()
        await expect(button(page, '目录已改名')).to_have_count(0)
        row = page.locator('tr').filter(has_text=UNSAFE + '.txt')
        await button(row, '重新切片').click()
        await expect(modal(page).get_by_label('切片大小', exact=True)).to_have_attribute('aria-valuemax', '4000')
        await modal(page).get_by_label('切片大小', exact=True).fill('4000')
        await expect(modal(page).get_by_label('重叠大小', exact=True)).to_have_attribute('aria-valuemax', '1000')
        await modal(page).get_by_label('重叠大小', exact=True).fill('1000')
        await button(modal(page), '确定').click()
        await expect(modal(page)).to_have_count(0)

        await button(page, '上传文档').click()
        await modal(page).locator('input[type=file]').set_input_files({'name': 'upload.txt', 'mimeType': 'text/plain', 'buffer': b'knowledge test file'})
        await expect(modal(page).get_by_text('模拟文件解析失败', exact=True)).to_be_visible()
        await page.wait_for_timeout(1100)  # shared request client intentionally rejects identical submissions within 1 s
        await button(modal(page), '重试').click()
        await expect(modal(page).get_by_text('完成', exact=True)).to_be_visible()
        await modal(page).get_by_role('button', name='Close', exact=True).click()
        await expect(button(page, 'upload.txt')).to_be_visible()
        fixture.fail_document_refresh = True
        await button(page, '刷新文档').click()
        await expect(page.get_by_text('模拟文档刷新失败', exact=True)).to_be_visible()
        await expect(button(page, UNSAFE + '.txt')).to_be_visible()

        await button(page, '检索测试').click()
        await expect(page).to_have_url(re.compile(r'dbId=kb-main.*view=query'))
        await page.get_by_label('检索问题', exact=True).fill('验证知识')
        await button(page, '检索').click()
        await expect(page.locator('.query-passage p')).to_contain_text(UNSAFE)
        assert await page.locator('img[onerror]').count() == 0
        fixture.query_delay = 0.5
        await page.get_by_label('检索问题', exact=True).fill('第一次延迟查询')
        await button(page, '检索').click()
        await page.get_by_label('检索问题', exact=True).fill('第二次替换查询')
        await page.get_by_label('检索问题', exact=True).press('Enter')
        await page.wait_for_timeout(700)
        assert any(
            path.endswith('/query') and body.get('query') == '第二次替换查询'
            for path, _, body, _ in fixture.calls
        )
        fixture.query_delay = 0.5
        await page.get_by_label('检索问题', exact=True).fill('验证切换请求')
        await button(page, '检索').click()
        await expect(button(page, '重新检索')).to_be_visible()
        await button(page, '文档管理').click()
        await button(page, '检索测试').click()
        await expect(button(page, '检索')).to_be_visible()
        fixture.query_delay = 0
        await page.get_by_label('检索问题', exact=True).fill('切页恢复查询')
        await button(page, '检索').click()
        await expect(page.locator('.query-passage p')).to_contain_text(UNSAFE)
        await page.get_by_label('检索问题', exact=True).fill('验证知识')
        await choose(page, '检索目标', '知识图谱')
        await button(page, '检索').click()
        await expect(page.locator('.query-answer')).to_contain_text('图谱回答')
        await button(page, '知识图谱').click()
        await expect(page).to_have_url(re.compile(r'dbId=kb-main.*view=graph'))
        await expect(page.locator('.knowledge-graph canvas')).to_be_visible()
        await expect(page.get_by_text('当前展示部分节点。可选择具体实体或调整深度继续探索。', exact=True)).to_be_visible()
        await choose(page, '图谱实体', LABEL)
        await page.keyboard.press('Escape')
        await button(page, '加载图谱').click()
        await expect(button(page, '加载图谱')).not_to_have_class(re.compile('ant-btn-loading'))
        await page.wait_for_timeout(400)
        assert any(path.endswith('/subgraph') and params.get('node_label') == [LABEL] for path, _, _, params in fixture.calls)
        assert await page.evaluate('window.__kbInjected || null') is None
        await page.screenshot(path='/tmp/knowledge-desktop-graph.png', full_page=True)

        fixture.subgraph_delay = 0.5
        await button(page, '加载图谱').click()
        await button(page, '文档管理').click()
        await button(page, '知识图谱').click()
        await expect(button(page, '加载图谱')).not_to_have_class(re.compile('ant-btn-loading'))
        fixture.subgraph_delay = 0

        await button(page, '文档管理').click()
        await expect(page).to_have_url(re.compile(r'dbId=kb-main.*view=documents'))
        await button(page, '第一目录').click()
        await expect(button(page, '目录文档.md')).to_be_visible()
        await button(page, '返回知识库').click()
        row = page.locator('.knowledge-card').filter(has_text='第二知识库')
        await button(row, '管理文档').click()
        await expect(page.get_by_text('当前目录暂无文档', exact=True)).to_be_visible()
        await page.goto(BASE_URL + '/ai/knowledge?dbId=kb-second&view=graph', wait_until='networkidle')
        await expect(page).to_have_url(re.compile(r'dbId=kb-second.*view=documents'))
        await button(page, '返回知识库').click()
        row = page.locator('.knowledge-card').filter(has_text='资料库')
        await button(row, '管理文档').click()
        await expect(button(page, UNSAFE + '.txt')).to_be_visible()
        row = page.locator('tr').filter(has_text='upload.txt')
        await button(row, '删除').click()
        await button(modal(page), '删除').click()
        await expect(button(page, 'upload.txt')).to_have_count(0)

        fixture.preview_delay = 0.5
        await button(page, UNSAFE + '.txt').click()
        await expect(modal(page)).to_be_visible()
        await page.go_back()
        await expect(page.locator('.ant-modal:visible')).to_have_count(0)
        await page.go_forward()
        await expect(button(page, UNSAFE + '.txt')).to_be_visible()
        fixture.preview_delay = 0

        row = page.locator('tr').filter(has_text=UNSAFE + '.txt')
        await button(row, '重新切片').click()
        await expect(modal(page)).to_be_visible()
        await page.go_back()
        await expect(page.locator('.ant-modal:visible')).to_have_count(0)
        await page.go_forward()
        await expect(button(page, UNSAFE + '.txt')).to_be_visible()

        row = page.locator('tr').filter(has_text=UNSAFE + '.txt')
        await button(row, '删除').click()
        await expect(modal(page)).to_be_visible()
        await page.go_back()
        await expect(page.locator('.ant-modal:visible')).to_have_count(0)
        await page.go_forward()
        await expect(button(page, UNSAFE + '.txt')).to_be_visible()

        fixture.upload_delay = 0.5
        await button(page, '上传文档').click()
        await modal(page).locator('input[type=file]').set_input_files({
            'name': 'stale-upload.txt',
            'mimeType': 'text/plain',
            'buffer': b'stale upload context',
        })
        await expect(modal(page).get_by_text('上传中', exact=True)).to_be_visible()
        await page.go_back()
        await expect(page.locator('.ant-modal:visible')).to_have_count(0)
        await page.go_forward()
        await expect(button(page, UNSAFE + '.txt')).to_be_visible()
        fixture.upload_delay = 0

        await button(page, '返回知识库').click()
        await button(page.locator('.knowledge-card').filter(has_text='新库已改名'), '删除知识库').click()
        await button(modal(page), '删除').click()
        await expect(page.get_by_text('新库已改名', exact=True)).to_have_count(0)

        await page.goto(BASE_URL + '/ai/knowledge?dbId=kb-late&view=query', wait_until='networkidle')
        await expect(page.get_by_text('知识库不存在或已被删除', exact=True)).to_be_visible()
        fixture.databases.append({
            'db_id': 'kb-late',
            'name': '延迟出现的知识库',
            'description': '',
            'kb_type': 'chroma',
            'enable_knowledge_graph': False,
        })
        await button(page, '重试').click()
        await expect(page.get_by_label('检索问题', exact=True)).to_be_visible()
        await expect(page).to_have_url(re.compile(r'dbId=kb-late.*view=query'))
        assert not errors, errors
        print('desktop: CRUD, folders, preview, rechunk, upload failure/retry, vector/graph queries, safe text, graph label encoding, truncation and cross-KB folder reset passed')
    except Exception:
        await page.screenshot(path='/tmp/knowledge-smoke-failure.png', full_page=True)
        print('page errors:', errors)
        print('visible text:', (await page.locator('body').inner_text())[-8000:])
        raise
    finally:
        await context.close()


async def mobile_permissions(browser):
    context = await browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True)
    fixture = KnowledgeFixture(['ai:knowledge:list', 'ai:knowledge:query'])
    await fixture.install(context)
    page = await context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        await page.goto(BASE_URL + '/ai/knowledge', wait_until='networkidle')
        await expect(button(page, '创建知识库')).to_have_count(0)
        await expect(button(page, '删除知识库')).to_have_count(0)
        row = page.locator('.knowledge-card').filter(has_text='资料库')
        await button(row, '管理文档').click()
        await expect(page).to_have_url(re.compile(r'dbId=kb-main.*view=documents'))
        await expect(button(page, '上传文档')).to_have_count(0)
        await expect(button(page, '新建文件夹')).to_have_count(0)
        await expect(button(page, '重新切片')).to_have_count(0)
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.screenshot(path='/tmp/knowledge-mobile-documents.png', full_page=True)
        assert not errors, errors
        print('mobile: 390px workspace fits viewport; query-only permissions hide mutation controls')
    finally:
        await context.close()


async def mobile_full_permissions(browser):
    context = await browser.new_context(viewport={'width': 390, 'height': 844}, is_mobile=True)
    fixture = KnowledgeFixture()
    fixture.databases[0]['name'] = '超' * 100
    await fixture.install(context)
    page = await context.new_page()
    try:
        await page.goto(BASE_URL + '/ai/knowledge?dbId=kb-main&view=documents', wait_until='networkidle')
        await expect(page.get_by_role('heading', name='超' * 100, exact=True)).to_be_visible()
        for label in ('返回知识库', '刷新文档', '新建文件夹', '上传文档'):
            control = button(page, label)
            await expect(control).to_be_visible()
            box = await control.bounding_box()
            assert box and box['x'] >= 0 and box['x'] + box['width'] <= 390, (label, box)
        title_box = await page.get_by_role('heading', name='超' * 100, exact=True).bounding_box()
        assert title_box and title_box['x'] >= 0 and title_box['x'] + title_box['width'] <= 390, title_box
        assert title_box['height'] <= 60, title_box
        assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await page.screenshot(path='/tmp/knowledge-mobile-full-permissions.png', full_page=True)
        print('mobile full permissions: long title and all toolbar actions stay inside viewport')
    finally:
        await context.close()


async def query_only_deep_link(browser):
    context = await browser.new_context(viewport={'width': 1280, 'height': 800})
    fixture = KnowledgeFixture(['ai:knowledge:query'])
    await fixture.install(context)
    page = await context.new_page()
    try:
        await page.goto(BASE_URL + '/ai/knowledge?dbId=kb-main&view=query', wait_until='networkidle')
        await expect(page.get_by_label('检索问题', exact=True)).to_be_visible()
        await expect(page).to_have_url(re.compile(r'dbId=kb-main.*view=query'))
        print('query-only deep link: database detail loads without list permission')
    finally:
        await context.close()


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            await desktop(browser)
            await mobile_permissions(browser)
            await mobile_full_permissions(browser)
            await query_only_deep_link(browser)
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
