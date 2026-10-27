"""Known staged uploads stay recoverable until import or explicit cleanup is confirmed."""

import re
import unittest
from urllib.parse import parse_qs, urlparse

from playwright.async_api import expect
from files_regressions import FILE
from knowledge_regressions import KnowledgeUIRegressions, STAMP


class KnowledgeUploadLifecycle(KnowledgeUIRegressions):
    async def asyncSetUp(self):
        self.uploaded = False
        self.retained = False
        self.partial = False
        self.unknown = False
        self.confirm_import = False
        self.cleanup_failures = 0
        self.cleanup_unauthorized = False
        self.upload_calls = []
        self.import_calls = []
        self.discard_calls = []
        await super().asyncSetUp()

    def source(self):
        return {
            **FILE,
            "id": "staged-file",
            "display_name": "待清理原文",
            "original_filename": "a.txt",
            "reference_count": int(self.retained),
        }

    async def respond(self, route):
        parsed = urlparse(route.request.url)
        path, method = parsed.path, route.request.method
        if path == "/api/knowledge/bases/base-1/uploads" and method == "POST":
            self.upload_calls.append(route.request.url)
            if self.partial and parse_qs(parsed.query)["filename"][0] == "b.txt":
                await route.fulfill(
                    status=422,
                    json={
                        "error": {"code": "invalid_source", "message": "第二份原文无效"}
                    },
                )
            else:
                self.uploaded = self.retained = True
                await route.fulfill(
                    status=201,
                    json={
                        "file": self.source(),
                        "upload_id": "staged-acceptance",
                        "folder_id": None,
                    },
                )
        elif path == "/api/knowledge/bases/base-1/uploads/staged-acceptance":
            self.discard_calls.append(route.request.url)
            self.assertEqual(method, "DELETE")
            if route.request.post_data != "{}":
                await route.fulfill(
                    status=422,
                    json={"error": {"code": "invalid_request", "message": "需要空 JSON 请求体"}},
                )
                return
            if self.cleanup_unauthorized:
                await route.fulfill(
                    status=401,
                    json={"error": {"code": "unauthorized", "message": "登录已过期"}},
                )
            elif self.cleanup_failures:
                self.cleanup_failures -= 1
                await route.fulfill(
                    status=503,
                    json={
                        "error": {"code": "unavailable", "message": "清理响应未确认"}
                    },
                )
            else:
                self.retained = False
                await route.fulfill(status=204)
        elif path == "/api/knowledge/bases/base-1/documents" and method == "POST":
            self.import_calls.append(route.request.post_data_json)
            if self.confirm_import:
                self.retained = False
                job = {
                    "id": "job-1",
                    "base_id": "base-1",
                    "operation": "import",
                    "status": "completed",
                    "phase": "completed",
                    "version": 3,
                    "attempt_id": "attempt-1",
                    "index_revision": "revision-2",
                    "total_documents": 1,
                    "completed_documents": 1,
                    "failed_documents": 0,
                    "chunk_count": 1,
                    "document_results": [],
                    "error": None,
                    "created_at": STAMP,
                    "updated_at": STAMP,
                    "finished_at": STAMP,
                    "cancel_allowed": False,
                }
                await route.fulfill(status=202, json=job)
            else:
                await route.fulfill(
                    status=503 if self.unknown else 422,
                    json={
                        "error": {
                            "code": "unavailable" if self.unknown else "invalid_source",
                            "message": "接纳结果未确认"
                            if self.unknown
                            else "导入已明确拒绝",
                        }
                    },
                )
        elif path == "/api/files":
            await route.fulfill(
                json={
                    "items": [self.source()] if self.uploaded else [],
                    "total": int(self.uploaded),
                    "page": 1,
                    "page_size": 20,
                }
            )
        elif path == "/api/files/stats":
            await route.fulfill(
                json={
                    "total_count": int(self.uploaded),
                    "total_bytes": 12,
                    "active_count": int(self.uploaded),
                    "deleted_count": 0,
                    "public_count": 0,
                    "public_bytes": 0,
                    "private_bytes": 12,
                    "expired_count": 0,
                    "retention_expiring_count": 0,
                    "acl_expiring_count": 0,
                }
            )
        elif path == "/api/files/users":
            await route.fulfill(json=[])
        elif path == "/api/files/staged-file/references":
            await route.fulfill(
                json=[
                    {
                        "id": "stage-ref",
                        "resource_type": "knowledge_upload",
                        "resource_id": "staged-acceptance",
                        "retain_until": None,
                        "created_at": STAMP,
                        "version": 1,
                    }
                ]
                if self.retained
                else []
            )
        else:
            await super().respond(route)

    async def fail_batch(self, partial=False):
        self.partial = partial
        await self.page.goto(self.origin + "/knowledge")
        await self.page.get_by_role("button", name="管理文档", exact=True).click()
        await self.page.get_by_role("button", name="上传文档", exact=True).click()
        modal = self.page.get_by_role("dialog")
        files = [
            {
                "name": "a.txt",
                "mimeType": "text/plain",
                "buffer": b"Already stored source",
            }
        ]
        if partial:
            files.append(
                {
                    "name": "b.txt",
                    "mimeType": "text/plain",
                    "buffer": b"Invalid second source",
                }
            )
        await modal.locator("input[type=file]").set_input_files(files)
        await modal.get_by_role("button", name="上传并建立索引", exact=True).click()
        await expect(
            modal.get_by_text(
                "第二份原文无效"
                if partial
                else "接纳结果未确认"
                if self.unknown
                else "导入已明确拒绝",
                exact=True,
            ).first
        ).to_be_visible()
        return modal

    async def verify_released_file(self):
        self.assertFalse(self.retained)
        self.assertEqual(
            self.discard_calls,
            [self.origin + "/api/knowledge/bases/base-1/uploads/staged-acceptance"],
        )
        await self.page.goto(self.origin + "/files")
        row = self.page.locator("tr.ant-table-row").filter(has_text="待清理原文")
        await expect(row).to_be_visible()
        await expect(row.locator("button:has(.anticon-delete)")).to_be_enabled()

    async def test_partial_upload_close_releases_known_stage(self):
        modal = await self.fail_batch(partial=True)
        await modal.get_by_role("button", name="Close", exact=True).click()
        await expect(modal).not_to_be_visible()
        await self.verify_released_file()
        self.assertEqual(self.import_calls, [])

    async def test_rejected_import_close_releases_stage_without_deleting_owned_file(
        self,
    ):
        modal = await self.fail_batch()
        await modal.get_by_role("button", name="Close", exact=True).click()
        await expect(modal).not_to_be_visible()
        await self.verify_released_file()
        self.assertEqual(len(self.import_calls), 1)

    async def test_close_then_normal_navigation_does_not_forget_staged_references(self):
        modal = await self.fail_batch(partial=True)
        await modal.get_by_role("button", name="Close", exact=True).click()
        await expect(modal).not_to_be_visible()
        await self.page.get_by_role("button", name="返回知识库", exact=True).click()
        await self.page.get_by_role("button", name="管理文档", exact=True).click()
        await self.page.get_by_role("button", name="上传文档", exact=True).click()
        await expect(modal.locator(".upload-result")).to_have_count(0)
        await modal.get_by_role("button", name="Close", exact=True).click()
        await self.verify_released_file()

    async def test_direct_browser_navigation_preserves_a_resumable_stage(self):
        await self.fail_batch(partial=True)
        await self.page.go_back()
        await expect(self.page).to_have_url(self.origin + "/knowledge")
        await self.page.get_by_role("button", name="管理文档", exact=True).click()
        await self.page.get_by_role("button", name="上传文档", exact=True).click()
        modal = self.page.get_by_role("dialog")
        await expect(modal.locator(".upload-result")).to_have_count(1)
        self.assertEqual(self.discard_calls, [])
        await modal.get_by_role("button", name="Close", exact=True).click()
        await self.verify_released_file()

    async def test_failed_cleanup_retains_ids_and_can_retry_without_import(self):
        self.cleanup_failures = 1
        modal = await self.fail_batch()
        await modal.get_by_role("button", name="Close", exact=True).click()
        await expect(modal).to_be_visible()
        await expect(modal.get_by_text(re.compile("清理响应未确认"))).to_be_visible()
        await expect(modal.locator(".upload-result")).to_have_count(1)
        await expect(
            modal.get_by_role("button", name="上传并建立索引", exact=True)
        ).to_be_disabled()
        await modal.get_by_role("button", name="Close", exact=True).click()
        await expect(modal).not_to_be_visible()
        self.assertFalse(self.retained)
        self.assertEqual(len(self.discard_calls), 2)
        self.assertEqual(len(self.import_calls), 1)
        self.assertEqual(len(self.upload_calls), 1)

    async def test_unknown_import_navigation_keeps_original_confirmation_and_never_discards(
        self,
    ):
        self.unknown = True
        modal = await self.fail_batch()
        await expect(
            modal.get_by_role("button", name=re.compile(r"^(?:Cancel|取\s*消)$"))
        ).to_be_disabled()
        await self.page.go_back()
        await expect(self.page).to_have_url(self.origin + "/knowledge")
        await self.page.get_by_role("button", name="管理文档", exact=True).click()
        await self.page.get_by_role("button", name="上传文档", exact=True).click()
        await expect(modal.locator(".upload-result")).to_have_count(1)
        self.assertEqual(self.discard_calls, [])
        self.confirm_import = True
        await modal.get_by_role("button", name="确认原提交", exact=True).click()
        await expect(modal).not_to_be_visible()
        self.assertEqual(len(self.upload_calls), 1)
        self.assertEqual(self.import_calls[0], self.import_calls[1])
        self.assertEqual(self.discard_calls, [])
        await self.page.get_by_role("button", name="返回知识库", exact=True).click()
        await expect(self.page).to_have_url(self.origin + "/knowledge")

    async def test_reload_restores_known_file_ids_without_reupload(self):
        await self.fail_batch(partial=True)
        await self.page.reload()
        await self.page.get_by_role("button", name="上传文档", exact=True).click()
        modal = self.page.get_by_role("dialog")
        await expect(modal.locator(".upload-result")).to_have_count(1)
        self.assertEqual(len(self.upload_calls), 2)
        self.assertNotIn(
            "Already stored source",
            await self.page.evaluate("JSON.stringify({...sessionStorage})"),
        )
        await modal.get_by_role("button", name="Close", exact=True).click()
        await self.verify_released_file()

    async def test_recovery_is_scoped_to_the_authenticated_user(self):
        await self.fail_batch(partial=True)

        async def other_user(route):
            await route.fulfill(
                json={"id": "other-user", "username": "other-admin", "is_admin": True}
            )

        await self.page.route("**/api/auth/me", other_user)
        await self.page.reload()
        await self.page.get_by_role("button", name="上传文档", exact=True).click()
        modal = self.page.get_by_role("dialog")
        await expect(modal.locator(".upload-result")).to_have_count(0)
        await modal.get_by_role("button", name="Close", exact=True).click()
        self.assertEqual(self.discard_calls, [])
        self.assertTrue(self.retained)
        await self.page.unroute("**/api/auth/me", other_user)
        await self.page.reload()
        await self.page.get_by_role("button", name="上传文档", exact=True).click()
        await expect(modal.locator(".upload-result")).to_have_count(1)
        await modal.get_by_role("button", name="Close", exact=True).click()
        await self.verify_released_file()

    async def test_unavailable_draft_storage_keeps_in_memory_ids_until_cleanup(self):
        await self.page.add_init_script("""(() => {
          const original = Storage.prototype.setItem;
          Storage.prototype.setItem = function(key, value) {
            if (key.startsWith('zhiheng:knowledge-upload:')) throw new DOMException('Storage unavailable', 'QuotaExceededError');
            return original.call(this, key, value);
          };
        })()""")
        modal = await self.fail_batch(partial=True)
        await self.page.go_back()
        await expect(self.page).to_have_url(self.origin + "/knowledge/base-1/documents")
        await expect(modal).to_be_visible()
        await expect(modal.locator(".upload-result")).to_have_count(2)
        await modal.get_by_role("button", name="Close", exact=True).click()
        await expect(modal).not_to_be_visible()
        await self.verify_released_file()

    async def test_expired_login_can_recover_cleanup_when_draft_storage_is_unavailable(
        self,
    ):
        await self.page.add_init_script("""(() => {
          const original = Storage.prototype.setItem;
          Storage.prototype.setItem = function(key, value) {
            if (key.startsWith('zhiheng:knowledge-upload:')) throw new DOMException('Storage unavailable', 'QuotaExceededError');
            return original.call(this, key, value);
          };
        })()""")
        modal = await self.fail_batch(partial=True)
        self.cleanup_unauthorized = True
        await self.page.route(
            "**/api/auth/login",
            lambda route: route.fulfill(
                json={"user": {"id": "user-1", "username": "admin", "is_admin": True}}
            ),
        )
        await modal.get_by_role("button", name="Close", exact=True).click()
        await expect(self.page).to_have_url(re.compile(r"/login(?:\?|$)"))
        self.cleanup_unauthorized = False
        await self.page.get_by_placeholder("账号", exact=True).fill("admin")
        await self.page.get_by_placeholder("密码", exact=True).fill(
            "test-recovery-password"
        )
        await self.page.get_by_role("button", name="登录", exact=True).click()
        await expect(self.page).to_have_url(self.origin + "/chat")
        await self.page.go_back()
        await expect(self.page).to_have_url(self.origin + "/knowledge")
        await self.page.get_by_role("button", name="管理文档", exact=True).click()
        await self.page.get_by_role("button", name="上传文档", exact=True).click()
        await expect(modal.locator(".upload-result")).to_have_count(1)
        await modal.get_by_role("button", name="Close", exact=True).click()
        await expect(modal).not_to_be_visible()
        self.assertFalse(self.retained)
        self.assertEqual(len(self.discard_calls), 2)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(
        KnowledgeUploadLifecycle(name)
        for name in KnowledgeUploadLifecycle.__dict__
        if name.startswith("test_")
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
