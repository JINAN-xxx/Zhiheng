import asyncio
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPBearer

from zhiheng.api.common.auth import bearer
from zhiheng.api.common.contracts import EmptyRequest
from zhiheng.api.common.dependencies import ContainerDependency
from zhiheng.api.inference.responses import AgentCompletionResponse
from zhiheng.api.inference.streaming import chat_response
from zhiheng.modules.agents.public import AgentPublicRequest
from zhiheng.modules.knowledge.public import PREPARATION_TERMINAL, PreparationInfo
from zhiheng.shared.errors import AppError

router = APIRouter(prefix='/v1/agents', tags=['public agents'], dependencies=[Depends(HTTPBearer(auto_error=False))])
PREPARATION_WAIT_SECONDS = 30
PREPARATION_POLL_SECONDS = 0.5


async def wait_for_preparation(
    container: ContainerDependency, request: Request, token: str, agent_id: UUID, current: PreparationInfo
) -> PreparationInfo:
    try:
        async with asyncio.timeout(PREPARATION_WAIT_SECONDS):
            while current.run is None and current.status not in PREPARATION_TERMINAL:
                if await request.is_disconnected():
                    raise asyncio.CancelledError
                await asyncio.sleep(PREPARATION_POLL_SECONDS)
                current = await container.agent_preparations.get(token, agent_id, current.id)
    except TimeoutError:
        raise AppError('knowledge_preparation_wait_timeout', '等待知识库准备超时，请使用原请求编号重试', 504) from None
    return current


class AgentPreparationResponse(PreparationInfo):
    conversation_id: UUID


def preparation_receipt(prepared: PreparationInfo, conversation_id: UUID) -> JSONResponse:
    headers = {'Retry-After': '1', 'Preference-Applied': 'respond-async'}
    if prepared.polling_url:
        headers['Location'] = prepared.polling_url
    content = AgentPreparationResponse(**prepared.model_dump(), conversation_id=conversation_id)
    return JSONResponse(content.model_dump(mode='json'), status_code=202, headers=headers)


@router.post(
    '/{agent_id}/chat/completions',
    response_model=None,
    responses={200: {'model': AgentCompletionResponse}, 202: {'model': AgentPreparationResponse}},
    openapi_extra={'x-required-scopes': ['agents:invoke'], 'x-streaming': True},
)
async def chat(
    agent_id: UUID,
    body: AgentPublicRequest,
    request: Request,
    container: ContainerDependency,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias='Idempotency-Key',
            description='本回合的稳定 UUID。发送本头或 client_request_id，重试沿用原编号。',
        ),
    ] = None,
) -> JSONResponse | StreamingResponse:
    token = bearer(request)
    await container.database.require_schema()
    request_id = body.client_request_id
    header = idempotency_key
    if header:
        try:
            header_id = UUID(header)
        except ValueError:
            raise AppError('invalid_request', 'Idempotency-Key must be a UUID', 422) from None
        if request_id is not None and request_id != header_id:
            raise AppError('idempotency_conflict', 'Request identifiers differ', 409)
        request_id = header_id
    if request_id is None:
        raise AppError('stable_request_id_required', '请提供 client_request_id 或 Idempotency-Key', 422)
    prepared, identity, conversation_id = await container.agent_runs.submit_public(token, agent_id, body, request_id)
    if isinstance(prepared, PreparationInfo):
        respond_async = any(
            preference.partition(';')[0].strip().lower() == 'respond-async'
            for preference in request.headers.get('prefer', '').split(',')
        )
        if respond_async:
            return preparation_receipt(prepared, conversation_id)
        status = await wait_for_preparation(container, request, token, agent_id, prepared)
        if status.run is None:
            error = status.error
            raise AppError(
                error.code if error else 'knowledge_preparation_failed',
                error.message if error else '知识库准备未完成',
                409 if status.status == 'cancelled' else 504 if error and error.code == 'knowledge_timeout' else 502,
            )
        prepared = status.run
    async with container.database.sessions() as session:
        model_id = await container.knowledge_preparation.authorize_public_run(session, identity, agent_id, prepared.id)
    return await chat_response(
        container,
        request,
        prepared,
        identity.owner_id,
        token,
        model_id,
        stream=body.stream,
        scope='agents:invoke',
        conversation_id=conversation_id,
    )


@router.get('/{agent_id}/preparations/{intent_id}', openapi_extra={'x-required-scopes': ['agents:invoke']})
async def preparation(
    agent_id: UUID, intent_id: UUID, request: Request, container: ContainerDependency
) -> PreparationInfo:
    return await container.agent_preparations.get(bearer(request), agent_id, intent_id)


@router.post('/{agent_id}/preparations/{intent_id}/cancel', openapi_extra={'x-required-scopes': ['agents:invoke']})
async def cancel_preparation(
    agent_id: UUID, intent_id: UUID, request: Request, body: EmptyRequest, container: ContainerDependency
) -> PreparationInfo:
    return await container.agent_preparations.cancel(bearer(request), agent_id, intent_id)
