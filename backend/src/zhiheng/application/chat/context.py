"""对话来源校验与状态查询；摘要编排由官方 Middleware 唯一负责。"""

import logging
from dataclasses import dataclass
from uuid import UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.application.execution.admission import RunAdmission
from zhiheng.infrastructure.database import Database
from zhiheng.modules.billing.public import UsageService, lock_billing_admission
from zhiheng.modules.chat.public import (
    ChatContextInfo,
    ChatEffectiveMemory,
    ChatLastRunContext,
    ChatService,
    ContextMemory,
    ContextSnapshotDraft,
    SkillSnapshot,
    source_fingerprint,
)
from zhiheng.modules.files.public import FileService
from zhiheng.modules.identity.public import IdentityService
from zhiheng.modules.models.public import ModelBinding, ModelMessage, ModelRequest, ModelService
from zhiheng.modules.skills.public import (
    SkillRevisionValue,
    SkillSelection,
    require_frozen_available,
    resolve_selection,
)
from zhiheng.runtime.contracts import TERMINAL_STATUSES, ClaimedRun
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatContextConfig:
    input_tokens: int | None = None
    summary_output_tokens: int | None = None
    summary_trigger_tokens: int | None = None
    summary_keep_tokens: int | None = None


@dataclass(frozen=True)
class PreparedContext:
    messages: tuple[ModelMessage, ...]
    snapshot: ContextSnapshotDraft


class ChatContextService:
    def __init__(
        self,
        database: Database,
        chat: ChatService,
        models: ModelService,
        identity: IdentityService,
        files: FileService,
        admission: RunAdmission,
        config: ChatContextConfig = ChatContextConfig(),
        *,
        runs: RunStore,
        usage: UsageService,
    ) -> None:
        self.database, self.chat, self.models, self.identity = database, chat, models, identity
        self.files, self.admission = files, admission
        self.runs, self.usage = runs, usage
        self.memory = ContextMemory()
        self.input_tokens, self.summary_output_tokens = config.input_tokens, config.summary_output_tokens
        self.trigger_tokens, self.keep_tokens = config.summary_trigger_tokens, config.summary_keep_tokens

    async def validate_files(self, session: AsyncSession, principal: Principal, refs: list[dict[str, object]]) -> None:
        for ref in sorted(refs, key=lambda item: str(item['file_id'])):
            current = await self.files.require(session, principal, UUID(str(ref['file_id'])), active=True, lock=True)
            if current.sha256 != ref['sha256']:
                raise AppError('file_snapshot_changed', '会话上下文来源附件已变化', 409)

    async def require_owner(self, session: AsyncSession, owner_id: UUID, conversation_id: UUID) -> Principal:
        principal = await self.identity.principal_for_user(session, owner_id)
        if not await self.memory.lock_conversation(session, conversation_id, owner_id):
            raise AppError('conversation_not_found', '对话不存在', 404)
        # NO KEY UPDATE 串行化会话变更，同时允许回复插入所需的外键 key-share。
        conversation = await self.chat.require_conversation(session, principal, conversation_id)
        if conversation.archived:
            raise AppError('conversation_archived', '已归档对话不能继续执行', 409)
        return principal

    async def guard(self, claim: ClaimedRun) -> None:
        async with self.database.sessions.begin() as session:
            if claim.resource_type == 'chat_summary':
                task = await self.memory.task(session, claim.resource_id)
                if task is None or task.run_id != claim.id:
                    raise AppError('chat_summary_stale', '会话摘要任务已失效', 409)
                if task.parent_run_id is None or task.parent_attempt_id is None:
                    raise AppError('chat_summary_stale', '会话摘要缺少父执行身份', 409)
                await self.runs.lock_execution(
                    session,
                    task.parent_run_id,
                    task.parent_attempt_id,
                    allow_cancel=False,
                )
                principal = await self.require_owner(session, task.owner_id, task.conversation_id)
                state = await self.memory.find_state(session, task.conversation_id, task.owner_id)
                if (
                    state is None
                    or task.status != 'pending'
                    or state.version != task.expected_version
                    or state.pending_task_id != task.id
                ):
                    raise AppError('chat_summary_stale', '会话摘要状态已失效', 409)
                live = await self.models.bindings.resolve(
                    session, ModelBinding.model_validate(task.model_binding).model_id
                )
                if live != ModelBinding.model_validate(task.model_binding):
                    raise AppError('model_binding_changed', '会话摘要模型配置已变化', 409)
                await self.validate_files(session, principal, task.file_refs)
                turns = await self.chat.context_turns(session, principal, task.conversation_id, load_inputs=False)
                if source_fingerprint(turns[: task.covered_count]) != task.source_hash:
                    raise AppError('chat_summary_stale', '会话摘要来源已变化', 409)
                return
            snapshot = await self.memory.snapshot(session, claim.id)
            if snapshot is None:
                raise AppError('chat_context_unavailable', '本次对话缺少上下文快照', 409)
            principal = await self.require_owner(session, claim.owner_id, snapshot.conversation_id)
            request = ModelRequest.model_validate(claim.payload)
            frozen_skills = await self.chat.regeneration_skill_snapshots(
                session,
                principal,
                snapshot.conversation_id,
                claim.id,
            )
            await self.validate_frozen_skills(session, principal, frozen_skills, request.messages)
            await self.models.bindings.resolve(session, request.binding.model_id)
            await self.validate_files(session, principal, snapshot.file_refs)

    async def validate_frozen_skills(
        self,
        session: AsyncSession,
        principal: Principal,
        snapshots: list[SkillSnapshot],
        messages: tuple[ModelMessage, ...],
    ) -> None:
        current = await resolve_selection(session, principal.id, [item.slug for item in snapshots])
        by_slug = {item.slug: item for item in current.revisions}
        for frozen in snapshots:
            found = False
            for message in messages:
                if message.role != 'system':
                    continue
                value = SkillRevisionValue(
                    by_slug[frozen.slug].skill_id,
                    frozen.revision_id,
                    frozen.slug,
                    frozen.name,
                    frozen.content_hash,
                    message.content,
                )
                try:
                    await require_frozen_available(session, principal.id, SkillSelection((value,)))
                except AppError as error:
                    if error.code != 'skill_unavailable':
                        raise
                else:
                    found = True
                    break
            if not found:
                raise AppError('skill_unavailable', '原技能修订无法完整恢复或当前不可用', 422)

    async def cancel_conversation(self, session: AsyncSession, principal: Principal, conversation_id: UUID) -> None:
        state = await self.memory.find_state(session, conversation_id, principal.id)
        if state is None or state.pending_task_id is None:
            return
        task = await self.memory.task(session, state.pending_task_id)
        if task is None or task.run_id is None:
            return
        _, release = await self.runs.cancel(session, task.run_id, task.owner_id)
        if release:
            await self.usage.settle(session, task.run_id, rejected_before_execution=True)
        await self.memory.complete_agent_summary(session, task.id, None, 'chat_summary_cancelled')

    async def reconcile(self) -> None:
        """仅核对已接纳摘要的孤儿状态，绝不规划或重发模型请求。"""
        async with self.database.sessions() as session:
            pending = await self.memory.pending(session)
        for conversation_id, owner_id in pending:
            try:
                async with self.database.sessions.begin() as session:
                    await lock_billing_admission(session)
                    if not await self.memory.lock_conversation(session, conversation_id, owner_id):
                        continue
                    if await self.runs.has_active(session, conversation_id):
                        continue
                    state = await self.memory.state(session, conversation_id, owner_id, lock=True)
                    task = await self.memory.task(session, state.pending_task_id) if state.pending_task_id else None
                    if task is None or task.run_id is None:
                        continue
                    result = await self.runs.projection(session, task.run_id)
                    if result.status not in TERMINAL_STATUSES:
                        _, release = await self.runs.cancel(session, task.run_id, task.owner_id)
                        if release:
                            await self.usage.settle(session, task.run_id, rejected_before_execution=True)
                        continue
                    success = (
                        result.status == 'completed' and result.finish_reason == 'stop' and bool(result.text.strip())
                    )
                    await self.memory.complete_agent_summary(
                        session,
                        task.id,
                        result.text if success else None,
                        None if success else result.error.code if result.error else 'chat_summary_incomplete',
                    )
            except AppError as error:
                logger.warning('Summary reconciliation refused conversation_id=%s code=%s', conversation_id, error.code)

    async def info(self, session: AsyncSession, principal: Principal, conversation_id: UUID) -> ChatContextInfo:
        await self.chat.require_conversation(session, principal, conversation_id)
        state = await self.memory.find_state(session, conversation_id, principal.id)
        latest = await self.memory.latest_snapshot(session, conversation_id)
        turns = await self.chat.context_turns(session, principal, conversation_id, load_inputs=False)
        head = (
            await self.memory.checkpoint_snapshot(session, conversation_id, state.agent_checkpoint_id)
            if state and state.agent_checkpoint_id
            else None
        )
        effective = ChatEffectiveMemory(estimated_tokens=0 if not turns else None)
        reason = state.error_code if state else None
        if head and head.agent_context:
            spec = head.agent_context
            # The frozen sources precede this accepted reply; newer branches do not add rounds here.
            total_rounds = len(head.source_refs) + 1
            retained = (
                min(total_rounds, spec.chat_context_retained_rounds)
                if spec.chat_context_retained_rounds is not None
                else total_rounds
            )
            effective = ChatEffectiveMemory(
                summary_used=spec.summary_used,
                covered_rounds=max(0, total_rounds - retained) if spec.summary_used else 0,
                retained_rounds=retained,
                estimated_tokens=spec.chat_context_estimated_tokens,
            )
        elif turns:
            reason = reason or 'chat_checkpoint_missing'
        last_run = None
        if latest and latest.agent_context:
            spec = latest.agent_context
            task = await self.memory.task(session, uuid5(latest.run_id, 'agent-summary-v1'))
            last_run = ChatLastRunContext(
                run_id=latest.run_id,
                summary_prepared=spec.summary_used or bool(task and task.status == 'published'),
                estimated_input_tokens=spec.chat_context_model_input_tokens,
                input_budget=spec.input_budget,
                summary_trigger_tokens=spec.trigger_tokens,
                retained_token_budget=spec.keep_tokens,
            )
        return ChatContextInfo(
            status='degraded'
            if reason
            else 'summarizing'
            if state and state.pending_task_id
            else 'summarized'
            if effective.summary_used
            else 'full',
            effective_memory=effective,
            last_run_context=last_run,
            reason=reason,
        )
