"""Check real encrypted CAPTCHA traffic; never solve a challenge or sign in.

CAPTCHA_CHECK_URL=http://host:8060 python tests/e2e/captcha_transport_check.py
Requires Playwright Chromium and a running backend with transport crypto enabled.
"""

import asyncio
import json
import os
import time
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, expect


async def check_captcha(page):
    image = page.locator('img[alt="验证码"]')
    await expect(image).to_be_visible()
    await page.wait_for_function(
        "document.querySelector('img[alt=验证码]')?.naturalWidth > 0"
    )
    return image


async def main():
    expect.set_options(timeout=20000)
    base_url = os.environ.get('CAPTCHA_CHECK_URL', 'http://127.0.0.1:12580').rstrip('/')
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page(viewport={'width': 1440, 'height': 900})
            page.set_default_timeout(20000)
            errors = []
            responses = []
            page.on('pageerror', lambda error: errors.append(str(error)))

            def record(response):
                path = urlsplit(response.url).path
                if path.endswith('/captchaImage') or (
                    path.endswith('/login') and response.request.method == 'POST'
                ):
                    responses.append(response)

            page.on('response', record)
            await page.goto(base_url + '/login', wait_until='networkidle')
            await check_captcha(page)

            # Reload also exercises the public key restored from session storage.
            await page.reload(wait_until='networkidle')
            image = await check_captcha(page)
            previous = await image.get_attribute('src')
            await page.get_by_role('button', name='刷新验证码', exact=True).click()
            await page.wait_for_function(
                "old => { const image = document.querySelector('img[alt=验证码]');"
                "return image?.naturalWidth > 0 && image.getAttribute('src') !== old }",
                arg=previous,
            )

            await page.get_by_placeholder('账号', exact=True).fill(f'captcha_probe_{time.time_ns()}')
            await page.get_by_placeholder('密码', exact=True).fill('NotARealAccount!42')
            await page.get_by_placeholder('验证码', exact=True).fill('invalid')
            # The backend must reject this deliberately invalid CAPTCHA, then refresh it.
            async with page.expect_response(lambda response: urlsplit(response.url).path.endswith('/captchaImage')):
                await page.get_by_role('button', name='登 录', exact=True).click()
                await expect(page.get_by_text('验证码错误', exact=True)).to_be_visible()
            await page.wait_for_load_state('networkidle')
            image = await check_captcha(page)

            traffic = []
            for response in responses:
                request_headers = await response.request.all_headers()
                response_headers = await response.all_headers()
                traffic.append({
                    'path': urlsplit(response.url).path,
                    'method': response.request.method,
                    'status': response.status,
                    'requestEncrypted': request_headers.get('x-transport-encrypt') == '1',
                    'responseEncrypted': response_headers.get('x-body-encrypted') == '1',
                })
            assert len(traffic) >= 5, traffic
            assert any(item['method'] == 'POST' for item in traffic), traffic
            assert all(item['status'] == 200 and item['requestEncrypted'] and item['responseEncrypted'] for item in traffic), traffic
            assert not errors, errors
            print(json.dumps({
                'secureContext': await page.evaluate('isSecureContext'),
                'captchaImageSize': await image.evaluate('image => [image.naturalWidth, image.naturalHeight]'),
                'reloadAndRefresh': True,
                'encryptedLoginRejectedInvalidCaptcha': True,
                'pageErrors': errors,
                'traffic': traffic,
            }, ensure_ascii=False))
        finally:
            await browser.close()


if __name__ == '__main__':
    asyncio.run(main())
