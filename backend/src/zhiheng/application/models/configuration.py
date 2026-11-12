"""Validate a frozen provider configuration outside SQL transactions before committing it."""

from uuid import UUID

from zhiheng.infrastructure.database import Database
from zhiheng.modules.identity.public import IdentityService
from zhiheng.modules.models.public import ModelInfo, ModelService
from zhiheng.shared.principal import Principal


class ModelConfigurationService:
    def __init__(self, database: Database, models: ModelService, identity: IdentityService) -> None:
        self.database, self.models, self.identity = database, models, identity

    async def save(self, principal: Principal, fields: dict[str, object], model_id: UUID | None = None) -> ModelInfo:
        async with self.database.sessions() as session:
            check = await self.models.prepare_save(session, principal, fields, model_id)
        await check.provider.validate()
        async with self.database.sessions.begin() as session:
            current = await self.identity.principal_for_user(session, principal.id)
            return await self.models.save_verified(session, current, check)
