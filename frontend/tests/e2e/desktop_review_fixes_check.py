"""Desktop regressions found in the layout review, using isolated HTTP fixtures."""

import asyncio
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

import platform_shell_check as shell
from chat_attachments_check import AttachmentFixture, choose, chosen_file
from desktop_layout_check import desktop_page, ready


async def check_send_with_attachments(browser, height=620, empty=False):
    context, page, console_errors, page_errors = await shell.new_page(
        browser, {"width": 1280, "height": height}
    )
    fixture = AttachmentFixture(page)
    await fixture.install()
    try:
        path = "/ai/chat" if empty else "/ai/chat?sessionId=upload-session"
        await page.goto(shell.BASE_URL + path)
        await expect(page.locator(".composer textarea")).to_be_enabled()
        await choose(page, [chosen_file(f"image-{i}.png") for i in range(8)])
        await expect(page.locator(".queued-file-status").filter(has_text="已上传")).to_have_count(8)
        await page.locator(".composer textarea").fill("\n".join(f"草稿第 {i} 行" for i in range(10)))
        send = page.get_by_role("button", name="发送", exact=True)
        await expect(send).to_be_enabled()
        if empty:
            workspace = await page.locator(".chat-workspace").bounding_box()
            await page.mouse.move(workspace["x"] + 8, workspace["y"] + 80)
            await page.mouse.wheel(0, 2000)
        await shell.take_screenshot(page, f"chat-files-{height}-empty-{empty}.png")
        button = await send.bounding_box()
        workspace = await page.locator(".platform-chat").bounding_box()
        assert button["y"] >= workspace["y"], (button, workspace)
        assert button["y"] + button["height"] <= workspace["y"] + workspace["height"], (button, workspace)
        assert await send.evaluate("""e => {
            const box = e.getBoundingClientRect();
            return document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2)?.closest('button') === e;
        }"""), "Send is clipped or covered"
        files = page.locator(".file-queue")
        await files.evaluate("e => { e.scrollTop = e.scrollHeight; }")
        last_file = page.locator(".queued-file-name").filter(has_text="image-7.png")
        file_box = await last_file.bounding_box()
        queue_box = await files.bounding_box()
        assert queue_box["y"] <= file_box["y"]
        assert file_box["y"] + file_box["height"] <= queue_box["y"] + queue_box["height"], (file_box, queue_box)
        await send.click()
        await page.get_by_text("已读取本次上传文件", exact=True).wait_for()
        assert len(fixture.sends) == 1
        assert len(fixture.sends[0]["images"]) == 8
        assert not page_errors, page_errors
        assert not console_errors, console_errors
        await shell.assert_no_overflow(page)
    finally:
        await context.close()


async def check_narrow_model_names(browser, width=992, writable=True):
    permissions = None if writable else ["ai:model:list"]
    context, page, console_errors, page_errors, requests = await desktop_page(
        browser, width, permissions=permissions
    )
    names = ("gpt-4o", "gpt-4o-mini")
    models = [
        {"modelId": shell.MODEL_IDS[key], "modelCode": name, "displayName": name,
         "modelType": "chat", "lifecycleStatus": "active", "bindingStatus": "active",
         "capabilities": {"vision": False}, "updateTime": "2026-10-12T01:00:00Z"}
        for key, name in zip(("active", "disabled"), names)
    ]

    async def handle(route):
        path = "/" + urlparse(route.request.url).path.split("/", 2)[-1]
        if path == "/ai/models":
            await route.fulfill(json={"code": 200, "rows": models, "total": 2})
        elif any(path == "/ai/models/" + model["modelId"] for model in models):
            model = next(m for m in models if path.endswith(m["modelId"]))
            await route.fulfill(json={"code": 200, "data": model})
        else:
            await route.fallback()

    await context.route("**/dev-api/**", handle)
    await context.route("**/docker-api/**", handle)
    try:
        await page.goto(shell.BASE_URL + "/ai/model")
        rows = page.locator(".model-table tbody tr.ant-table-row")
        await expect(rows).to_have_count(2)
        await ready(page)
        await shell.take_screenshot(page, f"model-names-{width}-writable-{writable}.png")
        for name in names:
            code = rows.filter(has_text=name).locator(".resource-text").filter(has_text=name).first
            assert await code.evaluate("e => e.scrollWidth <= e.clientWidth"), f"Model {name} is truncated"
        if writable:
            row = rows.filter(has_text="gpt-4o-mini")
            await row.get_by_role("button", name="更多操作", exact=True).click()
            await expect(page.get_by_role("menuitem", name="停用", exact=True)).to_be_visible()
            await page.keyboard.press("Escape")
        assert not any(method != "GET" for method, _ in requests), requests
        assert not page_errors, page_errors
        assert not console_errors, console_errors
        await shell.assert_no_overflow(page)
    finally:
        await context.close()


async def check_keyboard_vision_reason(browser, enabled=False):
    context, page, console_errors, page_errors = await shell.new_page(
        browser, {"width": 1280, "height": 800}
    )
    fixture = AttachmentFixture(page)
    fixture.vision = enabled
    await fixture.install()
    try:
        await page.goto(shell.BASE_URL + "/ai/chat")
        badge = page.locator(".model-capability")
        await badge.wait_for()
        description = ("当前模型支持图片理解，也可上传文档附件。" if enabled else
                       "当前模型未启用视觉能力，请切换支持图片的模型；文档附件仍可上传。")
        await expect(badge).to_have_accessible_description(description)
        await badge.focus()
        assert await badge.evaluate("e => document.activeElement === e")
        await expect(page.get_by_role("tooltip")).to_have_text(description)
        await page.keyboard.press("Tab")
        await expect(page.get_by_role("tooltip")).to_have_count(0)
        assert not page_errors, page_errors
        assert not console_errors, console_errors
    finally:
        await context.close()


async def check_model_resize(browser):
    context, page, console_errors, page_errors, _ = await desktop_page(browser, 992)
    try:
        await page.goto(shell.BASE_URL + "/ai/model")
        await page.locator(".model-table tbody tr.ant-table-row").first.wait_for()
        await ready(page)
        await page.set_viewport_size({"width": 375, "height": 800})
        await expect(page.locator(".model-table thead th")).to_have_count(2)
        table = await page.locator(".model-table table").bounding_box()
        content = await page.locator(".model-table .ant-table-content").bounding_box()
        assert table["width"] <= content["width"] + 1, (table, content)
        menu = page.locator(".model-table tbody tr.ant-table-row").first.get_by_role("button", name="更多操作", exact=True)
        box = await menu.bounding_box()
        assert content["x"] <= box["x"] and box["x"] + box["width"] <= content["x"] + content["width"], (box, content)
        await page.set_viewport_size({"width": 992, "height": 800})
        await expect(page.locator(".model-table thead th")).to_have_count(6)
        name = await page.locator(".model-table tbody tr.ant-table-row td").first.bounding_box()
        assert name["width"] >= 300, name
        assert not page_errors, page_errors
        assert not console_errors, console_errors
    finally:
        await context.close()


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            for height, empty in ((620, False), (550, False), (800, False), (620, True)):
                await shell.run_browser_check(check_send_with_attachments, browser, height, empty)
                print(f"PASS attachment send height={height} empty={empty}", flush=True)
            for width, writable in ((992, True), (1024, True), (1280, True), (992, False)):
                await shell.run_browser_check(check_narrow_model_names, browser, width, writable)
                print(f"PASS model names width={width} writable={writable}", flush=True)
            for enabled in (False, True):
                await shell.run_browser_check(check_keyboard_vision_reason, browser, enabled)
                print(f"PASS keyboard capability description vision={enabled}", flush=True)
            await shell.run_browser_check(check_model_resize, browser)
            print("PASS table width after viewport breakpoint changes", flush=True)
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
