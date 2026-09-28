"""Credential endpoint isolation and safe connection failure display."""
import asyncio

from playwright.async_api import async_playwright, expect
from model_onboarding_check import Fixture, open_form
from knowledge_check import button


class ProfileFixture(Fixture):
    def __init__(self):
        super().__init__()
        self.failure_reason = 'timeout'
        self.credentials = [
            {'credentialName': 'siliconflow-embedding', 'provider': 'openai', 'connectionProfile': {
                'status': 'ready', 'apiBase': 'https://api.siliconflow.cn/v1', 'providerIds': ['OpenAI_Compatible', 'CUSTOM_OPENAI']}},
            {'credentialName': 'dp', 'provider': 'openai', 'connectionProfile': {
                'status': 'ready', 'apiBase': 'https://api.deepseek.com', 'providerIds': ['Deepseek', 'CUSTOM_OPENAI']}},
            {'credentialName': 'openai-saved', 'provider': 'openai', 'connectionProfile': {
                'status': 'ready', 'apiBase': 'https://api.openai.com/v1', 'providerIds': ['OpenAI', 'CUSTOM_OPENAI']}},
            {'credentialName': 'unknown-connection', 'provider': 'openai', 'connectionProfile': {
                'status': 'missing', 'apiBase': None, 'providerIds': []}},
            {'credentialName': 'conflicted-connection', 'provider': 'openai', 'connectionProfile': {
                'status': 'conflict', 'apiBase': None, 'providerIds': []}},
        ]

    def respond(self, path, method, body):
        if path.endswith('/providers'):
            data = []
            for provider, engine in [('Deepseek', 'deepseek'), ('OpenAI_Compatible', 'openai'), ('OpenAI', 'openai')]:
                data.append({'provider': provider, 'displayName': provider, 'litellmProvider': engine,
                             'credentialFields': [{'fieldKey': 'api_key', 'label': 'API Key', 'fieldType': 'password', 'required': True}]})
            return {'code': 200, 'data': data}
        if path.endswith('/test-connection'):
            self.calls.append((path, method, body))
            return {'code': 200, 'data': {'success': not self.failure_reason, 'latencyMs': 60000,
                'failureReason': self.failure_reason, 'message': 'upstream-secret-should-not-be-visible'}}
        return super().respond(path, method, body)


async def select_provider(page, drawer, label):
    await drawer.locator('.ant-form-item:has(label:text-is("服务商")) .ant-select-selector').click()
    await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').get_by_text(label, exact=True).click()


async def main():
    expect.set_options(timeout=15000)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        fixture = ProfileFixture()
        context, page, drawer, errors = await open_form(browser, fixture)
        try:
            await select_provider(page, drawer, '硅基流动')
            await expect(drawer.get_by_text('服务地址：https://api.siliconflow.cn/v1', exact=True)).to_be_visible()
            await drawer.locator('.ant-form-item:has(label:text-is("已保存连接")) .ant-select-selector').click()
            await expect(page.locator('.ant-select-dropdown:visible .ant-select-item-option-content')).to_have_text(['siliconflow-embedding'])
            await page.locator('.ant-select-dropdown:visible .ant-select-item-option-content').filter(has_text='siliconflow-embedding').click()
            await expect(drawer.get_by_label('模型名称', exact=True)).to_have_attribute('placeholder', '例如 deepseek-ai/DeepSeek-V4-Pro')
            await drawer.get_by_label('模型名称', exact=True).fill('deepseek-ai/DeepSeek-V4-Pro')
            for reason, fragment in [('timeout', '连接测试超时'), ('authentication', '模型服务拒绝了 API Key'), ('model_not_found', '服务商未找到该模型'), ('quota', '模型账户余额')]:
                fixture.failure_reason = reason
                await button(drawer, '测试连接').click()
                await expect(drawer.locator('.status-message')).to_contain_text(fragment)
                await expect(button(drawer, '接入模型')).to_be_disabled()
            assert 'upstream-secret-should-not-be-visible' not in await page.locator('body').inner_text()
            fixture.failure_reason = None
            await button(drawer, '测试连接').click()
            await expect(button(drawer, '接入模型')).to_be_enabled()
            assert fixture.writes('/test-connection')[-1]['credentialName'] == 'siliconflow-embedding'
            await select_provider(page, drawer, 'DeepSeek')
            await expect(drawer.get_by_text('服务地址：https://api.deepseek.com', exact=True)).to_be_visible()
            await expect(button(drawer, '接入模型')).to_be_disabled()
            await drawer.locator('.ant-form-item:has(label:text-is("已保存连接")) .ant-select-selector').click()
            await expect(page.locator('.ant-select-dropdown:visible .ant-select-item-option-content')).to_have_text(['dp'])
            assert not errors, errors
            await page.screenshot(path='/tmp/model-connection-profile.png', full_page=True)
            print('PASS provider isolation, inherited endpoint display, fixed error reasons, secret suppression and test invalidation')
        finally:
            await context.close()
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
