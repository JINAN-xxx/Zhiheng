import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import Browser, BrowserContext, Page, Route, async_playwright

BASE_URL = os.environ.get("PLATFORM_SHELL_BASE_URL", "http://127.0.0.1:15174")
SCREENSHOT_DIR = Path(os.environ.get("PLATFORM_SHELL_SCREENSHOT_DIR", "/tmp"))

ALL_PERMISSIONS = [
    "ai:chat:list",
    "ai:model:list",
    "ai:model:query",
    "ai:model:add",
    "ai:model:edit",
    "ai:model:publish",
    "ai:model:test",
    "ai:model:remove",
    "ai:application:list",
    "ai:application:add",
    "ai:application:edit",
    "ai:application:key:list",
    "ai:application:key:add",
    "ai:application:key:rotate",
    "ai:application:key:revoke",
]

SESSIONS = [
    {
        "sessionId": "session-analysis",
        "title": "分析会话",
        "updatedAt": "2026-08-20T08:00:00Z",
    },
    {
        "sessionId": "session-planning",
        "title": "规划会话",
        "updatedAt": "2026-08-20T07:00:00Z",
    },
]


def ai_route(name: str, path: str, component: str, title: str) -> dict:
    return {
        "name": name,
        "path": path,
        "component": component,
        "hidden": False,
        "meta": {"title": title, "icon": "component", "noCache": False},
    }


def routes_for(access: str) -> list[dict]:
    children = []
    if access in {"all", "chat"}:
        children.append(ai_route("AiChat", "chat", "plugin/ai/chat/index", "AI 对话"))
    if access in {"all", "model"}:
        children.append(ai_route("AiModel", "model", "plugin/ai/model/index", "模型"))
    if access in {"all", "application"}:
        children.extend(
            [
                ai_route(
                    "AiApplication",
                    "application",
                    "plugin/ai/application/index",
                    "应用接入",
                ),
                {
                    **ai_route("AiUsage", "usage", "plugin/ai/usage/index", "用量"),
                    "hidden": True,
                },
            ]
        )

    routes = []
    if children:
        routes.append(
            {
                "name": "AiWorkspace",
                "path": "/ai",
                "component": "Layout",
                "redirect": f"/ai/{children[0]['path']}",
                "alwaysShow": True,
                "meta": {"title": "AI 工作台", "icon": "chat"},
                "children": children,
            }
        )
    routes.append(
        {
            "name": "System",
            "path": "/system",
            "component": "Layout",
            "redirect": "/system/overview",
            "alwaysShow": True,
            "meta": {"title": "系统管理", "icon": "system"},
            "children": [
                ai_route(
                    "SystemOverview",
                    "overview",
                    "plugin/ai/usage/index",
                    "系统概览",
                )
            ],
        }
    )
    return routes


def permissions_for(access: str) -> list[str]:
    if access == "all":
        return ALL_PERMISSIONS
    if access == "chat":
        return ["ai:chat:list"]
    if access == "model":
        return ["ai:model:list", "ai:model:query"]
    if access == "application":
        return ["ai:application:list"]
    return []


def response_for(path: str, method: str, access: str) -> dict:
    if path == "/transport/crypto/frontend-config":
        return {"code": 200, "data": {"enabled": False}}
    if path == "/getInfo":
        return {
            "code": 200,
            "user": {
                "userId": 1,
                "userName": "admin",
                "nickName": "平台管理员",
                "avatar": "",
            },
            "roles": ["admin"],
            "permissions": permissions_for(access),
            "pwdChrtype": "none",
            "isDefaultModifyPwd": False,
            "isPasswordExpired": False,
        }
    if path == "/getRouters":
        return {"code": 200, "data": routes_for(access)}
    if path == "/ai/chat/session/list":
        return {"code": 200, "rows": SESSIONS}
    if path.startswith("/ai/chat/session/"):
        return {
            "code": 200,
            "data": {
                "sessionId": path.rsplit("/", 1)[-1],
                "messages": [
                    {
                        "messageId": "message-1",
                        "role": "assistant",
                        "content": "这是已保存的会话内容。",
                    }
                ],
            },
        }
    if path == "/ai/chat/config":
        return {
            "code": 200,
            "data": {
                "temperature": None,
                "addHistory": True,
                "historyMessageCount": 20,
                "systemPrompt": None,
                "metricsVisible": True,
                "visionEnabled": True,
                "imageMaxSizeMb": 10,
            },
        }
    if path == "/ai/models":
        return {
            "code": 200,
            "rows": [
                {
                    "modelId": "mdl_11111111111141118111111111111111",
                    "modelCode": "workspace-chat",
                    "displayName": "Workspace Chat",
                    "modelType": "chat",
                    "capabilities": {"streaming": True, "vision": False},
                    "lifecycleStatus": "draft",
                    "updateTime": "2026-08-20T08:00:00Z",
                }
            ],
            "total": 1,
            "pageNum": 1,
            "pageSize": 100,
            "hasNext": False,
        }
    if path.startswith("/ai/models/"):
        return {
            "code": 200,
            "data": {
                "modelId": path.split("/")[3],
                "modelCode": "workspace-chat",
                "displayName": "Workspace Chat",
                "modelType": "chat",
                "capabilities": {"streaming": True},
                "lifecycleStatus": "draft",
                "binding": None,
            },
        }
    if path == "/ai/applications":
        return {
            "code": 200,
            "rows": [
                {
                    "applicationId": "app_11111111111141118111111111111111",
                    "applicationCode": "workspace",
                    "applicationName": "工作台应用",
                    "status": "active",
                    "version": 1,
                }
            ],
            "total": 1,
            "pageNum": 1,
            "pageSize": 100,
            "hasNext": False,
        }
    if path.endswith("/usage"):
        return {
            "code": 200,
            "data": {
                "rows": [],
                "total": 0,
                "pageNum": 1,
                "pageSize": 20,
                "hasNext": False,
            },
        }
    if path == "/system/notice/listTop":
        return {
            "code": 200,
            "data": [
                {
                    "noticeId": 42,
                    "noticeTitle": "维护公告",
                    "noticeType": "1",
                    "createTime": "2026-08-21 10:00:00",
                    "isRead": True,
                }
            ],
            "unreadCount": 0,
        }
    if path == "/system/notice/42":
        return {
            "code": 200,
            "data": {
                "noticeId": 42,
                "noticeTitle": "维护公告",
                "noticeType": "1",
                "status": "0",
                "createBy": "admin",
                "createTime": "2026-08-21 10:00:00",
                "noticeContent": (
                    '<p><strong>维护窗口</strong>，请查看 '
                    '<a href="https://example.com/help">操作说明</a>。</p>'
                    '<img src=x onerror="window.__noticeXss = true">'
                    '<script>window.__noticeXss = true</script>'
                ),
            },
        }
    if method == "DELETE":
        return {"code": 200, "msg": "操作成功"}
    return {"code": 200, "rows": [], "data": [], "total": 0}


async def install_backend_mock(context: BrowserContext, access: str = "all") -> None:
    async def handle(route: Route) -> None:
        request = route.request
        parsed = urlparse(request.url)
        if not parsed.path.startswith("/dev-api/"):
            await route.continue_()
            return
        api_path = parsed.path.removeprefix("/dev-api")
        payload = response_for(api_path, request.method, access)
        await route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload, ensure_ascii=False),
        )

    await context.route("**/dev-api/**", handle)
    await context.add_cookies(
        [{"name": "Admin-Token", "value": "e2e-token", "url": BASE_URL}]
    )


async def new_page(
    browser: Browser,
    viewport: dict[str, int],
    access: str = "all",
) -> tuple[BrowserContext, Page, list[str], list[str]]:
    context = await browser.new_context(viewport=viewport)
    await install_backend_mock(context, access)
    page = await context.new_page()
    console_errors: list[str] = []
    page_errors: list[str] = []
    page.on(
        "console",
        lambda message: (
            console_errors.append(message.text) if message.type == "error" else None
        ),
    )
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    return context, page, console_errors, page_errors


async def assert_no_overflow(page: Page) -> None:
    assert await page.evaluate(
        "document.documentElement.scrollWidth <= window.innerWidth"
    )


async def check_desktop(browser: Browser) -> None:
    context, page, console_errors, page_errors = await new_page(
        browser, {"width": 1440, "height": 900}
    )
    await page.goto(f"{BASE_URL}/index")
    await page.wait_for_url("**/ai/chat")
    assert await page.locator(".platform-sidebar").count() == 1
    assert await page.get_by_text("Developer API", exact=True).count() == 0
    assert await page.get_by_role("link", name="AI 资源").count() == 1
    assert await page.get_by_text("分析会话", exact=True).count() == 1
    await assert_no_overflow(page)

    expanded_width = await page.locator(".platform-shell__sidebar").evaluate(
        "(element) => element.getBoundingClientRect().width"
    )
    assert expanded_width == 230
    await page.get_by_role("button", name="打开主菜单").click()
    await page.wait_for_timeout(250)
    collapsed_width = await page.locator(".platform-shell__sidebar").evaluate(
        "(element) => element.getBoundingClientRect().width"
    )
    assert collapsed_width == 56
    await page.get_by_role("button", name="打开主菜单").click()
    await page.wait_for_timeout(250)
    await page.screenshot(path=str(SCREENSHOT_DIR / "platform-shell-desktop-1440.png"))

    async def fail_session_refresh(route: Route) -> None:
        await route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {"code": 500, "msg": "会话服务暂时不可用"},
                ensure_ascii=False,
            ),
        )

    await page.route("**/dev-api/ai/chat/session/list", fail_session_refresh)
    await page.get_by_role("button", name="刷新最近会话").click()
    await page.get_by_text("刷新失败，列表可能已过期", exact=True).wait_for()
    assert await page.get_by_text("分析会话", exact=True).count() == 1
    await page.unroute("**/dev-api/ai/chat/session/list", fail_session_refresh)

    await page.get_by_text("分析会话", exact=True).click()
    await page.wait_for_url("**/ai/chat?sessionId=session-analysis")
    await page.get_by_text("这是已保存的会话内容。", exact=True).wait_for()
    await page.go_back()
    await page.wait_for_url("**/ai/chat")
    await page.get_by_role("button", name="通知公告").click()
    await page.get_by_text("维护公告", exact=True).click()
    await page.locator(".notice-content strong", has_text="维护窗口").wait_for()
    assert await page.locator('.notice-content a[href="https://example.com/help"]').count() == 1
    assert await page.locator(".notice-content script").count() == 0
    assert not await page.evaluate("Boolean(window.__noticeXss)")
    assert console_errors == []
    assert page_errors == []
    await context.close()


async def check_admin(browser: Browser) -> None:
    context, page, console_errors, page_errors = await new_page(
        browser, {"width": 1024, "height": 768}
    )
    await page.goto(f"{BASE_URL}/ai/chat")
    await page.get_by_role("button", name="管理控制台").first.click()
    await page.wait_for_url("**/system/overview")
    await page.get_by_text("用量台账", exact=True).wait_for()
    assert await page.locator(".admin-sidebar").count() == 1
    await page.get_by_role("menuitem", name="系统概览").wait_for()
    assert await page.locator("#tags-view-container").count() == 1
    await page.get_by_role("button", name="打开主菜单").click()
    await page.wait_for_timeout(250)
    assert await page.locator(".platform-shell__sidebar").evaluate(
        "element => element.getBoundingClientRect().width === 56"
    )
    await assert_no_overflow(page)
    await page.screenshot(path=str(SCREENSHOT_DIR / "platform-shell-admin-1024.png"))
    await page.get_by_role("button", name="返回 AI 工作台").first.click()
    await page.wait_for_url("**/ai/chat")
    await page.get_by_text("从一个问题开始", exact=True).wait_for()
    assert await page.locator("#tags-view-container").count() == 0
    await assert_no_overflow(page)
    assert console_errors == []
    assert page_errors == []
    await context.close()


async def check_mobile(browser: Browser, width: int, height: int) -> None:
    context, page, console_errors, page_errors = await new_page(
        browser, {"width": width, "height": height}
    )
    await page.goto(f"{BASE_URL}/ai/chat")
    assert await page.locator(".platform-shell__sidebar").count() == 0
    menu_button = page.get_by_role("button", name="打开主菜单")
    await menu_button.click()
    drawer = page.locator(".platform-mobile-drawer")
    await page.wait_for_function(
        "document.querySelector('.platform-mobile-drawer')?.classList.contains('ant-drawer-open')"
    )
    await drawer.locator(":focus").wait_for()
    assert await drawer.locator(":focus").count() == 1
    await page.keyboard.press("Escape")
    await page.wait_for_function(
        "!document.querySelector('.platform-mobile-drawer')?.classList.contains('ant-drawer-open')"
    )
    await page.wait_for_function(
        "element => document.activeElement === element",
        arg=await menu_button.element_handle(),
    )
    assert await menu_button.evaluate("(element) => document.activeElement === element")
    await menu_button.click()
    await page.wait_for_function(
        "document.querySelector('.platform-mobile-drawer')?.classList.contains('ant-drawer-open')"
    )
    await drawer.get_by_text("规划会话", exact=True).click()
    await page.wait_for_url("**/ai/chat?sessionId=session-planning")
    await page.wait_for_function(
        "!document.querySelector('.platform-mobile-drawer')?.classList.contains('ant-drawer-open')"
    )
    await page.get_by_text("这是已保存的会话内容。", exact=True).wait_for()
    await assert_no_overflow(page)
    await page.screenshot(
        path=str(SCREENSHOT_DIR / f"platform-shell-mobile-{width}.png")
    )
    assert console_errors == []
    assert page_errors == []
    await context.close()


async def check_resources_320(browser: Browser) -> None:
    context, page, console_errors, page_errors = await new_page(
        browser, {"width": 320, "height": 720}
    )
    await page.goto(f"{BASE_URL}/ai/model")
    await page.get_by_role("navigation", name="AI 资源导航").wait_for()
    for label in ("模型", "应用接入", "用量"):
        assert await page.get_by_role("link", name=label, exact=True).count() == 1
    await page.get_by_role("button", name="更多操作").click()
    assert await page.get_by_text("费率配置", exact=True).count() == 1
    await assert_no_overflow(page)
    await page.screenshot(path=str(SCREENSHOT_DIR / "platform-shell-resource-320.png"))
    assert console_errors == []
    assert page_errors == []
    await context.close()


async def check_permission_landings(browser: Browser) -> None:
    expectations = {
        "chat": "/ai/chat",
        "model": "/ai/model",
        "application": "/ai/application",
        "none": "/system/overview",
    }
    for access, expected_path in expectations.items():
        context, page, console_errors, page_errors = await new_page(
            browser, {"width": 1024, "height": 768}, access
        )
        await page.goto(f"{BASE_URL}/index")
        await page.wait_for_url(f"**{expected_path}")
        assert urlparse(page.url).path == expected_path
        assert console_errors == []
        assert page_errors == []
        await context.close()


async def main() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            await check_desktop(browser)
            await check_admin(browser)
            await check_mobile(browser, 390, 844)
            await check_resources_320(browser)
            await check_permission_landings(browser)
        finally:
            await browser.close()
    print("platform shell browser checks passed")


if __name__ == "__main__":
    asyncio.run(main())
