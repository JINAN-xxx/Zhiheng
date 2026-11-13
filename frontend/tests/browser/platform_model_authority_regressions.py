"""A directory or model-configuration role must never fetch credential metadata."""
import os
import unittest
from urllib.parse import urlparse
from playwright.async_api import expect
from developer_regressions import DeveloperBoundary

class ModelAuthorityRegressions(DeveloperBoundary):
    async def asyncSetUp(self):
        self.permissions = ['platform:models:enabled','platform:models:read']
        self.authority_requests = []
        await super().asyncSetUp()

    async def respond(self, route):
        path = urlparse(route.request.url).path
        self.authority_requests.append(path)
        if path == '/api/auth/me':
            return await route.fulfill(json={'id':'user-1','username':'member','is_admin':False,'permissions':self.permissions})
        if path == '/api/models/connections':
            return await route.fulfill(json=[{'id':'connection-1','name':'Company credential','provider':'openai_compatible','base_url':'https://provider.fixture/v1','has_api_key':True,'enabled':True}])
        if path == '/api/models/connection-options':
            return await route.fulfill(json=[{'id':'connection-1','name':'Company connection','enabled':True}])
        return await super().respond(route)

    async def test_directory_role_never_fetches_credentials_or_exposes_management(self):
        await self.page.goto(self.origin + '/models')
        await expect(self.page.get_by_role('heading', name='模型目录', exact=True)).to_be_visible()
        await expect(self.page.get_by_role('tab', name='凭据管理', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button', name='接入模型', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button', name='新增凭据', exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button', name='查看模型详情：日常助手', exact=True)).to_be_visible()
        self.assertNotIn('/api/models/connections', self.authority_requests)
        self.assertNotIn('/api/models/connection-options', self.authority_requests)

    async def test_model_configurator_uses_safe_choices_without_credential_tab(self):
        self.permissions=['platform:models:enabled','platform:models:read','platform:models:config_read','platform:models:create','platform:models:update','platform:models:delete','platform:models:toggle']
        await self.page.goto(self.origin + '/models')
        await expect(self.page.get_by_role('button', name='接入模型', exact=True)).to_be_enabled()
        await expect(self.page.get_by_role('tab', name='凭据管理', exact=True)).to_have_count(0)
        await self.page.get_by_role('button', name='接入模型', exact=True).click()
        await expect(self.page.locator('.ant-drawer-content')).to_be_visible()
        await expect(self.page.locator('.ant-drawer-content')).to_contain_text('Company connection')
        self.assertIn('/api/models/connection-options', self.authority_requests)
        self.assertNotIn('/api/models/connections', self.authority_requests)
        await expect(self.page.get_by_role('textbox', name='API Key', exact=True)).to_have_count(0)

    async def test_credential_reader_has_no_model_or_credential_write_controls(self):
        self.permissions=['ai:credentials:enabled','ai:credentials:read']
        await self.page.goto(self.origin + '/credentials')
        await expect(self.page.get_by_text('Company credential',exact=True)).to_be_visible()
        await expect(self.page.get_by_role('button',name='新增凭据',exact=True)).to_have_count(0)
        await expect(self.page.get_by_role('button',name='接入模型',exact=True)).to_have_count(0)
        self.assertIn('/api/models/connections',self.authority_requests)

if __name__ == '__main__':
    os.environ.setdefault('PLATFORM_BROWSER_URL','http://127.0.0.1:19080')
    unittest.main()
