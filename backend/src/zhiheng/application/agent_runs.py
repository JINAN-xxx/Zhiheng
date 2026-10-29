import hashlib
import json
from dataclasses import asdict
from typing import TYPE_CHECKING
from uuid import UUID, uuid4, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.application.agent_management import AgentManagementService, owner
from zhiheng.application.knowledge_preparation import PreparationStart
from zhiheng.modules.agents.public import (
    AgentConfig,
    AgentInfo,
    AgentService,
    PreviewRequest,
)
from zhiheng.modules.billing.public import UsageService, lock_billing_admission
from zhiheng.modules.developer.public import DeveloperIdentity, KeyService
from zhiheng.modules.knowledge.public import PreparationInfo, PreparedKnowledge, RetrievalState
from zhiheng.modules.knowledge.public import fingerprint as dependency_fingerprint
from zhiheng.modules.models.public import ModelMessage, ModelOptions, ModelRequest, ModelService
from zhiheng.modules.skills.public import require_available, resolve_selection
from zhiheng.runtime.contracts import RunSnapshot
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

if TYPE_CHECKING:
    from zhiheng.application.knowledge_preparation import KnowledgePreparation


class AgentRunService:
    def __init__(
        self,
        agents: AgentService,
        management: AgentManagementService,
        keys: KeyService,
        models: ModelService,
        usage: UsageService,
        runs: RunStore,
    ) -> None:
        self._agents, self._management, self._keys, self._models, self._usage, self._runs = (
            agents,
            management,
            keys,
            models,
            usage,
            runs,
        )
        self.knowledge: KnowledgePreparation | None = None

    async def preview(
        self,
        session: AsyncSession,
        principal: Principal,
        agent_id: UUID,
        body: PreviewRequest,
        token_limit: int,
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
            token_limit,
            caller_id=principal.id,
            request_id=body.client_request_id,
            fingerprint=fingerprint,
            prepared=prepared,
        )

    async def public(
        self, session: AsyncSession, token: str, agent_id: UUID, body: PreviewRequest, token_limit: int
    ) -> tuple[RunSnapshot, DeveloperIdentity, UUID]:
        await lock_billing_admission(session)
        identity = await self._keys.authenticate(session, token, lock=True)
        principal = Principal(identity.owner_id, '', False)
        try:
            agent = await self._agents.get(session, principal, agent_id)
        except AppError:
            raise AppError('agent_not_found', 'Agent is unavailable', 404) from None
        config = agent.published_config
        key = await self._keys.info_for_identity(session, identity)
        if config is None or config.application_id != identity.application_id or config.model_id not in key.model_ids:
            raise AppError('agent_not_found', 'Agent is unavailable', 404)
        try:
            run = await self._admit(
                session,
                agent,
                body,
                token_limit,
                caller_id=identity.owner_id,
                request_id=uuid4(),
                fingerprint=uuid4().hex,
                identity=identity,
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
        return run, identity, config.model_id

    async def _admit(
        self,
        session: AsyncSession,
        agent: AgentInfo,
        body: PreviewRequest,
        token_limit: int,
        *,
        caller_id: UUID,
        request_id: UUID,
        fingerprint: str,
        identity: DeveloperIdentity | None = None,
        prepared: PreparedKnowledge | None = None,
    ) -> RunSnapshot:
        await lock_billing_admission(session)
        config = (
            agent.published_config
            if identity
            else AgentConfig.model_validate(agent.model_dump(include=set(AgentConfig.model_fields)))
        )
        assert config is not None
        binding = await self._management.validate(session, owner(agent), config, publish=identity is not None)
        await self._agents.get(session, owner(agent), agent.id, lock=True, expected_version=agent.version)
        if identity is not None:
            await require_available(session, config.skill_slugs)
            assert agent.published_version is not None
            selection = await self._agents.published_selection(session, owner(agent), agent.id, agent.published_version)
        else:
            selection = await resolve_selection(session, config.skill_slugs)
        rate = await self._usage.money_rate(session, self._models, config.model_id, required=identity is not None)
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
            messages = prepared.apply_context(messages)
        resource_type = 'agent_invocation' if identity else 'agent_preview'
        request = ModelRequest(binding=binding, messages=messages)
        run = await self._runs.create(
            session,
            owner_id=caller_id,
            billing_owner_id=agent.owner_id,
            resource_type=resource_type,
            resource_id=agent.id,
            request_id=request_id,
            fingerprint=fingerprint,
            payload=request.model_dump(mode='json'),
        )
        input_tokens = sum(
            len(item.content.encode('utf-8')) + 16 + sum(image.estimated_tokens for image in item.images)
            for item in request.messages
        )
        tokens = input_tokens + request.binding.max_output_tokens
        await self._usage.reserve(
            session,
            user_id=agent.owner_id,
            run_id=run.id,
            model_id=config.model_id,
            source=resource_type,
            tokens=tokens,
            estimated_input_tokens=input_tokens,
            max_output_tokens=request.binding.max_output_tokens,
            token_limit=token_limit,
            key_id=identity.key_id if identity else None,
            rate=rate,
            operation='chat',
        )
        await self._agents.audit(
            session,
            run_id=run.id,
            agent=agent,
            config=config,
            skills=selection,
            published=identity is not None,
            knowledge=[hit.model_dump(mode='json') for hit in prepared.hits] if prepared else [],
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
    ) -> RunSnapshot | PreparationInfo:
        assert self.knowledge is not None
        prep = self.knowledge
        kind = 'agent_invocation' if identity else 'agent_preview'
        # Keep the private preview's existing fingerprint and caller namespace.
        value = hashlib.sha256(
            json.dumps(
                [kind, str(agent_id), [message.model_dump() for message in body.messages]]
                + ([response_stream] if identity else []),
                ensure_ascii=False,
                separators=(',', ':'),
            ).encode()
        ).hexdigest()
        request_id = uuid5(identity.key_id, str(body.client_request_id)) if identity else body.client_request_id
        namespace = identity.key_id if identity else principal.id
        async with prep.database.sessions.begin() as session:
            await lock_billing_admission(session)
            if identity:
                await self._keys.authorize_identity(session, identity, [], lock=True)
            agent = await self._agents.get(session, principal, agent_id)
            config = (
                agent.published_config
                if identity
                else AgentConfig.model_validate(agent.model_dump(include=set(AgentConfig.model_fields)))
            )
            if config is None:
                raise AppError('agent_not_found', 'Agent is unavailable', 404)
            if identity and config.application_id != identity.application_id:
                raise AppError('agent_not_found', 'Agent is unavailable', 404)
            await self._runs.lock_request(session, principal.id, request_id)
            existing = await self._runs.existing_request(session, principal.id, request_id, value)
            if existing:
                if identity:
                    await prep.authorize_public_run(session, identity, agent_id, existing.id)
                return existing
            intent = await prep.store.existing(session, namespace, request_id, value)
            if intent:
                if identity:
                    original = ModelRequest.model_validate(intent.dependencies['main_request']).binding.model_id
                    await self._keys.authorize_identity(session, identity, [original], lock=True)
                return prep.store.info(intent)
            if identity:
                await self._keys.authorize_identity(session, identity, [config.model_id], lock=True)
            binding = await self._management.validate(session, owner(agent), config, publish=identity is not None)
            selection = (
                await self._agents.published_selection(session, owner(agent), agent_id, agent.published_version)
                if identity and agent.published_version
                else await resolve_selection(session, config.skill_slugs)
            )
            dependencies = {
                'agent_version': agent.version,
                'config': config.model_dump(mode='json'),
                'skills_hash': dependency_fingerprint([asdict(item) for item in selection.revisions]),
                'main_request': ModelRequest(binding=binding, messages=()).model_dump(mode='json'),
            }
            if not config.knowledge_base_ids:
                return await self._admit(
                    session,
                    agent,
                    body,
                    prep.token_limit,
                    caller_id=principal.id,
                    request_id=request_id,
                    fingerprint=value,
                    identity=identity,
                )
            request: dict[str, object] = {
                'query': body.messages[-1].content,
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
            agent.published_config
            if identity
            else AgentConfig.model_validate(agent.model_dump(include=set(AgentConfig.model_fields)))
        )
        if (
            config is None
            or agent.version != state.dependencies['agent_version']
            or config.model_dump(mode='json') != state.dependencies['config']
        ):
            raise AppError('knowledge_preparation_stale', '智能体配置已变化', 409)
        selection = (
            await self._agents.published_selection(session, owner(agent), agent.id, agent.published_version)
            if identity and agent.published_version
            else await resolve_selection(session, config.skill_slugs)
        )
        if dependency_fingerprint([asdict(item) for item in selection.revisions]) != state.dependencies['skills_hash']:
            raise AppError('knowledge_preparation_stale', '技能依赖已变化', 409)
        return await self._admit(
            session,
            agent,
            PreviewRequest.model_validate(state.request['body']),
            self.knowledge.token_limit,
            caller_id=state.actor_id,
            request_id=UUID(state.request['runtime_request_id']),
            fingerprint=state.request['runtime_fingerprint'],
            identity=identity,
            prepared=prepared,
        )

    async def check_knowledge_dependencies(self, session: AsyncSession, state: RetrievalState) -> None:
        assert self.knowledge is not None
        principal = await self.knowledge.knowledge.identity.principal_for_user(session, state.owner_id)
        agent = await self._agents.get(session, principal, state.resource_id)
        config = (
            agent.published_config
            if state.key_id
            else AgentConfig.model_validate(agent.model_dump(include=set(AgentConfig.model_fields)))
        )
        if (
            config is None
            or agent.version != state.dependencies['agent_version']
            or config.model_dump(mode='json') != state.dependencies['config']
        ):
            raise AppError('knowledge_preparation_stale', '智能体配置已变化', 409)
        selection = (
            await self._agents.published_selection(session, principal, agent.id, agent.published_version)
            if state.key_id and agent.published_version
            else await resolve_selection(session, config.skill_slugs)
        )
        if dependency_fingerprint([asdict(item) for item in selection.revisions]) != state.dependencies['skills_hash']:
            raise AppError('knowledge_preparation_stale', '技能依赖已变化', 409)
