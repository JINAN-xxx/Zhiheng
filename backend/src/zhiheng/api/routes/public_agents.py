from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPBearer

from zhiheng.api.contracts import EmptyRequest
from zhiheng.api.dependencies import ContainerDependency
from zhiheng.api.routes.public import bearer, chat_response
from zhiheng.modules.agents.public import AgentPublicRequest, PreviewRequest
from zhiheng.modules.knowledge.public import PreparationInfo
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

router = APIRouter(prefix='/v1/agents', tags=['public agents'], dependencies=[Depends(HTTPBearer(auto_error=False))])


@router.post('/{agent_id}/chat/completions', response_model=None)
async def chat(
    agent_id: UUID, body: AgentPublicRequest, request: Request, container: ContainerDependency
) -> JSONResponse | StreamingResponse:
    token = bearer(request)
    await container.database.require_schema()
    request_id = body.client_request_id
    header = request.headers.get('Idempotency-Key')
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
            await container.keys.authorize_identity(session, identity, [config.model_id], lock=True)
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
        return await chat_response(container, request, prepared, identity.owner_id, token, model_id, stream=body.stream)
    async with container.database.sessions.begin() as session:
        run, identity, model_id = await container.agent_runs.public(
            session,
            token,
            agent_id,
            PreviewRequest(messages=body.messages, client_request_id=uuid4()),
            container.settings.token_limit,
        )
    return await chat_response(container, request, run, identity.owner_id, token, model_id, stream=body.stream)


@router.get('/{agent_id}/preparations/{intent_id}')
async def preparation(
    agent_id: UUID, intent_id: UUID, request: Request, container: ContainerDependency
) -> PreparationInfo:
    token = bearer(request)
    async with container.database.sessions() as session:
        identity = await container.keys.authenticate(session, token)
        agent = await container.agents.get(session, Principal(identity.owner_id, '', False), agent_id)
        config = agent.published_config
        if config is None or config.application_id != identity.application_id:
            raise AppError('agent_not_found', 'Agent is unavailable', 404)
        return await container.knowledge_preparation.public_info(session, identity, agent_id, intent_id)


@router.post('/{agent_id}/preparations/{intent_id}/cancel')
async def cancel_preparation(
    agent_id: UUID, intent_id: UUID, request: Request, body: EmptyRequest, container: ContainerDependency
) -> PreparationInfo:
    token = bearer(request)
    async with container.database.sessions.begin() as session:
        identity = await container.keys.authenticate(session, token, lock=True)
        agent = await container.agents.get(session, Principal(identity.owner_id, '', False), agent_id, lock=True)
        config = agent.published_config
        if config is None or config.application_id != identity.application_id:
            raise AppError('agent_not_found', 'Agent is unavailable', 404)
        row = await container.knowledge_preparation.store.get(session, intent_id, lock=True)
        if (
            row.key_id != identity.key_id
            or row.application_id != identity.application_id
            or row.resource_id != agent_id
        ):
            raise AppError('preparation_not_found', 'Preparation is unavailable', 404)
        await container.knowledge_preparation.public_info(session, identity, agent_id, intent_id)
        if row.main_run_id:
            result = await container.knowledge_preparation.cancel_main(session, intent_id)
        else:
            await container.knowledge_preparation.store.fail(
                session, row, 'cancelled', 'knowledge_cancelled', 'Knowledge preparation cancelled'
            )
            row = await container.knowledge_preparation.store.get(session, intent_id)
            result = container.knowledge_preparation.store.info(row)
    await container.knowledge_preparation.cancel_children_if_terminal(intent_id)
    return result
