"""Knowledge indexing observes jobs without replacing the user's document workspace."""

import asyncio
import contextlib
import copy
import json
import os
import unittest
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import expect
from files_regressions import FILE
from knowledge_regressions import KnowledgeUIRegressions, STAMP


class KnowledgeBackgroundRegressions(KnowledgeUIRegressions):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.counts = Counter()
        self.job = {
            'id': 'job-active', 'base_id': 'base-1', 'operation': 'rechunk',
            'status': 'waiting_model', 'phase': 'embedding', 'version': 1,
            'attempt_id': 'attempt-1', 'index_revision': None, 'total_documents': 1,
            'completed_documents': 0, 'failed_documents': 0, 'chunk_count': None,
            'document_results': [], 'error': None, 'cancel_allowed': True,
            'created_at': STAMP, 'updated_at': STAMP, 'finished_at': None,
        }
        self.history = {**self.job, 'id': 'job-old', 'status': 'completed',
                        'phase': 'completed', 'cancel_allowed': False,
                        'completed_documents': 1, 'chunk_count': 1,
                        'finished_at': STAMP}
        self.base.update(active_job_id='job-active', index_status='building', document_policy='library')
        self.observation_errors = []
        self.advance_progress = False
        self.hold_observation = None
        self.trace = []
        self.page.on('request', lambda r: self.trace.append({'method': r.method, 'url': r.url})
                     if '/api/knowledge/' in r.url else None)

    async def asyncTearDown(self):
        evidence = os.environ.get('KNOWLEDGE_BACKGROUND_EVIDENCE')
        if evidence:
            destination = Path(evidence)
            destination.mkdir(parents=True, exist_ok=True)
            (destination / (self._testMethodName + '.json')).write_text(
                json.dumps({'traffic': self.trace, 'counts': dict(self.counts)},
                           ensure_ascii=False, indent=2))
            with contextlib.suppress(Exception):
                await self.page.screenshot(path=str(destination / (self._testMethodName + '.png')))
        await super().asyncTearDown()

    async def respond(self, route):
        path = urlparse(route.request.url).path
        self.counts[path] += 1
        if path == '/api/knowledge/bases/base-1/jobs':
            await route.fulfill(json=[self.job, self.history])
        elif path == '/api/knowledge/jobs/job-active':
            if self.hold_observation:
                await self.hold_observation.wait()
            if self.observation_errors:
                status = self.observation_errors.pop(0)
                await route.fulfill(status=status, json={'error': {
                    'code': 'observation_failed', 'message': '任务状态暂时无法读取'}})
            else:
                if self.advance_progress and self.job['status'] == 'waiting_model':
                    self.job.update(completed_documents=min(self.counts[path], 2),
                                    updated_at=f'2026-10-23T00:00:{self.counts[path]:02d}Z')
                with contextlib.suppress(Exception):
                    await route.fulfill(json=copy.deepcopy(self.job))
        else:
            await super().respond(route)

    async def wait_reads(self, count):
        async def observed():
            while self.counts['/api/knowledge/jobs/job-active'] < count:
                await asyncio.sleep(.03)
        await asyncio.wait_for(observed(), timeout=12)

    async def open_documents(self):
        await self.page.goto(self.origin + '/knowledge/base-1/documents')
        await expect(self.page.get_by_role('button', name='业务说明.txt', exact=True)).to_be_visible()

    async def test_background_observation_does_not_reload_document_lists(self):
        await self.open_documents()
        await self.page.wait_for_timeout(3500)
        self.assertEqual(self.counts['/api/knowledge/bases/base-1/documents'], 1)
        self.assertEqual(self.counts['/api/knowledge/bases/base-1/folders'], 1)
        self.assertGreaterEqual(self.counts['/api/knowledge/jobs/job-active'], 2)

    async def test_background_observation_preserves_document_dom_focus_and_scroll(self):
        self.advance_progress = True
        self.job.update(total_documents=3)
        self.documents[0].update(status='processing')
        await self.open_documents()
        search = self.page.get_by_role('textbox', name='搜索资料', exact=True)
        await search.fill('业务')
        await search.focus()
        await search.evaluate('(el) => { window.keptSearch = el; el.setSelectionRange(0, 1) }')
        await self.page.locator('.ant-table').evaluate('(el) => { window.keptTable = el }')
        history = self.page.locator('details[aria-label="索引任务历史"]')
        await expect(history).to_have_count(0)
        await search.evaluate('(el) => el.focus({preventScroll: true})')
        await self.page.locator('.knowledge-documents__content').evaluate(
            '(el) => { window.keptContent = el; el.scrollTop = 80; window.keptScroll = el.scrollTop }')
        await self.wait_reads(3)
        await expect(search).to_have_value('业务')
        self.assertTrue(await search.evaluate('(el) => el === window.keptSearch && document.activeElement === el && el.selectionStart === 0 && el.selectionEnd === 1'))
        self.assertTrue(await self.page.locator('.ant-table').evaluate('(el) => el === window.keptTable'))
        self.assertTrue(await self.page.locator('.knowledge-documents__content').evaluate('(el) => el === window.keptContent && el.scrollTop === window.keptScroll'))
        await expect(history).to_have_count(0)
        await expect(self.page.locator('.knowledge-documents .ant-spin-spinning')).to_have_count(0)
        self.assertEqual(self.counts['/api/knowledge/bases/base-1/documents'], 1)
        self.assertEqual(self.counts['/api/knowledge/bases/base-1/folders'], 1)
        self.assertEqual(self.counts['/api/knowledge/bases/base-1/jobs'], 1)
        self.job.update(status='completed', phase='completed', completed_documents=3,
                        chunk_count=7, cancel_allowed=False, version=2, finished_at=STAMP)
        self.base.update(active_job_id=None, index_status='ready')
        self.documents[0].update(chunk_count=7, status='ready')
        await expect(self.page.locator('.ant-table')).to_contain_text('可用', timeout=6000)
        await expect(self.page.locator('.ant-table')).not_to_contain_text('处理中')
        self.assertTrue(await search.evaluate('(el) => el === window.keptSearch && document.activeElement === el'))
        await expect(history).to_have_count(0)
        await expect(self.page.locator('[aria-label="当前索引任务"]')).to_have_count(0)
        self.assertTrue(await self.page.locator('.knowledge-documents__content').evaluate('(el) => el === window.keptContent && el.scrollTop === window.keptScroll'))
        self.assertEqual(self.counts['/api/knowledge/bases/base-1/documents'], 2)
        self.assertEqual(self.counts['/api/knowledge/bases/base-1/folders'], 2)
        await self.page.wait_for_timeout(1700)
        self.assertEqual(self.counts['/api/knowledge/bases/base-1/documents'], 2)

    async def test_preview_and_text_selection_survive_background_progress(self):
        await self.open_documents()
        await self.page.get_by_role('button', name='业务说明.txt', exact=True).click()
        text = self.page.get_by_text('业务工作时间是九点到十七点。', exact=True)
        await expect(text).to_be_visible()
        await text.evaluate('''(el) => {
            window.keptPreview = el;
            const selection = window.getSelection(), range = document.createRange();
            range.selectNodeContents(el); selection.removeAllRanges(); selection.addRange(range);
        }''')
        await self.wait_reads(2)
        self.assertTrue(await text.evaluate('(el) => el === window.keptPreview'))
        self.assertEqual(await self.page.evaluate('window.getSelection().toString()'), '业务工作时间是九点到十七点。')
        self.assertEqual(self.counts['/api/knowledge/bases/base-1/documents'], 1)

    async def test_transient_observation_failure_keeps_content_and_recovers_automatically(self):
        self.observation_errors = [503]
        await self.open_documents()
        search = self.page.get_by_role('textbox', name='搜索资料', exact=True)
        await search.fill('业务')
        await expect(self.page.get_by_text('任务状态暂时无法读取', exact=False)).to_be_visible(timeout=6000)
        await expect(self.page.get_by_role('button', name='业务说明.txt', exact=True)).to_be_visible()
        await self.wait_reads(2)
        await expect(self.page.get_by_text('任务状态暂时无法读取', exact=False)).to_have_count(0)
        await expect(search).to_have_value('业务')
        self.assertEqual(self.counts['/api/knowledge/bases/base-1/documents'], 1)

    async def test_permission_failure_clears_documents_and_preview(self):
        self.observation_errors = [403]
        await self.open_documents()
        await self.page.get_by_role('button', name='业务说明.txt', exact=True).click()
        await expect(self.page.get_by_text('业务工作时间是九点到十七点。', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('知识库不存在或无权访问', exact=True)).to_be_visible(timeout=6000)
        await expect(self.page.get_by_text('业务工作时间是九点到十七点。', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('textbox', name='搜索资料', exact=True)).to_have_count(0)

    async def test_leaving_stops_observation_without_cancelling_server_work(self):
        self.hold_observation = asyncio.Event()
        await self.open_documents()
        await self.wait_reads(1)
        await self.page.get_by_role('button', name='返回知识库', exact=True).click()
        await expect(self.page.get_by_role('heading', name='知识库', exact=True)).to_be_visible()
        self.hold_observation.set()
        await self.page.wait_for_timeout(1700)
        self.assertEqual(self.counts['/api/knowledge/jobs/job-active'], 1)
        self.assertEqual(self.counts['/api/knowledge/jobs/job-active/cancel'], 0)
        await expect(self.page.get_by_role('textbox', name='搜索资料', exact=True)).to_have_count(0)

    async def test_switching_base_rejects_an_old_inflight_observation(self):
        other_base = {**self.base, 'id': 'base-2', 'name': '第二知识库', 'active_job_id': None}
        self.bases.append(other_base)
        await self.page.route('**/api/knowledge/bases/base-2', lambda route: route.fulfill(json=other_base))
        await self.page.route('**/api/knowledge/bases/base-2/documents', lambda route: route.fulfill(json={'items': [], 'total': 0}))
        await self.page.route('**/api/knowledge/bases/base-2/folders', lambda route: route.fulfill(json=[]))
        await self.page.route('**/api/knowledge/bases/base-2/jobs', lambda route: route.fulfill(json=[]))
        self.hold_observation = asyncio.Event()
        await self.open_documents()
        await self.wait_reads(1)
        await self.page.get_by_role('button', name='返回知识库', exact=True).click()
        await self.page.get_by_role('button', name='打开知识库：第二知识库', exact=True).click()
        await expect(self.page.get_by_role('heading', name='第二知识库', exact=True)).to_be_visible()
        self.hold_observation.set()
        await self.page.wait_for_timeout(1700)
        await expect(self.page.get_by_role('heading', name='第二知识库', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='业务说明.txt', exact=True)).to_have_count(0)
        await expect(self.page.locator('[aria-label="当前索引任务"]')).to_have_count(0)
        self.assertEqual(self.counts['/api/knowledge/jobs/job-active/cancel'], 0)

    async def test_manual_refresh_preserves_an_empty_filtered_table(self):
        self.base.update(active_job_id=None)
        self.job.update(status='completed', cancel_allowed=False)
        await self.open_documents()
        search = self.page.get_by_role('textbox', name='搜索资料', exact=True)
        await search.fill('没有匹配的文档')
        await self.page.locator('.ant-table').evaluate('(el) => { window.keptEmptyTable = el }')
        started, release = asyncio.Event(), asyncio.Event()
        async def docs(route):
            started.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await route.fulfill(json={'items': self.documents, 'total': len(self.documents)})
        await self.page.route('**/api/knowledge/bases/base-1/documents', docs)
        try:
            await self.page.get_by_role('button', name='刷新资料', exact=True).click()
            await asyncio.wait_for(started.wait(), timeout=5)
            self.assertTrue(await self.page.locator('.ant-table').evaluate('(el) => el === window.keptEmptyTable'))
            await expect(search).to_have_value('没有匹配的文档')
            await expect(self.page.locator('.knowledge-documents .ant-spin-spinning')).to_have_count(0)
        finally:
            release.set()

    async def test_an_open_editor_keeps_its_draft_during_observation_and_completion(self):
        self.base.update(active_job_id=None)
        self.job.update(status='completed', cancel_allowed=False)
        self.documents[0].update(status='processing')
        await self.open_documents()
        started, release = asyncio.Event(), asyncio.Event()
        async def base(route):
            started.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await route.fulfill(json=self.base)
        await self.page.route('**/api/knowledge/bases/base-1', base)
        try:
            await self.page.get_by_role('button', name='刷新资料', exact=True).click()
            await asyncio.wait_for(started.wait(), timeout=5)
            await self.page.get_by_role('button', name='新建文件夹', exact=True).click()
            field = self.page.get_by_role('textbox', name='文件夹名称', exact=True)
            await field.fill('尚未保存的草稿')
            await field.focus()
            await field.evaluate('(el) => { window.keptEditor = el; el.setSelectionRange(2, 4) }')
            self.base.update(active_job_id='job-active')
            self.job.update(status='waiting_model', cancel_allowed=True)
            release.set()
            await self.wait_reads(2)
            await expect(field).to_have_value('尚未保存的草稿')
            self.assertTrue(await field.evaluate('(el) => el === window.keptEditor && document.activeElement === el && el.selectionStart === 2 && el.selectionEnd === 4'))
            self.job.update(status='completed', phase='completed', completed_documents=1,
                            chunk_count=4, cancel_allowed=False, version=2, finished_at=STAMP)
            self.base.update(active_job_id=None)
            self.documents[0].update(chunk_count=4, status='ready')
            await expect(self.page.locator('.ant-table')).to_contain_text('可用', timeout=6000)
            await expect(self.page.locator('.ant-table')).not_to_contain_text('处理中')
            await expect(field).to_have_value('尚未保存的草稿')
            self.assertTrue(await field.evaluate('(el) => el === window.keptEditor && document.activeElement === el'))
        finally:
            release.set()

    async def test_terminal_sync_failure_retries_without_restarting_the_job(self):
        self.documents[0].update(status='processing')
        await self.open_documents()
        search = self.page.get_by_role('textbox', name='搜索资料', exact=True)
        await search.fill('业务')
        await search.focus()
        await search.evaluate('(el) => { window.keptSyncSearch = el }')
        attempts = []
        async def docs(route):
            attempts.append(route.request.url)
            self.counts['/api/knowledge/bases/base-1/documents'] += 1
            if len(attempts) == 1:
                await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '列表同步暂时失败'}})
            else:
                await route.fulfill(json={'items': self.documents, 'total': len(self.documents)})
        await self.page.route('**/api/knowledge/bases/base-1/documents', docs)
        self.job.update(status='completed', completed_documents=1, cancel_allowed=False,
                        version=2, finished_at=STAMP)
        self.base.update(active_job_id=None)
        self.documents[0].update(chunk_count=8, status='ready')
        await expect(self.page.get_by_text('列表同步暂时失败', exact=False)).to_be_visible(timeout=6000)
        await expect(self.page.locator('.ant-table')).to_contain_text('处理中')
        self.assertTrue(await search.evaluate('(el) => el === window.keptSyncSearch && document.activeElement === el'))
        await expect(self.page.locator('.ant-table')).to_contain_text('可用', timeout=7000)
        await expect(self.page.locator('.ant-table')).not_to_contain_text('处理中')
        await expect(self.page.get_by_text('列表同步暂时失败', exact=False)).to_have_count(0)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self.counts['/api/knowledge/jobs/job-active'], 1)
        self.assertTrue(all(request['method'] == 'GET' for request in self.trace))

    async def test_terminal_overview_failure_retries_only_the_overview_and_restores_its_actions(self):
        self.documents[0].update(status='processing')
        await self.open_documents()
        attempts = []
        async def overview(route):
            attempts.append(route.request.url)
            self.counts['/api/knowledge/bases'] += 1
            if len(attempts) == 1:
                await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '知识库摘要暂时无法同步'}})
            else:
                await route.fulfill(json=self.bases)
        await self.page.route('**/api/knowledge/bases', overview)
        self.job.update(status='completed', completed_documents=1, cancel_allowed=False,
                        version=2, finished_at=STAMP)
        self.base.update(active_job_id=None, index_status='ready')
        self.documents[0].update(chunk_count=9, status='ready')
        await expect(self.page.locator('.ant-table')).to_contain_text('可用', timeout=6000)
        await expect(self.page.locator('.ant-table')).not_to_contain_text('处理中')
        await expect(self.page.get_by_text('知识库摘要暂时无法同步', exact=False)).to_be_visible()
        await expect(self.page.get_by_text('知识库摘要暂时无法同步', exact=False)).to_have_count(0, timeout=6000)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self.counts['/api/knowledge/bases/base-1/documents'], 2)
        self.assertEqual(self.counts['/api/knowledge/bases/base-1/folders'], 2)
        self.assertEqual(self.counts['/api/knowledge/jobs/job-active'], 1)
        await self.page.get_by_role('button', name='返回知识库', exact=True).click()
        await expect(self.page.get_by_role('button', name='打开知识库：业务知识', exact=True)).to_be_enabled()
        await self.page.get_by_role('button', name='更多操作：业务知识', exact=True).click()
        await expect(self.page.get_by_role('menuitem', name='删除知识库', exact=True)).to_be_enabled()

    async def test_cancelled_job_is_not_replaced_by_a_late_running_snapshot(self):
        stale = copy.deepcopy(self.job)
        started, release = asyncio.Event(), asyncio.Event()
        async def observe(route):
            self.counts['/api/knowledge/jobs/job-active'] += 1
            started.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await route.fulfill(json=stale)
        async def cancel(route):
            self.counts['/api/knowledge/jobs/job-active/cancel'] += 1
            self.job.update(status='cancelled', phase='cancelled', cancel_allowed=False,
                            version=2, finished_at=STAMP)
            self.base.update(active_job_id=None)
            await route.fulfill(json=self.job)
        await self.page.route('**/api/knowledge/jobs/job-active', observe)
        await self.page.route('**/api/knowledge/jobs/job-active/cancel', cancel)
        try:
            await self.open_documents()
            await asyncio.wait_for(started.wait(), timeout=5)
            await self.page.get_by_role('button', name='取消任务', exact=True).click()
            await expect(self.page.locator('[aria-label="当前索引任务"]')).to_have_count(0)
            release.set()
            await self.page.wait_for_timeout(1800)
            await expect(self.page.locator('[aria-label="当前索引任务"]')).to_have_count(0)
            await expect(self.page.get_by_role('button', name='取消任务', exact=True)).to_have_count(0)
            await expect(self.page.get_by_role('button', name='添加资料', exact=True)).to_be_enabled()
            self.assertEqual(self.counts['/api/knowledge/jobs/job-active/cancel'], 1)
            self.assertEqual(self.counts['/api/knowledge/jobs/job-active'], 1)
        finally:
            release.set()

    async def test_failed_job_syncs_its_error_and_keeps_the_old_preview(self):
        await self.open_documents()
        await self.page.get_by_role('button', name='业务说明.txt', exact=True).click()
        text = self.page.get_by_text('业务工作时间是九点到十七点。', exact=True)
        await expect(text).to_be_visible()
        await text.evaluate('(el) => { window.keptFailedPreview = el }')
        failure = {'code': 'model_failed', 'message': '模型暂时不可用'}
        self.job.update(status='failed', phase='failed', error=failure, cancel_allowed=False,
                        version=2, finished_at=STAMP)
        self.base.update(active_job_id=None, index_status='failed', last_error=failure)
        self.documents[0].update(status='failed', error=failure)
        await expect(self.page.locator('.ant-table')).to_contain_text('失败（旧索引可用）', timeout=6000)
        self.assertTrue(await text.evaluate('(el) => el === window.keptFailedPreview'))
        await expect(text).to_be_visible()
        self.assertEqual(self.counts['/api/knowledge/bases/base-1/documents'], 2)
        await expect(self.page.locator('details[aria-label="索引任务历史"]')).to_have_count(0)

    async def test_binary_upload_keeps_its_file_until_server_acceptance_then_allows_leaving(self):
        self.base.update(active_job_id=None)
        self.job.update(status='completed', cancel_allowed=False)
        started, release = asyncio.Event(), asyncio.Event()
        uploads, admissions = [], []
        async def upload(route):
            uploads.append(route.request.url)
            started.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await route.fulfill(status=201, json={
                    'file': {**FILE, 'id': 'uploaded-file'},
                    'upload_id': 'uploaded-acceptance', 'folder_id': None})
        async def admit(route):
            if route.request.method == 'GET':
                await route.fulfill(json={'items': self.documents, 'total': len(self.documents)})
                return
            admissions.append(route.request.post_data_json)
            self.job.update(status='waiting_model', operation='import', cancel_allowed=True)
            self.base.update(active_job_id='job-active')
            await route.fulfill(status=202, json=self.job)
        await self.page.route('**/api/knowledge/bases/base-1/uploads?*', upload)
        await self.page.route('**/api/knowledge/bases/base-1/documents', admit)
        try:
            await self.page.goto(self.origin + '/knowledge')
            await self.page.get_by_role('button', name='打开知识库：业务知识', exact=True).click()
            await self.page.get_by_role('button', name='添加资料', exact=True).click()
            modal = self.page.get_by_role('dialog')
            await modal.locator('input[type=file]').set_input_files({'name': 'still-uploading.txt', 'mimeType': 'text/plain', 'buffer': b'Keep this upload identity'})
            await modal.get_by_role('button', name='添加并自动处理', exact=True).click()
            await asyncio.wait_for(started.wait(), timeout=5)
            await self.page.go_back()
            await expect(self.page).to_have_url(self.origin + '/knowledge/base-1/documents')
            await expect(modal.get_by_text('原文仍在上传，请等待上传完成后再离开。', exact=True)).to_be_visible()
            self.assertEqual(len(uploads), 1)
            self.assertEqual(admissions, [])
            release.set()
            await expect(modal).not_to_be_visible()
            await self.page.get_by_role('button', name='返回知识库', exact=True).click()
            await expect(self.page.get_by_role('heading', name='知识库', exact=True)).to_be_visible()
            self.assertEqual(len(uploads), 1)
            self.assertEqual(len(admissions), 1)
            self.assertEqual(admissions[0]['file_ids'], ['uploaded-file'])
            self.assertEqual(self.counts['/api/knowledge/jobs/job-active/cancel'], 0)
        finally:
            release.set()


def load_tests(loader, _tests, _pattern):
    return unittest.TestSuite(KnowledgeBackgroundRegressions(name)
                              for name in loader.getTestCaseNames(KnowledgeBackgroundRegressions)
                              if name in KnowledgeBackgroundRegressions.__dict__)


if __name__ == '__main__':
    unittest.main(verbosity=2)
