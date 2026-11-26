"""Official Agent integration for latest-config assistants and durable native accounting."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Protocol
from uuid import UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.application.agents.attachments import AssistantAttachments
from zhiheng.application.execution.admission import AdmissionIdentity, RunAdmission
from zhiheng.application.execution.timeouts import remaining_seconds
from zhiheng.application.knowledge.preparation import KnowledgePreparation
from zhiheng.infrastructure.database import Database
from zhiheng.modules.agents.public import (
    AgentService,
    AssistantConversations,
    AssistantConversationValue,
    AssistantExecutionSpec,
)
from zhiheng.modules.billing.public import UsageService, lock_billing_admission
from zhiheng.modules.files.public import PreparedAttachment
from zhiheng.modules.identity.public import IdentityService
from zhiheng.modules.knowledge.public import Hit
from zhiheng.modules.models.public import (
    ModelBinding,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelService,
    SummaryPolicy,
    estimate_messages,
    input_budget_for,
)
from zhiheng.modules.skills.public import SkillSelection, platform_skill_id, require_frozen_available
from zhiheng.runtime.contracts import TERMINAL_STATUSES, ClaimedRun, RunSnapshot
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.agent import AgentSettings
from zhiheng.shared.errors import AppError
from zhiheng.shared.platform_permissions import require_capability, require_execution_capabilities
from zhiheng.shared.principal import Principal

logger = logging.getLogger(__name__)
MAX_MEMORY_SOURCE_REFS = 4096
SummaryWriter = Callable[[tuple[ModelMessage, ...]], Awaitable[str]]
ModelStart = Callable[[], Awaitable[None]]


class AssistantAgentFactory(Protocol):
    def __call__(
        self,
        provider: ModelProvider,
        thread_id: str,
        settings: AgentSettings,
        summarizer: SummaryWriter | None,
        on_model_start: ModelStart,
        *,
        persistent: bool,
    ) -> AbstractAsyncContextManager[ModelProvider]: ...


class AssistantAgentService:
    def __init__(  # noqa: PLR0913, PLR0917 - explicit business resources and adapter factory
        self,
        database: Database,
        agents: AgentService,
        store: AssistantConversations,
        models: ModelService,
        identity: IdentityService,
        runs: RunStore,
        admission: RunAdmission,
        usage: UsageService,
        knowledge: KnowledgePreparation,
        factory: AssistantAgentFactory,
        cleanup: Callable[[str], Awaitable[None]],
        policy: SummaryPolicy,
        *,
        input_tokens: int | None,
        timeout_seconds: float,
        attachments: AssistantAttachments,
    ) -> None:
        self.database, self.agents, self.store = database, agents, store
        self.models, self.identity, self.runs, self.admission = models, identity, runs, admission
        self.usage, self.knowledge, self.factory, self.cleanup = usage, knowledge, factory, cleanup
        self.policy, self.input_tokens, self.timeout_seconds = policy, input_tokens, timeout_seconds
        self.attachments = attachments

    @staticmethod
    def thread(owner_id: UUID, agent_id: UUID, conversation_id: UUID) -> str:
        return f'agent_chat:{owner_id}:{agent_id}:{conversation_id}'

    async def validate_skills(
        self, session: AsyncSession, skill_owner_id: UUID, skills: SkillSelection, *, plaza: bool
    ) -> None:
        try:
            if plaza and any(platform_skill_id(skill.slug) is None for skill in skills.revisions):
                publisher = await self.identity.principal_for_user(session, skill_owner_id)
                require_capability(publisher, 'skills', 'use')
            await require_frozen_available(session, skill_owner_id, skills)
        except AppError as error:
            if plaza:
                raise AppError(
                    'skill_unavailable', '该广场助手绑定的技能已停用或不可用，请联系发布者处理', 422
                ) from error
            raise

    def settings(
        self,
        binding: ModelBinding,
        messages: tuple[ModelMessage, ...],
        *,
        message_ids: tuple[str, ...],
        previous: AssistantExecutionSpec | None = None,
        checkpoint_id: str | None = None,
        base_run_id: UUID | None = None,
        sequence: int = 0,
        history_sources: list[dict[str, object]] | None = None,
    ) -> AssistantExecutionSpec:
        prefix = tuple(message for message in messages if message.role in {'system', 'developer'})
        current = messages[-1]
        budget = input_budget_for(
            input_budget=self.input_tokens,
            context_window_tokens=binding.context_window_tokens,
            max_output_tokens=binding.output_reservation_tokens,
        )
        if budget is not None and estimate_messages((*prefix, current)) > budget:
            raise AppError('context_input_too_long', '本轮配置、资料和问题超过模型输入容量，请缩短内容', 422)
        policy = self.policy.resolve(
            input_budget=budget,
            model_window_tokens=binding.context_window_tokens,
            model_max_output_tokens=binding.max_output_tokens,
            prefix_tokens=estimate_messages(prefix),
            current_tokens=estimate_messages((current,)),
        )
        reservation = sum(
            len(message.content.encode()) + 16 + sum(image.estimated_tokens for image in message.images)
            for message in messages
        )
        if checkpoint_id:
            if previous is None or previous.completion is None:
                raise AppError('assistant_memory_unavailable', '助手记忆状态不完整，请新建对话', 409)
            reservation += previous.completion.context_reservation_tokens
        elif budget is not None and estimate_messages(messages) > budget:
            raise AppError('context_input_too_long', '本次消息超过模型输入容量，请缩短历史或内容', 422)
        return AssistantExecutionSpec(
            version=1,
            reservation_input_tokens=reservation,
            base_checkpoint_id=checkpoint_id,
            expected_head=checkpoint_id,
            base_run_id=base_run_id,
            incremental=checkpoint_id is not None,
            message_ids=message_ids,
            trigger_tokens=policy.trigger_tokens,
            keep_tokens=policy.keep_tokens,
            summary_output_tokens=policy.output_tokens,
            summary_input_tokens=policy.input_tokens,
            input_budget=budget,
            sequence=sequence,
            history_sources=history_sources or [],
            summary_used=previous.summary_used if previous else False,
        )

    async def prepare(
        self,
        session: AsyncSession,
        principal: Principal,
        value: AssistantConversationValue,
        binding: ModelBinding,
        messages: tuple[ModelMessage, ...],
        request_id: UUID,
        sequence: int,
        *,
        attachments: tuple[PreparedAttachment, ...] = (),
    ) -> AssistantExecutionSpec:
        row = await self.store.authorize(session, principal, value.info.id, lock=True)
        if row.runtime_version != 1:
            raise AppError('assistant_memory_legacy', '旧对话保留查看，请新建对话使用自动记忆', 409)
        previous = await self.store.snapshot(session, row.memory_run_id) if row.memory_run_id else None
        if bool(row.checkpoint_id) != bool(previous) or (previous and previous.checkpoint_id != row.checkpoint_id):
            raise AppError('assistant_memory_unavailable', '助手记忆状态不一致，请新建对话', 409)
        sources = previous.execution.history_sources + previous.knowledge if previous else []
        # Source dependency records need identities, never another copy of retrieved plaintext.
        by_chunk = {str(item['chunk_id']): {**item, 'content': ''} for item in sources}
        if len(by_chunk) > MAX_MEMORY_SOURCE_REFS:
            raise AppError('assistant_memory_sources_limit', '本次会话引用资料过多，请新建对话', 422)
        history_sources = list(by_chunk.values())
        source_owner = principal
        if value.info.plaza and history_sources:
            if (
                previous is None
                or value.plaza_version is None
                or previous.execution.plaza_version != value.plaza_version
            ):
                raise AppError('assistant_history_source_unavailable', '广场知识授权已更新，请新建对话', 409)
            source_owner = await self.knowledge.assistant_sources(
                session, principal, value.info.id, value.plaza_version, value.config.knowledge_base_ids
            )
        await self.validate_sources(session, source_owner, value.config.knowledge_base_ids, history_sources)
        file_records = previous.execution.history_files if previous else []
        by_file = {item.file_id: item for item in [*file_records, *(item.summary for item in attachments)]}
        if len(by_file) > MAX_MEMORY_SOURCE_REFS:
            raise AppError('assistant_memory_files_limit', '会话附件过多，请新建对话', 422)
        await self.attachments.validate(session, principal, list(by_file.values()), binding)
        ids = tuple([f'prefix:{index}' for index in range(len(messages) - 1)] + [str(uuid5(row.id, str(request_id)))])
        metadata = self.settings(
            binding,
            messages,
            message_ids=ids,
            previous=previous.execution if previous else None,
            checkpoint_id=row.checkpoint_id,
            base_run_id=row.memory_run_id,
            sequence=sequence,
            history_sources=history_sources,
        )
        return metadata.model_copy(
            update={'history_files': list(by_file.values()), 'plaza_version': value.plaza_version}
        )

    async def validate_sources(
        self, session: AsyncSession, principal: Principal, allowed: list[UUID], records: list[dict[str, object]]
    ) -> None:
        if not records:
            return
        hits = [Hit.model_validate(item) for item in records]
        if any(hit.knowledge_base_id not in allowed for hit in hits):
            raise AppError('assistant_history_source_unavailable', '历史回答的知识资料已移出助手，请新建对话', 409)
        visible = await self.knowledge.visible_hits(session, principal, hits)
        if len(visible) != len(hits):
            raise AppError('assistant_history_source_unavailable', '历史回答的知识资料权限已变化，请新建对话', 409)
        for base_id in sorted({hit.knowledge_base_id for hit in hits}):
            await self.knowledge.knowledge.require(session, principal, base_id)

    async def guard(self, claim: ClaimedRun) -> None:
        async with self.database.sessions.begin() as session:
            if claim.resource_type == 'agent_summary':
                link = await self.agents.summary_for_run(session, claim.id)
                if link is None or link.parent_run_id != claim.resource_id:
                    raise AppError('assistant_summary_stale', '助手摘要执行身份已失效', 409)
                await self.runs.lock_execution(session, link.parent_run_id, link.parent_attempt_id, allow_cancel=False)
                parent = await self.runs.resource_snapshot(
                    session, link.parent_run_id, 'agent_chat', link.conversation_id
                )
                payload = await self.runs.resource_payload(
                    session, link.parent_run_id, 'agent_chat', link.conversation_id
                )
                await self._guard(
                    session, parent.id, claim.owner_id, link.conversation_id, ModelRequest.model_validate(payload)
                )
                return
            request = ModelRequest.model_validate(claim.payload)
            await self._guard(
                session,
                claim.id,
                claim.owner_id,
                claim.resource_id if claim.resource_type == 'agent_chat' else None,
                request,
                require_enabled=claim.resource_type != 'agent_preview',
            )

    async def _guard(
        self,
        session: AsyncSession,
        run_id: UUID,
        owner_id: UUID,
        conversation_id: UUID | None,
        request: ModelRequest,
        *,
        require_enabled: bool = True,
    ) -> None:
        principal = await self.identity.principal_for_user(session, owner_id)
        frozen = await self.store.snapshot(session, run_id)
        source_owner = principal
        skill_owner_id = owner_id
        plaza = False
        if conversation_id is not None:
            row = await self.store.authorize(session, principal, conversation_id)
            if (
                row.agent_id != frozen.agent_id
                or row.checkpoint_id != frozen.execution.expected_head
                or row.memory_run_id != frozen.execution.base_run_id
            ):
                raise AppError('assistant_memory_changed', '助手记忆状态已变化，请重新发送', 409)
            plaza = row.plaza
        if plaza:
            current = await self.store.current(session, principal, frozen.agent_id, plaza=True, load_skills=False)
            if current.config.execution_config() != frozen.config.execution_config():
                raise AppError('assistant_configuration_changed', '广场助手已更新，请重新发送', 409)
            skill_owner_id = current.skill_owner_id
            base_ids = current.config.knowledge_base_ids
            if base_ids:
                assert conversation_id is not None
                if frozen.execution.plaza_version is None:
                    raise AppError('assistant_plaza_knowledge_revoked', '广场知识授权已变化，请重新发送', 409)
                source_owner = await self.knowledge.assistant_sources(
                    session, principal, conversation_id, frozen.execution.plaza_version, base_ids
                )
        else:
            agent = await self.agents.get(session, principal, frozen.agent_id)
            if require_enabled and not agent.enabled:
                raise AppError('assistant_disabled', '助手已停用，请重新启用后发送', 409)
            base_ids = agent.knowledge_base_ids
        await self.validate_skills(session, skill_owner_id, frozen.skills, plaza=plaza)
        await self.attachments.validate(session, principal, frozen.execution.history_files, request.binding)
        await self.models.bindings.resolve(session, request.binding.model_id)
        await self.validate_sources(
            session, source_owner, base_ids, frozen.execution.history_sources + frozen.knowledge
        )
        for base_id in frozen.config.knowledge_base_ids:
            await self.knowledge.knowledge.require(session, source_owner, base_id)

    @asynccontextmanager
    async def provider(
        self, claim: ClaimedRun, request: ModelRequest, provider: ModelProvider, on_model_start: ModelStart
    ) -> AsyncIterator[ModelProvider]:
        async with self.database.sessions() as session:
            frozen = await self.store.snapshot(session, claim.id)
        persistent = claim.resource_type == 'agent_chat'
        thread_id = self.thread(claim.owner_id, frozen.agent_id, claim.resource_id) if persistent else str(claim.id)
        async with self.factory(
            provider,
            thread_id,
            frozen.execution,
            (lambda messages: self.summarize(claim, request, messages)) if persistent else None,
            on_model_start,
            persistent=persistent,
        ) as agent:
            yield agent

    async def summarize(self, parent: ClaimedRun, request: ModelRequest, messages: tuple[ModelMessage, ...]) -> str:
        child_id: UUID | None = None
        try:
            await self.guard(parent)
            async with self.database.sessions.begin() as session:
                await lock_billing_admission(session)
                await self.runs.lock_execution(session, parent.id, parent.attempt_id, allow_cancel=False)
                frozen = await self.store.snapshot(session, parent.id)
                prior = await self.agents.summary_for_parent(session, parent.id)
                if prior is not None:
                    if prior.parent_attempt_id != parent.attempt_id:
                        raise AppError('assistant_summary_stale', '助手摘要所属执行已失效', 409)
                    child_id = prior.run_id
                else:
                    binding = request.binding.model_copy(
                        update={
                            'max_output_tokens': frozen.execution.summary_output_tokens,
                            'options': request.binding.options.model_copy(
                                update={'temperature': 0.0, 'provider_output_default': False}
                            ),
                        }
                    )
                    budget = input_budget_for(
                        input_budget=frozen.execution.summary_input_tokens,
                        context_window_tokens=binding.context_window_tokens,
                        max_output_tokens=binding.max_output_tokens,
                    )
                    if budget is not None and estimate_messages(messages) > budget:
                        raise AppError(
                            'assistant_summary_input_too_long', '待整理历史超过摘要模型容量，请调整模型或新建对话', 422
                        )
                    principal = await self.identity.principal_for_user(session, parent.owner_id)
                    require_execution_capabilities(principal, 'agent_summary')
                    child = await self.admission.reserve(
                        session,
                        identity=AdmissionIdentity(
                            owner_id=parent.owner_id, billing_owner_id=parent.billing_owner_id or parent.owner_id
                        ),
                        resource_type='agent_summary',
                        resource_id=parent.id,
                        client_request_id=uuid5(parent.id, 'assistant-summary-v1'),
                        fingerprint=str(parent.attempt_id),
                        request=ModelRequest(
                            binding=binding,
                            messages=messages,
                            required_modules=request.required_modules,
                            execution_deadline=request.execution_deadline,
                        ),
                    )
                    await self.agents.attach_summary(session, parent, child.id)
                    child_id = child.id

            async def wait_result() -> RunSnapshot:
                while True:
                    async with self.database.sessions() as session:
                        child = await self.runs.projection(session, child_id)
                    if child.status in TERMINAL_STATUSES:
                        return child
                    await asyncio.sleep(0.05)

            result = await asyncio.wait_for(
                wait_result(), timeout=remaining_seconds(request.execution_deadline, self.timeout_seconds)
            )
            if result.status != 'completed' or result.finish_reason != 'stop' or not result.text.strip():
                raise AppError('assistant_summary_failed', '助手历史整理未完成，本次未重复调用，可重新发送', 502)
            return result.text
        except BaseException:
            if child_id is not None:
                async with self.database.sessions.begin() as session:
                    _, release = await self.runs.cancel(session, child_id, parent.owner_id)
                    if release:
                        await self.usage.settle(session, child_id, rejected_before_execution=True)
            raise

    async def finish(self, session: AsyncSession, snapshot: RunSnapshot, result: dict[str, object] | None) -> None:
        accepted = snapshot.status == 'completed' and bool(snapshot.text.strip())
        await self.store.finish(session, snapshot.id, result, accepted=accepted)

    async def reconcile(self) -> None:
        async with self.database.sessions() as session:
            orphans = await self.agents.orphan_summaries(session)
        for child_id, owner_id in orphans:
            async with self.database.sessions.begin() as session:
                _, release = await self.runs.cancel(session, child_id, owner_id)
                if release:
                    await self.usage.settle(session, child_id, rejected_before_execution=True)

    async def cleanup_checkpoints(self) -> None:
        deadline = asyncio.get_running_loop().time() + 30
        async with self.database.sessions() as session:
            candidates = await self.store.deleted_checkpoints(session)
        for conversation_id, owner_id in candidates:
            if asyncio.get_running_loop().time() >= deadline:
                break
            async with self.database.sessions.begin() as session:
                if await self.runs.has_active(session, conversation_id):
                    continue
                agent_id = await self.agents.conversation_agent(session, conversation_id, owner_id)
                if agent_id is None or not await self.store.cleanup_state(session, conversation_id, owner_id):
                    continue
            try:
                await asyncio.wait_for(
                    self.cleanup(self.thread(owner_id, agent_id, conversation_id)),
                    timeout=max(0.001, deadline - asyncio.get_running_loop().time()),
                )
            except (AppError, TimeoutError):
                logger.warning('Assistant checkpoint cleanup deferred conversation_id=%s', conversation_id)
                continue
            async with self.database.sessions.begin() as session:
                await self.store.cleanup_state(session, conversation_id, owner_id, cleaned=True)
