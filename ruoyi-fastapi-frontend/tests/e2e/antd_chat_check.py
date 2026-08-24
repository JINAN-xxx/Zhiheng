import asyncio
import inspect
import json
import os

from playwright.async_api import Browser, Page, Route, async_playwright

from platform_shell_check import (
    SCREENSHOT_DIR,
    assert_mobile_profile_layout,
    assert_no_overflow,
    new_page,
    take_screenshot,
)

BASE_URL = os.environ.get("PLATFORM_SHELL_BASE_URL", "http://127.0.0.1:15175")
DESKTOP_VIEWPORT = {"width": 1440, "height": 900}
MOBILE_VIEWPORT = {"width": 375, "height": 812}


def check_screenshot_contract() -> None:
    source = inspect.getsource(take_screenshot)
    for required_fragment in (
        "document.fonts.ready",
        "document.images",
        "getAnimations",
        "full_page=True",
    ):
        assert required_fragment in source, required_fragment


async def install_chat_routes(page: Page) -> tuple[dict, asyncio.Event]:
    trace = {
        "cancel_bodies": [],
        "send_bodies": [],
        "preference_bodies": [],
        "upload_count": 0,
    }
    release_pending = asyncio.Event()

    async def handle_send(route: Route) -> None:
        payload = route.request.post_data_json
        trace["send_bodies"].append(payload)
        message = payload.get("message")
        if message == "停止测试":
            await release_pending.wait()
            try:
                await route.abort()
            except Exception:
                pass
            return
        if message == "服务失败":
            await route.fulfill(
                status=503,
                content_type="application/json",
                body=json.dumps(
                    {"detail": {"code": "MODEL_RUNTIME_UNAVAILABLE", "message": "模型运行暂不可用"}},
                    ensure_ascii=False,
                ),
            )
            return
        body = "".join(
            [
                'event: meta\ndata: {"sessionId":"session-stream","runId":"run-old"}\n\n',
                'event: reasoning\ndata: {"delta":"先分析"}\n\n',
                'event: content\ndata: {"delta":"流式"}\n\n',
                'event: content\ndata: {"delta":"回答"}\n\n',
                'event: usage\ndata: {"inputTokens":4,"outputTokens":2}\n\n',
                'event: done\ndata: {"status":"completed"}\n\n',
            ]
        )
        await route.fulfill(status=200, content_type="text/event-stream", body=body)

    async def handle_cancel(route: Route) -> None:
        trace["cancel_bodies"].append(route.request.post_data_json)
        await route.fulfill(
            status=200,
            content_type="application/json",
            body='{"code":200}',
        )

    async def handle_upload(route: Route) -> None:
        trace["upload_count"] += 1
        await route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {"code": 200, "data": {"fileId": f"file-{trace['upload_count']}"}}
            ),
        )

    async def handle_preferences(route: Route) -> None:
        if route.request.method == "PUT":
            payload = route.request.post_data_json
            trace["preference_bodies"].append(payload)
        else:
            payload = {
                "temperature": None,
                "addHistory": True,
                "historyMessageCount": 20,
                "systemPrompt": None,
                "metricsVisible": True,
                "visionEnabled": True,
                "imageMaxSizeMb": 10,
            }
        await route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({"code": 200, "data": payload}, ensure_ascii=False),
        )

    await page.route("**/dev-api/ai/chat/send", handle_send)
    await page.route("**/dev-api/ai/chat/cancel", handle_cancel)
    await page.route("**/dev-api/common/upload", handle_upload)
    await page.route("**/dev-api/ai/chat/config", handle_preferences)
    return trace, release_pending


async def send_message(page: Page, message: str) -> None:
    composer = page.get_by_placeholder("输入消息，按回车发送，按住上档键回车换行")
    await composer.fill(message)
    await page.get_by_role("button", name="发送").click()


async def check_streaming(browser: Browser) -> None:
    context, page, console_errors, page_errors = await new_page(
        browser, DESKTOP_VIEWPORT
    )
    release_pending = None
    try:
        trace, release_pending = await install_chat_routes(page)
        await page.goto(f"{BASE_URL}/ai/chat")
        await page.get_by_text("从一个问题开始", exact=True).wait_for()

        send_button = page.get_by_role("button", name="发送")
        send_width = await send_button.evaluate(
            "element => element.getBoundingClientRect().width"
        )
        await page.locator('input[type="file"]').set_input_files(
            {"name": "evidence.png", "mimeType": "image/png", "buffer": b"png"}
        )
        await page.locator(".image-queue", has_text="文件 file-1").wait_for()
        await send_message(page, "正常回复")
        await page.get_by_text("流式回答", exact=True).wait_for()
        await page.get_by_text("输入 4 · 输出 2", exact=True).wait_for()
        await page.get_by_text("已完成", exact=True).wait_for()
        await page.wait_for_url("**/ai/chat?sessionId=session-stream")
        assert trace["send_bodies"][0]["images"] == [{"fileId": "file-1"}]
        await assert_no_overflow(page)
        assert console_errors == [], console_errors
        assert page_errors == [], page_errors
        await take_screenshot(page, "chat-answer-1440x900-light.png")

        await send_message(page, "停止测试")
        stop_button = page.get_by_role("button", name="停止")
        await stop_button.wait_for()
        await page.get_by_text("连接中", exact=True).wait_for()
        assert await page.get_by_role("button", name="发送").count() == 0
        composer = page.get_by_placeholder("输入消息，按回车发送，按住上档键回车换行")
        assert await composer.is_disabled()
        await assert_no_overflow(page)
        assert console_errors == [], console_errors
        assert page_errors == [], page_errors
        await take_screenshot(page, "chat-generating-1440x900-light.png")
        stop_width = await stop_button.evaluate(
            "element => element.getBoundingClientRect().width"
        )
        assert send_width == stop_width == 84
        await stop_button.click()
        release_pending.set()
        await send_button.wait_for()
        assert trace["cancel_bodies"] == []
        await page.get_by_text("已取消", exact=True).wait_for()

        await send_message(page, "服务失败")
        await page.get_by_text("模型运行暂不可用", exact=True).wait_for()
        await page.get_by_text("失败", exact=True).wait_for()
        expected_errors = [
            message for message in console_errors
            if "503" in message and "Failed to load resource" in message
        ]
        assert len(expected_errors) == 1, console_errors
        console_errors.remove(expected_errors[0])
        assert console_errors == [], console_errors

        await page.get_by_text("规划会话", exact=True).click()
        await page.wait_for_url("**/ai/chat?sessionId=session-planning")
        await page.get_by_text("这是已保存的会话内容。", exact=True).wait_for()
        await page.get_by_role("button", name="对话偏好").click()
        await page.get_by_text("系统提示词", exact=True).wait_for()
        assert await page.locator(".ant-modal-footer button").count() == 2
        modal = page.locator(".ant-modal")
        history_count = modal.locator(".ant-form-item").filter(has_text="历史消息数")
        system_prompt = modal.locator(".ant-form-item").filter(has_text="系统提示词")
        await history_count.locator("input").fill("7")
        await system_prompt.locator("textarea").fill("只回答平台相关问题")
        async with page.expect_response(
            lambda response: response.url.endswith("/ai/chat/config")
            and response.request.method == "PUT"
        ):
            await modal.locator(".ant-modal-footer .ant-btn-primary").click()
        assert trace["preference_bodies"][-1]["historyMessageCount"] == 7
        assert trace["preference_bodies"][-1]["systemPrompt"] == "只回答平台相关问题"
        await page.get_by_role("button", name="对话偏好").click()
        await system_prompt.locator("textarea").wait_for()
        assert await system_prompt.locator("textarea").input_value() == "只回答平台相关问题"
        await modal.locator(".ant-modal-footer button").first.click()

        await page.locator('input[type="file"]').set_input_files(
            {"name": "remove.png", "mimeType": "image/png", "buffer": b"png"}
        )
        queue = page.locator(".image-queue", has_text="文件 file-2")
        await queue.wait_for()
        await queue.locator(".ant-tag-close-icon").click()
        await queue.wait_for(state="hidden")
        assert trace["upload_count"] == 2
        await assert_no_overflow(page)
        assert console_errors == [], console_errors
        assert page_errors == [], page_errors
        await take_screenshot(page, "chat-failed-history-1440x900-light.png")
        assert console_errors == [], console_errors
        assert page_errors == [], page_errors
    finally:
        if release_pending is not None:
            release_pending.set()
        await context.close()


async def check_mobile(browser: Browser) -> None:
    context, page, console_errors, page_errors = await new_page(
        browser, MOBILE_VIEWPORT
    )
    try:
        await install_chat_routes(page)
        await page.goto(f"{BASE_URL}/ai/chat")
        await page.get_by_text("从一个问题开始", exact=True).wait_for()
        await assert_mobile_profile_layout(page)
        await assert_no_overflow(page)
        await take_screenshot(page, "chat-mobile-375x812-light.png")
        assert console_errors == [], console_errors
        assert page_errors == [], page_errors
    finally:
        await context.close()


async def main() -> None:
    check_screenshot_contract()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            await check_streaming(browser)
            await check_mobile(browser)
        finally:
            await browser.close()
    print("Ant Design chat browser checks passed")


if __name__ == "__main__":
    asyncio.run(main())
