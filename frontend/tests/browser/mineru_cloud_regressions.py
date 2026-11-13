"""Cloud parsing availability through the real settings UI and fixture HTTP."""
import unittest
from urllib.parse import urlparse

from playwright.async_api import expect

from developer_regressions import DeveloperBoundary


class MinerUCloudSettingsRegressions(DeveloperBoundary):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.available = True
        self.enabled = True

    async def respond(self, route):
        path = urlparse(route.request.url).path
        if path == '/api/knowledge/settings/parsing':
            await route.fulfill(json={
                'version': 3, 'local_url': 'http://local.test:8000', 'has_local_key': False,
                'cloud_enabled': self.enabled, 'cloud_url': 'https://mineru.net/api/v4',
                'has_cloud_key': True, 'tier': 'standard', 'timeout_seconds': 900,
                'queue_timeout_seconds': 120,
            })
        elif path == '/api/knowledge/settings/parsing/status':
            await route.fulfill(json={
                'local': {'configured': True, 'available': True},
                'cloud': {'configured': True, 'available': self.available, 'version': 'api-v4'},
                'cloud_enabled': self.enabled,
            })
        elif path == '/api/knowledge/settings':
            await route.fulfill(json={
                'default_embed_model_id': None, 'default_rerank_model_id': None,
                'chunk_size': 1000, 'chunk_overlap': 200, 'version': 1,
            })
        else:
            await super().respond(route)

    async def test_cloud_status_refreshes_and_official_address_is_visible(self):
        await self.page.goto(self.origin + '/knowledge/settings')
        await expect(self.page.get_by_text('云端可用', exact=True)).to_be_visible()
        await expect(self.page.get_by_placeholder('https://mineru.net/api/v4')).to_have_value('https://mineru.net/api/v4')
        await expect(self.page.get_by_text('云端 MinerU 地址', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('本地可用', exact=True)).to_be_visible()
        self.available = False
        await self.page.get_by_role('button', name='刷新解析服务', exact=True).click()
        await expect(self.page.get_by_text('云端不可用', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('本地可用', exact=True)).to_be_visible()

    async def test_disabled_cloud_does_not_show_an_available_badge(self):
        self.enabled = False
        await self.page.goto(self.origin + '/knowledge/settings')
        await expect(self.page.get_by_text('本地可用', exact=True)).to_be_visible()
        await expect(self.page.get_by_text('云端可用', exact=True)).to_have_count(0)
        await expect(self.page.get_by_placeholder('https://mineru.net/api/v4')).to_have_count(0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
