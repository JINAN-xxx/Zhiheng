"""Library upload recovery after permission loss; replaces the retired managed-file picker."""
import unittest
import json
import re
from urllib.parse import urlparse

from playwright.async_api import expect
from knowledge_upload_regressions import KnowledgeUploadLifecycle


class LibraryUploadAccessRecovery(KnowledgeUploadLifecycle):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.unknown = True
        self.deny_next = False
        self.restored = False
        self.denied_status = 403

    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path == '/api/knowledge/bases/base-1/documents' and route.request.method == 'POST' and self.deny_next and not self.restored:
            self.import_calls.append(route.request.post_data_json)
            return await route.fulfill(status=self.denied_status, json={'error': {
                'code': 'knowledge_forbidden' if self.denied_status == 403 else 'knowledge_not_found',
                'message': '知识库权限已撤销'}})
        if path == '/api/knowledge/bases/base-1' and self.deny_next and not self.restored:
            return await route.fulfill(status=self.denied_status, json={'error': {
                'code': 'knowledge_forbidden', 'message': '知识库权限已撤销'}})
        await super().respond(route)

    async def verify_permission_recovery(self, status):
        self.denied_status = status
        await self.page.goto(self.origin + '/knowledge/base-1/documents')
        await self.page.get_by_role('button', name='添加资料', exact=True).click()
        modal = self.page.get_by_role('dialog', name='添加资料', exact=True)
        await modal.locator('input[type=file]').set_input_files({
            'name': 'a.txt', 'mimeType': 'text/plain', 'buffer': b'protected upload'})
        await modal.get_by_role('button', name='添加并自动处理', exact=True).click()
        await expect(modal.get_by_text('接纳结果未确认', exact=True)).to_be_visible()
        original = dict(self.import_calls[0])
        self.deny_next = True
        await modal.get_by_role('button', name='确认原提交', exact=True).click()
        await expect(modal).not_to_be_visible()
        await expect(self.page.get_by_text('知识库权限已撤销', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button', name='业务说明.txt', exact=True)).to_have_count(0)
        self.assertEqual(self.import_calls, [original, original])
        self.assertEqual(len(self.upload_calls), 1)
        self.restored = self.confirm_import = True
        await self.page.get_by_role('button', name='重试', exact=True).click()
        await self.page.get_by_role('button', name='添加资料', exact=True).click()
        await expect(modal.get_by_role('button', name='确认原提交', exact=True)).to_be_visible()
        await modal.get_by_role('button', name='确认原提交', exact=True).click()
        await expect(modal).not_to_be_visible()
        self.assertEqual(self.import_calls, [original, original, original])
        self.assertEqual(len(self.upload_calls), 1)

    async def test_401_hides_protected_upload_and_retains_owner_recovery_metadata(self):
        self.denied_status = 401
        await self.page.goto(self.origin + '/knowledge/base-1/documents')
        await self.page.get_by_role('button', name='添加资料', exact=True).click()
        modal = self.page.get_by_role('dialog', name='添加资料', exact=True)
        await modal.locator('input[type=file]').set_input_files({
            'name': 'a.txt', 'mimeType': 'text/plain', 'buffer': b'private upload'})
        await modal.get_by_role('button', name='添加并自动处理', exact=True).click()
        await expect(modal.get_by_text('接纳结果未确认', exact=True)).to_be_visible()
        original = dict(self.import_calls[0])
        self.deny_next = True
        await modal.get_by_role('button', name='确认原提交', exact=True).click()
        await expect(self.page).to_have_url(re.compile('/login(?:[?].*)?$'))
        await expect(modal).not_to_be_visible()
        await expect(self.page.get_by_role('button', name='业务说明.txt', exact=True)).to_have_count(0)
        saved = await self.page.evaluate(
            "sessionStorage.getItem('zhiheng:knowledge-upload:user-1:base-1:root')")
        self.assertEqual(json.loads(saved)['requestId'], original['client_request_id'])
        self.assertEqual(self.import_calls, [original, original])
        self.assertEqual(len(self.upload_calls), 1)

    async def test_403_closes_uncertain_upload_and_restores_original_request(self):
        await self.verify_permission_recovery(403)

    async def test_404_closes_uncertain_upload_and_restores_original_request(self):
        await self.verify_permission_recovery(404)


def load_tests(loader, _tests, _pattern):
    return unittest.TestSuite(LibraryUploadAccessRecovery(name) for name in LibraryUploadAccessRecovery.__dict__ if name.startswith('test_'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
