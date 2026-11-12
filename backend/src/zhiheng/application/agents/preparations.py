"""Authorize public agent preparation reads and cancellation through one application boundary."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.application.knowledge.preparation import KnowledgePreparation
from zhiheng.infrastructure.database import Database
from zhiheng.modules.agents.public import AgentService
from zhiheng.modules.developer.public import DeveloperIdentity, KeyService
from zhiheng.modules.knowledge.public import PreparationInfo
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


class AgentPreparations:
    def __init__(
        self, database: Database, keys: KeyService, agents: AgentService, preparation: KnowledgePreparation
    ) -> None:
        self.database, self.keys, self.agents, self.preparation = database, keys, agents, preparation

    async def _authorize(
        self, session: AsyncSession, token: str, agent_id: UUID, *, lock: bool = False
    ) -> DeveloperIdentity:
        identity = await self.keys.authenticate(session, token, lock=lock)
        await self.keys.authorize_identity(session, identity, [], scope='agents:invoke')
        agent = await self.agents.get(session, Principal(identity.owner_id, '', False), agent_id, lock=lock)
        config = agent.published_config
        if config is None or config.application_id != identity.application_id:
            raise AppError('agent_not_found', 'Agent is unavailable', 404)
        return identity

    async def get(self, token: str, agent_id: UUID, intent_id: UUID) -> PreparationInfo:
        async with self.database.sessions() as session:
            identity = await self._authorize(session, token, agent_id)
            return await self.preparation.public_info(session, identity, agent_id, intent_id)

    async def cancel(self, token: str, agent_id: UUID, intent_id: UUID) -> PreparationInfo:
        async with self.database.sessions.begin() as session:
            identity = await self._authorize(session, token, agent_id, lock=True)
            result = await self.preparation.cancel_public(session, identity, agent_id, intent_id)
        await self.preparation.cancel_children_if_terminal(intent_id)
        return result
