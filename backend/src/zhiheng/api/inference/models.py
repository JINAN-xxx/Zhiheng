from fastapi import APIRouter, Depends, Request
from fastapi.security import HTTPBearer

from zhiheng.api.common.auth import bearer
from zhiheng.api.common.dependencies import ContainerDependency

router = APIRouter(prefix='/v1', tags=['inference'], dependencies=[Depends(HTTPBearer(auto_error=False))])


@router.get('/models', openapi_extra={'x-required-scopes': ['models:invoke']})
async def models(request: Request, container: ContainerDependency) -> dict[str, object]:
    token = bearer(request)
    await container.database.require_schema()
    async with container.database.sessions() as session:
        identity = await container.keys.authenticate(session, token)
        await container.keys.authorize_identity(session, identity, [], scope='models:invoke')
        key = await container.keys.info_for_identity(session, identity)
        rows = await container.models.list_models(session)
        visible = []
        for row in rows:
            if row.id not in key.model_ids:
                continue
            visible.append({'id': str(row.id), 'object': 'model', 'created': 0, 'owned_by': 'zhiheng'})
        return {'object': 'list', 'data': visible}
