import hashlib
import json
from dataclasses import asdict
from typing import TYPE_CHECKING
from uuid import UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.application.agents.management import AgentManagementService, owner
from zhiheng.application.execution.admission import AdmissionIdentity, RunAdmission
from zhiheng.application.knowledge.preparation import PreparationStart
from zhiheng.modules.agents.public import (
    AgentConfig,
    AgentInfo,
    AgentPublicRequest,
    AgentService,
    AssistantConversations,
    AssistantConversationValue,
    PreviewRequest,
)
from zhiheng.modules.billing.public import lock_billing_admission
from zhiheng.modules.developer.public import DeveloperIdentity, KeyService
from zhiheng.modules.identity.public import IdentityService
from zhiheng.modules.knowledge.public import PreparationInfo, PreparedKnowledge, RetrievalState
from zhiheng.modules.knowledge.public import fingerprint as dependency_fingerprint
from zhiheng.modules.models.public import ModelMessage, ModelOptions, ModelRequest
from zhiheng.modules.skills.public import require_available, require_frozen_available, resolve_selection
from zhiheng.runtime.contracts import TERMINAL_STATUSES, RunSnapshot
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.errors import AppError
from zhiheng.shared.platform_permissions import require_capability
from zhiheng.shared.principal import Principal

if TYPE_CHECKING:
    from zhiheng.application.agents.execution import AssistantAgentService
    from zhiheng.application.knowledge.preparation import KnowledgePreparation


class AgentRunService:
    def __init__(
        self,
        agents: AgentService,
        management: AgentManagementService,
        keys: KeyService,
        runs: RunStore,
        identity: IdentityService,
        *,
        admission: RunAdmission,
    ) -> None:
        self._agents, self._management, self._keys, self._runs = (
            agents,
            management,
            keys,
            runs,
        )
        self.identity, self.admission = identity, admission
        self.knowledge: KnowledgePreparation | None = None
        self.execution: AssistantAgentService | None = None
        self.conversations = AssistantConversations()

    @staticmethod
    def public_conversation_id(identity: DeveloperIdentity, request_id: UUID) -> UUID:
        return uuid5(identity.key_id, 'assistant-conversation:' + str(request_id))

    async def _api_turn(
        self, session: AsyncSession, value: AssistantConversationValue, prepared: PreparedKnowledge | None = None
    ) -> int:
        assert self.knowledge is not None
        await self.knowledge.store.ensure_idle(
            session,
            'agent_invocation',
            value.info.agent_id,
            prepared.intent_id if prepared else None,
            conversation_id=value.info.id,
        )
        turns = await self.conversations.turns(session, value.info.id, limit=1)
        if not turns:
            return 1
        latest = await self._runs.resource_snapshot(session, turns[0].run_id, 'agent_invocation', value.info.agent_id)
        if latest.status not in TERMINAL_STATUSES:
            raise AppError('assistant_conversation_busy', '当前会话正在回复，请等待后再发送', 409)
        return turns[0].sequence + 1

    async def preview(
        self,
        session: AsyncSession,
        principal: Principal,
        agent_id: UUID,
        body: PreviewRequest,
        prepared: PreparedKnowledge | None = None,
    ) -> RunSnapshot:
        await lock_billing_admission(session)
        fingerprint = hashlib.sha256(
            json.dumps(
                ['agent_preview', str(agent_id), [message.model_dump() for message in body.messages]],
                ensure_ascii=False,
                separators=(',', ':'),
            ).encode()
        ).hexdigest()
        await self._runs.lock_request(session, principal.id, body.client_request_id)
        # Authorization still applies to idempotent reads after an agent is removed.
        agent = await self._agents.get(session, principal, agent_id)
        existing = await self._runs.existing_request(session, principal.id, body.client_request_id, fingerprint)
        if existing is not None:
            return existing
        return await self._admit(
            session,
            agent,
            body,
            caller_id=principal.id,
            request_id=body.client_request_id,
            fingerprint=fingerprint,
            prepared=prepared,
        )

    async def submit_public(
        self, token: str, agent_id: UUID, body: AgentPublicRequest, request_id: UUID
    ) -> tuple[RunSnapshot | PreparationInfo, DeveloperIdentity, UUID]:
        assert self.knowledge is not None
        async with self.knowledge.database.sessions() as session:
            identity = await self._keys.authenticate(session, token)
        try:
            prepared = await self.prepare_knowledge_agent(
                Principal(identity.owner_id, '', False),
                agent_id,
                PreviewRequest(messages=[{'role': 'user', 'content': body.message}], client_request_id=request_id),
                identity,
                response_stream=body.stream,
                api_user=body.user,
                conversation_id=body.conversation_id,
            )
        except AppError as error:
            if error.code in {
                'model_unavailable',
                'agent_version_conflict',
                'skill_unavailable',
                'application_not_found',
                'application_conflict',
            }:
                raise AppError('agent_not_found', 'Agent is unavailable', 404) from None
            raise
        return prepared, identity, body.conversation_id or self.public_conversation_id(identity, request_id)

    async def _admit(
        self,
        session: AsyncSession,
        agent: AgentInfo,
        body: PreviewRequest,
        *,
        caller_id: UUID,
        request_id: UUID,
        fingerprint: str,
        identity: DeveloperIdentity | None = None,
        prepared: PreparedKnowledge | None = None,
        conversation: AssistantConversationValue | None = None,
    ) -> RunSnapshot:
        await lock_billing_admission(session)
        config = (
            (agent.configuration if agent.enabled else None)
            if identity
            else AgentConfig.model_validate(agent.model_dump(include=set(AgentConfig.model_fields)))
        )
        assert config is not None
        actor = await self.identity.principal_for_user(session, agent.owner_id)
        require_capability(actor, 'agents', 'use' if identity else 'debug')
        if config.knowledge_base_ids:
            require_capability(actor, 'knowledge', 'query')
        if config.skill_slugs:
            require_capability(actor, 'skills', 'use')
        binding = await self._management.validate(session, owner(agent), config, publish=identity is not None)
        await self._agents.get(session, owner(agent), agent.id, lock=True, expected_version=agent.version)
        if identity is not None:
            await require_available(session, agent.owner_id, config.skill_slugs)
            assert agent.enabled
            selection = await resolve_selection(session, agent.owner_id, config.skill_slugs)
        else:
            selection = await resolve_selection(session, agent.owner_id, config.skill_slugs)
        await require_frozen_available(session, agent.owner_id, selection)
        binding = binding.model_copy(
            update={'max_output_tokens': config.max_tokens, 'options': ModelOptions(temperature=config.temperature)}
        )
        skill_messages = tuple(ModelMessage('system', item.content) for item in selection.revisions)
        messages = (
            *((ModelMessage('system', config.system_prompt),) if config.system_prompt else ()),
            *skill_messages,
            *(ModelMessage(item.role, item.content) for item in body.messages),
        )
        # Retrieval is not wired in this slice. Never await external retrieval while
        # holding application/model/agent or budget SQL locks. The knowledge owner
        # will prepare bounded context outside this admission transaction.
        if config.knowledge_base_ids and prepared is None:
            raise AppError('knowledge_unavailable', '知识检索服务暂不可用', 503)
        if prepared and self.knowledge:
            knowledge_owner = await self.knowledge.knowledge.identity.principal_for_user(session, agent.owner_id)
            await self.knowledge.check(session, prepared, knowledge_owner)
            messages = (*prepared.agent_prefix(), *messages) if conversation else prepared.apply_context(messages)
        if self.execution is None:
            raise AppError('assistant_execution_unavailable', '智能助手执行服务未装配', 503)
        if conversation:
            sequence = await self._api_turn(session, conversation, prepared)
            metadata = await self.execution.prepare(
                session,
                actor,
                conversation,
                binding,
                messages,
                request_id,
                sequence,
                api=True,
                reference_message_index=1 if prepared else None,
            )
        else:
            metadata = self.execution.settings(
                binding, messages, message_ids=tuple(f'{request_id}:{index}' for index in range(len(messages)))
            )
        resource_type = 'agent_invocation' if identity else 'agent_preview'
        request = ModelRequest(
            binding=binding,
            messages=messages,
            required_modules=tuple(
                module
                for module, used in [
                    ('knowledge', bool(config.knowledge_base_ids)),
                    ('skills', bool(config.skill_slugs)),
                ]
                if used
            ),
        )
        run = await self.admission.reserve(
            session,
            identity=AdmissionIdentity(
                owner_id=caller_id, billing_owner_id=agent.owner_id, key_id=identity.key_id if identity else None
            ),
            resource_type=resource_type,
            resource_id=agent.id,
            client_request_id=request_id,
            fingerprint=fingerprint,
            request=request,
            input_token_estimate=metadata.reservation_input_tokens if conversation else None,
        )
        if conversation:
            await self.conversations.add_turn(
                session,
                actor,
                conversation.info.id,
                request_id,
                body.messages[-1].content,
                run.id,
                metadata.sequence,
                [hit.model_dump(mode='json') for hit in prepared.hits] if prepared else [],
                config.name,
                api=True,
            )
        await self._agents.audit(
            session,
            run_id=run.id,
            agent_id=agent.id,
            owner_id=agent.owner_id,
            config=config,
            skills=selection,
            knowledge=[hit.model_dump(mode='json') for hit in prepared.hits] if prepared else [],
            execution=metadata.model_dump(mode='json'),
        )
        if prepared and self.knowledge:
            knowledge_owner = await self.knowledge.knowledge.identity.principal_for_user(session, agent.owner_id)
            await self.knowledge.bind(session, prepared, knowledge_owner, run.id)
        return run

    async def prepare_knowledge_agent(
        self,
        principal: Principal,
        agent_id: UUID,
        body: PreviewRequest,
        identity: DeveloperIdentity | None = None,
        *,
        response_stream: bool = False,
        api_user: str | None = None,
        conversation_id: UUID | None = None,
    ) -> RunSnapshot | PreparationInfo:
        assert self.knowledge is not None
        prep = self.knowledge
        kind = 'agent_invocation' if identity else 'agent_preview'
        # Keep the private preview's existing fingerprint and caller namespace.
        value = hashlib.sha256(
            json.dumps(
                [kind, str(agent_id), [message.model_dump() for message in body.messages]]
                + ([response_stream, api_user, str(conversation_id) if conversation_id else None] if identity else []),
                ensure_ascii=False,
                separators=(',', ':'),
            ).encode()
        ).hexdigest()
        request_id = uuid5(identity.key_id, str(body.client_request_id)) if identity else body.client_request_id
        namespace = identity.key_id if identity else principal.id
        async with prep.database.sessions.begin() as session:
            await lock_billing_admission(session)
            if identity:
                await self._keys.authorize_agent(session, identity, agent_id, lock=True)
                agent = await self._agents.require_enabled_application(
                    session, principal, agent_id, identity.application_id
                )
            else:
                agent = await self._agents.get(session, principal, agent_id)
            conversation = None
            if identity is not None and api_user is not None:
                api_owner = (identity.application_id, api_user)
                if conversation_id is None:
                    created = await self.conversations.create(
                        session,
                        principal,
                        agent_id,
                        self.public_conversation_id(identity, body.client_request_id),
                        api_owner=api_owner,
                    )
                    conversation_id = created.id
                conversation = await self.conversations.require(
                    session,
                    principal,
                    agent_id,
                    conversation_id,
                    lock=True,
                    api_owner=api_owner,
                )
            config = agent.configuration
            await self._runs.lock_request(session, principal.id, request_id)
            existing = await self._runs.existing_request(session, principal.id, request_id, value)
            if existing:
                if identity:
                    await prep.authorize_public_run(session, identity, agent_id, existing.id)
                return existing
            intent = await prep.store.existing(session, namespace, request_id, value)
            if intent:
                if identity:
                    return await prep.public_info(session, identity, agent_id, intent.id)
                return prep.store.info(intent)
            if conversation:
                await self._api_turn(session, conversation)
            binding = await self._management.validate(session, owner(agent), config, publish=identity is not None)
            selection = await resolve_selection(session, agent.owner_id, config.skill_slugs)
            dependencies = {
                'agent_version': agent.version,
                'config': config.model_dump(mode='json'),
                'skills_hash': dependency_fingerprint([asdict(item) for item in selection.revisions]),
                'main_request': ModelRequest(binding=binding, messages=()).model_dump(mode='json'),
                **({'api_conversation_id': str(conversation.info.id), 'api_user': api_user} if conversation else {}),
            }
            if not config.knowledge_base_ids:
                return await self._admit(
                    session,
                    agent,
                    body,
                    caller_id=principal.id,
                    request_id=request_id,
                    fingerprint=value,
                    identity=identity,
                    conversation=conversation,
                )
            request: dict[str, object] = {
                'query': body.messages[-1].content,
                'top_k': config.knowledge_top_k,
                'body': body.model_dump(mode='json'),
                'runtime_request_id': str(request_id),
                'runtime_fingerprint': value,
            }
            return await prep.start(
                session,
                principal=principal,
                identity=identity,
                body=PreparationStart(
                    owner_id=agent.owner_id,
                    resource_type=kind,
                    resource_id=agent_id,
                    request_id=request_id,
                    value=value,
                    request=request,
                    base_ids=config.knowledge_base_ids,
                    dependencies=dependencies,
                    namespace_id=namespace,
                ),
            )

    async def finalize_knowledge(
        self, session: AsyncSession, state: RetrievalState, prepared: PreparedKnowledge
    ) -> RunSnapshot:
        assert self.knowledge is not None
        principal = await self.knowledge.knowledge.identity.principal_for_user(session, state.actor_id)
        agent = await self._agents.get(session, principal, state.resource_id)
        identity = (
            DeveloperIdentity(state.application_id, state.key_id, state.owner_id)
            if state.key_id is not None and state.application_id is not None
            else None
        )
        config = (
            (agent.configuration if agent.enabled else None)
            if identity
            else AgentConfig.model_validate(agent.model_dump(include=set(AgentConfig.model_fields)))
        )
        if (
            config is None
            or agent.version != state.dependencies['agent_version']
            or config.model_dump(mode='json') != state.dependencies['config']
        ):
            raise AppError('knowledge_preparation_stale', '智能体配置已变化', 409)
        selection = await resolve_selection(session, agent.owner_id, config.skill_slugs)
        if dependency_fingerprint([asdict(item) for item in selection.revisions]) != state.dependencies['skills_hash']:
            raise AppError('knowledge_preparation_stale', '技能依赖已变化', 409)
        conversation = None
        if identity and state.dependencies.get('api_conversation_id'):
            conversation = await self.conversations.require(
                session,
                principal,
                agent.id,
                UUID(str(state.dependencies['api_conversation_id'])),
                lock=True,
                api_owner=(identity.application_id, str(state.dependencies['api_user'])),
            )
        return await self._admit(
            session,
            agent,
            PreviewRequest.model_validate(state.request['body']),
            caller_id=state.actor_id,
            request_id=UUID(state.request['runtime_request_id']),
            fingerprint=state.request['runtime_fingerprint'],
            identity=identity,
            prepared=prepared,
            conversation=conversation,
        )

    async def check_knowledge_dependencies(self, session: AsyncSession, state: RetrievalState) -> None:
        assert self.knowledge is not None
        principal = await self.knowledge.knowledge.identity.principal_for_user(session, state.owner_id)
        agent = await self._agents.get(session, principal, state.resource_id)
        config = (
            (agent.configuration if agent.enabled else None)
            if state.key_id
            else AgentConfig.model_validate(agent.model_dump(include=set(AgentConfig.model_fields)))
        )
        if (
            config is None
            or agent.version != state.dependencies['agent_version']
            or config.model_dump(mode='json') != state.dependencies['config']
        ):
            raise AppError('knowledge_preparation_stale', '智能体配置已变化', 409)
        selection = await resolve_selection(session, agent.owner_id, config.skill_slugs)
        if dependency_fingerprint([asdict(item) for item in selection.revisions]) != state.dependencies['skills_hash']:
            raise AppError('knowledge_preparation_stale', '技能依赖已变化', 409)
