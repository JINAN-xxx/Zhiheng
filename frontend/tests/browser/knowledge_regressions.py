"""Original knowledge workspace against the built-in core's HTTP contracts."""
import asyncio
import contextlib
import re
import unittest
from urllib.parse import urlparse

from playwright.async_api import expect
from developer_regressions import DeveloperBoundary
from files_regressions import FILE


STAMP = '2026-10-23T00:00:00Z'


class KnowledgeUIRegressions(DeveloperBoundary):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.base = {'id': 'base-1', 'name': '业务知识', 'description': '业务资料', 'owner_id': 'user-1', 'access': 'private', 'version': 1, 'acl_version': 1, 'embed_model_id': 'embed-1', 'chunk_size': 1000, 'chunk_overlap': 200, 'active_revision': 'revision-1', 'active_job_id': None, 'index_status': 'ready', 'document_count': 1, 'last_error': None, 'enable_knowledge_graph': False, 'llm_model_id': None, 'graph_status': 'disabled', 'created_at': STAMP, 'updated_at': STAMP, 'can_manage': True}
        self.bases = [self.base]
        self.documents = [{'id': 'doc-1', 'base_id': 'base-1', 'file_id': 'file-1', 'file_name': '业务说明.txt', 'file_size': 64, 'mime': 'text/plain', 'folder_id': None, 'version': 1, 'status': 'ready', 'active_revision': 'revision-1', 'job_id': None, 'chunk_count': 1, 'chunk_size': 1000, 'chunk_overlap': 200, 'error': None, 'graph_status': 'disabled', 'graph_error': None, 'created_at': STAMP, 'updated_at': STAMP}]
        self.folders = []
        self.queries = []
        self.preparation = {'kind': 'knowledge_preparation', 'id': 'prep-1', 'resource_type': 'knowledge_query', 'resource_id': 'base-1', 'status': 'completed', 'phase': 'completed', 'run': None, 'hits': [{'chunk_id': 'chunk-1', 'document_id': 'doc-1', 'file_id': 'file-1', 'source_id': 'doc-1', 'knowledge_base_id': 'base-1', 'index_revision': 'revision-1', 'title': '业务说明.txt', 'content': '业务工作时间是九点到十七点。', 'score': 0.87}], 'error': None, 'created_at': STAMP, 'updated_at': STAMP}

    async def respond(self, route):
        path, method = urlparse(route.request.url).path, route.request.method
        if path == '/api/knowledge/capabilities':
            body = {'milvus_configured': True, 'milvus_available': True, 'native_formats': ['txt', 'md', 'pdf', 'docx', 'xlsx'], 'graph_available': False, 'mineru_available': False}
        elif path == '/api/models':
            body = [{'id': 'embed-1', 'name': '业务向量模型', 'connection_id': 'connection-1', 'upstream_model': 'embed-private', 'max_output_tokens': 512, 'model_type': 'embedding', 'published': True, 'enabled': True, 'capabilities': ['embedding']}, {'id': 'chat-1', 'name': '对话模型', 'connection_id': 'connection-1', 'upstream_model': 'chat-private', 'max_output_tokens': 512, 'model_type': 'chat', 'published': True, 'enabled': True, 'capabilities': ['chat', 'stream']}]
        elif path == '/api/knowledge/bases' or path == '/api/knowledge/available':
            if method == 'POST':
                self.posts.append(route.request.post_data_json)
                self.base.update(route.request.post_data_json)
                self.bases = [self.base]
                await route.fulfill(status=201, json=self.base)
                return
            body = self.bases
        elif path == '/api/knowledge/bases/base-1':
            body = self.base
        elif path == '/api/knowledge/bases/base-1/documents':
            body = {'items': self.documents, 'total': len(self.documents)}
        elif path == '/api/knowledge/bases/base-1/folders':
            body = self.folders
        elif path == '/api/knowledge/bases/base-1/jobs':
            body = []
        elif path == '/api/knowledge/bases/base-1/documents/doc-1/preview':
            body = {'document_id': 'doc-1', 'file_id': 'file-1', 'text': '业务工作时间是九点到十七点。', 'parser_version': 'native-v1', 'chunk_count': 1}
        elif path == '/api/knowledge/bases/base-1/query':
            self.queries.append(route.request.post_data_json)
            await route.fulfill(status=202, json=self.preparation)
            return
        elif path == '/api/knowledge/preparations/prep-1':
            body = self.preparation
        else:
            await super().respond(route)
            return
        await route.fulfill(json=body)

    async def test_original_overview_and_document_navigation(self):
        await self.page.goto(self.origin + '/knowledge')
        await expect(self.page.get_by_role('heading', name='我的知识库', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='管理文档', exact=True).click()
        await expect(self.page.get_by_role('heading', name='业务知识', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='业务说明.txt', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='业务说明.txt', exact=True).click()
        await expect(self.page.get_by_text('业务工作时间是九点到十七点。', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('原文权限与知识库权限分别生效', exact=False).first).to_be_visible()

    async def test_create_uses_an_embedding_model_and_preserves_empty_chunk_validation(self):
        self.bases = []
        await self.page.goto(self.origin + '/knowledge')
        await self.page.get_by_role('button', name='创建知识库', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        await dialog.get_by_label('知识库名称', exact=True).fill('业务知识')
        await dialog.get_by_role('combobox', name=re.compile(r'向量模型$')).click()
        await expect(self.page.get_by_text('对话模型', exact=True)).to_have_count(0)
        await self.page.get_by_text('业务向量模型', exact=True).click()
        await dialog.get_by_role('spinbutton', name='切片大小', exact=True).fill('')
        await dialog.get_by_role('button', name=re.compile(r'^保\s*存$')).click()
        self.assertEqual(self.posts, [])
        await expect(dialog.get_by_text('请填写有效的切片大小与重叠大小', exact=True)).to_be_visible()
        await dialog.get_by_role('spinbutton', name='切片大小', exact=True).fill('1000')
        await dialog.get_by_role('button', name=re.compile(r'^保\s*存$')).click()
        await expect(dialog).not_to_be_visible()
        self.assertEqual(self.posts[0]['embed_model_id'], 'embed-1')
        self.assertEqual(self.posts[0]['access'], 'private')
        self.assertFalse(self.posts[0]['enable_knowledge_graph'])

    async def test_query_displays_actual_passages_and_uses_document_source_identity(self):
        await self.page.goto(self.origin + '/knowledge/base-1/query')
        await self.page.get_by_role('textbox', name='检索问题', exact=True).fill('工作时间？')
        await self.page.get_by_role('button', name='检索', exact=True).click()
        await expect(self.page.locator('.query-passage')).to_contain_text('业务工作时间是九点到十七点。')
        await expect(self.page.get_by_text('1 条结果', exact=True)).to_be_visible()
        self.assertEqual(len(self.queries), 1)
        self.assertEqual(self.queries[0]['query'], '工作时间？')
        self.assertTrue(self.queries[0]['client_request_id'])
        await self.page.get_by_role('button', name='查看来源：业务说明.txt', exact=True).click()
        await expect(self.page.get_by_role('dialog')).to_contain_text('业务工作时间是九点到十七点。')

    async def test_import_lost_response_reuses_file_ids_and_request_key(self):
        uploads, imports = [], []
        job = {'id': 'job-1', 'base_id': 'base-1', 'operation': 'import', 'status': 'completed', 'phase': 'completed', 'version': 3, 'attempt_id': 'attempt-1', 'index_revision': 'revision-2', 'total_documents': 1, 'completed_documents': 1, 'failed_documents': 0, 'chunk_count': 1, 'document_results': [], 'error': None, 'created_at': STAMP, 'updated_at': STAMP, 'finished_at': STAMP, 'cancel_allowed': False}
        async def upload(route):
            uploads.append(route.request.url)
            await route.fulfill(status=201, json={'file': {**FILE, 'id': 'managed-file-upload', 'original_filename': 'note.txt'}, 'upload_id': 'upload-acceptance', 'folder_id': None})
        async def admit(route):
            imports.append(route.request.post_data_json)
            if len(imports) == 1:
                await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '索引接纳结果待确认'}})
            else:
                await route.fulfill(status=202, json=job)
        await self.page.route('**/api/knowledge/bases/base-1/uploads?*', upload)
        await self.page.route('**/api/knowledge/bases/base-1/documents', lambda route: admit(route) if route.request.method == 'POST' else route.fulfill(json={'items': self.documents, 'total': 1}))
        await self.page.goto(self.origin + '/knowledge/base-1/documents')
        await self.page.get_by_role('button', name='上传文档', exact=True).click()
        modal = self.page.get_by_role('dialog')
        await modal.locator('input[type=file]').set_input_files({'name': 'note.txt', 'mimeType': 'text/plain', 'buffer': b'Business facts'})
        await modal.get_by_role('button', name='上传并建立索引', exact=True).click()
        await expect(modal.get_by_text('索引接纳结果待确认', exact=True)).to_be_visible()
        await modal.get_by_role('button', name='确认原提交', exact=True).click()
        await expect(modal).not_to_be_visible()
        self.assertEqual(len(uploads), 1)
        self.assertEqual(imports[0], imports[1])
        self.assertEqual(imports[0]['file_ids'], ['managed-file-upload'])
        self.assertNotIn('upload-acceptance', imports[0]['file_ids'])

    async def test_non_admin_source_permissions_use_the_base_user_directory(self):
        directories = []
        await self.page.route('**/api/auth/me', lambda route: route.fulfill(json={'id': 'user-1', 'username': 'owner', 'is_admin': False}))
        await self.page.route('**/api/files/file-1', lambda route: route.fulfill(json=FILE))
        await self.page.route('**/api/files/file-1/acl', lambda route: route.fulfill(json={'file': FILE, 'entries': [], 'builtin_permissions': [{'source': 'owner', 'enabled': True, 'deny_overridable': False}]}))
        async def users(route):
            directories.append(urlparse(route.request.url).path)
            await route.fulfill(json=[{'id': 'user-2', 'username': 'colleague'}])
        await self.page.route('**/api/knowledge/bases/base-1/users?*', users)
        await self.page.goto(self.origin + '/knowledge/base-1/documents')
        await self.page.get_by_role('button', name='原文权限：业务说明.txt', exact=True).click()
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await drawer.get_by_role('button', name='添加授权', exact=True).click()
        await drawer.locator('.ant-select input').first.fill('colleague')
        await expect(self.page.get_by_text('colleague', exact=True)).to_be_visible()
        self.assertEqual(directories, ['/api/knowledge/bases/base-1/users'])

    async def test_failed_rechunk_keeps_the_old_index_visible(self):
        self.documents[0].update(status='failed', error={'code': 'model_failed', 'message': '模型调用失败'})
        await self.page.goto(self.origin + '/knowledge/base-1/documents')
        await expect(self.page.get_by_text('失败（旧索引可用）', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='业务说明.txt', exact=True)).to_be_enabled()

    async def test_returning_to_completed_query_rechecks_source_permissions(self):
        await self.page.goto(self.origin + '/knowledge/base-1/query')
        await self.page.get_by_role('textbox', name='检索问题', exact=True).fill('工作时间？')
        await self.page.get_by_role('button', name='检索', exact=True).click()
        await expect(self.page.locator('.query-passage')).to_contain_text('业务工作时间是九点到十七点。')
        await self.page.get_by_role('button', name=re.compile(r'返回知识库$')).click()
        await expect(self.page.get_by_role('heading', name='我的知识库', exact=True)).to_be_visible()
        await self.page.route('**/api/knowledge/preparations/prep-1', lambda route: route.fulfill(status=403, json={'error': {'code': 'source_forbidden', 'message': '来源权限已撤销'}}))
        await self.page.get_by_role('button', name='测试检索', exact=True).click()
        await expect(self.page.get_by_text('来源权限已撤销', exact=True)).to_be_visible()
        await expect(self.page.locator('.query-passage')).to_have_count(0)
        self.assertEqual(len(self.queries), 1)

    async def test_query_observation_failure_recovers_without_new_paid_request(self):
        self.preparation.update(status='waiting_models', phase='embedding', hits=[])
        observations = []
        async def observe(route):
            observations.append(route.request.url)
            if len(observations) == 1:
                await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '检索观察中断'}})
            else:
                self.preparation.update(status='completed', phase='completed')
                await route.fulfill(json=self.preparation)
        await self.page.route('**/api/knowledge/preparations/prep-1', observe)
        await self.page.goto(self.origin + '/knowledge/base-1/query')
        await self.page.get_by_role('textbox', name='检索问题', exact=True).fill('工作时间？')
        await self.page.get_by_role('button', name='检索', exact=True).click()
        await expect(self.page.get_by_text('检索观察中断', exact=True)).to_be_visible()
        await self.page.get_by_role('button', name='恢复查看', exact=True).click()
        await expect(self.page.get_by_text('没有检索到相关内容', exact=True)).to_be_visible()
        self.assertEqual(len(self.queries), 1)

    async def test_base_acl_uses_cas_and_does_not_change_source_file_permissions(self):
        acl_writes = []
        async def acl(route):
            if route.request.method == 'PATCH':
                acl_writes.append(route.request.post_data_json)
                self.base.update(access=route.request.post_data_json['access'], acl_version=2)
            await route.fulfill(json={'base': self.base, 'entries': [], 'builtin_permissions': [{'source': 'owner', 'enabled': True, 'deny_overridable': False}]})
        await self.page.route('**/api/knowledge/bases/base-1/acl', acl)
        await self.page.route('**/api/knowledge/bases/base-1/users?*', lambda route: route.fulfill(json=[{'id': 'user-2', 'username': 'colleague'}]))
        await self.page.goto(self.origin + '/knowledge')
        await self.page.get_by_role('button', name='访问权限', exact=True).click()
        drawer = self.page.locator('.ant-drawer-open .ant-drawer-content')
        await drawer.get_by_text('已登录用户可读', exact=True).click()
        await drawer.get_by_role('button', name=re.compile(r'添加授权$')).click()
        await drawer.locator('.ant-select input').first.fill('colleague')
        await self.page.get_by_text('colleague', exact=True).click()
        await drawer.get_by_text('允许访问', exact=True).click()
        await self.page.locator('.ant-select-item-option-content').get_by_text('拒绝访问', exact=True).click()
        await drawer.get_by_role('button', name=re.compile(r'^保\s*存$')).click()
        await expect(drawer).not_to_be_visible()
        self.assertEqual(acl_writes, [{'expected_version': 1, 'access': 'public', 'entries': [{'user_id': 'user-2', 'effect': 'deny', 'expires_at': None}]}])

    async def test_lost_query_admission_can_recover_the_same_request_after_reload(self):
        requests = []
        async def admit(route):
            requests.append(route.request.post_data_json)
            if len(requests) == 1:
                await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '检索接纳响应丢失'}})
            else:
                await route.fulfill(status=202, json=self.preparation)
        await self.page.route('**/api/knowledge/bases/base-1/query', admit)
        await self.page.goto(self.origin + '/knowledge/base-1/query')
        await self.page.get_by_role('textbox', name='检索问题', exact=True).fill('工作时间？')
        await self.page.get_by_role('button', name='检索', exact=True).click()
        await expect(self.page.get_by_text('检索接纳响应丢失', exact=True)).to_be_visible()
        await self.page.reload()
        await self.page.get_by_role('button', name='恢复查看', exact=True).click()
        await expect(self.page.locator('.query-passage')).to_contain_text('业务工作时间是九点到十七点。')
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0], requests[1])

    async def test_known_preparation_read_error_on_reload_recovers_with_get(self):
        await self.page.add_init_script("sessionStorage.setItem('zhiheng:knowledge-query:user-1:base-1', 'prep-1')")
        reads = []
        async def observe(route):
            reads.append(route.request.url)
            if len(reads) == 1:
                await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '准备状态暂时无法读取'}})
            else:
                await route.fulfill(json=self.preparation)
        await self.page.route('**/api/knowledge/preparations/prep-1', observe)
        await self.page.goto(self.origin + '/knowledge/base-1/query')
        await expect(self.page.get_by_text('准备状态暂时无法读取', exact=True)).to_be_visible()
        await self.page.get_by_role('textbox', name='检索问题', exact=True).fill('另一个问题')
        await expect(self.page.get_by_role('button', name='检索', exact=True)).to_be_disabled()
        await self.page.get_by_role('button', name='恢复查看', exact=True).click()
        await expect(self.page.locator('.query-passage')).to_contain_text('业务工作时间是九点到十七点。')
        self.assertEqual(len(reads), 2)
        self.assertEqual(self.queries, [])

    async def test_known_id_can_cancel_before_first_preparation_snapshot(self):
        self.preparation.update(status='waiting_models', phase='embedding', hits=[])
        await self.page.add_init_script("sessionStorage.setItem('zhiheng:knowledge-query:user-1:base-1', 'prep-1')")
        reading, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
        reads = []
        cancels = []
        async def observe(route):
            reads.append(route.request.url)
            if len(reads) == 1:
                await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '首次状态读取失败'}})
            else:
                reading.set()
                await release.wait()
                with contextlib.suppress(Exception):
                    await route.fulfill(json=self.preparation)
        async def cancel(route):
            cancels.append(route.request.url)
            self.preparation.update(status='cancelled', phase='cancelled', hits=[])
            await route.fulfill(json=self.preparation)
            cancelled.set()
        await self.page.route('**/api/knowledge/preparations/prep-1', observe)
        await self.page.route('**/api/knowledge/preparations/prep-1/cancel', cancel)
        try:
            await self.page.goto(self.origin + '/knowledge/base-1/query')
            await expect(self.page.get_by_text('首次状态读取失败', exact=True)).to_be_visible()
            await self.page.get_by_role('button', name='恢复查看', exact=True).click()
            await asyncio.wait_for(reading.wait(), timeout=4)
            await self.page.get_by_role('button', name='取消检索', exact=True).click()
            try:
                await asyncio.wait_for(cancelled.wait(), timeout=3)
            except TimeoutError:
                self.fail('已知准备 ID 的取消没有送达服务端')
            self.assertEqual(len(cancels), 1)
            self.assertEqual(self.queries, [])
        finally:
            release.set()


if __name__ == '__main__':
    unittest.main(verbosity=2)
