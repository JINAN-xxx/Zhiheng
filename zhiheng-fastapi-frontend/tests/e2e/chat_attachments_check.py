"""Desktop file-chooser regression against the real app with isolated HTTP fixtures.

Run with PLATFORM_SHELL_BASE_URL=http://127.0.0.1:15174 python tests/e2e/chat_attachments_check.py.
The fixture token is used only for intercepted APIs; no live conversations or files are changed.
"""

import asyncio
import base64
import json
import re
from email import policy
from email.parser import BytesParser
from urllib.parse import unquote, urlparse

from playwright.async_api import Error as PlaywrightError, Route, async_playwright, expect

from platform_shell_check import BASE_URL, assert_no_overflow, new_page


MB = 1024 * 1024
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgaPj/HwAEggJ/59habAAAAABJRU5ErkJggg==')


def chosen_file(name, *, size=None):
    content = PNG if name.endswith('.png') else b'attachment fixture content\n'
    if size is not None:
        content = (content + b' ' * size)[:size]
    return {'name': name, 'mimeType': 'image/png' if name.endswith('.png') else 'application/octet-stream', 'buffer': content}


def button(scope, label):
    # Ant buttons include icon accessible names and may insert space between two Chinese characters.
    return scope.get_by_role('button', name=re.compile(
        r'^(?:[A-Za-z][A-Za-z0-9_-]*\s+)*' + r'\s*'.join(map(re.escape, label)) + r'\s*$'))


async def choose(page, files):
    async with page.expect_file_chooser() as chooser:
        await page.get_by_role('button', name='添加图片或附件', exact=True).click()
    await (await chooser.value).set_files(files)


class AttachmentFixture:
    def __init__(self, page):
        self.page = page
        self.uploads = []
        self.downloads = []
        self.sends = []
        self.failed_uploads = {}
        self.send_failures = 0
        self.upload_gate = None
        self.upload_started = asyncio.Event()
        self.vision = True
        self.preferences = {'visionEnabled': True, 'imageMaxSizeMb': 10, 'addHistory': True}

    async def json(self, route, data, status=200):
        await route.fulfill(status=status, content_type='application/json', body=json.dumps(data, ensure_ascii=False))

    async def install(self):
        async def models(route):
            await self.json(route, {'code': 200, 'rows': [
                {'modelId': 'vision-model', 'modelCode': 'vision-model', 'title': '图片对话模型', 'modelType': 'chat', 'capabilities': {'vision': self.vision}},
                {'modelId': 'text-model', 'modelCode': 'text-model', 'title': '文字对话模型', 'modelType': 'chat', 'capabilities': {'vision': False}},
            ]})

        async def preferences(route):
            if route.request.method == 'PUT':
                self.preferences.update(route.request.post_data_json)
            await self.json(route, {'code': 200, 'data': self.preferences})

        async def sessions(route):
            resource = urlparse(route.request.url).path.split('/session/', 1)[1]
            sessions = [
                {'sessionId': 'upload-session', 'title': '附件测试会话', 'modelId': 'vision-model', 'status': 'active'},
                {'sessionId': 'history-session', 'title': '附件历史会话', 'modelId': 'vision-model', 'status': 'active'},
            ]
            if resource == 'list':
                await self.json(route, {'code': 200, 'rows': sessions, 'total': 2})
            else:
                session = next(item for item in sessions if item['sessionId'] == resource)
                messages = [] if resource != 'history-session' else [{
                    'messageId': 'historical-files', 'role': 'user', 'content': '历史附件仍可查看',
                    'images': [{'fileId': 'history-image', 'fileName': '历史照片.png', 'mimeType': 'image/png', 'sizeBytes': len(PNG)}, {'fileId': 'legacy-image'}],
                    'attachments': [{'fileId': 'history-pdf', 'fileName': '产品手册.pdf', 'mimeType': 'application/pdf', 'sizeBytes': 2048, 'downloadUrl': 'https://untrusted.invalid/ignored'}],
                }]
                await self.json(route, {'code': 200, 'data': {'session': session, 'messages': messages, 'runs': []}})

        async def upload(route):
            assert route.request.headers.get('authorization') == 'Bearer e2e-token'
            content_type = route.request.headers.get('content-type', '')
            assert content_type.startswith('multipart/form-data; boundary=')
            body = BytesParser(policy=policy.default).parsebytes(
                f'Content-Type: {content_type}\r\n\r\n'.encode() + route.request.post_data_buffer
            )
            parts = [part for part in body.iter_parts() if part.get_param('name', header='content-disposition') == 'file']
            assert len(parts) == 1
            name = parts[0].get_filename()
            record = {'name': name, 'size': len(parts[0].get_payload(decode=True)), 'fileId': f'uploaded-{len(self.uploads) + 1}'}
            self.uploads.append(record)
            self.upload_started.set()
            if self.upload_gate is not None:
                await self.upload_gate.wait()
            if self.failed_uploads.get(name, 0):
                self.failed_uploads[name] -= 1
                await self.json(route, {'code': 500, 'msg': '文件存储暂时不可用'})
                return
            try:
                await self.json(route, {'code': 200, 'fileId': record['fileId'], 'originalFilename': name, 'accessType': 'private'})
            except PlaywrightError:
                # An intentionally cancelled request may have left the page before this gate opens.
                pass

        async def send(route):
            self.sends.append(route.request.post_data_json)
            if self.send_failures:
                self.send_failures -= 1
                await self.json(route, {'detail': {'message': '附件解析暂不可用，请重试', 'code': 'FILE_UNAVAILABLE'}}, status=400)
                return
            await route.fulfill(status=200, content_type='text/event-stream', body=(
                'event: meta\ndata: {"sessionId":"upload-session","runId":"file-run"}\n\n'
                'event: content\ndata: {"delta":"已读取本次上传文件"}\n\n'
                'event: done\ndata: {"status":"completed"}\n\n'
            ))

        async def download(route):
            assert route.request.headers.get('authorization') == 'Bearer e2e-token'
            parsed = urlparse(route.request.url)
            assert parsed.netloc == urlparse(BASE_URL).netloc
            assert 'token' not in parsed.query.lower()
            self.downloads.append(unquote(parsed.path))
            image = '/history-image/' in parsed.path or '/legacy-image/' in parsed.path
            await route.fulfill(status=200, content_type='application/octet-stream', body=PNG if image else b'%PDF fixture download')

        await self.page.route(re.compile(r'/ai/models(?:\?.*)?$'), models)
        await self.page.route('**/ai/chat/config', preferences)
        await self.page.route('**/ai/chat/session/**', sessions)
        await self.page.route('**/common/files/upload', upload)
        await self.page.route('**/common/files/*/download/*', download)
        await self.page.route('**/ai/chat/send', send)


async def check_files(browser):
    context, page, _, page_errors = await new_page(browser, {'width': 1440, 'height': 1000})
    page.set_default_timeout(10000)
    fixture = AttachmentFixture(page)
    await fixture.install()
    try:
        await page.goto(f'{BASE_URL}/ai/chat?sessionId=upload-session')
        await expect(page.locator('.composer textarea')).to_be_enabled()
        # Five independent document slots plus an image, including an actual multipart body over 1 MB.
        await choose(page, [chosen_file(f'report.{extension}') for extension in ['pdf', 'docx', 'txt', 'md', 'xlsx']] + [chosen_file('large.png', size=2 * MB)])
        await expect(page.locator('.queued-file-status').filter(has_text='已上传')).to_have_count(6)
        assert next(item for item in fixture.uploads if item['name'] == 'large.png')['size'] == 2 * MB
        await expect(page.locator('.queued-file-preview')).to_have_count(1)
        await expect(page.get_by_role('button', name='发送', exact=True)).to_be_enabled()
        await page.get_by_role('button', name='发送', exact=True).click()
        await page.get_by_text('已读取本次上传文件', exact=True).wait_for()
        assert fixture.sends[0]['message'] == '请分析上传的文件。'
        image_id = next(item['fileId'] for item in fixture.uploads if item['name'] == 'large.png')
        assert fixture.sends[0]['images'] == [{'fileId': image_id}]
        assert len(fixture.sends[0]['attachments']) == 5
        assert all(set(item) == {'fileId'} for item in fixture.sends[0]['attachments'])
        await expect(page.locator('.queued-file')).to_have_count(0)
        await expect(page.get_by_role('button', name='下载附件：report.pdf', exact=True)).to_be_visible()

        await choose(page, [chosen_file('table.csv')])
        await expect(page.locator('.queued-file-status').filter(has_text='已上传')).to_have_count(1)
        await page.locator('.composer textarea').fill('比较 CSV 中的数据')
        await page.get_by_role('button', name='发送', exact=True).click()
        await expect(page.get_by_text('已读取本次上传文件', exact=True)).to_have_count(2)
        assert fixture.sends[1]['images'] == []
        assert len(fixture.sends[1]['attachments']) == 1
        await assert_no_overflow(page)
        assert not page_errors, page_errors
        await page.screenshot(path='/tmp/chat-attachments-desktop.png')
        print('PASS actual multipart chooser: six document formats, >1 MB image, mixed payload, file-only send, history names')
    finally:
        await context.close()


async def check_retry(browser):
    context, page, _, page_errors = await new_page(browser, {'width': 1440, 'height': 900})
    fixture = AttachmentFixture(page)
    fixture.failed_uploads['retry.csv'] = 1
    fixture.send_failures = 1
    await fixture.install()
    try:
        await page.goto(f'{BASE_URL}/ai/chat?sessionId=upload-session')
        await expect(page.locator('.composer textarea')).to_be_enabled()
        await choose(page, [chosen_file('retry.csv')])
        await expect(page.locator('.queued-file-error')).to_have_text('文件存储暂时不可用')
        await page.locator('.composer textarea').fill('保留失败草稿')
        await expect(page.get_by_role('button', name='发送', exact=True)).to_be_disabled()
        await page.get_by_role('button', name='重试上传：retry.csv', exact=True).click()
        await expect(page.locator('.queued-file-status')).to_have_text('已上传')
        await page.get_by_role('button', name='发送', exact=True).click()
        await page.get_by_text('附件解析暂不可用，请重试', exact=True).wait_for()
        await expect(page.locator('.composer textarea')).to_have_value('保留失败草稿')
        await expect(page.locator('.queued-file')).to_have_count(1)
        await expect(page.locator('.message-row')).to_have_count(0)
        await page.get_by_role('button', name='发送', exact=True).click()
        await page.get_by_text('已读取本次上传文件', exact=True).wait_for()
        assert fixture.sends[0]['attachments'] == fixture.sends[1]['attachments']
        assert len(fixture.uploads) == 2
        assert not page_errors, page_errors
        print('PASS upload error/retry and initial chat request failure preserve the selected file and draft')
    finally:
        await context.close()


async def check_limits_and_navigation(browser):
    context, page, _, page_errors = await new_page(browser, {'width': 1440, 'height': 1000})
    fixture = AttachmentFixture(page)
    fixture.upload_gate = asyncio.Event()
    await fixture.install()
    try:
        await page.goto(f'{BASE_URL}/ai/chat?sessionId=upload-session')
        await expect(page.locator('.composer textarea')).to_be_enabled()
        await choose(page, [chosen_file(f'batch-{i}.txt') for i in range(6)] + [chosen_file(f'batch-{i}.png') for i in range(11)])
        await expect(page.locator('.queued-file')).to_have_count(15)
        await expect(page.get_by_role('button', name='发送', exact=True)).to_be_disabled()
        await expect(page.locator('.composer-hint')).to_have_text('图片 10/10 · 文档 5/5')
        await expect(page.get_by_text('batch-5.txt：每条消息最多添加 5 个文档', exact=True)).to_be_visible()
        await expect(page.get_by_text('batch-10.png：每条消息最多添加 10 张图片', exact=True)).to_be_visible()
        await choose(page, [chosen_file('too-big.txt', size=20 * MB + 1), chosen_file('too-big.png', size=10 * MB + 1)])
        await expect(page.get_by_text('too-big.txt：单个文档不能超过 20 MB', exact=True)).to_be_visible()
        await expect(page.get_by_text('too-big.png：单个图片不能超过 10 MB', exact=True)).to_be_visible()
        await page.locator('.recent-sessions:visible').get_by_text('附件历史会话', exact=True).click()
        await page.get_by_text('历史附件仍可查看', exact=True).wait_for()
        await expect(page.locator('.queued-file')).to_have_count(0)
        fixture.upload_gate.set()
        await page.wait_for_load_state('networkidle')
        await expect(page.locator('.ant-message-notice-content').filter(has_text=re.compile(r'cancel(?:ed|led)', re.I))).to_have_count(0)
        await expect(page.get_by_role('button', name='发送', exact=True)).to_be_disabled()
        # A delayed old upload cannot leak into this historical conversation or a fresh one.
        await page.get_by_role('button', name='新建对话', exact=True).click()
        await expect(page.locator('.queued-file')).to_have_count(0)
        await choose(page, [chosen_file('fresh.txt')])
        await expect(page.locator('.queued-file-status')).to_have_text('已上传')
        await expect(page.locator('.queued-file-name')).to_have_text('fresh.txt')
        await page.get_by_role('button', name='移除文件：fresh.txt', exact=True).click()
        await expect(page.locator('.queued-file')).to_have_count(0)
        await assert_no_overflow(page)
        assert not page_errors, page_errors
        print('PASS concurrent slot limits, 10/20 MB limits, pending-send block, session/new-chat cleanup, remove')
    finally:
        fixture.upload_gate.set()
        await context.close()


async def check_history_and_vision(browser):
    context, page, _, page_errors = await new_page(browser, {'width': 1440, 'height': 900})
    fixture = AttachmentFixture(page)
    fixture.vision = False
    await fixture.install()
    try:
        await page.goto(f'{BASE_URL}/ai/chat?sessionId=history-session')
        await page.get_by_text('历史附件仍可查看', exact=True).wait_for()
        await expect(page.locator('.file-guidance').filter(has_text='当前模型未启用视觉能力')).to_be_visible()
        await choose(page, [chosen_file('unsupported.png'), chosen_file('allowed.pdf')])
        await expect(page.locator('.queued-file-status')).to_have_text('已上传')
        assert [item['name'] for item in fixture.uploads] == ['allowed.pdf']
        await expect(page.get_by_role('button', name='发送', exact=True)).to_be_enabled()
        await expect(page.locator('.message-files')).not_to_contain_text('history-pdf')
        async with page.expect_download() as download:
            await page.get_by_role('button', name='下载附件：产品手册.pdf', exact=True).click()
        assert (await download.value).suggested_filename == '产品手册.pdf'
        await page.get_by_role('button', name='预览图片：历史照片.png', exact=True).click()
        await expect(page.locator('.file-preview')).to_be_visible()
        await page.wait_for_function("document.querySelector('.file-preview')?.naturalWidth === 1")
        assert (await page.locator('.file-preview').get_attribute('src')).startswith('blob:')
        async with page.expect_download() as download:
            await button(page, '下载图片').click()
        assert (await download.value).suggested_filename == '历史照片.png'
        await button(page.locator('.ant-modal-footer'), '关闭').click()
        await page.get_by_role('button', name='预览图片：图片', exact=True).click()
        await expect(page.locator('.file-preview')).to_be_visible()
        await page.wait_for_function("document.querySelector('.file-preview')?.naturalWidth === 1")
        assert fixture.downloads[-1].endswith('/legacy-image/download/attachment')
        assert not page_errors, page_errors
        print('PASS vision capability hint keeps docs available; history metadata, legacy image, authenticated preview/download')
    finally:
        await context.close()


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            await check_files(browser)
            await check_retry(browser)
            await check_limits_and_navigation(browser)
            await check_history_and_vision(browser)
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
