import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import platform_shell_check as checks
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

LANDINGS = {
    "chat": ("/ai/chat", '<section class="chat-empty-state"><h2>Ready</h2></section>'),
    "model": ("/ai/model", '<main class="ai-model-center">Ready</main>'),
    "application": ("/ai/application", '<main class="developer-api-page">Ready</main>'),
    "usage": ("/ai/usage", '<main class="usage-ledger-page">Ready</main>'),
    "skill": ("/extensions/skill", '<main class="skill-management-page">Ready</main>'),
    "none": ("/system/overview", '<main class="usage-ledger-page">Ready</main>'),
}


class FailedRequest(SimpleNamespace):
    __hash__ = object.__hash__


class PermissionLandingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.contexts = []
        self.closed_contexts = []
        self.attempts = []
        self.diagnostics = TemporaryDirectory()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()
        self.diagnostics.cleanup()

    async def run_landings(self, failures=(), wrong_path=False, wrong_page=False):
        async def fixture_page(browser, viewport, access):
            context = await browser.new_context(viewport=viewport)
            self.contexts.append(context)
            self.attempts.append(access)
            context.on("close", lambda _: self.closed_contexts.append(context))
            page = await context.new_page()
            page.set_default_timeout(150)
            console_errors, page_errors = [], []
            callbacks = {}
            original_on = page.on

            def on(event, callback):
                callbacks.setdefault(event, []).append(callback)
                return original_on(event, callback)

            page.on = on
            expected_path, body = LANDINGS[access]
            actual_path = "/wrong" if wrong_path else expected_path
            if wrong_page:
                body = "<main>Unrelated page</main>"

            async def document(route):
                await route.fulfill(
                    content_type="text/html",
                    body=(
                        f'<script>history.replaceState(null, "", "{actual_path}")</script>{body}'
                    ),
                )

            await context.route("**/*", document)
            original_goto = page.goto
            failure = (
                failures[len(self.attempts) - 1]
                if len(self.attempts) <= len(failures)
                else None
            )

            async def goto(url):
                if failure:
                    request = FailedRequest(
                        url=f"{checks.BASE_URL}/src/main.js",
                        resource_type="script",
                        failure="net::ERR_NETWORK_CHANGED",
                        method="GET",
                    )
                    api_paths = {
                        "api": "/dev-api/getRouters",
                        "api_started": "/dev-api/getInfo",
                        "business": "/dev-api/ai/chat/session/list",
                        "bootstrap_get": "/dev-api/transport/crypto/frontend-config",
                        "bootstrap_post": "/captchaImage",
                    }
                    if failure in api_paths:
                        api_request = FailedRequest(
                            url=f"{checks.BASE_URL}{api_paths[failure]}",
                            method="POST" if failure == "bootstrap_post" else "GET",
                        )
                        for callback in callbacks.get("request", []):
                            callback(api_request)
                    if failure == "other_network":
                        request.failure = "net::ERR_CONNECTION_REFUSED"
                    if failure != "timeout":
                        for callback in callbacks.get("requestfailed", []):
                            callback(request)
                        console_errors.append(
                            "Failed to load resource: net::ERR_NETWORK_CHANGED"
                        )
                    if failure == "javascript":
                        page_errors.append("TypeError: application bootstrap failed")
                    if failure == "api":
                        console_errors.append("API failed")
                    if failure == "console":
                        console_errors.append("Unexpected application error")
                    raise PlaywrightTimeoutError("injected startup failure")
                return await original_goto(url)

            page.goto = goto
            checks.observe_check_page(context, page, console_errors, page_errors)
            return context, page, console_errors, page_errors

        output = io.StringIO()
        with (
            patch.object(checks, "new_page", side_effect=fixture_page),
            patch.object(checks, "SCREENSHOT_DIR", Path(self.diagnostics.name)),
            redirect_stdout(output),
        ):
            await checks.check_permission_landings(self.browser)
        return output.getvalue()

    async def test_network_change_before_auth_retries_once_and_closes_contexts(self):
        output = await self.run_landings(failures=("network",))
        self.assertEqual(self.attempts, ["chat", *LANDINGS])
        self.assertEqual(self.closed_contexts, self.contexts)
        self.assertIn("net::ERR_NETWORK_CHANGED", output)
        self.assertIn("retry", output)
        reports = list(Path(self.diagnostics.name).glob("*.json"))
        self.assertEqual(len(reports), 1)
        report = json.loads(reports[0].read_text())
        self.assertTrue(report["retry"])
        self.assertEqual(report["attempt"], 1)
        self.assertEqual(report["pages"][0]["api_events"], [])

    async def test_repeated_network_change_fails_after_two_attempts(self):
        with self.assertRaises((AssertionError, PlaywrightTimeoutError)):
            await self.run_landings(failures=("network", "network"))
        self.assertEqual(self.attempts, ["chat", "chat"])
        self.assertEqual(self.closed_contexts, self.contexts)

    async def test_wrong_landing_fails_without_retry(self):
        with self.assertRaises((AssertionError, PlaywrightTimeoutError)):
            await self.run_landings(wrong_path=True)
        self.assertEqual(self.attempts, ["chat"])
        self.assertEqual(self.closed_contexts, self.contexts)

    async def test_expected_url_without_expected_page_fails(self):
        with self.assertRaises((AssertionError, PlaywrightTimeoutError)):
            await self.run_landings(wrong_page=True)
        self.assertEqual(self.attempts, ["chat"])

    async def test_script_error_is_not_retried(self):
        with self.assertRaises((AssertionError, PlaywrightTimeoutError)):
            await self.run_landings(failures=("javascript",))
        self.assertEqual(self.attempts, ["chat"])

    async def test_auth_request_start_prevents_retry(self):
        for failure in ("api", "api_started", "business", "bootstrap_post"):
            with self.subTest(failure=failure):
                self.attempts.clear()
                with self.assertRaises((AssertionError, PlaywrightTimeoutError)):
                    await self.run_landings(failures=(failure,))
                self.assertEqual(self.attempts, ["chat"])

    async def test_read_only_bootstrap_config_allows_startup_retry(self):
        await self.run_landings(failures=("bootstrap_get",))
        self.assertEqual(self.attempts, ["chat", *LANDINGS])

    async def test_other_network_or_console_errors_are_not_retried(self):
        for failure in ("other_network", "console"):
            with self.subTest(failure=failure):
                self.attempts.clear()
                with self.assertRaises((AssertionError, PlaywrightTimeoutError)):
                    await self.run_landings(failures=(failure,))
                self.assertEqual(self.attempts, ["chat"])

    async def test_unexplained_timeout_is_not_retried(self):
        with self.assertRaises((AssertionError, PlaywrightTimeoutError)):
            await self.run_landings(failures=("timeout",))
        self.assertEqual(self.attempts, ["chat"])


if __name__ == "__main__":
    unittest.main()
