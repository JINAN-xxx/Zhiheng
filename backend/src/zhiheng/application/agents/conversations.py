"""Published private assistants with durable, isolated conversation histories."""

import hashlib
import json
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.application.execution.admission import RunAdmission
from zhiheng.application.knowledge.preparation import KnowledgePreparation, PreparationStart
from zhiheng.infrastructure.database import Database
from zhiheng.modules.agents.public import (
    AssistantConversationInfo,
    AssistantConversations,
    AssistantConversationValue,
    select_agent_history,
)
from zhiheng.modules.billing.public import UsageService, lock_billing_admission
from zhiheng.modules.identity.public import IdentityService
from zhiheng.modules.knowledge.public import Hit, PreparationInfo, PreparedKnowledge, RetrievalState
from zhiheng.modules.models.public import ModelBinding, ModelMessage, ModelOptions, ModelRequest, ModelService
from zhiheng.modules.skills.public import require_frozen_available
from zhiheng.runtime.contracts import TERMINAL_STATUSES, ClaimedRun, RunSnapshot
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.errors import AppError
from zhiheng.shared.platform_permissions import require_execution_capabilities
from zhiheng.shared.principal import Principal

CONVERSATION_PAGE_SIZE = 50


def message_fingerprint(conversation_id: UUID, message: str) -> str:
    return hashlib.sha256(
        json.dumps(['agent_chat', str(conversation_id), message], ensure_ascii=False).encode()
    ).hexdigest()


class AssistantConversationService:
    def __init__(
        self,
        database: Database,
        store: AssistantConversations,
        models: ModelService,
        runs: RunStore,
        admission: RunAdmission,
        usage: UsageService,
        knowledge: KnowledgePreparation,
        identity: IdentityService,
        token_limit: int,
    ) -> None:
        self.database, self.store, self.models, self.runs = database, store, models, runs
        self.admission, self.usage, self.knowledge, self.identity = admission, usage, knowledge, identity
        self.token_limit = token_limit

    async def create(self, principal: Principal, agent_id: UUID, request_id: UUID) -> AssistantConversationInfo:
        async with self.database.sessions.begin() as session:
            return await self.store.create(session, principal, agent_id, request_id)

    async def list(self, principal: Principal, agent_id: UUID, before: UUID | None) -> list[AssistantConversationInfo]:
        async with self.database.sessions() as session:
            rows = await self.store.list_conversations(session, principal, agent_id, before=before)
            for row in rows:
                row.preparation_id = await self.knowledge.store.current(session, 'agent_chat', row.id)
            return rows

    async def detail(
        self, principal: Principal, agent_id: UUID, conversation_id: UUID, before: int | None = None
    ) -> dict[str, object]:
        async with self.database.sessions() as session:
            conversation = await self.store.require(session, principal, agent_id, conversation_id)
            info = conversation.info
            info.preparation_id = await self.knowledge.store.current(session, 'agent_chat', info.id)
            preparing_message: dict[str, str] | None = None
            if info.preparation_id is not None:
                preparation = await self.knowledge.store.get(session, info.preparation_id)
                preparing_message = {
                    'request_id': str(preparation.request['runtime_request_id']),
                    'content': str(preparation.request['message']),
                }
            turns = await self.store.turns(session, info.id, before=before)
            items: list[dict[str, object]] = []
            for turn in turns:
                run = await self.runs.resource_snapshot(session, turn.run_id, 'agent_chat', info.id)
                hits = await self.knowledge.visible_hits(
                    session, principal, [Hit.model_validate(row) for row in turn.sources]
                )
                items.append(
                    {
                        **turn.model_dump(mode='json'),
                        'sources': [hit.model_dump(mode='json') for hit in hits],
                        'run': run.model_dump(mode='json'),
                    }
                )
            return {
                'conversation': info.model_dump(mode='json'),
                'preparing_message': preparing_message,
                'turns': items,
                'next_before': turns[0].sequence if len(turns) == CONVERSATION_PAGE_SIZE else None,
            }

    async def change(
        self,
        principal: Principal,
        agent_id: UUID,
        conversation_id: UUID,
        expected_version: int,
        *,
        title: str | None = None,
        remove: bool = False,
    ) -> AssistantConversationInfo:
        async with self.database.sessions.begin() as session:
            # Use the same admission lock as send, so deletion cannot race a new response.
            await lock_billing_admission(session)
            value = await self.store.require(session, principal, agent_id, conversation_id, lock=True)
            if remove:
                await self._idle(session, value)
            await self.store.change(
                session, principal, conversation_id, expected_version, title=title, remove=remove
            )
            if remove:
                return value.info
            return (await self.store.require(session, principal, agent_id, conversation_id)).info

    async def _validate(
        self,
        session: AsyncSession,
        principal: Principal,
        value: AssistantConversationValue,
        *,
        lock: bool = False,
        latest: bool = True,
    ) -> ModelBinding:
        config = value.config
        require_execution_capabilities(principal, 'agent_chat', knowledge=bool(config.knowledge_base_ids), skills=bool(config.skill_slugs))
        binding = await self.models.resolve(session, config.model_id)
        if binding.model_type != 'chat' or config.max_tokens > binding.max_output_tokens:
            raise AppError('assistant_model_unavailable', '助手所用模型或输出配置已不可用', 409)
        # New messages follow the latest publication. Already-admitted Runs retain their own snapshot.
        version, _, _ = await self.store.publication(session, principal, value.info.agent_id, lock=lock)
        if latest and version != value.info.published_version:
            raise AppError('assistant_configuration_changed', '助手刚刚发布了更新，请重新发送', 409)
        await require_frozen_available(session, principal.id, value.skills)
        return binding.model_copy(
            update={'max_output_tokens': config.max_tokens, 'options': ModelOptions(temperature=config.temperature)}
        )

    async def _idle(
        self, session: AsyncSession, value: AssistantConversationValue, intent_id: UUID | None = None
    ) -> int:
        await self.knowledge.store.ensure_idle(session, 'agent_chat', value.info.id, intent_id)
        latest = await self.store.turns(session, value.info.id, limit=1)
        if latest:
            run = await self.runs.resource_snapshot(session, latest[0].run_id, 'agent_chat', value.info.id)
            if run.status not in TERMINAL_STATUSES:
                raise AppError('assistant_conversation_busy', '当前对话仍在回复，请等待或停止后再发送', 409)
            return latest[0].sequence + 1
        return 1

    async def send(
        self, principal: Principal, agent_id: UUID, conversation_id: UUID, message: str, request_id: UUID
    ) -> RunSnapshot | PreparationInfo:
        value_hash = message_fingerprint(conversation_id, message)
        async with self.database.sessions.begin() as session:
            await lock_billing_admission(session)
            await self.runs.lock_request(session, principal.id, request_id)
            conversation = await self.store.require(session, principal, agent_id, conversation_id)
            existing = await self.runs.existing_request(session, principal.id, request_id, value_hash)
            if existing is not None:
                return existing
            prior = await self.knowledge.store.existing(session, principal.id, request_id, value_hash)
            if prior is not None:
                return self.knowledge.store.info(prior)
            binding = await self._validate(session, principal, conversation, lock=True)
            conversation = await self.store.require(session, principal, agent_id, conversation_id, lock=True)
            await self._idle(session, conversation)
            if not conversation.config.knowledge_base_ids:
                return await self._admit(session, principal, conversation, message, request_id, binding)
            return await self.knowledge.start(
                session,
                principal=principal,
                body=PreparationStart(
                    owner_id=principal.id,
                    resource_type='agent_chat',
                    resource_id=conversation_id,
                    request_id=request_id,
                    value=value_hash,
                    request={
                        'query': message,
                        'message': message,
                        'runtime_request_id': str(request_id),
                        'runtime_fingerprint': value_hash,
                    },
                    base_ids=conversation.config.knowledge_base_ids,
                    dependencies={
                        'agent_id': str(agent_id),
                        'published_version': conversation.info.published_version,
                        'main_request': ModelRequest(
                            binding=binding.model_copy(
                                update={
                                    'max_output_tokens': (
                                        await self.models.resolve(session, conversation.config.model_id)
                                    ).max_output_tokens,
                                    'options': ModelOptions(),
                                }
                            ),
                            messages=(),
                        ).model_dump(mode='json'),
                    },
                ),
            )

    async def _admit(
        self,
        session: AsyncSession,
        principal: Principal,
        value: AssistantConversationValue,
        message: str,
        request_id: UUID,
        binding: ModelBinding,
        prepared: PreparedKnowledge | None = None,
    ) -> RunSnapshot:
        number = await self._idle(session, value, prepared.intent_id if prepared else None)
        history: list[ModelMessage] = []
        for turn in await self.store.turns(session, value.info.id, limit=20):
            run = await self.runs.resource_snapshot(session, turn.run_id, 'agent_chat', value.info.id)
            if run.status == 'completed':
                history.extend((ModelMessage('user', turn.content), ModelMessage('assistant', run.text)))
        selected = select_agent_history(
            (*history, ModelMessage('user', message)), value.config.history_rounds, max_bytes=65536, max_messages=96
        )
        messages = (
            *((ModelMessage('system', value.config.system_prompt),) if value.config.system_prompt else ()),
            *(ModelMessage('system', skill.content) for skill in value.skills.revisions),
            *selected,
        )
        if prepared:
            await self.knowledge.check(session, prepared, principal)
            messages = prepared.apply_context(messages)
        run = await self.admission.reserve(
            session,
            principal,
            resource_type='agent_chat',
            resource_id=value.info.id,
            client_request_id=request_id,
            fingerprint=message_fingerprint(value.info.id, message),
            request=ModelRequest(binding=binding, messages=messages, required_modules=tuple(module for module, used in [('knowledge', bool(value.config.knowledge_base_ids)), ('skills', bool(value.skills.revisions))] if used)),
            token_limit=self.token_limit,
        )
        await self.store.add_turn(
            session,
            principal,
            value.info.id,
            request_id,
            message,
            run.id,
            number,
            [hit.model_dump(mode='json') for hit in prepared.hits] if prepared else [],
            value.info.published_version,
            value.config.name,
        )
        if prepared:
            await self.knowledge.bind(session, prepared, principal, run.id)
        return run

    async def finalize(self, session: AsyncSession, state: RetrievalState, prepared: PreparedKnowledge) -> RunSnapshot:
        principal = await self.identity.principal_for_user(session, state.owner_id)
        value = await self.store.require(
            session, principal, UUID(str(state.dependencies['agent_id'])), state.resource_id
        )
        binding = await self._validate(session, principal, value, lock=True)
        if value.info.published_version != state.dependencies['published_version']:
            raise AppError('knowledge_preparation_stale', '知识准备期间助手已发布更新，请重新发送', 409)
        value = await self.store.require(session, principal, value.info.agent_id, value.info.id, lock=True)
        return await self._admit(
            session,
            principal,
            value,
            str(state.request['message']),
            UUID(str(state.request['runtime_request_id'])),
            binding,
            prepared,
        )

    async def check_preparation(self, session: AsyncSession, state: RetrievalState) -> None:
        principal = await self.identity.principal_for_user(session, state.owner_id)
        value = await self.store.require(
            session, principal, UUID(str(state.dependencies['agent_id'])), state.resource_id
        )
        await self._validate(session, principal, value)
        if value.info.published_version != state.dependencies['published_version']:
            raise AppError('knowledge_preparation_stale', '知识准备期间助手已发布更新，请重新发送', 409)

    async def guard(self, claim: ClaimedRun) -> None:
        async with self.database.sessions.begin() as session:
            principal = await self.identity.principal_for_user(session, claim.owner_id)
            value = await self.store.for_run(session, principal, claim.id)
            binding = await self._validate(session, principal, value, latest=False)
            expected = ModelRequest.model_validate(claim.payload).binding
            if binding.connection_id != expected.connection_id or binding.upstream_model != expected.upstream_model:
                raise AppError('assistant_model_changed', '助手模型配置已变化，请重新发送', 409)
            turn = await self.store.turn_for_run(session, principal, claim.id)
            if turn.sources:
                hits = [Hit.model_validate(row) for row in turn.sources]
                visible = await self.knowledge.visible_hits(session, principal, hits)
                if len(visible) != len(hits):
                    raise AppError('assistant_source_unavailable', '助手资料权限已变化，请重新发送', 409)
            if value.config.knowledge_base_ids:
                # Recheck current KB grants before a queued response uses frozen retrieval input.
                for base_id in value.config.knowledge_base_ids:
                    await self.knowledge.knowledge.require(session, principal, base_id)
