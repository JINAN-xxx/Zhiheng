"""Exercise the actual archive UI with isolated HTTP fixtures, without touching live conversations."""

import asyncio
import json
from urllib.parse import parse_qs, urlparse

from playwright.async_api import Route, async_playwright, expect

from platform_shell_check import BASE_URL, MODEL_IDS, assert_no_overflow, new_page


async def check_archive(browser, *, mobile=False):
    viewport = {"width": 375, "height": 812} if mobile else {"width": 1440, "height": 900}
    context, page, _, page_errors = await new_page(browser, viewport)
    page.set_default_timeout(8000)
    sessions = {
        "archive-example": {
            "sessionId": "archive-example", "modelId": MODEL_IDS["active"],
            "title": "归档验收会话", "status": "active",
            "createdAt": "2026-09-15T08:00:00", "updatedAt": "2026-09-15T08:00:00",
        },
        "older-archive": {
            "sessionId": "older-archive", "modelId": MODEL_IDS["active"],
            "title": "旧归档会话", "status": "archived",
            "createdAt": "2026-09-14T08:00:00", "updatedAt": "2026-09-14T08:00:00",
        },
    }
    mutations = []
    sent_requests = []

    async def handle_session(route: Route):
        parsed = urlparse(route.request.url)
        resource = parsed.path.split("/session/", 1)[1]
        if resource == "list":
            query = parse_qs(parsed.query)
            status = query.get("status", ["active"])[0]
            rows = [dict(row) for row in sessions.values() if row["status"] == status]
            payload = {"code": 200, "rows": rows, "total": len(rows)}
        elif resource.endswith("/status"):
            session_id = resource.split("/")[0]
            status = route.request.post_data_json["status"]
            assert route.request.method == "PATCH"
            mutations.append((session_id, status))
            sessions[session_id]["status"] = status
            payload = {"code": 200, "data": dict(sessions[session_id])}
        elif route.request.method == "DELETE":
            mutations.append((resource, "deleted"))
            del sessions[resource]
            payload = {"code": 200}
        else:
            payload = {"code": 200, "data": {
                "session": dict(sessions[resource]),
                "messages": [{"messageId": "saved-message", "role": "user", "content": "归档后仍然保留的消息"}],
                "runs": [],
            }}
        await route.fulfill(status=200, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

    await page.route("**/ai/chat/session/**", handle_session)

    async def handle_send(route: Route):
        sent_requests.append(route.request.post_data_json)
        body = (
            'event: meta\ndata: {"sessionId":"archive-example","runId":"restored-run"}\n\n'
            'event: content\ndata: {"delta":"恢复后的新回复"}\n\n'
            'event: done\ndata: {"status":"completed"}\n\n'
        )
        await route.fulfill(status=200, content_type="text/event-stream", body=body)

    await page.route("**/ai/chat/send", handle_send)

    async def show_sidebar():
        if mobile:
            await page.get_by_role("button", name="打开主菜单", exact=True).click()
        panel = page.locator(".recent-sessions:visible")
        await panel.wait_for()
        return panel

    try:
        await page.goto(f"{BASE_URL}/ai/chat?sessionId=archive-example")
        await page.get_by_text("归档后仍然保留的消息", exact=True).wait_for()
        panel = await show_sidebar()
        target = panel.locator(".recent-session").filter(has_text="归档验收会话")
        await target.hover()
        await target.get_by_role("button", name="更多操作").click()
        await page.get_by_role("menuitem", name="归档会话").click()
        await page.wait_for_url("**/ai/chat")
        panel = await show_sidebar() if mobile else panel
        await expect(panel.get_by_text("归档验收会话", exact=True)).to_have_count(0)
        await panel.locator('label.ant-radio-button-wrapper').filter(has_text="已归档").click()
        await panel.get_by_text("归档验收会话", exact=True).click()
        await page.get_by_text("归档后仍然保留的消息", exact=True).wait_for()
        await expect(page.locator(".composer textarea")).to_have_count(0)
        await page.get_by_role("button", name="恢复对话", exact=True).click()
        await expect(page.locator(".composer textarea")).to_be_enabled()
        await expect(page.get_by_text("归档后仍然保留的消息", exact=True)).to_be_visible()
        await page.locator(".composer textarea").fill("恢复后继续讨论")
        await expect(page.get_by_role("button", name="发送", exact=True)).to_be_enabled()
        await page.get_by_role("button", name="发送", exact=True).click()
        await page.get_by_text("恢复后的新回复", exact=True).wait_for()
        assert sent_requests[0]["sessionId"] == "archive-example"
        assert sent_requests[0]["modelId"] == MODEL_IDS["active"]

        panel = await show_sidebar()
        await expect(panel.get_by_role("radio", name="最近会话", exact=True)).to_be_checked()
        await panel.locator('label.ant-radio-button-wrapper').filter(has_text="已归档").click()
        old = panel.locator(".recent-session").filter(has_text="旧归档会话")
        await old.hover()
        await old.get_by_role("button", name="更多操作").click()
        await page.get_by_role("menuitem", name="删除会话").click()
        await expect(old).to_be_visible()
        await page.locator(".ant-modal-confirm-btns .ant-btn-primary").click()
        await expect(panel.get_by_text("旧归档会话", exact=True)).to_have_count(0)
        await assert_no_overflow(page)
        assert mutations == [("archive-example", "archived"), ("archive-example", "active"), ("older-archive", "deleted")]
        assert not page_errors, page_errors
        await page.screenshot(path=f"/tmp/chat-archive-{'mobile' if mobile else 'desktop'}.png")
        print(f"PASS archive/read/restore/delete-confirmation: {'mobile' if mobile else 'desktop'}")
    except BaseException:
        await page.screenshot(path="/tmp/chat-archive-failure.png")
        print(await page.locator('.composer').aria_snapshot())
        raise
    finally:
        await context.close()


async def check_restore_during_detail(browser):
    context, page, _, page_errors = await new_page(browser, {"width": 1440, "height": 900})
    started, release = asyncio.Event(), asyncio.Event()
    session = {
        "sessionId": "race-session", "modelId": MODEL_IDS["active"], "title": "延迟详情的归档会话",
        "status": "archived", "createdAt": "2026-09-15T08:00:00", "updatedAt": "2026-09-15T08:00:00",
    }

    async def handle(route: Route):
        parsed = urlparse(route.request.url)
        resource = parsed.path.split("/session/", 1)[1]
        if resource == "list":
            status = parse_qs(parsed.query).get("status", ["active"])[0]
            rows = [dict(session)] if status == session["status"] else []
            payload = {"code": 200, "rows": rows, "total": len(rows)}
        elif resource.endswith("/status"):
            session["status"] = route.request.post_data_json["status"]
            payload = {"code": 200, "data": dict(session)}
        else:
            payload = {"code": 200, "data": {"session": dict(session), "messages": [
                {"messageId": "race-message", "role": "user", "content": "延迟返回的历史消息"},
            ], "runs": []}}
            started.set()
            await release.wait()
        await route.fulfill(status=200, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

    await page.route("**/ai/chat/session/**", handle)
    try:
        await page.goto(f"{BASE_URL}/ai/chat")
        panel = page.locator(".recent-sessions:visible")
        await panel.locator('label.ant-radio-button-wrapper').filter(has_text="已归档").click()
        target = panel.locator(".recent-session").filter(has_text=session["title"])
        await target.get_by_role("button").first.click()
        await asyncio.wait_for(started.wait(), timeout=8)
        await target.hover()
        await target.get_by_role("button", name="更多操作").click()
        await page.get_by_role("menuitem", name="恢复对话").click()
        await expect(panel.get_by_role("radio", name="最近会话", exact=True)).to_be_checked()
        release.set()
        await page.get_by_text("延迟返回的历史消息", exact=True).wait_for()
        await expect(page.locator(".composer textarea")).to_be_enabled()
        assert not page_errors, page_errors
        print("PASS a delayed archived detail cannot undo a confirmed restoration")
    finally:
        release.set()
        await context.close()


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            await check_restore_during_detail(browser)
            await check_archive(browser)
            await check_archive(browser, mobile=True)
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
