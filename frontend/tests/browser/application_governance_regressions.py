"""Governance controls never load private application details or keys."""
import re
from urllib.parse import urlparse

from playwright.async_api import expect
from admin_regressions import AdminRegressions


class ApplicationGovernanceRegressions(AdminRegressions):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.application = {'id': 'app-a', 'name': '测试应用', 'owner_id': 'owner-a',
            'owner_enabled': False, 'platform_enabled': True, 'version': 4,
            'source_file_limit': 1000, 'source_byte_limit': 1073741824,
            'source_count': 3, 'source_bytes': 1024}
        self.governance_reads = 0
        self.changes = []
        self.private_reads = []

    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path.startswith('/api/admin/applications'):
            if route.request.method == 'GET':
                self.governance_reads += 1
                await route.fulfill(json={'items': [self.application], 'total': 1, 'page': 1, 'page_size': 20})
            else:
                body = route.request.post_data_json
                self.changes.append((path, body))
                if body['expected_version'] != self.application['version']:
                    await route.fulfill(status=409,json={'error': {'code': 'application_conflict', 'message': '应用已变化，请刷新'}})
                    return
                self.application['version'] += 1
                if path.endswith('/state'):
                    self.application['platform_enabled'] = body['enabled']
                    await route.fulfill(json=self.application)
                else:
                    self.application.update(source_file_limit=body['source_file_limit'], source_byte_limit=body['source_byte_limit'])
                    await route.fulfill(json={'version': self.application['version']})
            return
        if path.startswith('/api/developer/applications/'):
            self.private_reads.append(path)
        await super().respond(route)

    async def test_admin_edits_only_governance_quota_with_current_version(self):
        await self.page.goto(self.origin + '/admin/applications')
        await expect(self.page.get_by_role('heading',name='应用治理',exact=True)).to_be_visible()
        await expect(self.page.get_by_text('测试应用',exact=True)).to_be_visible()
        await self.page.get_by_role('button',name='调整额度',exact=True).click()
        dialog=self.page.get_by_role('dialog',name='调整资源额度',exact=True)
        await dialog.get_by_label('文件上限',exact=True).fill('25')
        await dialog.get_by_label('空间上限（MB）',exact=True).fill('50')
        await dialog.get_by_role('button',name=re.compile(r'^保\s*存$')).click()
        await expect(dialog).to_have_count(0)
        self.assertEqual(self.changes,[('/api/admin/applications/app-a/resource-quota',
            {'source_file_limit':25,'source_byte_limit':52428800,'expected_version':4})])
        self.assertEqual(self.private_reads,[])
        self.assertGreaterEqual(self.governance_reads,2)

    async def test_platform_toggle_requires_confirmation_and_preserves_user_disabled_state(self):
        await self.page.goto(self.origin + '/admin/applications')
        await self.page.get_by_role('button',name='平台停用',exact=True).click()
        confirmation=self.page.locator('.ant-modal-confirm')
        await confirmation.get_by_role('button',name=re.compile(r'^取\s*消$')).click()
        self.assertEqual(self.changes,[])
        await self.page.get_by_role('button',name='平台停用',exact=True).click()
        await self.page.locator('.ant-modal-confirm').get_by_role('button',name=re.compile(r'^确\s*认$')).click()
        await expect(self.page.get_by_role('button',name='平台启用',exact=True)).to_be_visible()
        await expect(self.page.get_by_text('用户已停用',exact=True)).to_be_visible()
        self.assertEqual(self.changes,[('/api/admin/applications/app-a/state',{'enabled':False,'expected_version':4})])
        self.assertEqual(self.private_reads,[])

    async def test_delegated_user_manager_has_no_governance_route_or_requests(self):
        self.user.update(is_admin=False,permissions=['system:users:read','system:users:write'])
        await self.page.goto(self.origin + '/admin/users')
        await expect(self.page.get_by_role('heading',name='用户管理',exact=True)).to_be_visible()
        await expect(self.page.get_by_role('link',name='应用治理',exact=True)).to_have_count(0)
        await self.page.goto(self.origin + '/admin/applications')
        await expect(self.page).to_have_url(self.origin + '/chat')
        self.assertEqual(self.governance_reads,0)
        self.assertEqual(self.private_reads,[])


    async def test_stale_quota_save_shows_conflict_and_keeps_dialog_open(self):
        await self.page.goto(self.origin + '/admin/applications')
        await self.page.get_by_role('button',name='调整额度',exact=True).click()
        self.application['version'] = 9
        dialog = self.page.get_by_role('dialog',name='调整资源额度',exact=True)
        await dialog.get_by_role('button',name=re.compile(r'^保\s*存$')).click()
        await expect(dialog).to_be_visible()
        await expect(dialog).to_contain_text('应用已变化，请刷新')
        self.assertEqual(len(self.changes), 1)
        self.assertEqual(self.changes[0][1]['expected_version'], 4)
        self.assertEqual(self.private_reads, [])

    async def test_governance_quota_form_remains_usable_on_phone(self):
        await self.page.set_viewport_size({'width':390,'height':844})
        await self.page.goto(self.origin + '/admin/applications')
        await self.page.get_by_role('button',name='调整额度',exact=True).click()
        dialog = self.page.get_by_role('dialog',name='调整资源额度',exact=True)
        await expect(dialog.get_by_label('文件上限',exact=True)).to_be_in_viewport()
        await expect(dialog.get_by_role('button',name=re.compile(r'^保\s*存$'))).to_be_in_viewport()
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        self.assertEqual(self.changes, [])
        self.assertEqual(self.private_reads, [])


from developer_regressions import DeveloperBoundary


class ApplicationPlatformOwnerRegressions(DeveloperBoundary):
    async def test_suspended_owner_sees_reason_and_cannot_offer_reenable_or_create_key(self):
        self.app.update(platform_enabled=False,status='disabled')
        for width in (1440,390):
            await self.page.set_viewport_size({'width':width,'height':900})
            await self.page.goto(self.origin + '/developer')
            await expect(self.page.locator('.application-row')).to_contain_text('平台停用')
            await self.page.get_by_role('button',name='管理应用“内部工具”',exact=True).click()
            await expect(self.page.get_by_text('此应用已被平台停用，请联系管理员。',exact=True)).to_be_visible()
            await expect(self.page.get_by_role('button',name='重新启用',exact=True)).to_have_count(0)
            await self.page.get_by_role('tab',name='访问密钥',exact=True).click()
            await expect(self.page.get_by_role('button',name='创建密钥',exact=True)).to_have_count(0)
        self.assertEqual(self.posts, [])
