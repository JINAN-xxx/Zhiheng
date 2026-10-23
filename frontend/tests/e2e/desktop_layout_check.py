"""Desktop workspace layout and the interactions moved by its consolidation."""

import asyncio
import re
from urllib.parse import urlparse

from playwright.async_api import async_playwright, expect

import platform_shell_check as shell


async def desktop_page(browser, width=1280, permissions=None, color_scheme="light"):
    context, page, console_errors, page_errors = await shell.new_page(
        browser, {"width": width, "height": 800}, color_scheme=color_scheme
    )
    requests = []

    async def handle(route):
        path = "/" + urlparse(route.request.url).path.split("/", 2)[-1]
        requests.append((route.request.method, path))
        if path == "/getInfo":
            payload = shell.response_for(path, "GET", "all")
            payload["permissions"] = permissions if permissions is not None else ["*:*:*"]
            await route.fulfill(json=payload)
        else:
            await route.fallback()

    await context.route("**/dev-api/**", handle)
    await context.route("**/docker-api/**", handle)
    return context, page, console_errors, page_errors, requests


async def ready(page):
    await page.wait_for_function(
        "!document.querySelector('.ant-spin-spinning, .ant-spin-blur, #nprogress')"
    )
    await page.evaluate("document.fonts.ready")


async def check_desktop_resources(browser, width=1280, color_scheme="light"):
    context, page, console_errors, page_errors, requests = await desktop_page(
        browser, width, color_scheme=color_scheme
    )
    try:
        await page.goto(shell.BASE_URL + "/ai/model")
        rows = page.locator(".model-table tbody tr.ant-table-row")
        await expect(rows).to_have_count(2)
        await ready(page)
        assert await page.locator("html").evaluate("e => e.classList.contains('dark')") == (color_scheme == "dark")
        table = await page.locator(".model-table").bounding_box()
        name_cell = await rows.first.locator("td").first.bounding_box()
        assert table["y"] <= 210, table
        assert name_cell["width"] >= 300, name_cell
        await expect(page.locator(".recent-sessions")).to_have_count(0)
        toggle = page.get_by_role("button", name="展开最近会话", exact=True)
        await toggle.click()
        await expect(page.locator(".recent-sessions")).to_be_visible()
        await expect(page.get_by_role("button", name="收起最近会话", exact=True)).to_have_attribute("aria-expanded", "true")
        await page.get_by_role("button", name="收起最近会话", exact=True).click()
        await expect(page.locator(".recent-sessions")).to_have_count(0)

        refresh = page.get_by_role("button", name="刷新平台模型", exact=True)
        await expect(refresh).to_have_count(1)
        # There must be no second, generically labelled refresh outside the table.
        await expect(page.get_by_role("button", name=re.compile("刷新"))).to_have_count(1)
        count = requests.count(("GET", "/ai/models"))
        async with page.expect_response(lambda r: urlparse(r.url).path.endswith("/ai/models")):
            await refresh.click()
        await ready(page)
        assert requests.count(("GET", "/ai/models")) == count + 1

        active_row = rows.filter(has_text="workspace-chat-active")
        await expect(active_row.get_by_role("button", name="连接测试", exact=True)).to_have_text("测试")
        await expect(active_row.get_by_role("button", name="停用", exact=True)).to_have_count(0)
        await active_row.get_by_role("button", name="更多操作", exact=True).click()
        await page.get_by_role("menuitem", name="停用", exact=True).click()
        dialog = page.get_by_role("dialog")
        await expect(dialog).to_be_visible()
        await dialog.get_by_role("button", name=re.compile(r"取\s*消")).click()
        assert not any(method != "GET" for method, _ in requests), requests
        await page.mouse.move(0, 0)
        await shell.assert_no_overflow(page)
        await shell.take_screenshot(page, f"desktop-model-{width}-{color_scheme}.png")

        await page.get_by_role("tab", name="凭据管理", exact=True).click()
        await ready(page)
        await expect(page.get_by_role("button", name="刷新服务商凭据", exact=True)).to_have_count(1)
        await expect(page.get_by_role("button", name=re.compile("刷新"))).to_have_count(1)
        await expect(page.get_by_role("button", name=re.compile("新增凭据"))).to_have_count(1)

        for path, selector in (("application", ".developer-grid"), ("usage", ".usage-content")):
            await page.goto(shell.BASE_URL + "/ai/" + path)
            await page.locator(selector).wait_for()
            await ready(page)
            title = await page.locator(".workspace-toolbar__title").bounding_box()
            content = await page.locator(selector).bounding_box()
            padding = await page.locator(selector).evaluate("e => parseFloat(getComputedStyle(e).paddingLeft)")
            assert abs(title["x"] - content["x"] - padding) <= 1
            await expect(page.locator(".recent-sessions")).to_have_count(0)
            await shell.assert_no_overflow(page)
            await shell.take_screenshot(page, f"desktop-{path}-{width}-{color_scheme}.png")

        await page.get_by_role("link", name="智能对话", exact=True).click()
        await expect(page.locator(".recent-sessions")).to_be_visible()
        assert console_errors == [], console_errors
        assert page_errors == [], page_errors
    finally:
        await context.close()


async def check_desktop_composer(browser, width=1280, color_scheme="light"):
    context, page, console_errors, page_errors, _ = await desktop_page(
        browser, width, color_scheme=color_scheme
    )
    try:
        await page.goto(shell.BASE_URL + "/ai/chat")
        await page.locator(".chat-empty-state h2").wait_for()
        await ready(page)
        assert await page.locator("html").evaluate("e => e.classList.contains('dark')") == (color_scheme == "dark")
        composer = page.locator(".composer")
        model_select = page.locator(".chat-toolbar .model-select")
        await expect(model_select).to_have_count(1)
        await expect(composer.locator(".model-select")).to_have_count(0)
        await expect(page.locator(".model-select")).to_have_count(1)
        await expect(composer.locator(".model-capability")).to_have_text("文本与文档")
        await expect(composer.locator(".file-guidance")).to_have_count(0)
        preferences = page.locator(".chat-toolbar").get_by_role("button", name="对话偏好", exact=True)
        await expect(preferences).to_have_count(1)
        await expect(composer.get_by_role("button", name="对话偏好", exact=True)).to_have_count(0)
        await preferences.click()
        dialog = page.get_by_role("dialog", name="对话偏好", exact=True)
        await expect(dialog).to_be_visible()
        await dialog.get_by_role("button", name=re.compile(r"取\s*消")).click()
        send = composer.get_by_role("button", name="发送", exact=True)
        await expect(send).to_be_disabled()
        await composer.locator("textarea").fill("检查桌面输入区")
        await expect(send).to_be_enabled()
        # The model choice stays at the workspace's upper-right corner.
        select_box = await model_select.bounding_box()
        workspace_box = await page.locator(".chat-workspace").bounding_box()
        assert 0 <= select_box["y"] - workspace_box["y"] <= 20
        preferences_box = await preferences.bounding_box()
        assert 0 <= preferences_box["y"] - workspace_box["y"] <= 20
        assert 4 <= preferences_box["x"] - select_box["x"] - select_box["width"] <= 12
        right_gap = workspace_box["x"] + workspace_box["width"] - preferences_box["x"] - preferences_box["width"]
        assert 20 <= right_gap <= 28, (preferences_box, workspace_box)
        await shell.assert_no_overflow(page)
        await shell.take_screenshot(page, f"desktop-chat-{width}-{color_scheme}.png")
        assert console_errors == [], console_errors
        assert page_errors == [], page_errors
    finally:
        await context.close()


async def check_read_only_actions(browser):
    context, page, console_errors, page_errors, requests = await desktop_page(
        browser, permissions=["ai:model:list"]
    )
    try:
        await page.goto(shell.BASE_URL + "/ai/model")
        await page.locator(".model-table tbody tr.ant-table-row").first.wait_for()
        await ready(page)
        for label in ("更多操作", "接入模型", "图片理解设置", "费率配置", "连接测试", "展开最近会话"):
            await expect(page.get_by_role("button", name=label, exact=True)).to_have_count(0)
        await expect(page.locator(".workspace-toolbar").get_by_role("button")).to_have_count(1)
        await expect(page.get_by_role("tab", name="凭据管理", exact=True)).to_have_count(0)
        assert not any(method != "GET" for method, _ in requests), requests
        assert console_errors == [], console_errors
        assert page_errors == [], page_errors
    finally:
        await context.close()


async def check_refresh_states(browser, tab="platform"):
    context, page, console_errors, page_errors, requests = await desktop_page(browser)
    endpoint = "/ai/models" if tab == "platform" else "/ai/model-engine/credentials"
    label = "平台模型" if tab == "platform" else "服务商凭据"
    release = asyncio.Event()
    entered = asyncio.Event()
    state = "fail"

    async def handle(route):
        path = "/" + urlparse(route.request.url).path.split("/", 2)[-1]
        if path != endpoint:
            await route.fallback()
            return
        if state == "hold":
            entered.set()
            await release.wait()
        if state in {"hold", "fail"}:
            await route.fulfill(json={"code": 500, "msg": "受控刷新失败"})
        elif tab == "platform":
            await route.fallback()
        else:
            await route.fulfill(json={"code": 200, "data": [{
                "credentialName": "desktop-test-credential", "provider": "openai",
                "referencedDeploymentCount": 1,
            }]})

    await context.route("**/dev-api/**", handle)
    await context.route("**/docker-api/**", handle)
    try:
        await page.goto(shell.BASE_URL + "/ai/model")
        if tab == "credentials":
            await page.get_by_role("tab", name="凭据管理", exact=True).click()
        await page.get_by_text(label + "加载失败", exact=True).wait_for()
        subtitle = page.locator(".workspace-toolbar__subtitle")
        await expect(subtitle).to_have_count(0)
        state = "success"
        await page.get_by_role("button", name="重新加载", exact=True).click()
        await expect(subtitle).to_contain_text("2 个平台模型" if tab == "platform" else "1 个服务商凭据")
        await ready(page)
        previous_summary = await subtitle.inner_text()
        visible_rows = page.locator(".ant-tabs-tabpane-active .model-table tbody tr.ant-table-row")
        row_count = await visible_rows.count()
        state = "hold"
        refresh = page.get_by_role("button", name="刷新" + label, exact=True)
        await refresh.click()
        await asyncio.wait_for(entered.wait(), timeout=5)
        await expect(refresh).to_have_class(re.compile("ant-btn-loading"))
        await expect(subtitle).to_have_text(previous_summary)
        release.set()
        await page.get_by_text("刷新失败，数据可能已过期", exact=True).wait_for()
        await expect(subtitle).to_have_text(previous_summary)
        await expect(visible_rows).to_have_count(row_count)
        await expect(refresh).not_to_have_class(re.compile("ant-btn-loading"))
        state = "success"
        await refresh.click()
        await expect(page.get_by_text("刷新失败，数据可能已过期", exact=True)).to_have_count(0)
        await ready(page)
        if tab == "credentials":
            await visible_rows.first.get_by_role("button", name="更多操作", exact=True).click()
            await expect(page.get_by_role("menuitem", name="删除", exact=True)).to_have_attribute("aria-disabled", "true")
        assert not any(method != "GET" for method, _ in requests), requests
        assert page_errors == [], page_errors
        assert console_errors == [], console_errors
    finally:
        release.set()
        await context.close()


async def check_short_desktop_history(browser):
    context, page, console_errors, page_errors, _ = await desktop_page(browser)
    try:
        await page.set_viewport_size({"width": 1280, "height": 550})
        await page.goto(shell.BASE_URL + "/ai/model")
        await page.get_by_role("button", name="智能体扩展", exact=True).click()
        await page.get_by_role("button", name="展开最近会话", exact=True).click()
        history = page.locator(".recent-sessions__list")
        await expect(history.locator(".recent-session")).to_have_count(2)
        bounds = await history.bounding_box()
        assert bounds["height"] >= 40, bounds
        await history.get_by_role("button", name=re.compile("规划会话")).click()
        await page.wait_for_url("**/ai/chat?sessionId=session-planning")
        await page.get_by_text("这是已保存的会话内容。", exact=True).wait_for()
        admin = await page.get_by_role("button", name="管理后台", exact=True).bounding_box()
        assert admin["y"] + admin["height"] <= 550, admin
        await shell.assert_no_overflow(page)
        assert page_errors == [], page_errors
        assert console_errors == [], console_errors
    finally:
        await context.close()


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            for width, theme in ((1280, "light"), (1440, "light"), (1920, "light"), (1280, "dark")):
                for check in (check_desktop_resources, check_desktop_composer):
                    await shell.run_browser_check(check, browser, width, theme)
                    print(f"PASS {check.__name__} width={width} theme={theme}", flush=True)
            await shell.run_browser_check(check_read_only_actions, browser)
            print("PASS read-only permissions", flush=True)
            for tab in ("platform", "credentials"):
                await shell.run_browser_check(check_refresh_states, browser, tab)
                print(f"PASS refresh loading/failure/recovery {tab}", flush=True)
            await shell.run_browser_check(check_short_desktop_history, browser)
            print("PASS short desktop history navigation", flush=True)
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
