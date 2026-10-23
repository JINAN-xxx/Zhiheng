import hashlib
import json
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.application.agent_management import AgentManagementService, owner
from zhiheng.modules.agents.public import (
    AgentConfig,
    AgentInfo,
    AgentService,
    PreviewRequest,
)
from zhiheng.modules.developer.public import DeveloperIdentity, KeyService, UsageService
from zhiheng.modules.models.public import ModelMessage, ModelOptions, ModelRequest, ModelService
from zhiheng.modules.skills.public import require_available, resolve_selection
from zhiheng.runtime.contracts import RunSnapshot
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


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

    async def preview(
        self, session: AsyncSession, principal: Principal, agent_id: UUID, body: PreviewRequest, token_limit: int
    ) -> RunSnapshot:
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
        )

    async def public(
        self, session: AsyncSession, token: str, agent_id: UUID, body: PreviewRequest, token_limit: int
    ) -> tuple[RunSnapshot, DeveloperIdentity, UUID]:
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
    ) -> RunSnapshot:
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
        rate = await self._models.latest_rate(session, config.model_id) if identity else None
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
        if config.knowledge_base_ids:
            raise AppError('knowledge_unavailable', '知识检索服务暂不可用', 503)
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
        tokens = sum(len(item.content.encode('utf-8')) + 16 for item in messages) + config.max_tokens
        await self._usage.reserve(
            session,
            user_id=agent.owner_id,
            run_id=run.id,
            model_id=config.model_id,
            source=resource_type,
            tokens=tokens,
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
            knowledge=[],
        )
        return run
