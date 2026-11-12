from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPBearer

from zhiheng.api.common.auth import bearer
from zhiheng.api.common.contracts import EmptyRequest
from zhiheng.api.common.dependencies import ContainerDependency
from zhiheng.api.inference.streaming import chat_response
from zhiheng.modules.agents.public import AgentPublicRequest, PreviewRequest
from zhiheng.modules.knowledge.public import PreparationInfo
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

router = APIRouter(prefix='/v1/agents', tags=['public agents'], dependencies=[Depends(HTTPBearer(auto_error=False))])


@router.post(
    '/{agent_id}/chat/completions',
    response_model=None,
    responses={202: {'model': PreparationInfo}},
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
            description='稳定的请求 UUID。同一问题的准备、轮询后续调及重试须复用；关联知识库时必须提供本头或 client_request_id。',
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
    async with container.database.sessions.begin() as session:
        identity = await container.keys.authenticate(session, token, lock=True)
        await container.keys.authorize_identity(session, identity, [], scope='agents:invoke', lock=True)
        principal = Principal(identity.owner_id, '', False)
        try:
            agent = await container.agents.get(session, principal, agent_id)
        except AppError:
            raise AppError('agent_not_found', 'Agent is unavailable', 404) from None
        config = agent.published_config
        if config is None or config.application_id != identity.application_id:
            raise AppError('agent_not_found', 'Agent is unavailable', 404)
        if request_id is None:
            await container.models.resolve(session, config.model_id)
            await container.keys.authorize_identity(
                session, identity, [config.model_id], scope='agents:invoke', lock=True
            )
    if config.knowledge_base_ids or request_id is not None:
        if request_id is None:
            raise AppError(
                'stable_request_id_required', 'Knowledge calls require client_request_id or Idempotency-Key', 422
            )
        prepared = await container.agent_runs.prepare_knowledge_agent(
            principal,
            agent_id,
            PreviewRequest(messages=body.messages, client_request_id=request_id),
            identity,
            response_stream=body.stream,
        )
        if isinstance(prepared, PreparationInfo):
            return JSONResponse(prepared.model_dump(mode='json'), status_code=202)
        async with container.database.sessions() as session:
            model_id = await container.knowledge_preparation.authorize_public_run(
                session, identity, agent_id, prepared.id
            )
        return await chat_response(
            container, request, prepared, identity.owner_id, token, model_id, stream=body.stream, scope='agents:invoke'
        )
    async with container.database.sessions.begin() as session:
        run, identity, model_id = await container.agent_runs.public(
            session,
            token,
            agent_id,
            PreviewRequest(messages=body.messages, client_request_id=uuid4()),
            container.settings.token_limit,
        )
    return await chat_response(
        container, request, run, identity.owner_id, token, model_id, stream=body.stream, scope='agents:invoke'
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
