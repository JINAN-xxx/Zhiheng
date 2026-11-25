"""Published private assistants with durable, isolated conversation histories."""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, replace
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.application.agents.execution import AssistantAgentService
from zhiheng.application.agents.receipts import AssistantReceipts
from zhiheng.application.execution.admission import AdmissionIdentity, RunAdmission
from zhiheng.application.files.commands import FileCommands
from zhiheng.application.knowledge.preparation import KnowledgePreparation, PreparationStart
from zhiheng.infrastructure.database import Database
from zhiheng.modules.agents.public import (
    AgentService,
    AssistantConversationInfo,
    AssistantConversations,
    AssistantConversationValue,
)
from zhiheng.modules.billing.public import lock_billing_admission
from zhiheng.modules.files.public import FileInfo, PreparedAttachment, file_semantics
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


def message_fingerprint(conversation_id: UUID, message: str, file_ids: Sequence[UUID] | None = None) -> str:
    values: list[object] = ['agent_chat', str(conversation_id), message]
    if file_ids:
        values.append([str(value) for value in file_ids])
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def configuration_fingerprint(value: AssistantConversationValue) -> str:
    """Preparation freezes the actual latest input, not a publication identifier."""
    return hashlib.sha256(
        json.dumps(
            [value.config.model_dump(mode='json'), [asdict(item) for item in value.skills.revisions]],
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()


class AssistantConversationService:
    def __init__(
        self,
        database: Database,
        store: AssistantConversations,
        models: ModelService,
        runs: RunStore,
        admission: RunAdmission,
        knowledge: KnowledgePreparation,
        identity: IdentityService,
        execution: AssistantAgentService,
        agents: AgentService,
        file_commands: FileCommands,
    ) -> None:
        self.database, self.store, self.models, self.runs = database, store, models, runs
        self.admission, self.knowledge, self.identity = admission, knowledge, identity
        self.execution, self.agents = execution, agents
        self.attachments = execution.attachments
        self.file_commands = file_commands
        self.receipts = AssistantReceipts(store, runs, knowledge.store)

    async def create(
        self, principal: Principal, agent_id: UUID, request_id: UUID, *, plaza: bool = False
    ) -> AssistantConversationInfo:
        async with self.database.sessions.begin() as session:
            return await self.store.create(session, principal, agent_id, request_id, plaza=plaza)

    async def plaza_history(
        self, principal: Principal, *, before: UUID | None = None
    ) -> list[AssistantConversationInfo]:
        async with self.database.sessions() as session:
            return await self.store.plaza_history(session, principal, before=before)

    async def list(
        self, principal: Principal, agent_id: UUID, before: UUID | None, *, plaza: bool = False
    ) -> list[AssistantConversationInfo]:
        async with self.database.sessions() as session:
            rows = await self.store.list_conversations(session, principal, agent_id, before=before, plaza=plaza)
            for row in rows:
                row.preparation_id = await self.knowledge.store.current(session, 'agent_chat', row.id)
            return rows

    async def upload_attachment(
        self, principal: Principal, agent_id: UUID, *, filename: str, content: bytes, plaza: bool = False
    ) -> FileInfo:
        async with self.database.sessions() as session:
            current = await self.store.current(session, principal, agent_id, plaza=plaza)
            binding = await self.models.bindings.resolve(session, current.config.model_id)
        mime = file_semantics(filename, content, 'chat_attachment')['mime']
        if isinstance(mime, str) and mime.startswith('image/') and not binding.vision_enabled:
            raise AppError('unsupported_capability', '当前助手模型不支持图片，请使用支持视觉的模型', 422)
        return await self.file_commands.upload(principal, filename=filename, content=content, purpose='chat_attachment')

    async def attachment_content(
        self, principal: Principal, agent_id: UUID, conversation_id: UUID, file_id: UUID
    ) -> tuple[FileInfo, bytes]:
        files = self.attachments.files

        async def require_attachment(session: AsyncSession) -> FileInfo:
            await self.store.require(session, principal, agent_id, conversation_id)
            return await files.references.require_scoped_attachment(
                session, principal, file_id, resource_type='agent_chat', resource_id=conversation_id
            )

        async with self.database.sessions() as session:
            await require_attachment(session)
            ticket = await files.read_ticket(session, principal, file_id)
        content = await files.read_content(ticket)
        async with self.database.sessions() as session:
            return await require_attachment(session), content

    async def detail(
        self, principal: Principal, agent_id: UUID, conversation_id: UUID, before: int | None = None
    ) -> dict[str, object]:
        async with self.database.sessions() as session:
            conversation = await self.store.require(session, principal, agent_id, conversation_id)
            info = conversation.info
            info.preparation_id = await self.knowledge.store.current(session, 'agent_chat', info.id)
            preparing_message: dict[str, object] | None = None
            if info.preparation_id is not None:
                preparation = await self.knowledge.store.get(session, info.preparation_id)
                preparing_message = {
                    'request_id': str(preparation.request['runtime_request_id']),
                    'content': str(preparation.request['message']),
                    'attachments': [item['summary'] for item in preparation.request.get('attachment_inputs', [])],
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
            await self.store.change(session, principal, conversation_id, expected_version, title=title, remove=remove)
            if remove:
                await self.attachments.files.references.release_resources(
                    session, resource_type='agent_chat', resource_ids=(conversation_id,), actor_id=principal.id
                )
                return value.info
            return (await self.store.require(session, principal, agent_id, conversation_id)).info

    async def _validate(
        self,
        session: AsyncSession,
        principal: Principal,
        value: AssistantConversationValue,
        *,
        lock: bool = False,
    ) -> tuple[ModelBinding, AssistantConversationValue]:
        config = value.config
        require_execution_capabilities(
            principal,
            'agent_chat',
            knowledge=bool(config.knowledge_base_ids) and not value.info.plaza,
            skills=bool(config.skill_slugs) and not value.info.plaza,
        )
        binding = await self.models.bindings.resolve(session, config.model_id)
        if binding.model_type != 'chat' or config.max_tokens > binding.max_output_tokens:
            raise AppError('assistant_model_unavailable', '助手所用模型或输出配置已不可用', 409)
        current = await self.store.current(session, principal, value.info.agent_id, lock=lock, plaza=value.info.plaza)
        if current.config != config:
            raise AppError('assistant_configuration_changed', '助手配置刚刚更新，请重新发送', 409)
        await require_frozen_available(session, principal.id, current.skills)
        configured_binding = binding.model_copy(
            update={'max_output_tokens': config.max_tokens, 'options': ModelOptions(temperature=config.temperature)}
        )
        return configured_binding, replace(value, skills=current.skills, plaza_version=current.plaza_version)

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
        self,
        principal: Principal,
        agent_id: UUID,
        conversation_id: UUID,
        message: str,
        request_id: UUID,
        file_ids: Sequence[UUID] | None = None,
    ) -> RunSnapshot | PreparationInfo:
        file_ids = tuple(file_ids or ())
        message = message if message.strip() else '请分析附件。'
        value_hash = message_fingerprint(conversation_id, message, file_ids)
        attachments: tuple[PreparedAttachment, ...] = ()
        if file_ids:
            # Authorize and return accepted replays before reading any file bytes.
            async with self.database.sessions.begin() as session:
                conversation = await self.store.require(session, principal, agent_id, conversation_id)
                existing = await self.runs.existing_request(session, principal.id, request_id, value_hash)
                if existing is not None:
                    return existing
                prior = await self.knowledge.store.existing(session, principal.id, request_id, value_hash)
                if prior is not None:
                    return self.knowledge.store.info(prior)
                await self._validate(session, principal, conversation)
            attachments = await self.attachments.prepare(principal, file_ids)
        return await self._send(principal, agent_id, conversation_id, message, request_id, value_hash, attachments)

    async def _send(
        self,
        principal: Principal,
        agent_id: UUID,
        conversation_id: UUID,
        message: str,
        request_id: UUID,
        value_hash: str,
        attachments: tuple[PreparedAttachment, ...],
    ) -> RunSnapshot | PreparationInfo:
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
            binding, conversation = await self._validate(session, principal, conversation, lock=True)
            await self.store.authorize(session, principal, conversation_id, lock=True)
            await self._idle(session, conversation)
            await self.attachments.validate(session, principal, [item.summary for item in attachments], binding)
            await self.attachments.retain(session, principal, conversation_id, attachments)
            if not conversation.config.knowledge_base_ids:
                return await self._admit(
                    session, principal, conversation, message, request_id, binding, attachments=attachments
                )
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
                        'top_k': conversation.config.knowledge_top_k,
                        'message': message,
                        'attachment_inputs': [item.model_dump(mode='json') for item in attachments],
                        'runtime_request_id': str(request_id),
                        'runtime_fingerprint': value_hash,
                    },
                    base_ids=conversation.config.knowledge_base_ids,
                    dependencies={
                        'agent_id': str(agent_id),
                        **({'plaza_version': conversation.plaza_version} if conversation.info.plaza else {}),
                        'configuration': configuration_fingerprint(conversation),
                        'main_request': ModelRequest(
                            binding=binding.model_copy(
                                update={
                                    'max_output_tokens': (
                                        await self.models.bindings.resolve(session, conversation.config.model_id)
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
        attachments: tuple[PreparedAttachment, ...] = (),
    ) -> RunSnapshot:
        number = await self._idle(session, value, prepared.intent_id if prepared else None)
        await self.attachments.validate(session, principal, [item.summary for item in attachments], binding)
        messages = (
            *((ModelMessage('system', value.config.system_prompt),) if value.config.system_prompt else ()),
            *(ModelMessage('system', skill.content) for skill in value.skills.revisions),
            self.attachments.message(message, attachments),
        )
        source_owner = principal
        if prepared:
            if value.info.plaza and value.plaza_version is not None:
                source_owner = await self.knowledge.assistant_sources(
                    session, principal, value.info.id, value.plaza_version, value.config.knowledge_base_ids, lock=True
                )
            await self.knowledge.check(session, prepared, source_owner)
            messages = (prepared.agent_prefix(), *messages)
        metadata = await self.execution.prepare(
            session, principal, value, binding, messages, request_id, number, attachments=attachments
        )
        require_execution_capabilities(
            principal,
            'agent_chat',
            knowledge=bool(value.config.knowledge_base_ids) and not value.info.plaza,
            skills=bool(value.skills.revisions) and not value.info.plaza,
        )
        run = await self.admission.reserve(
            session,
            identity=AdmissionIdentity(owner_id=principal.id, billing_owner_id=principal.id),
            resource_type='agent_chat',
            resource_id=value.info.id,
            client_request_id=request_id,
            fingerprint=message_fingerprint(value.info.id, message, [item.summary.file_id for item in attachments]),
            request=ModelRequest(
                binding=binding,
                messages=messages,
                required_modules=tuple(
                    module
                    for module, used in [
                        ('knowledge', bool(value.config.knowledge_base_ids) and not value.info.plaza),
                        ('skills', bool(value.skills.revisions) and not value.info.plaza),
                    ]
                    if used
                ),
            ),
            input_token_estimate=metadata.reservation_input_tokens,
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
            value.config.name,
            attachments=tuple(item.summary for item in attachments),
        )
        await self.agents.audit(
            session,
            run_id=run.id,
            agent_id=value.info.agent_id,
            owner_id=principal.id,
            config=value.config,
            skills=value.skills,
            knowledge=[hit.model_dump(mode='json') for hit in prepared.hits] if prepared else [],
            execution=metadata.model_dump(
                mode='json', exclude={'plaza_version'} if metadata.plaza_version is None else None
            ),
        )
        if prepared:
            await self.knowledge.bind(session, prepared, source_owner, run.id)
        return run

    async def finalize(self, session: AsyncSession, state: RetrievalState, prepared: PreparedKnowledge) -> RunSnapshot:
        principal = await self.identity.principal_for_user(session, state.owner_id)
        value = await self.store.require(
            session, principal, UUID(str(state.dependencies['agent_id'])), state.resource_id
        )
        binding, value = await self._validate(session, principal, value, lock=True)
        if value.info.plaza and value.plaza_version != state.dependencies.get('plaza_version'):
            raise AppError('assistant_plaza_knowledge_revoked', '广场知识授权已变化，请重新发送', 409)
        if configuration_fingerprint(value) != state.dependencies['configuration']:
            raise AppError('knowledge_preparation_stale', '知识准备期间助手配置已变化，请重新发送', 409)
        await self.store.authorize(session, principal, value.info.id, lock=True)
        return await self._admit(
            session,
            principal,
            value,
            str(state.request['message']),
            UUID(str(state.request['runtime_request_id'])),
            binding,
            prepared,
            attachments=tuple(
                PreparedAttachment.model_validate(item) for item in state.request.get('attachment_inputs', [])
            ),
        )

    async def check_preparation(self, session: AsyncSession, state: RetrievalState) -> None:
        principal = await self.identity.principal_for_user(session, state.owner_id)
        value = await self.store.require(
            session, principal, UUID(str(state.dependencies['agent_id'])), state.resource_id
        )
        binding, value = await self._validate(session, principal, value)
        if value.info.plaza and value.plaza_version != state.dependencies.get('plaza_version'):
            raise AppError('assistant_plaza_knowledge_revoked', '广场知识授权已变化，请重新发送', 409)
        await self.attachments.validate(
            session,
            principal,
            [PreparedAttachment.model_validate(item).summary for item in state.request.get('attachment_inputs', [])],
            binding,
        )
        if configuration_fingerprint(value) != state.dependencies['configuration']:
            raise AppError('knowledge_preparation_stale', '知识准备期间助手配置已变化，请重新发送', 409)

    async def guard(self, claim: ClaimedRun) -> None:
        await self.execution.guard(claim)
