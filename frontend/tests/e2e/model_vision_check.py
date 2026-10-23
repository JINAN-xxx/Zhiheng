"""Model vision settings and the resulting chat attachment behavior."""

import asyncio
import io
import re
from urllib.parse import urlparse

from PIL import Image
from playwright.async_api import async_playwright

import platform_shell_check as shell


async def check_vision_settings(browser, width=1280, editable=True, fail_save=False, queryable=True):
    context, page, console_errors, page_errors = await shell.new_page(browser, {"width": width, "height": 800})
    model = {
        "modelId": shell.MODEL_IDS["active"], "modelCode": "vision-test", "displayName": "视觉测试模型",
        "modelType": "chat", "lifecycleStatus": "active",
        "capabilities": {"vision": False, "streaming": True, "reasoning": True, "tools": True,
                         "dimensionsOverride": False, "returnDocuments": False},
    }
    patches = []
    uploads = []
    detail_reads = 0

    async def handle(route):
        nonlocal detail_reads
        path = "/" + urlparse(route.request.url).path.split("/", 2)[-1]
        if path == "/getInfo" and (not editable or not queryable):
            payload = shell.response_for(path, "GET", "all")
            payload["permissions"] = list(payload["permissions"])
            if not editable:
                payload["permissions"].remove("ai:model:edit")
            if not queryable:
                payload["permissions"].remove("ai:model:query")
        elif path == "/ai/models":
            payload = {"code": 200, "rows": [model], "total": 1}
        elif path == f"/ai/models/{model['modelId']}":
            if route.request.method == "GET":
                detail_reads += 1
                if detail_reads == 3:
                    # Another administrator changed tools while this dialog was open.
                    model["capabilities"]["tools"] = False
            if route.request.method == "PATCH":
                patches.append(route.request.post_data_json)
                if fail_save:
                    await route.fulfill(json={"code": 500, "msg": "测试保存失败"})
                    return
                model.update(patches[-1])
            payload = {"code": 200, "data": model}
        elif path == "/common/files/upload":
            uploads.append(True)
            payload = {"code": 200, "fileId": "vision-test-file", "originalFilename": "vision-test.png"}
        else:
            await route.fallback()
            return
        await route.fulfill(json=payload)

    try:
        await context.route("**/dev-api/**", handle)
        await context.route("**/docker-api/**", handle)
        await page.goto(shell.BASE_URL + "/ai/model")
        row = page.locator(".model-table tbody tr").filter(has_text="vision-test")
        await row.wait_for()
        if width < 761:
            await row.get_by_role("button", name="更多操作").click()
            await page.locator(".ant-dropdown:not(.ant-dropdown-hidden)").wait_for()
            entry = page.get_by_role("menuitem", name="图片理解设置", exact=True)
        else:
            entry = row.get_by_role("button", name="图片理解设置", exact=True)
        if not editable or not queryable:
            assert await entry.count() == 0
            assert patches == []
            return
        await entry.wait_for(state="visible", timeout=5000)
        await entry.click()
        dialog = page.get_by_role("dialog", name="图片理解设置", exact=True)
        toggle = dialog.get_by_role("switch", name="支持图片理解", exact=True)
        await toggle.wait_for()
        assert await toggle.get_attribute("aria-checked") == "false"
        await toggle.click()
        if not fail_save:
            await shell.take_screenshot(page, f"model-vision-settings-{width}.png")
        await dialog.get_by_role("button", name=re.compile(r"^保\s*存$")).click()
        if fail_save:
            await dialog.get_by_text("保存失败，请重试", exact=True).wait_for()
            assert await toggle.get_attribute("aria-checked") == "true"
            assert model["capabilities"]["vision"] is False
        else:
            await dialog.wait_for(state="hidden")
            assert patches == [{"capabilities": {"streaming": True, "vision": True, "reasoning": True, "tools": False}}]
            await page.goto(shell.BASE_URL + "/ai/chat")
            await page.locator(".chat-empty-state h2").wait_for()
            png = io.BytesIO()
            Image.new("RGB", (64, 64), "red").save(png, format="PNG")
            await page.locator('input[type="file"]').set_input_files({"name": "vision-test.png", "mimeType": "image/png", "buffer": png.getvalue()})
            await page.locator(".queued-file-status").get_by_text("已上传", exact=True).wait_for()
            assert uploads == [True]
            assert await page.get_by_text("当前模型未启用视觉能力", exact=False).count() == 0
        assert page_errors == [], page_errors
        assert console_errors == [], console_errors
        await shell.assert_no_overflow(page)
    finally:
        await context.close()


async def main():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            for width, editable, fail_save, queryable in ((1280, True, False, True), (375, True, False, True), (1280, False, False, True), (375, False, False, True), (1280, True, True, True), (1280, True, False, False)):
                await shell.run_browser_check(check_vision_settings, browser, width, editable, fail_save, queryable)
                print(f"PASS vision settings width={width} editable={editable} fail_save={fail_save} queryable={queryable}")
            for model_type, enable_vision in (("chat", True), ("chat", False), ("embedding", False)):
                await shell.run_browser_check(check_onboarding_vision, browser, model_type, enable_vision)
                print(f"PASS onboarding {model_type} vision={enable_vision}")
        finally:
            await browser.close()


async def check_onboarding_vision(browser, model_type="chat", enable_vision=True):
    context, page, console_errors, page_errors = await shell.new_page(browser, shell.ADMIN_VIEWPORT)
    created = []
    model_id = "mdl_33333333333343338333333333333333"

    async def handle(route):
        path = "/" + urlparse(route.request.url).path.split("/", 2)[-1]
        method = route.request.method
        if path == "/getInfo":
            payload = shell.response_for(path, method, "all")
            payload["permissions"] = [*payload["permissions"], "ai:engine:credential:list", "ai:engine:credential:add", "ai:engine:deployment:list", "ai:engine:deployment:add"]
        elif path == "/ai/model-engine/providers":
            payload = {"code": 200, "data": [{"provider": "OpenAI_Compatible", "displayName": "Compatible", "litellmProvider": "openai",
                "credentialFields": [{"fieldKey": "api_base", "label": "API Base", "fieldType": "text", "required": True},
                                     {"fieldKey": "api_key", "label": "API Key", "fieldType": "password", "required": True}]}]}
        elif path == "/ai/model-engine/credentials":
            payload = {"code": 200, "data": []}
        elif path == "/ai/model-engine/test-connection":
            payload = {"code": 200, "data": {"success": True, "latencyMs": 100}}
        elif path == "/ai/model-engine/deployments":
            payload = {"code": 200, "data": [] if method == "GET" else {**route.request.post_data_json, "provider": "openai", "deploymentId": "vision-deployment"}}
        elif path == "/ai/models" and method == "POST":
            created.append(route.request.post_data_json)
            payload = {"code": 200, "data": {**created[-1], "modelId": model_id, "lifecycleStatus": "draft"}}
        elif path == f"/ai/models/{model_id}/binding":
            payload = {"code": 200, "data": {**route.request.post_data_json, "version": 1}}
        elif path in (f"/ai/models/{model_id}", f"/ai/models/{model_id}/publish"):
            payload = {"code": 200, "data": {**created[-1], "modelId": model_id, "lifecycleStatus": "active" if method == "POST" else "draft"}}
        else:
            await route.fallback()
            return
        await route.fulfill(json=payload)

    try:
        await context.route("**/dev-api/**", handle)
        await context.route("**/docker-api/**", handle)
        await page.goto(shell.BASE_URL + "/ai/model")
        await page.get_by_role("button", name="接入模型").click()
        drawer = page.locator(".model-onboarding-drawer")
        toggle = drawer.get_by_role("switch", name="支持图片理解", exact=True)
        assert await toggle.is_visible(), "接入对话模型时应提供图片理解开关"
        assert await toggle.get_attribute("aria-checked") == "false"
        await toggle.click()
        await drawer.get_by_label("模型名称", exact=True).fill("changed-model")
        assert await toggle.get_attribute("aria-checked") == "false"
        await toggle.click()
        await drawer.get_by_text("向量", exact=True).click()
        assert await toggle.count() == 0
        if model_type == "chat":
            await drawer.get_by_text("对话", exact=True).click()
            assert await toggle.get_attribute("aria-checked") == "false"
        await drawer.get_by_label("模型名称", exact=True).fill("Qwen/Qwen3.8-27B" if model_type == "chat" else "Qwen/Qwen3-Embedding-8B")
        if enable_vision:
            await toggle.click()
        await drawer.get_by_placeholder("输入API Key", exact=True).fill("browser-test-only")
        await drawer.get_by_role("button", name="测试连接").click()
        await drawer.get_by_text("连接测试通过", exact=False).wait_for()
        if model_type == "chat" and enable_vision:
            await shell.take_screenshot(page, "model-vision-onboarding.png")
        await drawer.get_by_role("button", name="接入模型").click()
        await drawer.get_by_text("接入成功", exact=True).wait_for()
        assert len(created) == 1
        assert created[0]["modelType"] == model_type
        expected = {"streaming": True, "vision": enable_vision, "reasoning": False, "tools": False} if model_type == "chat" else {"dimensionsOverride": False}
        assert created[0]["capabilities"] == expected
        assert console_errors == [], console_errors
        assert page_errors == [], page_errors
    finally:
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())
