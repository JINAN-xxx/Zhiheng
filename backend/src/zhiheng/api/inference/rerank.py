from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer

from zhiheng.api.common.dependencies import ContainerDependency
from zhiheng.api.inference.admission import admit
from zhiheng.api.inference.schemas import RerankBody
from zhiheng.api.inference.streaming import wait_result
from zhiheng.modules.models.public import ModelMessage

router = APIRouter(prefix='/v1', tags=['inference'], dependencies=[Depends(HTTPBearer(auto_error=False))])


@router.post('/rerank', openapi_extra={'x-required-scopes': ['models:invoke']})
async def rerank(body: RerankBody, request: Request, container: ContainerDependency) -> JSONResponse:
    messages = (
        ModelMessage('user', body.query),
        *(ModelMessage('user', value if isinstance(value, str) else value.text) for value in body.documents),
    )
    options = body.model_dump(include={'top_n', 'return_documents'}, exclude_none=True)
    run, owner_id, token = await admit(container, request, 'rerank', messages, body, options)
    result = await wait_result(container, request, run, owner_id, token, scope='models:invoke')
    return JSONResponse({'id': str(run.id), 'model': str(body.model), **(result.result or {})})
