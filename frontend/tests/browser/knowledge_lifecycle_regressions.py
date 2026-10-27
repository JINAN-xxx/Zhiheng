"""Preparation observation, cancellation acknowledgement and lazy permission UI regressions."""

import asyncio
import contextlib
import copy
import re
import unittest
from urllib.parse import urlparse
from playwright.async_api import expect
from files_regressions import FILE
from knowledge_regressions import KnowledgeUIRegressions
from knowledge_preparation_regressions import (
    ChatKnowledgePreparation,
    AgentKnowledgePreparation,
    WorkKnowledgePreparation,
)


class Evidence:
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.traffic = []
        self.responses = []
        self.observations = {}
        self.bad_network = []
        self.page.on(
            "request",
            lambda r: (
                self.traffic.append(
                    {"method": r.method, "url": r.url, "body": r.post_data}
                )
                if "/api/" in r.url
                else None
            ),
        )

        async def response(received):
            if "/api/" not in received.url:
                return
            record = {
                "url": received.url,
                "status": received.status,
                "method": received.request.method,
            }
            try:
                record["json"] = await received.json()
            except Exception:
                pass
            self.responses.append(record)

        self.page.on("response", response)

        async def boundary(route):
            parsed = urlparse(route.request.url)
            if parsed.netloc == urlparse(
                self.origin
            ).netloc and not parsed.path.startswith("/api/"):
                await route.continue_()
            else:
                self.bad_network.append(route.request.url)
                await route.abort()

        await self.context.route("**/*", boundary)

    async def asyncTearDown(self):
        self.assertEqual(self.bad_network, [])
        await super().asyncTearDown()


class ChatQuality(Evidence, ChatKnowledgePreparation):
    async def test_confirmed_cancel_is_not_revived_by_inflight_waiting_get(self):
        started, release, replied = asyncio.Event(), asyncio.Event(), asyncio.Event()
        reads = []
        stale = copy.deepcopy(self.preparation)

        async def observe(route):
            reads.append(route.request.url)
            if len(reads) == 1:
                started.set()
                await release.wait()
                with contextlib.suppress(Exception):
                    await route.fulfill(json=stale)
                replied.set()
            else:
                await route.fulfill(
                    status=503,
                    json={"error": {"code": "unavailable", "message": "后续观察失败"}},
                )

        await self.page.route("**/api/knowledge/preparations/prep-chat", observe)
        try:
            await self.start_preparation()
            await asyncio.wait_for(started.wait(), 4)
            await self.page.get_by_role("button", name="停止生成", exact=True).click()
            await expect(
                self.page.get_by_role("button", name="停止生成", exact=True)
            ).to_have_count(0)
            release.set()
            await asyncio.wait_for(replied.wait(), 4)
            await self.page.wait_for_timeout(850)
            self.observations.update(
                cancel_posts=self.cancels,
                reads=len(reads),
                stop_buttons=await self.page.get_by_role(
                    "button", name="停止生成", exact=True
                ).count(),
                recover_buttons=await self.page.get_by_role(
                    "button", name="恢复查看", exact=True
                ).count(),
            )
            self.assertEqual(
                self.observations["stop_buttons"],
                0,
                "Confirmed cancelled preparation was revived by an older waiting GET",
            )
            self.assertEqual(self.observations["recover_buttons"], 0)
        finally:
            release.set()

    async def test_cancel_requested_is_observed_without_reposting_stop(self):
        async def cancel(route):
            self.cancels += 1
            self.preparation.update(status="cancel_requested", phase="cancel_requested")
            await route.fulfill(json=self.preparation)

        await self.page.route("**/api/knowledge/preparations/prep-chat/cancel", cancel)
        await self.start_preparation()
        await self.page.get_by_role("button", name="停止生成", exact=True).click()
        await self.page.wait_for_timeout(150)
        stop = self.page.get_by_role("button", name="停止生成", exact=True)
        if await stop.count() and await stop.is_enabled():
            await stop.click()
        await self.page.wait_for_timeout(180)
        self.observations["cancel_posts"] = self.cancels
        self.assertEqual(
            self.cancels,
            1,
            "Already accepted cancellation can still be reposted from the visible stop button",
        )

    async def test_pending_stop_survives_selecting_another_conversation(self):
        self.run.update(status="pending", revision=4, text="")
        other = {**self.conversation, "id": "c2", "title": "另一会话"}

        async def list_conversations(route):
            await route.fulfill(json=[self.conversation, other])

        await self.page.route("**/api/chat/conversations", list_conversations)
        await self.page.route(
            "**/api/chat/conversations/c2/messages", lambda r: r.fulfill(json=[])
        )
        first_cancel = asyncio.Event()

        async def cancel(route):
            self.cancels += 1
            first_cancel.set()
            await route.fulfill(
                status=503,
                json={"error": {"code": "unavailable", "message": "取消响应未确认"}},
            )

        await self.page.route("**/api/knowledge/preparations/prep-chat/cancel", cancel)
        main = []

        async def stop_main(route):
            main.append(route.request.url)
            self.run.update(status="cancelled", revision=10)
            await route.fulfill(json=self.run)

        await self.page.route("**/api/runs/knowledge-run/cancel", stop_main)
        await self.start_preparation()
        await self.page.get_by_role("button", name="停止生成", exact=True).click()
        await expect(
            self.page.get_by_text("取消响应未确认", exact=True)
        ).to_be_visible()
        await self.page.get_by_role("button", name="另一会话", exact=True).click()
        await expect(
            self.page.get_by_role("button", name="选择知识库", exact=True)
        ).to_be_enabled()
        self.release = True
        await self.page.get_by_role("button", name="附件会话", exact=True).click()
        await expect(
            self.page.get_by_role("button", name="停止生成", exact=True)
        ).to_have_count(0)
        self.observations.update(
            preparation_cancels=self.cancels,
            main_cancels=len(main),
            paid_posts=len(self.changes),
        )
        self.assertEqual(len(main), 1)
        self.assertEqual(len(self.changes), 1)


class KnowledgeQuality(Evidence, KnowledgeUIRegressions):
    async def test_unknown_metadata_create_does_not_offer_idempotent_resubmit(self):
        requests = []

        async def unavailable(route):
            if route.request.method == "POST":
                requests.append(route.request.post_data_json)
                await route.fulfill(
                    status=503,
                    json={"error": {"code": "unavailable", "message": "创建结果未知"}},
                )
            else:
                await route.fallback()

        await self.page.route("**/api/knowledge/bases", unavailable)
        await self.page.goto(self.origin + "/knowledge")
        await self.page.get_by_role("button", name="创建知识库", exact=True).click()
        dialog = self.page.get_by_role("dialog")
        await dialog.get_by_label("知识库名称", exact=True).fill("待核对知识库")
        await dialog.get_by_role("combobox", name=re.compile(r"向量模型$")).click()
        await self.page.get_by_text("业务向量模型", exact=True).click()
        save = dialog.get_by_role("button", name=re.compile(r"^保\s*存$"))
        await save.click()
        await expect(dialog.get_by_text("创建结果未知", exact=True)).to_be_visible()
        await expect(save).to_be_disabled()
        await expect(dialog).not_to_contain_text("同一请求标识")
        await dialog.get_by_role("button", name="Close", exact=True).click()
        await expect(dialog).not_to_be_visible()
        self.assertEqual(len(requests), 1)

    async def test_source_acl_click_waits_for_lazy_drawer_chunk(self):
        requested, release, file_read = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
        chunk_urls = []

        async def delayed_chunk(route):
            chunk_urls.append(route.request.url)
            requested.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await route.continue_()

        async def file(route):
            file_read.set()
            await route.fulfill(json=FILE)

        await self.page.route("**/assets/FileAclDrawer-*.js", delayed_chunk)
        await self.page.route("**/api/files/file-1", file)
        await self.page.route(
            "**/api/files/file-1/acl",
            lambda r: r.fulfill(
                json={
                    "file": FILE,
                    "entries": [],
                    "builtin_permissions": [
                        {"source": "owner", "enabled": True, "deny_overridable": False}
                    ],
                }
            ),
        )
        try:
            await self.page.goto(self.origin + "/knowledge/base-1/documents")
            await asyncio.wait_for(requested.wait(), 4)
            button = self.page.get_by_role(
                "button", name="原文权限：业务说明.txt", exact=True
            )
            disabled = await button.is_disabled()
            if not disabled:
                await button.click()
            await self.page.wait_for_timeout(150)
            self.observations.update(
                chunk_requested=requested.is_set(),
                file_read_before_chunk=file_read.is_set(),
                button_disabled_before_chunk=disabled,
                chunk_urls=chunk_urls,
            )
            async with self.page.expect_response(
                lambda r: "/assets/FileAclDrawer-" in r.url and r.url.endswith(".js")
            ) as loaded:
                release.set()
            self.observations["chunk_status"] = (await loaded.value).status
            if disabled:
                await expect(button).to_be_enabled()
                await button.click()
            await self.page.wait_for_timeout(800)
            drawer = self.page.locator(".ant-drawer-open .ant-drawer-content")
            first_count = await drawer.count()
            self.observations.update(
                drawer_count_after_chunk_load=first_count,
                acl_get_count_after_first_click=sum(
                    "/api/files/file-1/acl" in r["url"] for r in self.traffic
                ),
            )
            if first_count == 0:
                await button.click()
                await expect(drawer).to_be_visible()
                self.observations.update(
                    drawer_count_after_second_click=await drawer.count(),
                    acl_get_count_after_second_click=sum(
                        "/api/files/file-1/acl" in r["url"] for r in self.traffic
                    ),
                )
            self.assertEqual(
                first_count,
                1,
                "The real ACL click fetched FileID but was silently lost while the public async drawer ref was null",
            )
        finally:
            release.set()

    async def test_query_cancellation_invalidates_a_delayed_old_get(self):
        self.preparation.update(status="waiting_models", phase="embedding", hits=[])
        stale = copy.deepcopy(self.preparation)
        started, release = asyncio.Event(), asyncio.Event()

        async def observe(route):
            started.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await route.fulfill(json=stale)

        cancels = []

        async def cancel(route):
            cancels.append(route.request.url)
            self.preparation.update(status="cancelled", phase="cancelled")
            await route.fulfill(json=self.preparation)

        await self.page.route("**/api/knowledge/preparations/prep-1", observe)
        await self.page.route("**/api/knowledge/preparations/prep-1/cancel", cancel)
        try:
            await self.page.goto(self.origin + "/knowledge/base-1/query")
            await self.page.get_by_role("textbox", name="检索问题", exact=True).fill(
                "范围隔离问题"
            )
            await self.page.get_by_role("button", name="检索", exact=True).click()
            await asyncio.wait_for(started.wait(), 4)
            await self.page.get_by_role("button", name="取消检索", exact=True).click()
            await expect(
                self.page.get_by_text("检索已取消", exact=True)
            ).to_be_visible()
            release.set()
            await self.page.wait_for_timeout(700)
            await expect(
                self.page.get_by_role("button", name="检索", exact=True)
            ).to_be_enabled()
            self.observations.update(
                cancel_posts=len(cancels), paid_posts=len(self.queries)
            )
            self.assertEqual(len(cancels), 1)
            self.assertEqual(len(self.queries), 1)
        finally:
            release.set()

    async def test_unavailable_graph_stays_disabled_for_retained_graph_settings(self):
        self.base.update(
            enable_knowledge_graph=True,
            graph_status="pending",
            llm_model_id="graph-model",
        )
        await self.page.goto(self.origin + "/knowledge")
        graph = self.page.get_by_role("button", name="查看图谱", exact=True)
        self.observations["graph_button_enabled"] = await graph.is_enabled()
        if self.observations["graph_button_enabled"]:
            await graph.click()
            await expect(
                self.page.get_by_role("heading", name="业务知识", exact=True)
            ).to_be_visible()
            self.observations["graph_click_url"] = self.page.url
            self.observations["document_toolbar_visible"] = await self.page.get_by_role(
                "button", name="上传文档", exact=True
            ).is_visible()
        self.assertFalse(
            self.observations["graph_button_enabled"],
            "graph_available=false must disable graph even if a retained base flag is true",
        )

    async def test_query_graph_choice_stays_disabled_when_capability_unavailable(self):
        self.base.update(
            enable_knowledge_graph=True,
            graph_status="pending",
            llm_model_id="graph-model",
        )
        await self.page.goto(self.origin + "/knowledge/base-1/query")
        await self.page.locator(".query-controls .ant-select").first.click()
        option = self.page.locator(".ant-select-item-option").filter(
            has_text="知识图谱"
        )
        self.observations["graph_option_class"] = await option.get_attribute("class")
        if (
            "ant-select-item-option-disabled"
            not in self.observations["graph_option_class"]
        ):
            await option.click()
            await self.page.get_by_role("textbox", name="检索问题", exact=True).fill(
                "图谱目标的问题"
            )
            await self.page.get_by_role("button", name="检索", exact=True).click()
            await expect(self.page.locator(".query-passage")).to_have_count(1)
            self.observations["query_bodies"] = self.queries
        self.assertIn(
            "ant-select-item-option-disabled", self.observations["graph_option_class"]
        )

    async def test_switching_base_aborts_old_preview_and_preserves_scope(self):
        start, release = asyncio.Event(), asyncio.Event()

        async def delayed(route):
            start.set()
            await release.wait()
            with contextlib.suppress(Exception):
                await route.fulfill(
                    json={
                        "document_id": "doc-1",
                        "file_id": "file-1",
                        "text": "旧库迟到预览",
                        "parser_version": "native-v1",
                        "chunk_count": 1,
                    }
                )

        await self.page.route(
            "**/api/knowledge/bases/base-1/documents/doc-1/preview", delayed
        )
        self.bases.append({**self.base, "id": "base-2", "name": "另一知识库"})
        await self.page.route(
            "**/api/knowledge/bases/base-2", lambda r: r.fulfill(json=self.bases[1])
        )
        for suffix, payload in [
            ("documents", {"items": [], "total": 0}),
            ("folders", []),
            ("jobs", []),
        ]:

            async def reply(route, request, payload=payload):
                await route.fulfill(json=payload)

            await self.page.route("**/api/knowledge/bases/base-2/" + suffix, reply)
        try:
            await self.page.goto(self.origin + "/knowledge/base-1/documents")
            await self.page.get_by_role(
                "button", name="业务说明.txt", exact=True
            ).click()
            await asyncio.wait_for(start.wait(), 4)
            await (
                self.page.get_by_role("dialog")
                .get_by_role("button", name="Close", exact=True)
                .click()
            )
            await self.page.get_by_role("button", name="返回知识库", exact=True).click()
            await (
                self.page.locator(".knowledge-card")
                .filter(has_text="另一知识库")
                .get_by_role("button", name="管理文档", exact=True)
                .click()
            )
            release.set()
            await self.page.wait_for_timeout(180)
            await expect(
                self.page.get_by_role("heading", name="另一知识库", exact=True)
            ).to_be_visible()
            self.assertEqual(
                await self.page.get_by_text("旧库迟到预览", exact=True).count(), 0
            )
            self.assertEqual(await self.page.get_by_role("dialog").count(), 0)
        finally:
            release.set()


class WorkQuality(Evidence, WorkKnowledgePreparation):
    async def test_lost_cancel_response_can_be_confirmed_by_get(self):
        await self.open_task()

        async def unavailable(route):
            await route.fulfill(
                status=503,
                json={"error": {"code": "unavailable", "message": "观察暂时失败"}},
            )

        async def cancel(route):
            self.preparation_cancels += 1
            self.preparation.update(status="cancel_requested", phase="cancel_requested")
            await route.fulfill(
                status=503,
                json={"error": {"code": "unavailable", "message": "停止响应丢失"}},
            )

        await self.page.route("**/api/knowledge/preparations/prep-work", unavailable)
        await self.page.route("**/api/knowledge/preparations/prep-work/cancel", cancel)
        await expect(self.page.get_by_text("观察暂时失败", exact=True)).to_be_visible()
        await self.page.get_by_role("button", name="停止", exact=True).click()
        await expect(self.page.get_by_text("停止响应丢失", exact=True)).to_be_visible()
        reads = []

        async def observe(route):
            reads.append(route.request.url)
            if len(reads) > 1:
                self.preparation.update(status="cancelled", phase="cancelled")
                self.task.update(preparation_id=None, version=2)
            await route.fulfill(json=self.preparation)

        await self.page.route("**/api/knowledge/preparations/prep-work", observe)
        await self.page.get_by_role("button", name="恢复查看", exact=True).click()
        await expect(
            self.page.get_by_role("textbox", name="工作消息", exact=True)
        ).to_be_enabled()
        self.assertEqual(self.preparation_cancels, 1)
        self.assertGreaterEqual(len(reads), 2)

    async def test_cancel_requested_is_observed_without_reposting_stop(self):
        async def cancel(route):
            self.preparation_cancels += 1
            self.preparation.update(status="cancel_requested", phase="cancel_requested")
            await route.fulfill(json=self.preparation)

        await self.page.route("**/api/knowledge/preparations/prep-work/cancel", cancel)
        await self.open_task()
        await self.page.get_by_role("button", name="停止", exact=True).click()
        await self.page.wait_for_timeout(150)
        stop = self.page.get_by_role("button", name="停止", exact=True)
        if await stop.count() and await stop.is_enabled():
            await stop.click()
        await self.page.wait_for_timeout(180)
        self.observations["cancel_posts"] = self.preparation_cancels
        self.assertEqual(
            self.preparation_cancels,
            1,
            "Work also allows another POST after cancellation is acknowledged",
        )

    async def test_confirmed_cancel_replaces_old_observation_scope(self):
        started, release = asyncio.Event(), asyncio.Event()
        reads = []
        stale = copy.deepcopy(self.preparation)

        async def observe(route):
            reads.append(route.request.url)
            if len(reads) == 1:
                await route.fulfill(json=stale)
            else:
                started.set()
                await release.wait()
                with contextlib.suppress(Exception):
                    await route.fulfill(json=stale)

        await self.page.route("**/api/knowledge/preparations/prep-work", observe)
        try:
            await self.open_task()
            await asyncio.wait_for(started.wait(), 4)
            await self.page.get_by_role("button", name="停止", exact=True).click()
            await expect(
                self.page.get_by_role("button", name="停止", exact=True)
            ).to_have_count(0)
            release.set()
            await self.page.wait_for_timeout(700)
            self.observations.update(
                cancel_posts=self.preparation_cancels,
                reads=len(reads),
                stop_buttons=await self.page.get_by_role(
                    "button", name="停止", exact=True
                ).count(),
            )
            self.assertEqual(self.observations["stop_buttons"], 0)
            await expect(
                self.page.get_by_role("textbox", name="工作消息", exact=True)
            ).to_be_enabled()
        finally:
            release.set()


class AgentQuality(Evidence, AgentKnowledgePreparation):
    async def test_cancel_requested_recovery_observes_without_reposting_cancel(self):
        reads = 0

        async def observe(route):
            nonlocal reads
            reads += 1
            await route.fulfill(
                status=503,
                json={"error": {"code": "unavailable", "message": "试聊观察失败"}},
            )

        async def cancel(route):
            self.preparation_cancels += 1
            self.preparation.update(status="cancel_requested", phase="cancel_requested")
            await route.fulfill(json=self.preparation)

        await self.page.route("**/api/knowledge/preparations/prep-agent", observe)
        await self.page.route("**/api/knowledge/preparations/prep-agent/cancel", cancel)
        await self.open_agents()
        await self.page.get_by_role("button", name="试聊", exact=True).click()
        drawer = self.page.locator(".ant-drawer-open .ant-drawer-content")
        await drawer.get_by_role("textbox", name="试聊消息", exact=True).fill(
            "工作时间？"
        )
        await drawer.get_by_role("button", name="发送", exact=True).click()
        await expect(drawer.get_by_text("正在检索知识库", exact=True)).to_be_visible()
        await drawer.get_by_role("button", name="停止生成", exact=True).click()
        await expect(drawer.get_by_text("试聊观察失败", exact=True)).to_be_visible()
        await self.page.unroute("**/api/knowledge/preparations/prep-agent", observe)
        await drawer.get_by_role("button", name="恢复查看", exact=True).click()
        await self.page.wait_for_timeout(180)
        self.observations.update(
            cancel_posts=self.preparation_cancels, reads_before_recovery=reads
        )
        self.assertEqual(
            self.preparation_cancels,
            1,
            "Recovery reposted cancellation although cancel_requested had already been acknowledged",
        )


class ChatMultiTurnQuality(Evidence, ChatKnowledgePreparation):
    async def test_previous_terminal_run_does_not_shadow_new_preparation_ack(self):
        previous = {
            **self.run,
            "id": "previous-run",
            "status": "completed",
            "text": "上一轮已完成",
        }
        history = []
        admissions = []
        cancels = []

        async def messages(route):
            await route.fulfill(json=history)

        async def admit(route):
            admissions.append(route.request.post_data_json)
            if len(admissions) == 1:
                history.append(
                    {
                        "id": "previous-answer",
                        "role": "assistant",
                        "run_id": previous["id"],
                        "content": previous["text"],
                        "reasoning": "",
                        "created_at": previous["created_at"],
                        "attachments": [],
                        "skill_snapshots": [],
                        "knowledge_sources": [],
                    }
                )
                await route.fulfill(status=202, json=previous)
            else:
                self.conversation["preparation_id"] = self.preparation["id"]
                await route.fulfill(status=202, json=self.preparation)

        async def cancel(route):
            cancels.append(route.request.url)
            self.preparation.update(status="cancel_requested", phase="cancel_requested")
            await route.fulfill(json=self.preparation)

        await self.page.route("**/api/chat/conversations/c1/messages", messages)
        await self.page.route("**/api/chat/conversations/c1/runs", admit)
        await self.page.route("**/api/knowledge/preparations/prep-chat/cancel", cancel)
        await self.page.goto(self.origin + "/chat")
        await self.page.get_by_role("button", name="附件会话", exact=True).click()
        text = self.page.get_by_role("textbox", name="消息内容", exact=True)
        await text.fill("先完成普通对话")
        await self.page.get_by_role("button", name="发送消息", exact=True).click()
        await expect(self.page.get_by_text("上一轮已完成", exact=True)).to_be_visible()
        await self.page.get_by_role("button", name="选择知识库", exact=True).click()
        await self.page.get_by_role(
            "button", name="选择知识库：业务知识", exact=True
        ).click()
        await self.page.get_by_role("button", name="完成选择知识库", exact=True).click()
        await text.fill("第二轮知识检索")
        await self.page.get_by_role("button", name="发送消息", exact=True).click()
        await expect(self.page.locator(".run-state")).to_have_text("正在检索知识库")
        stop = self.page.get_by_role("button", name="停止生成", exact=True)
        await stop.click()
        await self.page.wait_for_timeout(200)
        self.observations["stop_enabled_after_ack"] = await stop.is_enabled()
        if await stop.is_enabled():
            await stop.click()
        await self.page.wait_for_timeout(150)
        self.observations.update(run_posts=admissions, cancel_posts=cancels)
        self.assertEqual(
            len(cancels),
            1,
            "Previous completed activeRun shadows current preparation cancellation acknowledgement",
        )


class WorkMultiTurnQuality(Evidence, WorkKnowledgePreparation):
    async def test_previous_terminal_run_does_not_shadow_new_preparation_ack(self):
        self.task["preparation_id"] = None
        admissions = []
        cancels = []

        async def admit(route):
            body = route.request.post_data_json
            admissions.append(body)
            if len(admissions) == 1:
                self.task.update(version=2, current_run_id=None)
                self.task["messages"] = [
                    self.message("user", body),
                    self.message("assistant", body, self.run),
                ]
                await route.fulfill(status=202, json=self.run)
            else:
                self.task.update(version=3, preparation_id=self.preparation["id"])
                await route.fulfill(status=202, json=self.preparation)

        async def cancel(route):
            cancels.append(route.request.url)
            self.preparation.update(status="cancel_requested", phase="cancel_requested")
            await route.fulfill(json=self.preparation)

        await self.page.route("**/api/work/tasks/task-1/runs", admit)
        await self.page.route("**/api/knowledge/preparations/prep-work/cancel", cancel)
        await self.open_task()
        text = self.page.get_by_role("textbox", name="工作消息", exact=True)
        await text.fill("先完成第一轮")
        await self.page.get_by_role("button", name="发送", exact=True).click()
        await expect(self.page.get_by_text("核实后的结论", exact=True)).to_be_visible()
        await expect(text).to_be_enabled()
        await self.page.locator(".work-composer .ant-select-selector").click()
        await (
            self.page.locator(".ant-select-item-option-content")
            .filter(has_text="写作助手")
            .click()
        )
        await text.fill("第二轮知识检索")
        await self.page.get_by_role("button", name="发送", exact=True).click()
        await expect(
            self.page.get_by_text("正在检索知识库", exact=True)
        ).to_be_visible()
        stop = self.page.get_by_role("button", name="停止", exact=True)
        await stop.click()
        await self.page.wait_for_timeout(200)
        self.observations["stop_enabled_after_ack"] = await stop.is_enabled()
        if await stop.is_enabled():
            await stop.click()
        await self.page.wait_for_timeout(150)
        self.observations.update(run_posts=admissions, cancel_posts=cancels)
        self.assertEqual(
            len(cancels),
            1,
            "Previous completed Work activeRun shadows current preparation cancellation acknowledgement",
        )


CASES = [
    (KnowledgeQuality, "test_source_acl_click_waits_for_lazy_drawer_chunk"),
    (KnowledgeQuality, "test_query_cancellation_invalidates_a_delayed_old_get"),
    (WorkQuality, "test_cancel_requested_is_observed_without_reposting_stop"),
    (WorkQuality, "test_confirmed_cancel_replaces_old_observation_scope"),
    (ChatQuality, "test_confirmed_cancel_is_not_revived_by_inflight_waiting_get"),
    (ChatQuality, "test_cancel_requested_is_observed_without_reposting_stop"),
    (ChatQuality, "test_pending_stop_survives_selecting_another_conversation"),
    (
        KnowledgeQuality,
        "test_unavailable_graph_stays_disabled_for_retained_graph_settings",
    ),
    (
        KnowledgeQuality,
        "test_query_graph_choice_stays_disabled_when_capability_unavailable",
    ),
    (KnowledgeQuality, "test_switching_base_aborts_old_preview_and_preserves_scope"),
    (AgentQuality, "test_cancel_requested_recovery_observes_without_reposting_cancel"),
]


def load_tests(loader, tests, pattern):
    # Inherited fixture classes are setup helpers; their suites run separately.
    return unittest.TestSuite(
        [
            *(cls(name) for cls, name in CASES),
            ChatMultiTurnQuality(
                "test_previous_terminal_run_does_not_shadow_new_preparation_ack"
            ),
            WorkMultiTurnQuality(
                "test_previous_terminal_run_does_not_shadow_new_preparation_ack"
            ),
            KnowledgeQuality(
                "test_unknown_metadata_create_does_not_offer_idempotent_resubmit"
            ),
            WorkQuality("test_lost_cancel_response_can_be_confirmed_by_get"),
        ]
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
