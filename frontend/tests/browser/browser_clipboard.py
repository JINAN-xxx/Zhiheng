"""Read the real clipboard; normalize Windows CRLF while preserving all text and line breaks."""
import os


async def read_clipboard(page):
    if await page.evaluate('Boolean(navigator.clipboard?.readText)'):
        return (await page.evaluate('navigator.clipboard.readText()')).replace('\r\n', '\n')
    reader_url = os.environ.get('PLATFORM_CLIPBOARD_READER_URL')
    if not reader_url:
        raise RuntimeError('LAN tests require PLATFORM_CLIPBOARD_READER_URL on localhost or HTTPS')
    reader = await page.context.new_page()
    try:
        await reader.goto(reader_url, wait_until='domcontentloaded')
        return (await reader.evaluate('navigator.clipboard.readText()')).replace('\r\n', '\n')
    finally:
        await reader.close()
        await page.bring_to_front()
        await page.evaluate('new Promise(requestAnimationFrame)')
