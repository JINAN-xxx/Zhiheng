"""Application lifecycle preserves audit and validates reactivated grants."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.developer.public import ApplicationInfo, ApplicationService, KeyService
from zhiheng.modules.models.public import ModelService
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.references import reference_conflict


class DeveloperManagementService:
    def __init__(self, applications: ApplicationService, keys: KeyService, models: ModelService) -> None:
        self._applications, self._keys, self._models = applications, keys, models

    async def transition(
        self, session: AsyncSession, principal: Principal, application_id: UUID, action: str
    ) -> ApplicationInfo:
        await self._applications.require(session, principal, application_id, lock=True)
        if action == 'enable':
            model_ids = await self._keys.application_model_ids(session, application_id)
            try:
                await self._models.resolve_many(session, model_ids)
            except AppError as error:
                raise reference_conflict(
                    'application_dependencies_unavailable',
                    '应用密钥关联的模型已删除或不可用，请先调整或撤销相关密钥',
                    principal,
                    await self._keys.application_references(session, application_id),
                ) from error
        return await self._applications.transition(session, principal, application_id, action)
