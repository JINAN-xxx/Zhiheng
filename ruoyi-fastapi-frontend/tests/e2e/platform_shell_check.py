import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    Route,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

BASE_URL = os.environ.get("PLATFORM_SHELL_BASE_URL", "http://127.0.0.1:15174")
SCREENSHOT_DIR = Path(os.environ.get("PLATFORM_SHELL_SCREENSHOT_DIR", "/tmp"))
DESKTOP_VIEWPORT = {"width": 1440, "height": 900}
ADMIN_VIEWPORT = {"width": 1280, "height": 800}
TABLET_VIEWPORT = {"width": 768, "height": 1024}
MOBILE_VIEWPORT = {"width": 375, "height": 812}
PLATFORM_STABLE_SCREENSHOTS = (
    "platform-auth-login-1280x800-dark.png",
    "platform-shell-admin-1280x800-dark.png",
    "platform-model-rate-1280x800-dark.png",
    "platform-application-1280x800-dark.png",
    "platform-usage-1280x800-dark.png",
)

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
    "ai:application:enable",
    "ai:application:delete",
    "ai:application:key:list",
    "ai:application:key:add",
    "ai:application:key:rotate",
    "ai:application:key:revoke",
    "ai:application:query",
]

MODEL_IDS = {
    "disabled": "mdl_11111111111141118111111111111111",
    "active": "mdl_22222222222242228222222222222222",
}
MODEL_LIFECYCLE = {
    MODEL_IDS["disabled"]: "disabled",
    MODEL_IDS["active"]: "active",
}


def reset_model_lifecycle() -> None:
    MODEL_LIFECYCLE.update(
        {
            MODEL_IDS["disabled"]: "disabled",
            MODEL_IDS["active"]: "active",
        }
    )

LONG_SESSION_TITLE = "跨部门智能工作流方案评审与历史上下文追踪会话名称用于验证桌面侧栏在超长中文标题下的截断、滚动和可读性边界表现"
LONG_MODEL_NAME = "企业级多模态推理与知识检索编排模型展示名称用于验证桌面资源表格在超长模型标识下的截断、提示和布局边界表现"
LONG_APPLICATION_NAME = "面向跨区域客户支持和内部知识协作的企业智能应用展示名称用于验证桌面应用列表和详情标题在超长文本下的截断边界表现"
LONG_ERROR = "模型服务暂时不可用：上游网关在执行跨区域故障转移时返回了不可恢复的连接状态，请检查配置、稍后重试并联系平台管理员处理。"


def usage_rows() -> list[dict]:
    return [
        {
            "requestId": f"usage-{index}",
            "startedAt": f"2026-08-{20 + index:02d}T08:00:00Z",
            "operationType": "chat" if index % 3 == 0 else "embedding",
            "modelCode": LONG_MODEL_NAME if index == 0 else f"workspace-model-{index}",
            "inputTokens": 100 + index,
            "outputTokens": 50 + index,
            "searchUnits": index if index % 2 == 0 else None,
            "billableCost": "0.000123",
            "status": "completed",
        }
        for index in range(12)
    ]

SESSIONS = [
    {
        "sessionId": "session-analysis",
        "title": LONG_SESSION_TITLE,
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
        children.append(ai_route("AiChat", "chat", "plugin/ai/chat/index", "智能对话"))
    if access in {"all", "model"}:
        children.append(ai_route("AiModel", "model", "plugin/ai/model/index", "模型"))
    if access in {"all", "application"}:
        children.append(
            ai_route(
                "AiApplication",
                "application",
                "plugin/ai/application/index",
                "开发接入",
            )
        )
    if access in {"all", "usage"}:
        children.append(ai_route("AiUsage", "usage", "plugin/ai/usage/index", "用量记录"))

    routes = []
    if children:
        routes.append(
            {
                "name": "AiWorkspace",
                "path": "/ai",
                "component": "Layout",
                "redirect": f"/ai/{children[0]['path']}",
                "alwaysShow": True,
                "meta": {"title": "智能工作台", "icon": "chat"},
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
    if access == "usage":
        return ["ai:application:list", "ai:application:query"]
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
    if path == "/captchaImage":
        return {"code": 200, "captchaEnabled": False, "registerEnabled": False}
    if path.startswith("/ai/chat/session/") and method == "PATCH":
        return {
            "code": 200,
            "data": {
                "sessionId": path.rsplit("/", 1)[-1],
                "modelId": "mdl_11111111111141118111111111111111",
                "title": "已重命名会话",
                "status": "active",
                "createdAt": "2026-08-20T07:00:00Z",
                "updatedAt": "2026-08-24T02:00:00Z",
            },
        }
    if path.startswith("/ai/chat/session/") and method == "GET":
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
        disabled_status = MODEL_LIFECYCLE[MODEL_IDS["disabled"]]
        active_status = MODEL_LIFECYCLE[MODEL_IDS["active"]]
        return {
            "code": 200,
            "rows": [
                {
                    "modelId": MODEL_IDS["disabled"],
                    "modelCode": LONG_MODEL_NAME,
                    "displayName": LONG_MODEL_NAME,
                    "modelType": "chat",
                    "capabilities": {"streaming": True, "vision": False},
                    "lifecycleStatus": disabled_status,
                    "binding": {"bindingStatus": "active", "engineResourceId": "openai-compatible-chat", "version": 1},
                    "updateTime": "2026-08-20T08:00:00Z",
                },
                {
                    "modelId": MODEL_IDS["active"],
                    "modelCode": "workspace-chat-active",
                    "displayName": "工作台对话模型",
                    "modelType": "chat",
                    "capabilities": {"streaming": True},
                    "lifecycleStatus": active_status,
                    "binding": {"bindingStatus": "active", "engineResourceId": "active-chat-runtime", "version": 1},
                    "updateTime": "2026-08-20T08:00:00Z",
                }
            ],
            "total": 2,
            "pageNum": 1,
            "pageSize": 100,
            "hasNext": False,
        }
    if path.endswith("/rate-cards"):
        return {
            "code": 200,
            "data": [
                {
                    "version": 1,
                    "billingMode": "token",
                    "inputPricePerToken": "0.000001",
                    "outputPricePerToken": "0.000002",
                    "currency": "USD",
                    "status": "active",
                }
            ],
        }
    if path.startswith("/ai/models/") and path.endswith("/publish") and method == "POST":
        model_id = path.split("/")[3]
        if model_id in MODEL_LIFECYCLE:
            MODEL_LIFECYCLE[model_id] = "active"
        return {"code": 200, "data": {"modelId": model_id, "lifecycleStatus": "active"}}
    if path.startswith("/ai/models/"):
        model_id = path.split("/")[3]
        lifecycle_status = MODEL_LIFECYCLE.get(model_id, "disabled")
        is_active = model_id == MODEL_IDS["active"]
        return {
            "code": 200,
            "data": {
                "modelId": model_id,
                "modelCode": "workspace-chat-active" if is_active else LONG_MODEL_NAME,
                "displayName": "工作台对话模型" if is_active else LONG_MODEL_NAME,
                "modelType": "chat",
                "capabilities": {"streaming": True},
                "lifecycleStatus": lifecycle_status,
                "binding": {"bindingStatus": "active", "engineResourceId": "active-chat-runtime" if is_active else "openai-compatible-chat", "version": 1},
            },
        }
    if path == "/ai/applications":
        return {
            "code": 200,
            "rows": [
                {
                    "applicationId": "app_11111111111141118111111111111111",
                    "applicationCode": "workspace",
                    "applicationName": LONG_APPLICATION_NAME,
                    "status": "active",
                    "version": 1,
                }
            ],
            "total": 1,
            "pageNum": 1,
            "pageSize": 100,
            "hasNext": False,
        }
    if path.endswith("/keys"):
        return {
            "code": 200,
            "data": [
                {
                    "keyId": "key-1",
                    "applicationId": "app_11111111111141118111111111",
                    "keyName": "默认开发密钥",
                    "maskedKey": "sk-e2e-****************",
                    "status": "active",
                    "lastUsedAt": "2026-08-20T08:00:00Z",
                    "grants": [{"grantId": "grant-1", "modelId": "mdl_11111111111141118111111111"}],
                    "quota": {"rpmLimit": 60, "tpmLimit": 20000, "concurrencyLimit": 2},
                }
            ],
        }
    if path.endswith("/usage"):
        return {
            "code": 200,
            "data": {
                "rows": usage_rows(),
                "total": 12,
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


async def install_backend_mock(
    context: BrowserContext, access: str = "all", authenticated: bool = True
) -> None:
    async def handle(route: Route) -> None:
        request = route.request
        parsed = urlparse(request.url)
        direct_mock_paths = {
            "/transport/crypto/frontend-config",
            "/captchaImage",
        }
        if parsed.path in direct_mock_paths:
            api_path = parsed.path
        elif parsed.path.startswith("/dev-api/"):
            api_path = parsed.path.removeprefix("/dev-api")
        else:
            await route.continue_()
            return
        payload = response_for(api_path, request.method, access)
        await route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload, ensure_ascii=False),
        )

    await context.route("**/dev-api/**", handle)
    await context.route("**/transport/crypto/frontend-config", handle)
    await context.route("**/captchaImage", handle)
    if authenticated:
        await context.add_cookies(
            [{"name": "Admin-Token", "value": "e2e-token", "url": BASE_URL}]
        )


async def new_page(
    browser: Browser,
    viewport: dict[str, int],
    access: str = "all",
    color_scheme: str = "light",
    authenticated: bool = True,
) -> tuple[BrowserContext, Page, list[str], list[str]]:
    context = await browser.new_context(viewport=viewport, color_scheme=color_scheme)
    await install_backend_mock(context, access, authenticated)
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


async def assert_text_is_constrained(page: Page, text: str) -> None:
    locator = page.get_by_text(text, exact=True).first
    await locator.wait_for()
    assert await locator.evaluate(
        """element => {
            const style = getComputedStyle(element)
            const parent = element.parentElement
            return element.scrollWidth <= element.clientWidth
                || style.textOverflow === 'ellipsis'
                || parent?.scrollWidth <= parent?.clientWidth
                || getComputedStyle(parent).textOverflow === 'ellipsis'
        }"""
    )


async def assert_mobile_profile_layout(page: Page) -> None:
    layout = await page.locator(".platform-account-menu__profile").evaluate(
        """button => {
            const avatar = button.querySelector('.ant-avatar')
            const buttonRect = button.getBoundingClientRect()
            const avatarRect = avatar.getBoundingClientRect()
            return { button: buttonRect.toJSON(), avatar: avatarRect.toJSON() }
        }"""
    )
    button = layout["button"]
    avatar = layout["avatar"]
    assert button["width"] >= 32 and button["height"] >= 32
    assert avatar["width"] >= 28 and avatar["height"] >= 28
    assert button["left"] <= avatar["left"]
    assert button["top"] <= avatar["top"]
    assert avatar["right"] <= button["right"] <= MOBILE_VIEWPORT["width"]
    assert avatar["bottom"] <= button["bottom"] <= MOBILE_VIEWPORT["height"]


async def take_screenshot(page: Page, filename: str) -> None:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    if filename == "platform-usage-1280x800-dark.png":
        # Chromium can vary a handful of anti-aliased edge pixels between rounds.
        await page.add_style_tag(
            content=(
                ".usage-controls, .usage-table-wrap, "
                ".usage-ledger-page .workspace-toolbar .ant-btn-circle { "
                "border-radius: 0 !important; }"
            )
        )
    deadline = time.monotonic() + 3.0

    def remaining_ms() -> int:
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            raise AssertionError("screenshot helper deadline exceeded 3000ms")
        return remaining

    async def bounded(operation):
        try:
            return await operation(remaining_ms())
        except PlaywrightTimeoutError as error:
            raise AssertionError("screenshot helper deadline exceeded 3000ms") from error
        except Exception as error:
            if "deadline" in str(error).lower() or "exceeded" in str(error).lower():
                raise AssertionError("screenshot helper deadline exceeded 3000ms") from error
            raise

    nprogress = page.locator("#nprogress")
    if await nprogress.count():
        await bounded(lambda timeout: nprogress.wait_for(state="detached", timeout=timeout))
    else:
        await bounded(
            lambda timeout: page.wait_for_function(
                "!document.querySelector('#nprogress')", timeout=timeout
            )
        )
    await bounded(
        lambda timeout: page.evaluate(
            """async remainingMs => {
                const deadline = performance.now() + remainingMs
                const withinBudget = (promise, label) => new Promise((resolve, reject) => {
                    const remaining = Math.max(1, deadline - performance.now())
                    const timer = setTimeout(
                        () => reject(new Error(`${label} exceeded the shared screenshot deadline`)),
                        remaining,
                    )
                    Promise.resolve(promise).then(
                        value => { clearTimeout(timer); resolve(value) },
                        error => { clearTimeout(timer); reject(error) },
                    )
                })
                if (document.fonts?.ready) await withinBudget(document.fonts.ready, 'font readiness')
                const images = Array.from(document.images)
                await withinBudget(Promise.all(images.map(image => {
                    if (image.complete) {
                        if (image.naturalWidth > 0) return Promise.resolve()
                        return Promise.reject(new Error(`image failed: ${image.currentSrc || image.src}`))
                    }
                    return new Promise((resolve, reject) => {
                        const finish = () => {
                            if (image.naturalWidth > 0) resolve()
                            else reject(new Error(`image failed: ${image.currentSrc || image.src}`))
                        }
                        image.addEventListener('load', finish, { once: true })
                        image.addEventListener('error', () => reject(new Error(`image failed: ${image.currentSrc || image.src}`)), { once: true })
                        if (image.complete) finish()
                    })
                })), 'image readiness')
                const finiteAnimations = () => document.getAnimations({ subtree: true }).filter(animation => {
                    const timing = animation.effect?.getComputedTiming?.()
                    const configured = animation.effect?.getTiming?.()
                    const iterations = configured?.iterations ?? timing?.iterations
                    if (iterations === Infinity) return false
                    if (animation.playState === 'running' && !Number.isFinite(timing?.endTime)) return false
                    return animation.playState !== 'idle' && Number.isFinite(timing?.endTime)
                })
                const seen = new Set()
                let stableRounds = 0
                while (performance.now() < deadline) {
                    const animations = finiteAnimations()
                    const newAnimations = animations.filter(animation => !seen.has(animation))
                    animations.forEach(animation => seen.add(animation))
                    stableRounds = newAnimations.length === 0 ? stableRounds + 1 : 0
                    if (animations.length) {
                        await withinBudget(
                            Promise.all(animations.map(animation => animation.finished.catch(() => undefined))),
                            'animation readiness',
                        )
                    }
                    await withinBudget(new Promise(resolve => requestAnimationFrame(resolve)), 'animation sampling')
                    if (stableRounds >= 2) break
                }
                if (stableRounds < 2) throw new Error('finite animation stability exceeded shared deadline')
            }""",
            timeout,
        )
    )
    await bounded(
        lambda timeout: page.wait_for_function(
            "!document.querySelector('#nprogress')", timeout=timeout
        )
    )
    await bounded(
        lambda timeout: page.evaluate(
            """async remainingMs => {
                const deadline = performance.now() + remainingMs
                const wait = () => new Promise(resolve => requestAnimationFrame(
                    () => requestAnimationFrame(resolve)
                ))
                await Promise.race([
                    wait(),
                    new Promise((_, reject) => setTimeout(
                        () => reject(new Error('double RAF exceeded shared screenshot deadline')),
                        Math.max(1, deadline - performance.now()),
                    )),
                ])
                if (document.querySelector('#nprogress')) {
                    throw new Error('NProgress appeared during screenshot sampling')
                }
            }""",
            timeout,
        )
    )
    previous_screenshot = None
    while True:
        current_screenshot = await bounded(
            lambda timeout: page.screenshot(
                path=str(SCREENSHOT_DIR / filename),
                timeout=timeout,
                animations="disabled",
                style="*, *::before, *::after { animation: none !important; transition: none !important; caret-color: transparent !important; }",
                scale="css",
                full_page=True,
            )
        )
        if previous_screenshot == current_screenshot:
            return
        previous_screenshot = current_screenshot
        await bounded(
            lambda timeout: page.evaluate(
                """async remainingMs => {
                    await Promise.race([
                        new Promise(resolve => requestAnimationFrame(
                            () => requestAnimationFrame(resolve)
                        )),
                        new Promise((_, reject) => setTimeout(
                            () => reject(new Error('screenshot resampling exceeded shared deadline')),
                            remainingMs,
                        )),
                    ])
                }""",
                timeout,
            )
        )


async def check_screenshot_helper_behavior(browser: Browser) -> None:
    context = await browser.new_context(viewport={"width": 320, "height": 240})
    page = await context.new_page()
    try:
        await page.set_content(
            """
            <style>
              @keyframes finite { from { opacity: .2 } to { opacity: 1 } }
              @keyframes spinner { to { transform: rotate(360deg) } }
              #finite { animation: finite 25ms 1 linear }
              #spinner { animation: spinner 1s linear infinite }
            </style>
            <div id="nprogress"></div><div id="finite"></div><div id="spinner"></div>
            <img alt="pixel" src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=" />
            <script>setTimeout(() => document.querySelector('#nprogress').remove(), 40)</script>
            """
        )
        filename = "screenshot-helper-behavior.png"
        await take_screenshot(page, filename)
        screenshot = SCREENSHOT_DIR / filename
        assert screenshot.exists() and screenshot.stat().st_size > 100
    finally:
        await context.close()


def screenshot_hash(filename: str) -> str:
    screenshot = SCREENSHOT_DIR / filename
    assert screenshot.exists(), screenshot
    return hashlib.sha256(screenshot.read_bytes()).hexdigest()


async def check_desktop(browser: Browser) -> None:
    context, page, console_errors, page_errors = await new_page(
        browser, DESKTOP_VIEWPORT
    )
    await page.goto(f"{BASE_URL}/ai/chat")
    await page.get_by_text("从一个问题开始", exact=True).wait_for()
    assert await page.locator(".platform-sidebar").count() == 1
    assert await page.locator(".platform-topbar").count() == 0
    assert await page.get_by_text("Developer API", exact=True).count() == 0
    for label in ("智能对话", "开发接入", "用量记录"):
        assert await page.get_by_role("link", name=label, exact=True).count() == 1
    assert await page.get_by_role("button", name="模型中心", exact=True).count() == 1
    assert await page.get_by_text(LONG_SESSION_TITLE, exact=True).count() == 1
    await assert_text_is_constrained(page, LONG_SESSION_TITLE)
    await assert_no_overflow(page)
    assert not await page.locator("html").evaluate(
        "element => element.classList.contains('dark')"
    )

    sidebar_colors = await page.locator(".platform-sidebar").evaluate(
        """element => ({
            background: getComputedStyle(element).backgroundColor,
            color: getComputedStyle(element).color,
        })"""
    )
    assert sidebar_colors == {"background": "rgb(252, 254, 254)", "color": "rgb(21, 22, 22)"}
    active_chat_background = await page.get_by_role(
        "link", name="智能对话", exact=True
    ).evaluate("element => getComputedStyle(element).backgroundColor")
    assert active_chat_background == "rgb(225, 246, 251)"

    group_toggle = page.get_by_role("button", name="模型中心", exact=True)
    assert await group_toggle.get_attribute("aria-expanded") == "true"
    await group_toggle.click()
    assert await group_toggle.get_attribute("aria-expanded") == "false"
    await page.get_by_role("link", name="用量记录", exact=True).wait_for(state="hidden")
    await group_toggle.click()
    assert await group_toggle.get_attribute("aria-expanded") == "true"

    expanded_width = await page.locator(".platform-shell__sidebar").evaluate(
        "(element) => element.getBoundingClientRect().width"
    )
    assert expanded_width == 230
    await page.get_by_role("button", name="折叠侧边栏").click()
    await page.wait_for_timeout(250)
    collapsed_width = await page.locator(".platform-shell__sidebar").evaluate(
        "(element) => element.getBoundingClientRect().width"
    )
    assert collapsed_width == 56
    await page.get_by_role("button", name="模型中心").click()
    resource_menu = page.locator(".ant-dropdown").last
    await resource_menu.wait_for(state="visible")
    await resource_menu.get_by_role("menuitem", name="开发接入", exact=True).wait_for()
    await page.keyboard.press("Escape")
    await page.get_by_role("button", name="展开侧边栏").click()
    await page.wait_for_timeout(250)
    async def fail_session_refresh(route: Route) -> None:
        await route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {"code": 500, "msg": LONG_ERROR},
                ensure_ascii=False,
            ),
        )

    await page.route("**/dev-api/ai/chat/session/list", fail_session_refresh)
    await page.get_by_role("button", name="刷新最近会话").click()
    await page.get_by_text("刷新失败，列表可能已过期", exact=True).wait_for()
    await page.get_by_text(LONG_ERROR, exact=True).wait_for()
    await assert_text_is_constrained(page, LONG_ERROR)
    assert await page.get_by_text(LONG_SESSION_TITLE, exact=True).count() == 1
    await assert_no_overflow(page)
    assert console_errors == [], console_errors
    assert page_errors == [], page_errors
    await take_screenshot(page, "platform-shell-chat-1440x900-light.png")
    await page.unroute("**/dev-api/ai/chat/session/list", fail_session_refresh)

    session_row = page.locator(".recent-session").filter(has_text=LONG_SESSION_TITLE)
    await session_row.get_by_role("button", name="更多操作").click()
    await page.get_by_text("重命名", exact=True).click()
    prompt = page.locator(".ant-modal").last
    await prompt.locator("input").fill("已重命名会话")
    await prompt.locator(".ant-btn-primary").click()
    await page.get_by_text("已重命名会话", exact=True).wait_for()

    renamed_row = page.locator(".recent-session").filter(has_text="已重命名会话")
    await renamed_row.get_by_role("button", name="更多操作").click()
    await page.get_by_text("删除会话", exact=True).click()
    confirm = page.locator(".ant-modal").last
    await confirm.get_by_text("删除后无法恢复", exact=False).wait_for()
    await confirm.locator(".ant-btn-primary").click()
    await page.get_by_text("已重命名会话", exact=True).wait_for(state="hidden")

    await page.get_by_text("规划会话", exact=True).click()
    await page.wait_for_url("**/ai/chat?sessionId=session-planning")
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
        browser, ADMIN_VIEWPORT
    )
    await page.goto(f"{BASE_URL}/ai/chat")
    await page.get_by_role("button", name="切换主题").click()
    await page.locator("html.dark").wait_for()
    await page.get_by_role("button", name="管理后台").first.click()
    await page.wait_for_url("**/system/overview")
    await page.locator('main[aria-label="用量记录"]').wait_for()
    assert await page.locator(".admin-sidebar").count() == 1
    assert await page.locator("html").evaluate(
        "element => element.classList.contains('dark')"
    )
    assert await page.locator(".admin-sidebar").evaluate(
        "element => getComputedStyle(element).backgroundColor"
    ) == "rgb(20, 20, 20)"
    assert await page.locator(".platform-shell").evaluate(
        "element => getComputedStyle(element).backgroundColor"
    ) == "rgb(3, 3, 3)"
    await page.get_by_role("menuitem", name="系统概览").wait_for()
    assert await page.locator("#tags-view-container").count() == 1
    await page.get_by_role("button", name="折叠侧边栏").click()
    await page.wait_for_timeout(250)
    assert await page.locator(".platform-shell__sidebar").evaluate(
        "element => element.getBoundingClientRect().width === 56"
    )
    icon_layout = await page.locator(".admin-sidebar .svg-icon").evaluate_all(
        """elements => elements.map(element => {
            const rect = element.getBoundingClientRect()
            const sidebar = element.closest('.admin-sidebar').getBoundingClientRect()
            const width = Math.max(0, Math.min(rect.right, sidebar.right, innerWidth) - Math.max(rect.left, sidebar.left, 0))
            const height = Math.max(0, Math.min(rect.bottom, sidebar.bottom, innerHeight) - Math.max(rect.top, sidebar.top, 0))
            return { visible: getComputedStyle(element).visibility !== 'hidden' && getComputedStyle(element).display !== 'none' && rect.width > 0 && rect.height > 0, width, height }
        }).filter(item => item.visible)"""
    )
    assert len(icon_layout) >= 1
    assert all(icon["width"] >= 14 and icon["height"] >= 14 for icon in icon_layout)
    await assert_no_overflow(page)
    assert console_errors == [], console_errors
    assert page_errors == [], page_errors
    await take_screenshot(page, "platform-shell-admin-1280x800-dark.png")
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
    assert await page.locator(".platform-mobile-drawer .platform-account-menu").count() == 0
    assert await page.locator(".platform-mobile-drawer .platform-collapse-button").count() == 0
    assert await page.locator(".platform-mobile-drawer .admin-sidebar__collapse").count() == 0
    await drawer.locator(":focus").wait_for()
    assert await drawer.locator(":focus").count() == 1
    await page.mouse.click(width - 5, height / 2)
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
    await assert_mobile_profile_layout(page)
    await assert_no_overflow(page)
    await take_screenshot(page, f"platform-shell-mobile-{width}x{height}-light.png")
    assert console_errors == []
    assert page_errors == []
    await context.close()


async def check_model_resource_drawer(browser: Browser) -> None:
    context, page, console_errors, page_errors = await new_page(
        browser, TABLET_VIEWPORT
    )
    await page.goto(f"{BASE_URL}/ai/model")
    await page.locator(".model-table").wait_for()
    menu_button = page.get_by_role("button", name="打开主菜单")
    await menu_button.wait_for()
    await menu_button.click()
    drawer = page.locator(".platform-mobile-drawer")
    await page.wait_for_function(
        "document.querySelector('.platform-mobile-drawer')?.classList.contains('ant-drawer-open')"
    )
    for label in ("智能对话", "开发接入", "用量记录"):
        assert await drawer.get_by_role("link", name=label, exact=True).count() == 1
    assert await drawer.get_by_role("button", name="模型中心", exact=True).count() == 1
    await page.mouse.click(TABLET_VIEWPORT["width"] - 5, TABLET_VIEWPORT["height"] / 2)
    await page.wait_for_function(
        "!document.querySelector('.platform-mobile-drawer')?.classList.contains('ant-drawer-open')"
    )
    rate_row = page.locator(".model-table tbody tr").filter(has_text=LONG_MODEL_NAME)
    await rate_row.get_by_role("button", name="费率配置").click()
    modal = page.locator(".ant-modal").last
    await modal.get_by_text("费率配置", exact=True).wait_for()
    await modal.evaluate(
        """async element => {
            const completed = Promise.all(element.getAnimations({ subtree: true }).map(
                animation => animation.finished.catch(() => undefined)
            ))
            let timeoutId
            const timeout = new Promise((_, reject) => {
                timeoutId = setTimeout(() => reject(new Error('modal animation exceeded 3000ms')), 3000)
            })
            try {
                await Promise.race([completed, timeout])
            } finally {
                clearTimeout(timeoutId)
            }
        }"""
    )
    assert await modal.evaluate("element => getComputedStyle(element).opacity") == "1"
    modal_box = await modal.bounding_box()
    assert modal_box is not None and modal_box["width"] >= 360 and modal_box["height"] >= 320
    await modal.get_by_role("button", name="创建价格版本").wait_for()
    await assert_no_overflow(page)
    await take_screenshot(page, "platform-model-resource-768x1024-light.png")
    assert console_errors == []
    assert page_errors == []
    await context.close()


async def check_model_dark(browser: Browser) -> None:
    context, page, console_errors, page_errors = await new_page(
        browser, ADMIN_VIEWPORT
    )
    try:
        await page.goto(f"{BASE_URL}/ai/model")
        await page.locator(".model-table").wait_for()
        await page.get_by_role("button", name="切换主题").click()
        await page.locator("html.dark").wait_for()
        await assert_text_is_constrained(page, LONG_MODEL_NAME)
        rate_row = page.locator(".model-table tbody tr").filter(has_text=LONG_MODEL_NAME)
        await rate_row.get_by_role("button", name="费率配置").click()
        modal = page.locator(".ant-modal").last
        await modal.get_by_text("费率配置", exact=True).wait_for()
        await page.wait_for_function(
            """() => {
                const modal = document.querySelector('.ant-modal:last-of-type')
                return modal && getComputedStyle(modal).opacity === '1'
            }"""
        )
        assert await modal.locator(".ant-modal-content").evaluate(
            "element => getComputedStyle(element).backgroundColor"
        ) == "rgb(38, 38, 38)"
        await modal.get_by_role("button", name="创建价格版本").wait_for()
        await assert_no_overflow(page)
        assert console_errors == [], console_errors
        assert page_errors == [], page_errors
        await take_screenshot(page, "platform-model-rate-1280x800-dark.png")
        assert console_errors == [], console_errors
        assert page_errors == [], page_errors
    finally:
        await context.close()


async def check_model_reenable(browser: Browser) -> None:
    # Keep this scenario independent when it is run outside the full suite.
    reset_model_lifecycle()
    context, page, console_errors, page_errors = await new_page(
        browser, ADMIN_VIEWPORT
    )
    try:
        await page.goto(f"{BASE_URL}/ai/model")
        await page.locator(".model-table").wait_for()
        row = page.locator(".model-table tbody tr").filter(has_text=LONG_MODEL_NAME)
        await row.wait_for()
        assert await row.get_by_text("已停用", exact=True).count() == 1
        assert await row.get_by_text("已绑定", exact=True).count() == 1
        await row.get_by_role("button", name="重新启用").click()
        await page.get_by_text("模型已重新启用", exact=True).wait_for()
        await page.get_by_role("button", name="刷新平台模型").click()
        await row.get_by_text("已发布", exact=True).wait_for()
        assert await row.get_by_text("已停用", exact=True).count() == 0
        assert await page.locator(".platform-topbar").count() == 0
        await assert_no_overflow(page)
        assert console_errors == [], console_errors
        assert page_errors == [], page_errors
    finally:
        reset_model_lifecycle()
        await context.close()


async def check_application_api_docs(browser: Browser) -> None:
    context, page, console_errors, page_errors = await new_page(
        browser, ADMIN_VIEWPORT
    )
    try:
        await page.goto(f"{BASE_URL}/ai/application")
        await page.locator(".application-row").wait_for()
        application_query = urlparse(page.url).query
        assert "applicationId=" in application_query
        assert "tab=overview" in application_query
        await page.get_by_role("button", name="API 说明").click()
        await page.get_by_role("heading", name="API 说明").wait_for()
        assert "view=docs" in urlparse(page.url).query
        await page.get_by_role("navigation", name="接口目录").wait_for()
        await page.get_by_text("POST", exact=True).first.wait_for()
        await page.get_by_text("/v1/chat/completions", exact=True).wait_for()
        assert await page.get_by_text("支持流式", exact=True).count() == 1
        assert await page.get_by_text("Bearer <DEVELOPER_API_KEY>", exact=True).count() == 1
        assert await page.locator(".api-docs-content pre").count() >= 4
        await page.get_by_role("button", name="返回开发接入").click()
        await page.locator(".application-row").wait_for()
        returned_query = urlparse(page.url).query
        assert "view=docs" not in returned_query
        assert "applicationId=" in returned_query
        assert "tab=overview" in returned_query
        await assert_no_overflow(page)
        assert console_errors == [], console_errors
        assert page_errors == [], page_errors
    finally:
        await context.close()


async def check_application_usage_dark(browser: Browser) -> None:
    context, page, console_errors, page_errors = await new_page(
        browser, ADMIN_VIEWPORT, color_scheme="dark"
    )
    try:
        await page.goto(f"{BASE_URL}/ai/application")
        await page.locator(".application-row").wait_for()
        assert await page.locator(".platform-topbar").count() == 0
        sidebar_width = await page.locator(".platform-sidebar").evaluate(
            "element => element.getBoundingClientRect().width"
        )
        assert 225 <= sidebar_width <= 235, sidebar_width
        await page.locator("html.dark").wait_for()
        await page.locator(".overview-panel").wait_for()
        await page.get_by_text("调用入口", exact=True).wait_for()

        await page.get_by_role("tab", name="访问密钥").click()
        await page.locator(".key-row").wait_for()

        await page.get_by_role("button", name="API 说明").click()
        await page.get_by_role("heading", name="API 说明").wait_for()
        docs_query = urlparse(page.url).query
        assert "view=docs" in docs_query
        assert "tab=keys" in docs_query
        await page.get_by_role("button", name="返回开发接入").click()
        await page.wait_for_url("**/ai/application*")
        returned_query = urlparse(page.url).query
        assert "view=docs" not in returned_query
        assert "tab=keys" in returned_query
        await page.locator(".key-row").wait_for()

        await page.get_by_role("tab", name="调用记录").click()
        usage_rows_locator = page.locator(
            ".usage-list .ant-table-tbody > tr.ant-table-row"
        )
        await usage_rows_locator.first.wait_for()
        assert await usage_rows_locator.count() >= 12
        await assert_text_is_constrained(page, LONG_APPLICATION_NAME)
        assert await page.locator(".developer-grid .workspace-panel").first.evaluate(
            "element => getComputedStyle(element).backgroundColor !== 'rgba(0, 0, 0, 0)'"
        )
        await assert_no_overflow(page)
        assert console_errors == [], console_errors
        assert page_errors == [], page_errors
        await take_screenshot(page, "platform-application-1280x800-dark.png")

        await page.goto(f"{BASE_URL}/ai/usage")
        await page.locator(".usage-table-wrap").wait_for()
        rows = page.locator(".usage-table-wrap .ant-table-tbody > tr.ant-table-row")
        await rows.first.wait_for()
        assert await rows.count() >= 12
        assert await page.locator(".usage-table-wrap").evaluate(
            "element => getComputedStyle(element).backgroundColor !== 'rgba(0, 0, 0, 0)'"
        )
        await assert_no_overflow(page)
        assert console_errors == [], console_errors
        assert page_errors == [], page_errors
        await take_screenshot(page, "platform-usage-1280x800-dark.png")
        assert console_errors == [], console_errors
        assert page_errors == [], page_errors
    finally:
        await context.close()


async def check_auth_dark(browser: Browser) -> None:
    context, page, console_errors, page_errors = await new_page(
        browser, ADMIN_VIEWPORT, color_scheme="dark", authenticated=False
    )
    try:
        assert not await context.cookies(BASE_URL)
        await page.goto(f"{BASE_URL}/login")
        await page.get_by_role("heading", name="登录 智衡 AI").wait_for()
        await page.locator("html.dark").wait_for()
        assert await page.locator(".auth-shell").evaluate(
            "element => getComputedStyle(element).backgroundColor !== 'rgba(0, 0, 0, 0)'"
        )
        await assert_no_overflow(page)
        assert console_errors == [], console_errors
        assert page_errors == [], page_errors
        await take_screenshot(page, "platform-auth-login-1280x800-dark.png")
        assert console_errors == [], console_errors
        assert page_errors == [], page_errors
    finally:
        await context.close()


async def check_permission_landings(browser: Browser) -> None:
    expectations = {
        "chat": "/ai/chat",
        "model": "/ai/model",
        "application": "/ai/application",
        "usage": "/ai/usage",
        "none": "/system/overview",
    }
    for access, expected_path in expectations.items():
        context, page, console_errors, page_errors = await new_page(
            browser, {"width": 1024, "height": 768}, access
        )
        await page.goto(f"{BASE_URL}/ai/unavailable")
        await page.wait_for_url(f"**{expected_path}")
        assert urlparse(page.url).path == expected_path
        assert console_errors == []
        assert page_errors == []
        await context.close()


async def check_dark_theme(browser: Browser) -> None:
    context, page, console_errors, page_errors = await new_page(browser, DESKTOP_VIEWPORT)
    await page.goto(f"{BASE_URL}/ai/chat")
    await page.get_by_text("从一个问题开始", exact=True).wait_for()
    await page.get_by_role("button", name="切换主题").click()
    await page.locator("html.dark").wait_for()
    assert await page.locator(".platform-sidebar").evaluate(
        "element => getComputedStyle(element).backgroundColor"
    ) == "rgb(20, 20, 20)"
    assert await page.locator(".platform-shell").evaluate(
        "element => getComputedStyle(element).backgroundColor"
    ) == "rgb(3, 3, 3)"
    assert await page.locator(".platform-topbar").count() == 0
    surface = page.locator(".chat-toolbar .ant-select-selector")
    await surface.wait_for()
    assert await surface.evaluate(
        "element => getComputedStyle(element).backgroundColor"
    ) == "rgb(31, 31, 31)"
    session_row = page.locator(".recent-session").filter(has_text=LONG_SESSION_TITLE)
    await session_row.get_by_role("button", name="更多操作").click()
    await page.get_by_text("重命名", exact=True).click()
    modal = page.locator(".ant-modal").last
    await modal.wait_for()
    assert await modal.locator(".ant-modal-content").evaluate(
        "element => getComputedStyle(element).backgroundColor"
    ) == "rgb(38, 38, 38)"
    await assert_no_overflow(page)
    assert console_errors == []
    assert page_errors == []
    await take_screenshot(page, "platform-shell-chat-1440x900-dark.png")
    assert console_errors == []
    assert page_errors == []
    await context.close()


async def run_platform_checks(browser: Browser) -> dict[str, str]:
    reset_model_lifecycle()
    await check_desktop(browser)
    await check_admin(browser)
    await check_mobile(browser, **MOBILE_VIEWPORT)
    await check_model_resource_drawer(browser)
    await check_dark_theme(browser)
    await check_model_dark(browser)
    await check_model_reenable(browser)
    await check_application_api_docs(browser)
    await check_application_usage_dark(browser)
    await check_auth_dark(browser)
    await check_permission_landings(browser)
    hashes = {filename: screenshot_hash(filename) for filename in PLATFORM_STABLE_SCREENSHOTS}
    print(f"platform stable screenshot hashes: {hashes}")
    return hashes


async def main() -> None:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            await check_screenshot_helper_behavior(browser)
            first_hashes = await run_platform_checks(browser)
            second_hashes = await run_platform_checks(browser)
            assert second_hashes == first_hashes, (first_hashes, second_hashes)
            print("platform screenshot reproducibility passed (2 rounds)")
        finally:
            await browser.close()
    print("platform shell browser checks passed")


if __name__ == "__main__":
    asyncio.run(main())
