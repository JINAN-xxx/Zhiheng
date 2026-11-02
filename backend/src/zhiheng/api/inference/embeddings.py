from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer

from zhiheng.api.common.dependencies import ContainerDependency
from zhiheng.api.inference.admission import admit
from zhiheng.api.inference.schemas import EmbeddingBody
from zhiheng.api.inference.streaming import known_usage, wait_result
from zhiheng.modules.models.public import ModelMessage

router = APIRouter(prefix='/v1', tags=['inference'], dependencies=[Depends(HTTPBearer(auto_error=False))])


@router.post('/embeddings', openapi_extra={'x-required-scopes': ['models:invoke']})
async def embeddings(body: EmbeddingBody, request: Request, container: ContainerDependency) -> JSONResponse:
    values = [body.input] if isinstance(body.input, str) else body.input
    run, owner_id, token = await admit(
        container,
        request,
        'embedding',
        tuple(ModelMessage('user', value) for value in values),
        body,
        body.model_dump(include={'dimensions', 'user'}, exclude_none=True),
    )
    result = await wait_result(container, request, run, owner_id, token, scope='models:invoke')
    return JSONResponse(
        {'object': 'list', 'model': str(body.model), **(result.result or {}), 'usage': known_usage(result)}
    )
