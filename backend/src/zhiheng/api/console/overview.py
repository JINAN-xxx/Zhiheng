"""Workbench summaries use Cookie identity and explicit platform-statistics authority."""

from typing import Annotated, Final, Literal

from fastapi import APIRouter, Query

from zhiheng.api.common.dependencies import PrincipalDependency, SessionDependency
from zhiheng.application.overview import WorkbenchService
from zhiheng.modules.overview.public import WorkbenchOverview
from zhiheng.shared.errors import AppError

router = APIRouter(prefix='/api/overview', tags=['overview'])
SHORT_PERIOD: Final = 7


@router.get('')
async def overview(
    principal: PrincipalDependency,
    session: SessionDependency,
    scope: Literal['mine', 'platform'] = 'mine',
    days: Annotated[int, Query(ge=7, le=30)] = 7,
) -> WorkbenchOverview:
    if days not in {7, 30}:
        raise AppError('invalid_overview_period', '仅支持近7天或近30天', 422)
    await session.connection(execution_options={'isolation_level': 'REPEATABLE READ', 'postgresql_readonly': True})
    return await WorkbenchService().read(session, principal, scope, SHORT_PERIOD if days == SHORT_PERIOD else 30)
