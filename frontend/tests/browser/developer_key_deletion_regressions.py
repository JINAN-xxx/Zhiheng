"""Key removal uses isolated Console HTTP fixtures and preserves scoped UI state."""

import asyncio
import contextlib
import re
import unittest
from urllib.parse import urlparse

from playwright.async_api import expect
from developer_regressions import DeveloperBoundary


class DeveloperKeyDeletionRegressions(DeveloperBoundary):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.deletes = []
        self.keys = [self.key(status) for status in ('active', 'retiring', 'revoked', 'expired')]

    def key(self, status, application_id='app-1'):
        return {
            'id': 'key-' + status, 'application_id': application_id,
            'name': '客户端-' + status, 'prefix': 'sk-zh_fixture-' + status,
            'status': status, 'model_ids': [], 'scopes': ['files:read'],
            'resource_rpm_limit': 120, 'rpm_limit': None, 'tpm_limit': None,
            'concurrency_limit': None, 'monthly_points_limit': None,
            'expires_at': None, 'last_used_at': None,
        }

    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path.startswith('/api/developer/applications/app-1/keys/') and route.request.method == 'DELETE':
            self.deletes.append((path, route.request.post_data))
            key_id = path.rsplit('/', 1)[-1]
            self.keys = [key for key in self.keys if key['id'] != key_id]
            await route.fulfill(status=204)
        else:
            await super().respond(route)

    async def open_keys(self):
        await self.page.goto(self.origin + '/developer?applicationId=app-1&tab=keys')
        await expect(self.page.locator('.key-row')).to_have_count(len(self.keys), timeout=10000)

    def row(self, status):
        return self.page.locator('.key-row').filter(has_text='客户端-' + status)

    async def delete_menu(self, status):
        more = self.row(status).get_by_role('button', name='更多操作', exact=True)
        await expect(more).to_be_visible()
        await more.click()
        delete = self.page.get_by_role('menuitem', name='删除密钥', exact=True)
        await expect(delete).to_be_visible()
        await delete.click()
        return self.page.get_by_role('dialog').filter(has_text='删除后该密钥将立即失效')

    async def confirm_delete(self, dialog):
        await dialog.get_by_role('button', name=re.compile(r'^确\s*定$')).click()

    async def test_every_status_exposes_delete_and_preserves_usable_actions(self):
        await self.open_keys()
        for status in ('active', 'retiring', 'revoked', 'expired'):
            with self.subTest(status=status):
                await self.row(status).get_by_role('button', name='更多操作', exact=True).click()
                menu = self.page.get_by_role('menu')
                await expect(menu.get_by_role('menuitem', name='删除密钥', exact=True)).to_be_visible()
                await expect(menu.get_by_role('menuitem', name='轮换密钥', exact=True)).to_have_count(1 if status == 'active' else 0)
                await expect(menu.get_by_role('menuitem', name='撤销密钥', exact=True)).to_have_count(1 if status in ('active', 'retiring') else 0)
                await menu.get_by_role('menuitem', name='删除密钥', exact=True).click()
                dialog = self.page.get_by_role('dialog').filter(has_text='删除后该密钥将立即失效')
                await expect(dialog).to_contain_text('历史调用与账单记录仍会保留')
                self.assertEqual(await dialog.get_by_role('textbox').count(), 0)
                reads = len(self.key_pages)
                await self.confirm_delete(dialog)
                await expect(self.row(status)).to_have_count(0)
                self.assertGreater(len(self.key_pages), reads)
                self.assertIn(('keys/key-' + status), self.deletes[-1][0])
                self.assertIsNone(self.deletes[-1][1])
                self.assertIn('applicationId=app-1', self.page.url)
        await expect(self.page.get_by_text('暂无访问密钥', exact=True)).to_be_visible()
        await self.page.get_by_role('tab', name='调用记录', exact=True).click()
        await expect(self.page.get_by_text('待核对', exact=True)).not_to_have_count(0)
        self.assertEqual(len(self.deletes), 4)

    async def test_cancel_keeps_row_and_sends_no_delete(self):
        await self.open_keys()
        dialog = await self.delete_menu('revoked')
        await dialog.get_by_role('button', name=re.compile(r'^取\s*消$')).click()
        await expect(dialog).to_be_hidden()
        await expect(self.row('revoked')).to_be_visible()
        self.assertEqual(self.deletes, [])
        self.assertEqual(self.key_pages, [1])
        reopened = await self.delete_menu('revoked')
        await expect(reopened).to_be_visible()
        await reopened.get_by_role('button', name=re.compile(r'^取\s*消$')).click()
        self.assertEqual(self.deletes, [])

    async def test_failed_delete_shows_server_error_and_keeps_row_without_refresh(self):
        async def fail(route):
            self.deletes.append((urlparse(route.request.url).path, route.request.post_data))
            await route.fulfill(status=403, json={'error': {'code': 'forbidden', 'message': '无权删除此密钥'}})
        await self.page.route(self.origin + '/api/developer/applications/app-1/keys/key-active', fail)
        await self.open_keys()
        await self.confirm_delete(await self.delete_menu('active'))
        await expect(self.page.get_by_text('无权删除此密钥', exact=False)).to_be_visible()
        await expect(self.row('active')).to_be_visible()
        self.assertEqual(len(self.deletes), 1)
        self.assertEqual(self.key_pages, [1])

    async def test_failed_refresh_after_delete_keeps_stale_rows_until_retry(self):
        failed = False
        async def keys(route):
            nonlocal failed
            if self.deletes and not failed:
                failed = True
                await route.fulfill(status=503, json={'error': {'code': 'unavailable', 'message': '密钥列表暂时不可用'}})
            else:
                await super(DeveloperKeyDeletionRegressions, self).respond(route)
        await self.page.route(self.origin + '/api/developer/applications/app-1/keys?*', keys)
        await self.open_keys()
        await self.confirm_delete(await self.delete_menu('active'))
        await expect(self.page.get_by_text('密钥列表暂时不可用', exact=False)).to_be_visible()
        await expect(self.row('active')).to_be_visible()
        self.assertEqual(len(self.deletes), 1)
        await self.page.get_by_role('button', name='刷新应用', exact=True).click()
        await expect(self.row('active')).to_have_count(0)
        await expect(self.page.locator('.key-row')).to_have_count(3)
        await expect(self.page.get_by_text('密钥列表暂时不可用', exact=False)).to_have_count(0)

    async def test_repeated_menu_events_open_one_confirmation_and_send_one_delete(self):
        requested, release = asyncio.Event(), asyncio.Event()
        async def delayed(route):
            self.deletes.append((urlparse(route.request.url).path, route.request.post_data))
            requested.set()
            await release.wait()
            self.keys = [key for key in self.keys if key['id'] != 'key-active']
            with contextlib.suppress(Exception):
                await route.fulfill(status=204)
        await self.page.route(self.origin + '/api/developer/applications/app-1/keys/key-active', delayed)
        try:
            await self.open_keys()
            await self.row('active').get_by_role('button', name='更多操作', exact=True).click()
            item = self.page.get_by_role('menuitem', name='删除密钥', exact=True)
            await item.evaluate('(item) => { item.click(); item.click() }')
            dialog = self.page.get_by_role('dialog').filter(has_text='删除后该密钥将立即失效')
            await expect(dialog).to_have_count(1)
            await self.confirm_delete(dialog)
            await asyncio.wait_for(requested.wait(), timeout=10)
            await self.row('active').get_by_role('button', name='更多操作', exact=True).click()
            await self.page.get_by_role('menuitem', name='删除密钥', exact=True).click()
            await expect(self.page.get_by_role('dialog')).to_have_count(0)
            self.assertEqual(len(self.deletes), 1)
            release.set()
            await expect(self.row('active')).to_have_count(0)
        finally:
            release.set()

    async def test_navigation_while_confirming_does_not_send_delete(self):
        await self.open_keys()
        dialog = await self.delete_menu('expired')
        await self.page.evaluate("window.history.replaceState(null, '', '/developer?applicationId=app-1&tab=usage'); window.dispatchEvent(new PopStateEvent('popstate'))")
        await expect(self.page.get_by_text('待核对', exact=True)).not_to_have_count(0)
        await self.confirm_delete(dialog)
        await expect(dialog).to_be_hidden()
        self.assertEqual(self.deletes, [])

    async def test_late_delete_cannot_refresh_or_pollute_another_application(self):
        requested, release = asyncio.Event(), asyncio.Event()
        app2_reads = 0
        app2 = {**self.app, 'id': 'app-2', 'name': '另一个应用', 'code': 'another-app'}
        second_key = {**self.key('active', 'app-2'), 'name': '另一个应用的密钥'}
        async def application_two(route):
            nonlocal app2_reads
            if urlparse(route.request.url).path.endswith('/keys'):
                app2_reads += 1
                await route.fulfill(json={'items': [second_key], 'total': 1})
            else:
                await route.fulfill(json=app2)
        async def delayed(route):
            self.deletes.append((urlparse(route.request.url).path, route.request.post_data))
            requested.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await route.fulfill(status=204)
        await self.page.route(self.origin + '/api/developer/applications/app-2**', application_two)
        await self.page.route(self.origin + '/api/developer/applications/app-1/keys/key-active', delayed)
        try:
            await self.open_keys()
            await self.confirm_delete(await self.delete_menu('active'))
            await asyncio.wait_for(requested.wait(), timeout=10)
            await self.page.evaluate("window.history.replaceState(null, '', '/developer?applicationId=app-2&tab=keys'); window.dispatchEvent(new PopStateEvent('popstate'))")
            await expect(self.page.get_by_role('heading', name='另一个应用', exact=True)).to_be_visible()
            await expect(self.page.get_by_text('另一个应用的密钥', exact=True)).to_be_visible()
            self.assertEqual(app2_reads, 1)
            release.set()
            await self.page.wait_for_timeout(300)
            self.assertEqual(app2_reads, 1)
            self.assertEqual(self.key_pages, [1])
            await expect(self.page.get_by_text('另一个应用的密钥', exact=True)).to_be_visible()
            await expect(self.page.locator('.key-row')).to_have_count(1)
            self.assertIn('applicationId=app-2', self.page.url)
        finally:
            release.set()


if __name__ == '__main__':
    unittest.main(verbosity=2)
