from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPBearer

from zhiheng.api.common.dependencies import ContainerDependency
from zhiheng.api.inference.admission import admit
from zhiheng.api.inference.schemas import ChatBody
from zhiheng.api.inference.streaming import chat_response
from zhiheng.modules.models.public import ModelMessage

router = APIRouter(prefix='/v1', tags=['inference'], dependencies=[Depends(HTTPBearer(auto_error=False))])


@router.post(
    '/chat/completions',
    response_model=None,
    openapi_extra={'x-required-scopes': ['models:invoke'], 'x-streaming': True},
)
async def chat(body: ChatBody, request: Request, container: ContainerDependency) -> JSONResponse | StreamingResponse:
    messages = tuple(ModelMessage(message.role, message.content) for message in body.messages)
    options = body.model_dump(exclude={'model', 'messages', 'stream'}, exclude_none=True)
    run, owner_id, token = await admit(container, request, 'chat', messages, body, options)
    return await chat_response(
        container, request, run, owner_id, token, body.model, stream=body.stream, scope='models:invoke'
    )
