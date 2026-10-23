from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPBearer

from zhiheng.api.dependencies import ContainerDependency
from zhiheng.api.routes.public import bearer, chat_response
from zhiheng.modules.agents.public import AgentPublicRequest, PreviewRequest

router = APIRouter(prefix='/v1/agents', tags=['public agents'], dependencies=[Depends(HTTPBearer(auto_error=False))])


@router.post('/{agent_id}/chat/completions', response_model=None)
async def chat(
    agent_id: UUID, body: AgentPublicRequest, request: Request, container: ContainerDependency
) -> JSONResponse | StreamingResponse:
    token = bearer(request)
    await container.database.require_schema()
    async with container.database.sessions.begin() as session:
        run, identity, model_id = await container.agent_runs.public(
            session,
            token,
            agent_id,
            PreviewRequest(messages=body.messages, client_request_id=uuid4()),
            container.settings.token_limit,
        )
    return await chat_response(container, request, run, identity.owner_id, token, model_id, stream=body.stream)
