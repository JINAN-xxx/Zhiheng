"""Document workspace keeps maintenance and finished indexing out of the way."""

import unittest

from playwright.async_api import expect

from knowledge_background_regressions import KnowledgeBackgroundRegressions


class KnowledgeFileUIRegressions(KnowledgeBackgroundRegressions):
    async def test_completed_tasks_have_no_persistent_summary_or_history(self):
        self.job.update(status='completed', cancel_allowed=False, completed_documents=1)
        self.base.update(active_job_id=None)
        await self.open_documents()
        await expect(self.page.locator('[aria-label="当前索引任务"]')).to_have_count(0)
        await expect(self.page.get_by_text('索引任务历史', exact=False)).to_have_count(0)
        await expect(self.page.locator('.ant-message-success')).to_have_count(0)
        await self.page.get_by_role('button', name='刷新资料', exact=True).click()
        await expect(self.page.locator('[aria-label="当前索引任务"]')).to_have_count(0)
        await expect(self.page.locator('.ant-message-success')).to_have_count(0)

    async def test_running_task_collapses_on_success_and_notifies_only_once(self):
        await self.open_documents()
        task = self.page.locator('[aria-label="当前索引任务"]')
        await expect(task).to_contain_text('0/1')
        await expect(self.page.get_by_role('button', name='取消任务', exact=True)).to_be_enabled()
        await expect(self.page.get_by_text('索引任务历史', exact=False)).to_have_count(0)
        self.job.update(status='completed', cancel_allowed=False, completed_documents=1,
                        version=2)
        self.base.update(active_job_id=None, index_status='ready')
        await expect(task).to_have_count(0, timeout=6000)
        await expect(self.page.locator('.ant-message-success')).to_have_text('文档处理完成')
        await expect(self.page.locator('.ant-message-success')).to_have_count(0, timeout=6000)
        await self.page.evaluate('''() => {
            window.repeatedKnowledgeSuccess = false;
            new MutationObserver(() => {
                if (document.querySelector('.ant-message-success'))
                    window.repeatedKnowledgeSuccess = true;
            }).observe(document.body, {childList: true, subtree: true});
        }''')
        await self.page.get_by_role('button', name='刷新资料', exact=True).click()
        await expect(self.page.get_by_role('button', name='添加资料', exact=True)).to_be_enabled()
        await self.page.wait_for_timeout(1700)
        self.assertFalse(await self.page.evaluate('window.repeatedKnowledgeSuccess'))
        self.assertEqual(self.counts['/api/knowledge/jobs/job-active'], 1)

    async def test_maintenance_moves_to_more_and_rebuild_is_blocked_while_busy(self):
        await self.open_documents()
        await expect(self.page.get_by_role('button', name='重新处理全部资料', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='更多操作', exact=True).click()
        await expect(self.page.get_by_role('menuitem', name='重新处理全部资料', exact=True)).to_have_attribute('aria-disabled', 'true')
        await expect(self.page.get_by_role('menuitem', name='知识库设置', exact=True)).to_be_visible()

    async def test_idle_maintenance_menu_opens_existing_rebuild_confirmation(self):
        self.job.update(status='completed', cancel_allowed=False)
        self.base.update(active_job_id=None)
        await self.open_documents()
        await self.page.get_by_role('button', name='更多操作', exact=True).click()
        await self.page.get_by_role('menuitem', name='重新处理全部资料', exact=True).click()
        await expect(self.page.get_by_role('dialog')).to_be_visible()
        await expect(self.page.get_by_role('dialog')).to_contain_text('重新处理')
        self.assertFalse(any(row['method'] == 'POST' for row in self.trace))

    async def test_shared_reader_has_no_maintenance_menu_or_job_details(self):
        self.base.update(can_manage=False, owner_id='other-user')
        await self.open_documents()
        await expect(self.page.get_by_role('button', name='更多操作', exact=True)).to_have_count(0)
        await expect(self.page.locator('[aria-label="当前索引任务"]')).to_have_count(0)
        self.assertEqual(self.counts['/api/knowledge/bases/base-1/jobs'], 0)

    async def test_current_failure_keeps_error_details_without_history(self):
        self.job.update(status='partial', failed_documents=1, cancel_allowed=False,
                        error={'code': 'model_failed', 'message': '部分文档未完成'},
                        document_results=[{'document_id': 'doc-1', 'file_id': 'file-1', 'status': 'failed',
                            'chunk_count': None,
                            'error': {'code': 'model_failed', 'message': '模型暂时不可用'}}])
        self.base.update(active_job_id=None)
        await self.open_documents()
        task = self.page.locator('[aria-label="当前索引任务"]')
        await expect(task).to_contain_text('部分文档未完成')
        await task.get_by_text('查看失败文档', exact=False).click()
        await expect(task).to_contain_text('业务说明.txt')
        await expect(task).to_contain_text('模型暂时不可用')
        await expect(self.page.get_by_text('索引任务历史', exact=False)).to_have_count(0)
        await expect(task.get_by_text('任务标识', exact=True)).to_have_count(0)

    async def test_previous_failure_is_not_shown_after_a_newer_success(self):
        self.job.update(status='completed', cancel_allowed=False)
        self.history.update(status='failed', error={'code': 'old', 'message': '过去的错误'})
        self.base.update(active_job_id=None)
        await self.open_documents()
        await expect(self.page.locator('[aria-label="当前索引任务"]')).to_have_count(0)
        await expect(self.page.get_by_text('过去的错误', exact=True)).to_have_count(0)

    async def test_manual_refresh_reports_completion_of_an_observed_task(self):
        await self.open_documents()
        self.job.update(status='completed', completed_documents=1, cancel_allowed=False,
                        version=2)
        self.base.update(active_job_id=None, index_status='ready')
        await self.page.get_by_role('button', name='刷新资料', exact=True).click()
        await expect(self.page.locator('.ant-message-success')).to_have_text('文档处理完成')
        await expect(self.page.locator('[aria-label="当前索引任务"]')).to_have_count(0)

    async def test_cancel_racing_with_completion_reports_the_actual_result(self):
        async def cancel(route):
            self.job.update(status='completed', completed_documents=1, cancel_allowed=False,
                            version=2)
            self.base.update(active_job_id=None, index_status='ready')
            await route.fulfill(json=self.job)
        await self.page.route('**/api/knowledge/jobs/job-active/cancel', cancel)
        await self.open_documents()
        await self.page.get_by_role('button', name='取消任务', exact=True).click()
        await expect(self.page.locator('.ant-message-success')).to_have_text('文档处理完成')
        await expect(self.page.locator('[aria-label="当前索引任务"]')).to_have_count(0)

    async def test_interruption_uses_current_document_errors_when_results_are_not_updated(self):
        failure = {'code': 'interrupted', 'message': '后台处理已中断'}
        self.job.update(status='interrupted', error=failure, cancel_allowed=False,
                        document_results=[{'document_id': 'doc-1', 'file_id': 'file-1',
                            'status': 'parsed', 'chunk_count': None, 'error': None}])
        self.base.update(active_job_id=None)
        self.documents[0].update(status='failed', job_id='job-active', error=failure)
        await self.open_documents()
        task = self.page.locator('[aria-label="当前索引任务"]')
        await task.get_by_text('查看失败文档', exact=False).click()
        await expect(task.locator('details')).to_contain_text('业务说明.txt · 后台处理已中断')

    async def test_failure_details_do_not_reuse_errors_from_another_task(self):
        self.job.update(status='interrupted', cancel_allowed=False,
                        error={'code': 'interrupted', 'message': '本次任务已中断'},
                        document_results=[{'document_id': 'doc-1', 'file_id': 'file-1',
                            'status': 'parsed', 'chunk_count': None, 'error': None}])
        self.base.update(active_job_id=None)
        self.documents[0].update(status='failed', job_id='job-other',
                                error={'code': 'previous', 'message': '其他任务的错误'})
        await self.open_documents()
        task = self.page.locator('[aria-label="当前索引任务"]')
        await expect(task).to_contain_text('本次任务已中断')
        await expect(task.get_by_text('查看失败文档', exact=False)).to_have_count(0)
        await expect(task.get_by_text('其他任务的错误', exact=False)).to_have_count(0)


def load_tests(loader, _tests, _pattern):
    return unittest.TestSuite(KnowledgeFileUIRegressions(name)
                              for name in loader.getTestCaseNames(KnowledgeFileUIRegressions)
                              if name in KnowledgeFileUIRegressions.__dict__)


if __name__ == '__main__':
    unittest.main(verbosity=2)
