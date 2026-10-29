"""Authenticated credit balances and explicitly authorized administration."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query

from zhiheng.api.dependencies import ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.application.billing_management import BillingManagementService
from zhiheng.modules.billing.public import (
    CreditAccountStatePatch,
    CreditAdjustment,
    CreditRateCreate,
    CreditRateInfo,
    CreditReconcile,
    CreditReservation,
    CreditState,
    CreditStatePatch,
    CreditSummary,
)

router = APIRouter(prefix='/api/billing', tags=['billing'])
PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=200)]
UsageStatus = Literal['reserved', 'unknown', 'settled']


def _management(container: ContainerDependency) -> BillingManagementService:
    return BillingManagementService(
        container.credits,
        container.usage,
        container.identity,
        container.models,
        container.operations,
        container.runs,
        container.knowledge,
    )


@router.get('/state')
async def state(
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> CreditState:
    return await container.credits.state(session)


@router.patch('/state')
async def set_state(
    body: CreditStatePatch,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> CreditState:
    async with session.begin():
        return await _management(container).set_state(session, principal, body)


@router.get('/me')
async def me(
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> CreditSummary:
    return await container.credits.summary(session, principal.id)


@router.get('/me/ledger')
async def my_ledger(
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    page: PageNumber = 1,
    page_size: PageSize = 20,
) -> dict[str, object]:
    return await container.credits.ledger(session, principal, page=page, page_size=page_size)


@router.get('/me/usage')
async def my_usage(
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    status: UsageStatus | None = None,
) -> dict[str, object]:
    return await container.credits.reservations(session, principal, page=page, page_size=page_size, status=status)


@router.get('/accounts')
async def accounts(
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    search: Annotated[str, Query(max_length=200)] = '',
    page: PageNumber = 1,
    page_size: PageSize = 20,
) -> dict[str, object]:
    return await _management(container).accounts(session, principal, search=search, page=page, page_size=page_size)


@router.post('/accounts/{user_id}/adjust')
async def adjust(
    user_id: UUID,
    body: CreditAdjustment,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> CreditSummary:
    async with session.begin():
        return await _management(container).adjust(session, principal, user_id, body)


@router.patch('/accounts/{user_id}/state')
async def account_state(
    user_id: UUID,
    body: CreditAccountStatePatch,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> CreditSummary:
    async with session.begin():
        return await _management(container).set_account_state(session, principal, user_id, body)


@router.get('/accounts/{user_id}/ledger')
async def account_ledger(
    user_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    page: PageNumber = 1,
    page_size: PageSize = 20,
) -> dict[str, object]:
    return await _management(container).ledger(session, principal, user_id, page=page, page_size=page_size)


@router.get('/accounts/{user_id}/usage')
async def account_usage(
    user_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    status: UsageStatus | None = None,
) -> dict[str, object]:
    return await _management(container).reservations(
        session, principal, user_id, page=page, page_size=page_size, status=status
    )


@router.get('/models/{model_id}/rates')
async def rates(
    model_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> list[CreditRateInfo]:
    return await _management(container).rates(session, principal, model_id)


@router.post('/models/{model_id}/rates', status_code=201)
async def create_rate(
    model_id: UUID,
    body: CreditRateCreate,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> CreditRateInfo:
    async with session.begin():
        return await _management(container).create_rate(session, principal, model_id, body)


@router.get('/models/{model_id}/quote')
async def quote(
    model_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> CreditRateInfo | None:
    return await _management(container).quote(session, principal, model_id)


@router.post('/usage/{run_id}/reconcile')
async def reconcile(
    run_id: UUID,
    body: CreditReconcile,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> CreditReservation:
    async with session.begin():
        return await _management(container).reconcile(session, principal, run_id, body)
