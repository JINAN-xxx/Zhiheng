"""Resource UI contract checks in Chromium, using explicit controlled HTTP fixtures."""

import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.async_api import expect
from developer_regressions import DeveloperBoundary


class DeveloperResourceRegressions(DeveloperBoundary):
    async def asyncSetUp(self):
        self.bindings = []
        self.resource_posts = []
        self.conflict = False
        self.sources = {'source_count': 0, 'source_bytes': 0}
        self.docs_fail = False
        await super().asyncSetUp()
        await self.page.route(self.origin + '/openapi/**', self.schema)

    async def schema(self, route):
        if self.docs_fail:
            await route.fulfill(status=503, json={'error': 'unavailable'})
            return
        group = urlparse(route.request.url).path.rsplit('/', 1)[-1]
        await route.fulfill(status=200, content_type='application/json', body=(Path(os.environ['PLATFORM_SCHEMA_FIXTURES']) / group).read_text())

    async def respond(self, route):
        path, method = urlparse(route.request.url).path, route.request.method
        prefix = '/api/developer/applications/app-1'
        if path == prefix + '/resource-access':
            await route.fulfill(json={
                'application_version': self.app['version'], **self.sources,
                'source_file_limit': self.app['source_file_limit'], 'source_byte_limit': self.app['source_byte_limit'],
                'can_bind_resources': True, 'can_configure_quota': True, 'items': self.bindings,
            })
        elif path == prefix + '/resource-candidates':
            kind = parse_qs(urlparse(route.request.url).query)['kind'][0]
            await route.fulfill(json=[{'kind': kind, 'resource_id': 'resource-1', 'display_name': '我的资料', 'status': 'empty', 'owned': True, 'allowed_actions': ['read', 'write', 'delete'] if kind != 'knowledge_base' else ['read', 'write', 'query', 'delete']},
                {'kind': kind, 'resource_id': 'shared-1', 'display_name': '共享资料', 'status': 'empty', 'owned': False, 'allowed_actions': ['read', 'query'] if kind == 'knowledge_base' else ['read']}])
        elif path.startswith(prefix + '/resources/'):
            payload = route.request.post_data_json
            self.resource_posts.append({'method': method, **payload})
            if self.conflict:
                self.conflict = False
                self.app['version'] += 1
                await route.fulfill(status=409, json={'error': {'code': 'application_conflict', 'message': '版本已变化'}})
                return
            self.assertEqual(payload['expected_version'], self.app['version'])
            self.app['version'] += 1
            if method == 'DELETE':
                self.bindings = []
                await route.fulfill(json={'version': self.app['version']})
            else:
                kind, resource_id = path.split('/')[-2:]
                self.bindings = [{'kind': kind, 'resource_id': resource_id, 'display_name': '共享资料' if resource_id == 'shared-1' else '我的资料', 'status': 'empty', 'owned': resource_id != 'shared-1', 'allowed_actions': ['read', 'query'] if resource_id == 'shared-1' else ['read', 'write', 'delete'], 'actions': payload['actions'], 'available': True}]
                self.sources = {'source_count': 1, 'source_bytes': 5}
                await route.fulfill(json={'application_version': self.app['version']})
        elif path == prefix + '/resource-quota':
            payload = route.request.post_data_json
            self.resource_posts.append({'method': method, **payload})
            self.assertEqual(payload['expected_version'], self.app['version'])
            self.app.update(source_file_limit=payload['source_file_limit'], source_byte_limit=payload['source_byte_limit'])
            self.app['version'] += 1
            await route.fulfill(json={'version': self.app['version']})
        else:
            await super().respond(route)

    async def test_empty_scopes_rejected_and_files_only_key_accepts_empty_models(self):
        await self.open_details()
        await self.page.get_by_role('tab', name='访问密钥', exact=True).click()
        await self.page.get_by_role('button', name='创建密钥', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        await dialog.get_by_label('密钥名称', exact=True).fill('文件接入')
        await self.page.wait_for_timeout(300)
        await self.page.screenshot(path=os.environ['PLATFORM_RESOURCE_SCREENSHOTS'] + '/key-scopes-desktop.png')
        await dialog.get_by_role('checkbox', name=re.compile('模型调用')).uncheck()
        await dialog.get_by_role('checkbox', name=re.compile('智能体调用')).uncheck()
        await dialog.get_by_role('button', name=re.compile(r'^创\s*建$')).click()
        await expect(dialog.get_by_text('请选择操作能力', exact=True)).to_be_visible()
        self.assertEqual(self.posts, [])
        await dialog.get_by_role('checkbox', name=re.compile('读取文件')).check()
        await dialog.get_by_role('button', name=re.compile(r'^创\s*建$')).click()
        issued = self.page.get_by_role('dialog', name='访问密钥已生成', exact=True)
        await expect(issued).to_be_visible()
        self.assertEqual(self.posts[-1]['model_ids'], [])
        self.assertEqual(self.posts[-1]['scopes'], ['files:read'])
        self.assertEqual(self.posts[-1]['resource_rpm_limit'], 120)
        await issued.get_by_role('button', name='我已保存', exact=True).click()
        await expect(self.page.get_by_text('操作能力：读取文件（签发后固定）', exact=True)).to_be_visible()

    async def test_shared_actions_cas_retry_quota_and_unbind_preserve_application_fields(self):
        await self.open_details()
        await self.page.get_by_role('tab', name='资源授权', exact=True).click()
        await expect(self.page.get_by_text('尚未授权资源', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='授权资源', exact=True).click()
        dialog = self.page.get_by_role('dialog', name='授权资源', exact=True)
        await dialog.locator('.ant-select-selector').first.click()
        await self.page.locator('.ant-select-item-option-content').get_by_text('知识库', exact=True).click()
        await dialog.locator('.ant-select-selector').nth(1).click()
        await self.page.locator('.ant-select-item-option-content').filter(has_text='共享资料').click()
        await expect(dialog.get_by_role('checkbox', name='写入', exact=True)).to_have_count(0)
        await expect(dialog.get_by_role('checkbox', name='删除', exact=True)).to_have_count(0)
        await dialog.get_by_role('checkbox', name='读取', exact=True).check()
        self.conflict = True
        await dialog.get_by_role('button', name='保存授权', exact=True).click()
        await expect(dialog.get_by_text('应用已变化，已刷新当前版本。你的输入已保留，请检查后重新提交。', exact=True)).to_be_visible()
        await expect(dialog.get_by_role('checkbox', name='读取', exact=True)).to_be_checked()
        await dialog.get_by_role('button', name='保存授权', exact=True).click()
        await expect(dialog).to_be_hidden()
        await expect(self.page.get_by_text('共享资料', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('heading', name='内部工具', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='修改额度', exact=True).click()
        quota = self.page.get_by_role('dialog', name='修改应用资源额度', exact=True)
        await quota.get_by_label('原始文件数量上限', exact=True).fill('2000')
        await quota.get_by_role('button', name='保存额度', exact=True).click()
        await expect(quota).to_be_hidden()
        await expect(self.page.get_by_text('原始文件 1 / 2000 个', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='取消授权', exact=True).click()
        await self.page.get_by_role('tooltip').get_by_role('button', name='取消授权', exact=True).click()
        await expect(self.page.get_by_text('尚未授权资源', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('原始文件 1 / 2000 个', exact=True)).to_be_visible()
        await self.page.set_viewport_size({'width': 375, 'height': 812})
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'))
        await self.page.screenshot(path=os.environ['PLATFORM_RESOURCE_SCREENSHOTS'] + '/resources-mobile.png')

    async def test_document_schema_loading_error_retry_copy_and_personal_resources_catalog(self):
        self.docs_fail = True
        await self.page.goto(self.origin + '/developer?view=docs')
        await expect(self.page.get_by_text('接口规范暂时无法加载，请重试', exact=True)).to_be_visible()
        self.docs_fail = False
        await self.page.get_by_role('button', name='重试', exact=True).click()
        navigation = self.page.get_by_role('navigation', name='API 文档目录')
        for label in ('个人技能', '导入与确认'):
            await navigation.locator('summary').filter(has_text=re.compile('^' + label + '$')).click()
        await expect(navigation.get_by_role('link', name=re.compile(r'^POST\s+预检个人技能$'))).to_be_visible()
        for label in ('知识库', '库管理'):
            await navigation.locator('summary').filter(has_text=re.compile('^' + label + '$')).click()
        await expect(navigation.get_by_role('link', name=re.compile(r'^POST\s+创建知识库$'))).to_be_visible()
        for label in ('文件资源', '上传与读取'):
            await navigation.locator('summary').filter(has_text=re.compile('^' + label + '$')).click()
        await navigation.get_by_role('link', name=re.compile(r'^POST\s+上传单个文件$')).click()

        schema = json.loads((Path(os.environ['PLATFORM_SCHEMA_FIXTURES']) / 'resources-v1.json').read_text())
        upload = schema['paths']['/api/v1/files']['post']
        section = self.page.locator('#api-' + upload['operationId'])
        await expect(section.get_by_role('heading', name='上传单个文件', exact=True)).to_be_visible()
        key_row = section.get_by_role('row').filter(has=self.page.get_by_role('cell', name='Idempotency-Key', exact=True))
        await expect(key_row.locator('td').nth(2)).to_have_text('否')
        samples = section.locator('.api-doc-code')
        await expect(samples).to_have_count(2)
        basic, retry = samples.nth(0), samples.nth(1)
        basic_source = await basic.locator('pre').inner_text()
        retry_source = await retry.locator('pre').inner_text()
        self.assertNotIn('Idempotency-Key', basic_source)
        self.assertNotIn('REQUEST_ID', basic_source)
        self.assertIn('Idempotency-Key: $IDEMPOTENCY_KEY', retry_source)
        self.assertNotIn('REQUEST_ID', retry_source)
        self.assertIn('curl · 可靠重试', await retry.inner_text())
        for sample, expected in ((basic, basic_source), (retry, retry_source)):
            await sample.get_by_role('button', name='复制示例', exact=True).click()
            copied = await self.page.evaluate('navigator.clipboard.readText()')
            self.assertEqual(copied, expected)
            self.assertIn(self.origin + '/api/v1/files', copied)
            self.assertIn('Bearer $API_KEY', copied)

        recipes = self.page.locator('#api-recipes')
        await expect(recipes).to_contain_text('无需手动生成请求 UUID')
        setup = await recipes.locator('pre').inner_text()
        self.assertIn('API_KEY', setup)
        self.assertIn('FILE_PATH', setup)
        for manual_key in ('REQUEST_ID', 'IDEMPOTENCY_KEY', 'uuid.uuid4'):
            self.assertNotIn(manual_key, setup)
        await self.page.screenshot(path=os.environ['PLATFORM_RESOURCE_SCREENSHOTS'] + '/docs-desktop.png')

    async def test_conflict_refresh_does_not_restart_discovery_after_leaving_resources(self):
        import asyncio
        import contextlib
        requested, release = asyncio.Event(), asyncio.Event()
        hold = False
        candidate_reads = 0
        original = self.respond
        async def fixtures(route):
            nonlocal candidate_reads
            path = urlparse(route.request.url).path
            if path.endswith('/resource-access') and hold:
                requested.set()
                await release.wait()
                with contextlib.suppress(Exception):
                    await original(route)
            else:
                if path.endswith('/resource-candidates'):
                    candidate_reads += 1
                await original(route)
        await self.page.unroute(self.origin + '/api/**')
        await self.page.route(self.origin + '/api/**', fixtures)
        try:
            await self.open_details()
            await self.page.get_by_role('tab', name='资源授权', exact=True).click()
            await self.page.get_by_role('button', name='授权资源', exact=True).click()
            dialog = self.page.get_by_role('dialog', name='授权资源', exact=True)
            await dialog.locator('.ant-select-selector').nth(1).click()
            await self.page.locator('.ant-select-item-option-content').filter(has_text='我的资料').click()
            await dialog.get_by_role('checkbox', name='读取', exact=True).check()
            self.conflict = True
            hold = True
            await dialog.get_by_role('button', name='保存授权', exact=True).click()
            await requested.wait()
            before = candidate_reads
            await self.page.get_by_role('tab', name='概览', exact=True).click(force=True)
            release.set()
            await self.page.wait_for_timeout(500)
            self.assertEqual(candidate_reads, before, 'Leaving the tab must invalidate follow-up discovery too')
            await expect(self.page.get_by_role('dialog', name='授权资源', exact=True)).to_have_count(0)
        finally:
            release.set()


class DeveloperKeyFormRaceRegressions(DeveloperBoundary):
    async def delayed_models(self):
        import asyncio
        import contextlib
        requested = asyncio.Queue()
        releases = []
        original = self.respond
        async def delayed(route):
            if urlparse(route.request.url).path == '/api/models':
                release = asyncio.Event()
                releases.append(release)
                requested.put_nowait(release)
                await release.wait()
                with contextlib.suppress(Exception):
                    await original(route)
            else:
                await original(route)
        await self.page.unroute(self.origin + '/api/**')
        await self.page.route(self.origin + '/api/**', delayed)
        return requested, releases

    async def open_with_known_catalog(self):
        async with self.page.expect_response(lambda response: urlparse(response.url).path.endswith('/scope-catalog')) as response_info:
            await self.open_details()
        await (await response_info.value).body()
        await self.page.get_by_role('tab', name='访问密钥', exact=True).click()
        await self.page.get_by_role('button', name='创建密钥', exact=True).click()
        return self.page.get_by_role('dialog', name='创建访问密钥', exact=True)

    async def select_file_key(self, dialog):
        await dialog.get_by_label('密钥名称', exact=True).fill('文件资源客户端')
        await dialog.get_by_role('checkbox', name=re.compile('模型调用')).uncheck()
        await dialog.get_by_role('checkbox', name=re.compile('智能体调用')).uncheck()
        await dialog.get_by_role('checkbox', name=re.compile('读取文件')).check()
        rpm = dialog.get_by_label('资源每分钟请求数', exact=True)
        await rpm.fill('7')
        await rpm.blur()

    async def assert_file_key_selection(self, dialog):
        await expect(dialog.get_by_role('checkbox', name=re.compile('读取文件'))).to_be_checked()
        await expect(dialog.get_by_role('checkbox', name=re.compile('模型调用'))).not_to_be_checked()
        await expect(dialog.get_by_role('checkbox', name=re.compile('智能体调用'))).not_to_be_checked()
        await expect(dialog.get_by_label('资源每分钟请求数', exact=True)).to_have_value('7')

    async def test_delayed_model_response_preserves_edited_scope_and_rpm(self):
        import asyncio
        requested, releases = await self.delayed_models()
        try:
            dialog = await self.open_with_known_catalog()
            release = await asyncio.wait_for(requested.get(), 10)
            await self.select_file_key(dialog)
            async with self.page.expect_response(lambda response: urlparse(response.url).path == '/api/models') as response_info:
                release.set()
            await (await response_info.value).body()
            await self.assert_file_key_selection(dialog)
            await dialog.get_by_role('button', name=re.compile(r'^创\s*建$')).click()
            await expect(self.page.get_by_role('dialog', name='访问密钥已生成', exact=True)).to_be_visible()
            self.assertEqual(self.posts[-1]['scopes'], ['files:read'])
            self.assertEqual(self.posts[-1]['resource_rpm_limit'], 7)
            self.assertEqual(self.posts[-1]['model_ids'], [])
        finally:
            for release in releases:
                release.set()

    async def test_previous_modal_response_cannot_overwrite_reopened_form_or_block_resource_key(self):
        import asyncio
        requested, releases = await self.delayed_models()
        try:
            first = await self.open_with_known_catalog()
            previous = await asyncio.wait_for(requested.get(), 10)
            await first.get_by_role('button', name=re.compile(r'^取\s*消$')).click()
            await expect(first).to_be_hidden()
            await self.page.get_by_role('button', name='创建密钥', exact=True).click()
            current = self.page.get_by_role('dialog', name='创建访问密钥', exact=True)
            await asyncio.wait_for(requested.get(), 10)
            await self.select_file_key(current)
            previous.set()
            await self.assert_file_key_selection(current)
            # The current model request remains held: a resource-only Key can submit now.
            await current.get_by_role('button', name=re.compile(r'^创\s*建$')).click()
            await expect(self.page.get_by_role('dialog', name='访问密钥已生成', exact=True)).to_be_visible()
            self.assertEqual(self.posts[-1]['scopes'], ['files:read'])
            self.assertEqual(self.posts[-1]['resource_rpm_limit'], 7)
            self.assertEqual(self.posts[-1]['model_ids'], [])
        finally:
            for release in releases:
                release.set()
