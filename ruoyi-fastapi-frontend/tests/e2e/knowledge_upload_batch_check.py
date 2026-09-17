"""Browser regression: stage a chooser batch before automatic graph construction locks writes."""
import asyncio
import re
from playwright.async_api import async_playwright, expect
from knowledge_check import BASE_URL, KnowledgeFixture, button, modal


class BatchFixture(KnowledgeFixture):
    def __init__(self, failure=None):
        super().__init__()
        self.failure = failure
        self.failed_once = False
        self.uploads = {}
        self.events = []
        self.index_batches = []
        self.graph_locked = False

    def respond(self, path, method, body, params):
        if path == '/ai/knowledge/files/upload':
            name = re.search(r'filename="([^"]+)"', body['multipart']).group(1)
            self.events.append(('upload', name))
            if self.graph_locked:
                return {'_http_status': 409, 'code': 409, 'msg': '知识图谱正在构建中，请完成后再修改文档'}
            if self.failure == 'upload' and name == 'first.txt' and not self.failed_once:
                self.failed_once = True
                return {'_http_status': 503, 'code': 503, 'msg': '首个文件上传失败'}
            upload_id = 'upload-' + name
            self.uploads[upload_id] = name
            return {'code': 200, 'data': {'upload_id': upload_id}}
        if path == '/ai/knowledge/databases/kb-main/documents' and method == 'POST':
            self.events.append(('index', list(body['items'])))
            self.index_batches.append(list(body['items']))
            items = []
            for upload_id in body['items']:
                name = self.uploads[upload_id]
                failed = self.failure == 'index' and name == 'first.txt' and not self.failed_once
                self.failed_once = self.failed_once or failed
                document = {'id': 'doc-' + name, 'db_id': 'kb-main', 'file_name': name, 'status': 'failed' if failed else 'done', 'error': {'code': 'parse_error', 'message': '首个文件解析失败'} if failed else None}
                self.documents.append(document)
                items.append(document)
            self.start_graph()
            return {'code': 200, 'data': {'status': 'partial' if any(item['status'] == 'failed' for item in items) else 'success', 'items': items}}
        if path.endswith('/rechunk') and method == 'POST':
            self.events.append(('rechunk', path.split('/')[-2]))
            if self.graph_locked:
                return {'_http_status': 409, 'code': 409, 'msg': '知识图谱正在构建中，请完成后再修改文档'}
            self.start_graph()
            return {'code': 200, 'data': {'status': 'success', 'items': [{'id': path.split('/')[-2], 'status': 'done'}]}}
        return super().respond(path, method, body, params)

    def start_graph(self):
        self.graph_locked = True
        self.graph_jobs['kb-main'] = {'status': 'running', 'total_documents': 2, 'processed_documents': 0, 'failed_documents': 0}

    def finish_graph(self):
        self.graph_locked = False
        self.graph_jobs['kb-main'].update(status='completed', processed_documents=2)


async def upload_batch(browser, failure=None):
    fixture = BatchFixture(failure)
    context = await browser.new_context(viewport={'width': 1440, 'height': 1000})
    await fixture.install(context)
    page = await context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        await page.goto(BASE_URL + '/ai/knowledge?dbId=kb-main&view=documents', wait_until='networkidle')
        await button(page, '上传文档').click()
        await modal(page).locator('input[type=file]').set_input_files([
            {'name': name, 'mimeType': 'text/plain', 'buffer': b'batch document'} for name in ('first.txt', 'second.txt')
        ])
        await expect(modal(page).locator('.upload-result')).to_have_count(2)
        await expect(modal(page).get_by_text('完成', exact=True)).to_have_count(1 if failure else 2)
        assert fixture.events[:2] == [('upload', 'first.txt'), ('upload', 'second.txt')], fixture.events
        assert len(fixture.index_batches) == 1, fixture.index_batches
        assert fixture.index_batches[0] == (['upload-second.txt'] if failure == 'upload' else ['upload-first.txt', 'upload-second.txt'])
        await expect(page.get_by_text('图谱正在构建，完成后可继续上传、切片或删除文档。', exact=True)).to_be_visible()
        if failure:
            expected_error = '首个文件上传失败' if failure == 'upload' else '首个文件解析失败'
            await expect(modal(page).get_by_text(expected_error, exact=True)).to_be_visible()
            await expect(button(modal(page), '重试')).to_be_disabled()
            fixture.finish_graph()
            await expect(button(modal(page), '重试')).to_be_enabled()
            await button(modal(page), '重试').click()
            await expect(modal(page).get_by_text('完成', exact=True)).to_have_count(2)
            assert fixture.events[-1][0] == ('index' if failure == 'upload' else 'rechunk'), fixture.events
            assert len([event for event in fixture.events if event == ('upload', 'second.txt')]) == 1
            await expect(page.get_by_text('图谱正在构建，完成后可继续上传、切片或删除文档。', exact=True)).to_be_visible()
        assert not errors, errors
        print('upload batch:', failure or 'two files', 'stages uploads before indexing, retains file results and refreshes graph state passed')
    finally:
        await context.close()


async def rechunk_refreshes_graph(browser):
    fixture = BatchFixture()
    context = await browser.new_context(viewport={'width': 1440, 'height': 1000})
    await fixture.install(context)
    page = await context.new_page()
    try:
        await page.goto(BASE_URL + '/ai/knowledge?dbId=kb-main&view=documents', wait_until='networkidle')
        row = page.locator('tr').filter(has_text=fixture.documents[0]['file_name'])
        await button(row, '重新切片').click()
        await button(modal(page), '确定').click()
        await expect(modal(page)).to_have_count(0)
        await expect(page.get_by_text('图谱正在构建，完成后可继续上传、切片或删除文档。', exact=True)).to_be_visible()
        await expect(button(page, '上传文档')).to_be_disabled()
        await button(page, '知识图谱').click()
        await expect(page.get_by_text('正在构建图谱', exact=True)).to_be_visible()
        print('upload batch: rechunk immediately refreshes automatic graph progress and write locks passed')
    finally:
        await context.close()


async def batch_limit(browser):
    fixture = BatchFixture()
    context = await browser.new_context(viewport={'width': 1440, 'height': 1000})
    await fixture.install(context)
    page = await context.new_page()
    try:
        await page.goto(BASE_URL + '/ai/knowledge?dbId=kb-main&view=documents', wait_until='networkidle')
        await button(page, '上传文档').click()
        await modal(page).locator('input[type=file]').set_input_files([
            {'name': f'file-{index}.txt', 'mimeType': 'text/plain', 'buffer': b'file'} for index in range(21)
        ])
        await expect(modal(page).get_by_text('每次最多选择 20 个文件，请分批上传。', exact=True)).to_be_visible()
        assert not fixture.events, fixture.events
        print('upload batch: chooser limit blocks oversized batches before network writes passed')
    finally:
        await context.close()


async def documents_follow_graph_completion(browser):
    for terminal, label in (('completed', '已完成'), ('failed', '构建失败')):
        fixture = BatchFixture()
        fixture.start_graph()
        fixture.documents[0]['graph_status'] = 'running'
        context = await browser.new_context(viewport={'width': 1440, 'height': 1000})
        await fixture.install(context)
        page = await context.new_page()
        try:
            await page.goto(BASE_URL + '/ai/knowledge?dbId=kb-main&view=documents', wait_until='networkidle')
            row = page.locator('tr').filter(has_text=fixture.documents[0]['file_name'])
            await expect(row.get_by_text('构建中', exact=True)).to_be_visible()
            document_reads = lambda: len([1 for path, method, _, _ in fixture.calls if path == '/databases/kb-main/documents' and method == 'GET'])
            assert document_reads() == 1, 'entering documents should load rows once'
            fixture.graph_jobs['kb-main'].update(status=terminal, processed_documents=2, failed_documents=1 if terminal == 'failed' else 0)
            fixture.documents[0].update(graph_status=terminal, graph_error={'message': '图谱任务终态错误'} if terminal == 'failed' else None)
            await expect(row.get_by_text(label, exact=True)).to_be_visible()
            await expect(row.get_by_text('已索引', exact=True)).to_be_visible()
            if terminal == 'failed':
                await row.get_by_text(label, exact=True).hover()
                await expect(page.get_by_role('tooltip')).to_contain_text('图谱任务终态错误')
            await page.wait_for_timeout(2300)
            assert document_reads() == 2, 'terminal transition must refresh rows once without a reload loop'
            print('graph documents:', terminal, 'updates file status/error automatically without manual refresh passed')
        finally:
            await context.close()


async def main():
    expect.set_options(timeout=20000)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            for failure in (None, 'upload', 'index'):
                await upload_batch(browser, failure)
            await batch_limit(browser)
            await rechunk_refreshes_graph(browser)
            await documents_follow_graph_completion(browser)
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
