from fastapi import APIRouter

from zhiheng.api.common.dependencies import ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.modules.developer.public import UsageSummary

router = APIRouter(prefix='/api/usage', tags=['usage'])


@router.get('')
async def usage(
    container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> UsageSummary:
    return await container.usage.summary(session, principal.id, token_limit=container.settings.token_limit)
