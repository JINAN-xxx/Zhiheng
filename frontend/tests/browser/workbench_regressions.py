"""Current workbench layout and scopes over controlled HTTP, with no business proxy."""

import asyncio
import contextlib
import os
import re
import unittest
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from developer_regressions import DeveloperBoundary
from playwright.async_api import expect


class WorkbenchRegressions(DeveloperBoundary):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.page.set_viewport_size({"width": 1440, "height": 1050})
        self.user = {
            "id": "user-1",
            "username": "admin",
            "is_admin": True,
            "permissions": [],
        }
        self.calls = []
        self.hold_30 = False
        self.requested = asyncio.Event()
        self.release = asyncio.Event()
        self.empty = False
        self.unavailable = False
        self.error_status = 0
        self.evidence = Path(
            os.environ.get("WORKBENCH_EVIDENCE", "/tmp/workbench-evidence")
        )
        self.evidence.mkdir(parents=True, exist_ok=True)

    async def asyncTearDown(self):
        self.release.set()
        await super().asyncTearDown()

    async def respond(self, route):
        request = route.request
        path = urlparse(request.url).path
        if path == "/api/auth/me":
            return await route.fulfill(json=self.user)
        if path == "/api/admin/notices/published":
            return await route.fulfill(
                json={"items": [], "total": 0, "page": 1, "page_size": 20}
            )
        if path == "/api/overview":
            query = parse_qs(urlparse(request.url).query)
            period = int(query.get("days", ["7"])[0])
            scope = query.get("scope", ["mine"])[0]
            self.calls.append((scope, period))
            if self.error_status:
                return await route.fulfill(
                    status=self.error_status,
                    json={
                        "error": {
                            "code": "overview_unavailable",
                            "message": "统计服务暂时不可用",
                        }
                    },
                )
            if self.hold_30 and period == 30:
                self.requested.set()
                await self.release.wait()
            recent = [
                {
                    "kind": kind,
                    "id": kind + "-1",
                    "name": name,
                    "status": status,
                    "occurred_at": "2026-11-16T02:24:00Z",
                    "action": "used",
                }
                for kind, name, status in [
                    ("model", "bge-m3", "available"),
                    ("agent", "科研助理小助手", "published"),
                    ("knowledge", "个人知识库", "processing"),
                    ("skill", "团队资料整理", "enabled"),
                ]
            ]
            amounts = [1280, 1560, 1390, 1870, 1720, 2184, 1960]
            metrics = {
                "today_calls": 12840 if scope == "platform" else 42,
                "yesterday_calls": 11400,
                "successful_calls": 1240,
                "failed_calls": 10,
                "cancelled_calls": 2,
                "today_known_tokens": 2460000,
                "today_unconfirmed_calls": 2,
                "trend": [
                    {
                        "day": str(
                            date(2026, 11, 16) - timedelta(days=period - 1 - index)
                        ),
                        "calls": amounts[index % 7],
                    }
                    for index in range(period)
                ],
            }
            credits = {
                "enabled": True,
                "monthly_points": "328.600000000000000000",
                "unconfirmed_calls": 0,
            }
            alerts = [
                {
                    "id": "key-1",
                    "level": "warning",
                    "title": "业务 API Key 即将到期",
                    "description": "开发接入 · 建议及时处理",
                    "kind": "application",
                    "resource_id": "app-1",
                    "occurred_at": "2026-11-19T02:00:00Z",
                },
                {
                    "id": "knowledge-1",
                    "level": "info",
                    "title": "个人知识库 · 资料处理中",
                    "description": "后台正在处理资料，可离开页面后继续查看。",
                    "kind": "knowledge",
                    "resource_id": "knowledge-1",
                    "occurred_at": "2026-11-16T02:00:00Z",
                },
                {
                    "id": "recent-failures",
                    "level": "error",
                    "title": "近24小时有 1 次请求失败或中断",
                    "description": "查看调用记录可定位原因，已发生用量按实际记录保留。",
                    "kind": None,
                    "resource_id": None,
                    "occurred_at": None,
                },
            ]
            activities = [
                {**recent[2], "action": "created"},
                {**recent[1], "action": "published"},
                {**recent[3], "action": "created"},
            ]
            if self.empty:
                metrics.update(
                    today_calls=0,
                    yesterday_calls=0,
                    successful_calls=0,
                    failed_calls=0,
                    today_known_tokens=0,
                    today_unconfirmed_calls=0,
                    trend=[{**point, "calls": 0} for point in metrics["trend"]],
                )
                recent, activities, alerts = [], [], []
                credits.update(enabled=False, monthly_points="0")
            if self.unavailable:
                metrics, credits = None, None
                recent, activities, alerts = [], [], []
            with contextlib.suppress(Exception):
                return await route.fulfill(
                    json={
                        "scope": scope,
                        "can_view_platform": self.user["is_admin"],
                        "period_days": period,
                        "timezone": "Asia/Shanghai",
                        "observed_at": "2026-11-16T03:00:00Z",
                        "metrics": metrics,
                        "credits": credits,
                        "recent": recent,
                        "activities": activities,
                        "alerts": alerts,
                    }
                )
            return
        return await super().respond(route)

    async def open(self):
        await self.page.goto(self.origin + "/overview")
        await expect(self.page.get_by_role("region", name="运行指标")).to_be_visible()

    async def test_current_layout_uses_real_response_metrics_and_resource_filters(self):
        await self.open()
        await expect(self.page.get_by_role("region", name="运行指标")).to_contain_text(
            "12,840"
        )
        await expect(self.page.get_by_role("region", name="运行指标")).to_contain_text(
            "2.46M"
        )
        await expect(self.page.get_by_role("region", name="运行指标")).to_contain_text(
            "328.6"
        )
        await expect(self.page.locator(".workbench-chart svg")).to_be_visible()
        self.assertNotIn("¥", await self.page.locator(".workbench").inner_text())
        await expect(self.page.get_by_role("region", name="运行提醒")).to_contain_text(
            "业务 API Key"
        )
        await self.page.get_by_role("button", name="模型", exact=True).click()
        await expect(self.page.locator(".resource-table tbody tr")).to_have_count(1)
        await expect(self.page.locator(".resource-table")).to_contain_text("bge-m3")
        await self.page.get_by_role("button", name="全部资源", exact=True).click()
        await self.page.screenshot(path=str(self.evidence / "desktop.png"))

    async def test_period_and_scope_changes_fetch_matching_aggregates(self):
        await self.open()
        await self.page.get_by_role("button", name="近30天", exact=True).click()
        await expect(self.page.locator(".metric").nth(1)).to_contain_text("近 30 日")
        await self.page.get_by_role("button", name="仅本人", exact=True).click()
        await expect(self.page.locator(".metric").first).to_contain_text("42")
        self.assertIn(("mine", 30), self.calls)

    async def test_late_period_response_never_overwrites_the_new_scope(self):
        await self.open()
        self.hold_30 = True
        try:
            await self.page.get_by_role("button", name="近30天", exact=True).click()
            await asyncio.wait_for(self.requested.wait(), timeout=5)
            await self.page.get_by_role("button", name="近7天", exact=True).click()
            await self.page.get_by_role("button", name="仅本人", exact=True).click()
            await expect(self.page.locator(".metric").first).to_contain_text("42")
            self.release.set()
            await expect(self.page.locator(".metric").nth(1)).to_contain_text("近 7 日")
            await expect(self.page.locator(".workbench-scope")).to_contain_text(
                "我的运行统计"
            )
        finally:
            self.release.set()

    async def test_overview_only_role_gets_no_implicit_usage_or_sensitive_shortcuts(
        self,
    ):
        self.user.update(
            is_admin=False,
            permissions=["platform:overview:enabled", "platform:overview:read"],
        )
        self.unavailable = True
        await self.open()
        await expect(self.page.locator(".metric")).to_have_count(4)
        await expect(self.page.locator(".metric").last).to_contain_text(
            "未开放积分统计"
        )
        await expect(
            self.page.get_by_role("button", name="全平台", exact=True)
        ).to_have_count(0)
        await expect(
            self.page.get_by_role("navigation", name="快捷入口")
        ).to_have_count(0)
        self.assertEqual(self.calls, [("mine", 7)])

    async def test_zero_data_is_honest_and_mobile_has_no_horizontal_overflow(self):
        self.empty = True
        await self.page.set_viewport_size({"width": 390, "height": 844})
        await self.open()
        await expect(self.page.locator(".metric").nth(1)).to_contain_text("—")
        await expect(
            self.page.get_by_text("当前暂无运行提醒", exact=True)
        ).to_be_visible()
        self.assertTrue(
            await self.page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        )
        await self.page.screenshot(
            path=str(self.evidence / "mobile.png"), full_page=True
        )
        await self.page.locator(".platform-page").evaluate(
            "node => node.scrollTop = node.scrollHeight"
        )
        await self.page.screenshot(path=str(self.evidence / "mobile-lower.png"))

    async def test_failed_dashboard_read_has_retry_and_does_not_fabricate_zero_metrics(
        self,
    ):
        self.error_status = 503
        await self.open()
        await expect(
            self.page.get_by_text("统计服务暂时不可用", exact=True)
        ).to_be_visible()
        await expect(self.page.locator(".metric>strong").first).to_have_text("—")
        self.error_status = 0
        await self.page.get_by_role("button", name=re.compile(r"重\s*试")).click()
        await expect(self.page.locator(".metric").first).to_contain_text("12,840")


if __name__ == "__main__":
    unittest.main(verbosity=2)
