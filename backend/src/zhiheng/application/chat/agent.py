"""Ordinary-chat Agent integration; native Runs remain the only paid execution facts."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Protocol
from uuid import UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.application.chat.context import ChatContextService, PreparedContext
from zhiheng.application.execution.admission import AdmissionIdentity
from zhiheng.application.execution.timeouts import remaining_seconds
from zhiheng.modules.billing.public import lock_billing_admission
from zhiheng.modules.chat.public import (
    ChatExecutionSpec,
    ContextSnapshotDraft,
    ContextSnapshotValue,
    SummaryPolicy,
    SummaryTaskDraft,
    attachment_refs,
    context_image_bytes,
    estimate_messages,
    source_fingerprint,
    source_refs,
)
from zhiheng.modules.models.public import (
    MAX_CONTEXT_IMAGE_BYTES,
    ModelBinding,
    ModelMessage,
    ModelProvider,
    ModelRequest,
    input_budget_for,
)
from zhiheng.runtime.contracts import TERMINAL_STATUSES, ClaimedRun, RunSnapshot
from zhiheng.shared.errors import AppError
from zhiheng.shared.platform_permissions import require_execution_capabilities
from zhiheng.shared.principal import Principal

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager

SummaryWriter = Callable[[tuple[ModelMessage, ...]], Awaitable[str]]
ModelStart = Callable[[], Awaitable[None]]
CheckpointCleanup = Callable[[UUID], Awaitable[None]]
CHECKPOINT_CLEANUP_BUDGET_SECONDS = 30
SKILL_CAPABILITY = ModelMessage(
    'system',
    '对话体验只读取所选技能的 SKILL.md 文字说明。当前没有可调用工具，无法读取或运行技能包里的脚本及配套文件。'
    '请用说明完成可以直接回答的部分，不要发起工具调用或编造执行结果。'
    '仅当本次任务必须运行脚本时，简短告知用户可到工作空间继续。'
    '如果按文字说明就能完成，请直接回答，无需提示脚本限制。',
)
logger = logging.getLogger(__name__)


def _restore_skill_capability(
    messages: tuple[ModelMessage, ...], metadata: ChatExecutionSpec
) -> tuple[tuple[ModelMessage, ...], ChatExecutionSpec]:
    if SKILL_CAPABILITY in messages:
        return messages, metadata
    # Older runs have the skill text but not the capability message. Keep the question's identity.
    original_ids = metadata.message_ids
    return (
        (*messages[:-1], SKILL_CAPABILITY, messages[-1]),
        metadata.model_copy(
            update={
                'message_ids': (*original_ids[:-1], 'prefix:skill-capability', original_ids[-1]),
                'reservation_input_tokens': metadata.reservation_input_tokens
                + len(SKILL_CAPABILITY.content.encode())
                + 16,
            }
        ),
    )


class ChatAgentFactory(Protocol):
    def __call__(
        self,
        provider: ModelProvider,
        conversation_id: UUID,
        metadata: ChatExecutionSpec,
        summarizer: SummaryWriter,
        on_model_start: ModelStart,
    ) -> 'AbstractAsyncContextManager[ModelProvider]': ...


class ChatAgentService:
    def __init__(
        self,
        context: ChatContextService,
        factory: ChatAgentFactory,
        checkpoint_cleanup: CheckpointCleanup,
        *,
        trigger_tokens: int | None,
        keep_tokens: int | None,
        timeout_seconds: float,
        summary_input_tokens: int | None = None,
    ) -> None:
        self.context, self.factory, self.checkpoint_cleanup = context, factory, checkpoint_cleanup
        self.trigger_tokens, self.keep_tokens = trigger_tokens, keep_tokens
        self.timeout_seconds, self.summary_input_tokens = timeout_seconds, summary_input_tokens

    async def prepare(
        self,
        session: AsyncSession,
        principal: Principal,
        conversation_id: UUID,
        binding: ModelBinding,
        *,
        required: tuple[ModelMessage, ...],
        current: ModelMessage,
        client_request_id: UUID,
        original_run_id: UUID | None = None,
        uses_skills: bool = False,
    ) -> PreparedContext:
        memory = self.context.memory
        if uses_skills:
            required = (*required, SKILL_CAPABILITY)
        state = await memory.state(session, conversation_id, principal.id, lock=True)
        if state.pending_task_id is not None:
            raise AppError('chat_summary_busy', '会话正在整理历史，请完成后再发送', 409)
        turns = await self.context.chat.context_turns(session, principal, conversation_id, load_inputs=False)
        fingerprint = source_fingerprint(turns)
        budget = input_budget_for(
            input_budget=self.context.input_tokens,
            context_window_tokens=binding.context_window_tokens,
            max_output_tokens=binding.output_limit_tokens or binding.output_reservation_tokens,
        )
        if budget is not None and estimate_messages((*required, current)) > budget:
            raise AppError(
                'context_input_too_long',
                '本次内容超过已配置的模型输入容量',
                422,
                details={'estimated_input_tokens': estimate_messages((*required, current)), 'input_budget': budget},
            )
        summary = SummaryPolicy(
            trigger_tokens=self.trigger_tokens,
            keep_tokens=self.keep_tokens,
            input_tokens=self.summary_input_tokens,
            output_tokens=self.context.summary_output_tokens,
        ).resolve(
            input_budget=budget,
            model_window_tokens=binding.context_window_tokens,
            model_max_output_tokens=binding.max_output_tokens,
            prefix_tokens=estimate_messages(required),
            current_tokens=estimate_messages((current,)),
        )
        previous_snapshot = (
            await memory.checkpoint_snapshot(session, conversation_id, state.agent_checkpoint_id)
            if state.agent_checkpoint_id
            else None
        )
        previous_cost = (
            previous_snapshot.agent_context.chat_context_reservation_tokens
            if previous_snapshot and previous_snapshot.agent_context
            else None
        )
        incremental = bool(
            state.agent_checkpoint_id
            and state.agent_source_hash == fingerprint
            and type(previous_cost) is int
            and previous_cost >= 0
        )
        base = state.agent_checkpoint_id if incremental else None
        request_image_bytes = context_image_bytes((*required, current))
        messages = (*required, current)
        ids = [f'prefix:{index}' for index in range(len(required))]
        refs, files = source_refs(turns), attachment_refs(turns)
        if original_run_id is not None:
            original = ModelRequest.model_validate(
                await self.context.runs.resource_payload(session, original_run_id, 'chat', conversation_id)
            )
            old = await memory.snapshot(session, original_run_id)
            if not original.messages or original.messages[-1] != current:
                raise AppError('regeneration_sources_changed', '原请求资料已变化，请重新发送问题', 409)
            messages = original.messages
            if old and old.agent_context:
                regeneration_metadata = old.agent_context.regenerated(
                    expected_head=state.agent_checkpoint_id, input_budget=budget
                )
                if uses_skills:
                    messages, regeneration_metadata = _restore_skill_capability(messages, regeneration_metadata)
                self._require_image_capacity(
                    old.agent_context.chat_context_model_image_bytes
                    if old.agent_context.chat_context_model_image_bytes is not None
                    else request_image_bytes
                )
                await self.context.validate_files(session, principal, old.file_refs)
                return PreparedContext(
                    messages,
                    ContextSnapshotDraft(
                        conversation_id=conversation_id,
                        owner_id=principal.id,
                        source_refs=old.source_refs,
                        file_refs=old.file_refs,
                        agent_context=regeneration_metadata,
                    ),
                )
            raise AppError('chat_context_unavailable', '旧版回复不再导入，请新建对话使用新版体验', 409)
        if turns and not incremental:
            raise AppError('chat_context_unavailable', '会话检查点与有效历史不一致，请新建对话', 409)
        interrupted = await self.context.chat.previous_interrupted_question(
            session, principal, conversation_id, turns[-1].question_id if turns else None
        )
        if interrupted:
            previous_id, previous_text = interrupted
            previous = ModelMessage('user', previous_text)
            if budget is None or estimate_messages((*required, previous, current)) <= budget:
                messages = (*required, previous, current)
                ids.append(str(previous_id))
        self._require_image_capacity(
            (self._retained_image_bytes(previous_snapshot) if incremental else 0) + request_image_bytes
        )
        ids.append(str(uuid5(conversation_id, str(client_request_id))))
        frozen_cost = sum(
            len(message.content.encode()) + 16 + sum(image.estimated_tokens for image in message.images)
            for message in messages
        )
        if incremental:
            assert isinstance(previous_cost, int)
            frozen_cost += previous_cost
        metadata = ChatExecutionSpec(
            reservation_input_tokens=frozen_cost,
            version=1,
            base_checkpoint_id=base,
            expected_head=state.agent_checkpoint_id,
            incremental=incremental,
            message_ids=ids,
            trigger_tokens=summary.trigger_tokens,
            keep_tokens=summary.keep_tokens,
            summary_output_tokens=summary.output_tokens,
            summary_input_tokens=summary.input_tokens,
            input_budget=budget,
            summary_used=bool(
                previous_snapshot and previous_snapshot.agent_context and previous_snapshot.agent_context.summary_used
            )
            if incremental
            else False,
        )
        await self.context.validate_files(session, principal, files)
        return PreparedContext(
            messages,
            ContextSnapshotDraft(
                conversation_id=conversation_id,
                owner_id=principal.id,
                source_refs=refs,
                file_refs=files,
                agent_context=metadata,
            ),
        )

    @asynccontextmanager
    async def provider(
        self,
        claim: ClaimedRun,
        request: ModelRequest,
        provider: ModelProvider,
        on_model_start: ModelStart,
    ) -> AsyncIterator[ModelProvider]:
        async with self.context.database.sessions() as session:
            snapshot = await self.context.memory.snapshot(session, claim.id)
        if snapshot is None:
            raise AppError('chat_context_unavailable', '本次对话缺少正式执行快照', 409)
        if snapshot.agent_context is None:
            raise AppError('chat_context_unavailable', '普通对话缺少 Agent 执行快照', 409)
        async with self.factory(
            provider,
            claim.resource_id,
            snapshot.agent_context,
            lambda messages: self.summarize(
                claim, request.binding, messages, execution_deadline=request.execution_deadline
            ),
            on_model_start,
        ) as agent:
            yield agent

    async def summarize(
        self,
        parent: ClaimedRun,
        binding: ModelBinding,
        messages: tuple[ModelMessage, ...],
        *,
        execution_deadline: datetime | None = None,
    ) -> str:
        context = self.context
        task_id = uuid5(parent.id, 'agent-summary-v1')
        child_id: UUID | None = None
        try:
            async with context.database.sessions.begin() as session:
                await lock_billing_admission(session)
                await context.runs.lock_execution(session, parent.id, parent.attempt_id, allow_cancel=False)
                principal = await context.require_owner(session, parent.owner_id, parent.resource_id)
                snapshot = await context.memory.snapshot(session, parent.id)
                assert snapshot is not None and snapshot.agent_context is not None
                state = await context.memory.state(session, parent.resource_id, parent.owner_id, lock=True)
                prior = await context.memory.task(session, task_id)
                if prior is None:
                    turns = await context.chat.context_turns(session, principal, parent.resource_id, load_inputs=False)
                    live = await context.models.bindings.resolve(session, binding.model_id)
                    summary_binding = live.model_copy(
                        update={
                            'max_output_tokens': min(
                                snapshot.agent_context.summary_output_tokens, live.max_output_tokens
                            ),
                            'options': live.options.model_copy(update={'provider_output_default': False}),
                        }
                    )
                    limit = input_budget_for(
                        input_budget=snapshot.agent_context.summary_input_tokens,
                        context_window_tokens=live.context_window_tokens,
                        max_output_tokens=summary_binding.max_output_tokens,
                    )
                    if limit is not None and estimate_messages(messages) > limit:
                        raise AppError(
                            'chat_summary_input_too_long', '待整理历史超过摘要模型容量，请调整输入容量后重试', 422
                        )
                    prior = await context.memory.create_task(
                        session,
                        SummaryTaskDraft(
                            id=task_id,
                            parent_run_id=parent.id,
                            parent_attempt_id=parent.attempt_id,
                            conversation_id=parent.resource_id,
                            owner_id=parent.owner_id,
                            expected_version=state.version,
                            covered_count=len(turns),
                            source_hash=source_fingerprint(turns),
                            source_refs=source_refs(turns),
                            file_refs=attachment_refs(turns),
                            model_binding=live.model_dump(mode='json'),
                        ),
                    )
                    require_execution_capabilities(principal, 'chat_summary')
                    run = await context.admission.reserve(
                        session,
                        identity=AdmissionIdentity(owner_id=principal.id, billing_owner_id=principal.id),
                        resource_type='chat_summary',
                        resource_id=task_id,
                        client_request_id=uuid5(task_id, 'model'),
                        fingerprint=source_fingerprint(turns),
                        request=ModelRequest(
                            binding=summary_binding, messages=messages, execution_deadline=execution_deadline
                        ),
                    )
                    await context.memory.attach_run(session, state, prior, run.id)
                    child_id = run.id
                else:
                    child_id = prior.run_id
                    if prior.status == 'published' and prior.summary:
                        return prior.summary
                    if prior.status != 'pending' or child_id is None:
                        raise AppError('chat_summary_failed', '本次摘要已失败，未自动重复调用模型', 409)
            assert child_id is not None

            async def wait_result() -> RunSnapshot:
                while True:
                    async with context.database.sessions() as session:
                        current = await context.runs.projection(session, child_id)
                    if current.status in TERMINAL_STATUSES:
                        return current
                    await asyncio.sleep(0.05)

            result = await asyncio.wait_for(
                wait_result(), timeout=remaining_seconds(execution_deadline, self.timeout_seconds)
            )
            success = result.status == 'completed' and result.finish_reason == 'stop' and bool(result.text.strip())
            async with context.database.sessions.begin() as session:
                await context.memory.complete_agent_summary(
                    session,
                    task_id,
                    result.text if success else None,
                    None if success else result.error.code if result.error else 'chat_summary_incomplete',
                )
            if not success:
                raise AppError('chat_summary_failed', '会话摘要未完成，本次没有重复调用；可重新发送问题', 502)
            return result.text
        except BaseException:
            if child_id is not None:
                async with context.database.sessions.begin() as session:
                    _, release = await context.runs.cancel(session, child_id, parent.owner_id)
                    if release:
                        await context.usage.settle(session, child_id, rejected_before_execution=True)
            raise

    @staticmethod
    def _retained_image_bytes(snapshot: ContextSnapshotValue | None) -> int:
        value = snapshot.agent_context.chat_context_image_bytes if snapshot and snapshot.agent_context else None
        # Checkpoints without counters are still checked at the model boundary.
        return value if type(value) is int and value >= 0 else 0

    @staticmethod
    def _require_image_capacity(size: object) -> None:
        if type(size) is int and size > MAX_CONTEXT_IMAGE_BYTES:
            raise AppError('attachment_images_too_large', '本次请求含历史的安全图片数据合计最多16MiB', 422)

    async def finish(
        self,
        session: AsyncSession,
        snapshot: RunSnapshot,
        result: dict[str, object] | None,
    ) -> None:
        if snapshot.resource_type != 'chat':
            return
        context = self.context
        frozen = await context.memory.snapshot(session, snapshot.id)
        if frozen is None or frozen.agent_context is None:
            return
        accepted = snapshot.status == 'completed' and snapshot.finish_reason == 'stop' and bool(snapshot.text.strip())
        fingerprint = None
        if accepted:
            try:
                principal = await context.identity.principal_for_user(session, frozen.owner_id)
                turns = await context.chat.context_turns(session, principal, snapshot.resource_id, load_inputs=False)
                fingerprint = source_fingerprint(turns)
            except AppError:
                accepted = False
        await context.memory.finish_agent(session, snapshot.id, result, accepted=accepted, source_hash=fingerprint)

    async def cleanup_checkpoints(self) -> None:
        """软删除会话的 SDK 数据独立回收；重复扫描接住旧 worker 的迟到写入。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + CHECKPOINT_CLEANUP_BUDGET_SECONDS
        async with self.context.database.sessions() as session:
            candidates = await self.context.memory.deleted_checkpoints(session)
        for conversation_id, owner_id in candidates:
            if loop.time() >= deadline:
                return
            try:
                async with self.context.database.sessions.begin() as session:
                    if not await self.context.memory.lock_conversation(session, conversation_id, owner_id):
                        continue
                    if await self.context.runs.has_active(session, conversation_id):
                        continue
                    if not await self.context.memory.claim_checkpoint_cleanup(session, conversation_id, owner_id):
                        continue
                # Official saver I/O never runs while a business transaction holds locks.
                # Share one bounded sweep so recovery and other maintenance stay prompt.
                remaining = deadline - loop.time()
                if remaining <= 0:
                    return
                await asyncio.wait_for(self.checkpoint_cleanup(conversation_id), timeout=remaining)
                async with self.context.database.sessions.begin() as session:
                    await self.context.memory.mark_checkpoint_cleaned(session, conversation_id, owner_id)
            except Exception as error:
                logger.warning(
                    'Chat checkpoint cleanup failed conversation_id=%s error_type=%s',
                    conversation_id,
                    type(error).__name__,
                )
