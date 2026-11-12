"""Compose billing management through public domain services in one transaction."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.billing.public import (
    CreditAccountStatePatch,
    CreditAdjustment,
    CreditRateCreate,
    CreditRateInfo,
    CreditReconcile,
    CreditReservation,
    CreditService,
    CreditState,
    CreditStatePatch,
    CreditSummary,
    UsageService,
    lock_billing_switch,
)
from zhiheng.modules.identity.public import IdentityService
from zhiheng.modules.knowledge.public import KnowledgeService
from zhiheng.modules.models.public import ModelService
from zhiheng.modules.operations.public import OperationsService
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


class BillingManagementService:
    def __init__(
        self,
        credit_service: CreditService,
        usage: UsageService,
        identity: IdentityService,
        models: ModelService,
        operations: OperationsService,
        runs: RunStore,
        knowledge: KnowledgeService,
    ) -> None:
        self._credits, self._usage, self._identity, self._models = credit_service, usage, identity, models
        self._operations, self._runs, self._knowledge = operations, runs, knowledge

    async def accounts(
        self,
        session: AsyncSession,
        actor: Principal,
        *,
        search: str = '',
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, object]:
        directory = await self._identity.directory_page(session, actor, search=search, page=page, page_size=page_size)
        summaries = await self._credits.summaries(session, [entry.id for entry in directory.items])
        return {
            'items': [
                {
                    **summary.model_dump(mode='json'),
                    'username': entry.username,
                    'display_name': entry.display_name,
                    'is_active': entry.is_active,
                }
                for entry, summary in zip(directory.items, summaries, strict=True)
            ],
            'total': directory.total,
            'page': directory.page,
            'page_size': directory.page_size,
        }

    async def adjust(
        self,
        session: AsyncSession,
        actor: Principal,
        user_id: UUID,
        data: CreditAdjustment,
    ) -> CreditSummary:
        actor.require_permission('ai:credits:write')
        await self._identity.directory_user(session, actor, user_id, require_active=data.kind == 'grant', lock=True)
        result = await self._credits.adjust(session, actor, user_id, data)
        await self._record(session, actor, f'billing.{data.kind}', user_id)
        return result

    async def set_account_state(
        self,
        session: AsyncSession,
        actor: Principal,
        user_id: UUID,
        data: CreditAccountStatePatch,
    ) -> CreditSummary:
        actor.require_permission('ai:credits:write')
        await self._identity.directory_user(session, actor, user_id, lock=True)
        result = await self._credits.set_account_state(session, actor, user_id, data)
        await self._record(session, actor, 'billing.resume' if data.enabled else 'billing.pause', user_id)
        return result

    async def set_state(self, session: AsyncSession, actor: Principal, data: CreditStatePatch) -> CreditState:
        actor.require_admin()
        await lock_billing_switch(session)
        result = await self._credits.set_state(session, actor, data, before_change=self._before_switch)
        await self._record(session, actor, 'billing.enable' if data.enabled else 'billing.disable')
        return result

    async def _before_switch(self, session: AsyncSession, enabled: bool) -> None:
        if await self._runs.has_active_work(session) or await self._knowledge.has_active_work(session):
            raise AppError('billing_switch_busy', '仍有进行中的运行或知识任务，请等待完成后再切换积分计费', 409)
        if enabled:
            models = await self._models.billing_models(session)
            missing = [model.name for model in models if await self._credits.quote(session, model.id) is None]
            if missing:
                raise AppError('credit_rates_incomplete', f'以下已启用模型尚未配置积分费率：{"、".join(missing)}', 409)

    async def ledger(
        self,
        session: AsyncSession,
        actor: Principal,
        user_id: UUID,
        *,
        page: int = 1,
        page_size: int = 20,
    ) -> dict[str, object]:
        await self._target(session, actor, user_id)
        return await self._credits.ledger(session, actor, user_id=user_id, page=page, page_size=page_size)

    async def reservations(
        self,
        session: AsyncSession,
        actor: Principal,
        user_id: UUID,
        *,
        page: int = 1,
        page_size: int = 20,
        status: str | None = None,
    ) -> dict[str, object]:
        await self._target(session, actor, user_id)
        return await self._credits.reservations(
            session, actor, user_id=user_id, page=page, page_size=page_size, status=status
        )

    async def _target(self, session: AsyncSession, actor: Principal, user_id: UUID) -> None:
        if user_id != actor.id:
            actor.require_any_permission('ai:credits:read', 'ai:credits:write')
            await self._identity.directory_user(session, actor, user_id)

    async def rates(self, session: AsyncSession, actor: Principal, model_id: UUID) -> list[CreditRateInfo]:
        actor.require_any_permission('ai:rates:read', 'ai:rates:write')
        await self._models.billing_metadata(session, model_id)
        return await self._credits.rates(session, actor, model_id)

    async def create_rate(
        self,
        session: AsyncSession,
        actor: Principal,
        model_id: UUID,
        data: CreditRateCreate,
    ) -> CreditRateInfo:
        actor.require_permission('ai:rates:write')
        model = await self._models.billing_metadata(session, model_id)
        result = await self._credits.create_rate(session, actor, model_id, data, model_type=model.model_type)
        await self._record(session, actor, 'billing.rate_create', model_id)
        return result

    async def quote(self, session: AsyncSession, actor: Principal, model_id: UUID) -> CreditRateInfo | None:
        if not await self._models.is_public(session, model_id):
            if not actor.has_any_permission('ai:rates:read', 'ai:rates:write', 'ai:models:read', 'ai:models:write'):
                raise AppError('model_not_found', '模型不可用', 404)
            await self._models.billing_metadata(session, model_id)
        return await self._credits.quote(session, model_id)

    async def reconcile(
        self,
        session: AsyncSession,
        actor: Principal,
        run_id: UUID,
        data: CreditReconcile,
    ) -> CreditReservation:
        actor.require_permission('ai:credits:write')
        try:
            result = await self._usage.reconcile(session, actor, run_id, data)
        except ValueError as exc:
            raise AppError('invalid_usage', '实际用量无效，缓存 Token 不能超过输入 Token', 422) from exc
        await self._record(session, actor, 'billing.reconcile', run_id)
        return result

    async def _record(
        self, session: AsyncSession, actor: Principal, action: str, identifier: UUID | None = None
    ) -> None:
        await self._operations.record(
            session,
            principal=actor,
            action=action,
            resource_type='billing',
            resource_id=str(identifier) if identifier else None,
            message='积分管理操作已完成',
        )
