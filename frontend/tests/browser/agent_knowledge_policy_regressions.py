"""Agent knowledge settings use the canonical backend policy and published limit."""

import re

from agents_regressions import AgentsUIRegressions
from playwright.async_api import expect


class AgentKnowledgePolicyRegressions(AgentsUIRegressions):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.config.update(knowledge_base_ids=["kb-a", "kb-b"], knowledge_top_k=8)
        self.agent.update(self.config)
        self.policy_calls = []
        self.policy_failed = False
        self.base = {
            "id": "kb-a",
            "name": "知识库 A",
            "description": "",
            "owner_id": "user-1",
            "access": "private",
            "version": 1,
            "acl_version": 1,
            "embed_model_id": "embed-1",
            "chunk_size": 1000,
            "chunk_overlap": 200,
            "active_revision": "revision-1",
            "active_job_id": None,
            "index_status": "ready",
            "document_count": 1,
            "last_error": None,
            "enable_knowledge_graph": False,
            "llm_model_id": None,
            "graph_status": "disabled",
            "created_at": "2026-10-23T00:00:00Z",
            "updated_at": "2026-10-23T00:00:00Z",
            "can_manage": True,
        }
        await self.page.route(
            self.origin + "/api/knowledge/available*", self.knowledge_available
        )
        await self.page.route(self.origin + "/api/agents/knowledge-policy", self.policy)
        await self.page.route(self.origin + "/api/models", self.models)

    async def knowledge_available(self, route):
        await route.fulfill(
            json=[self.base, {**self.base, "id": "kb-b", "name": "知识库 B"}]
        )

    async def models(self, route):
        common = {
            "connection_id": "connection-1",
            "max_output_tokens": 512,
            "enabled": True,
            "vision_enabled": False,
        }
        await route.fulfill(
            json=[
                {
                    **common,
                    "id": "model-1",
                    "name": "日常助手",
                    "upstream_model": "chat",
                    "model_type": "chat",
                    "capabilities": ["chat", "stream"],
                },
                {
                    **common,
                    "id": "rank-1",
                    "name": "统一重排模型",
                    "upstream_model": "rank",
                    "model_type": "rerank",
                    "capabilities": ["rerank"],
                },
            ]
        )

    async def policy(self, route):
        self.policy_calls.append(route.request.post_data_json)
        if self.policy_failed:
            await route.fulfill(
                status=409,
                json={
                    "error": {
                        "code": "knowledge_rerank_required",
                        "message": "请先配置默认重排模型",
                    }
                },
            )
        else:
            await route.fulfill(
                json={"model_id": "rank-1", "source": "mixed_platform_default"}
            )

    async def test_combined_policy_and_limit_save_then_publish(self):
        await self.page.goto(self.origin + "/agents/agent-1")
        await expect(self.page.locator(".knowledge-policy")).to_contain_text(
            "统一重排模型"
        )
        await expect(self.page.locator(".knowledge-policy")).to_contain_text(
            "配置不一致"
        )
        control = self.page.get_by_role(
            "spinbutton", name=re.compile("最大召回片段数$")
        )
        await expect(control).to_have_value("8")
        await expect(control).to_have_attribute("aria-valuemax", "15")
        await control.fill("15")
        await self.page.get_by_role("button", name="保存", exact=True).click()
        await expect(
            self.page.get_by_text("所有更改已保存", exact=True)
        ).to_be_visible()
        self.assertEqual(self.posts[-1]["knowledge_top_k"], 15)
        await self.page.get_by_role("button", name="发布", exact=True).click()
        await expect(self.page.locator(".editor-status")).to_contain_text("已发布")
        self.assertGreaterEqual(
            len(self.policy_calls), 2, "Publishing must refresh the effective policy"
        )
        self.assertEqual(self.agent["published_config"]["knowledge_top_k"], 15)
        self.assertEqual(self.policy_calls[-1]["knowledge_base_ids"], ["kb-a", "kb-b"])

    async def test_unavailable_policy_blocks_publication_and_retry_recovers(self):
        self.policy_failed = True
        await self.page.goto(self.origin + "/agents/agent-1")
        await expect(self.page.locator(".knowledge-policy")).to_contain_text(
            "请先配置默认重排模型"
        )
        publish = self.page.get_by_role("button", name="发布", exact=True)
        await expect(publish).to_be_disabled()
        self.policy_failed = False
        await (
            self.page.locator(".knowledge-policy")
            .get_by_role("button", name="重试", exact=True)
            .click()
        )
        await expect(self.page.locator(".knowledge-policy")).to_contain_text(
            "统一重排模型"
        )
        await expect(publish).to_be_enabled()
        await self.page.set_viewport_size({"width": 390, "height": 844})
        await expect(self.page.locator(".knowledge-policy")).to_contain_text(
            "统一重排模型"
        )
        overflow = await self.page.locator(".agent-editor--page").evaluate(
            "(node) => node.scrollWidth > node.clientWidth + 1"
        )
        self.assertFalse(overflow)
