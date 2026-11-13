from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query

from zhiheng.api.common.contracts import EmptyRequest
from zhiheng.api.common.dependencies import ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.modules.agents.public import (
    AgentConfig,
    AgentCreate,
    AgentInfo,
    AgentKnowledgeRequest,
    AgentPage,
    AgentSummary,
    AgentUpdate,
    PreviewRequest,
    VersionRequest,
)
from zhiheng.modules.knowledge.public import PreparationInfo, RerankPolicy
from zhiheng.runtime.contracts import RunSnapshot

router = APIRouter(prefix='/api/agents', tags=['agents'])


@router.get('')
async def list_agents(
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    search: Annotated[str, Query(max_length=100)] = '',
    status: Literal['draft', 'published'] | None = None,
) -> AgentPage:
    return await container.agents.list_agents(
        session, principal, page=page, page_size=page_size, search=search, status=status
    )


@router.get('/available')
async def available(
    container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> list[AgentSummary]:
    return await container.agent_management.available(session, principal)


@router.post('/knowledge-policy')
async def knowledge_policy(
    body: AgentKnowledgeRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> RerankPolicy:
    return await container.agent_management.knowledge_policy(session, principal, body.knowledge_base_ids)


@router.post('', status_code=201)
async def create(
    body: AgentCreate, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> AgentInfo:
    async with session.begin():
        return await container.agent_management.create(
            session,
            principal,
            AgentConfig.model_validate(body.model_dump(exclude={'client_request_id'})),
            body.client_request_id,
        )


@router.get('/{agent_id}')
async def get(
    agent_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> AgentInfo:
    return await container.agents.get(session, principal, agent_id)


@router.patch('/{agent_id}')
async def update(
    agent_id: UUID,
    body: AgentUpdate,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> AgentInfo:
    async with session.begin():
        return await container.agent_management.update(session, principal, agent_id, body)


@router.post('/{agent_id}/publish')
async def publish(
    agent_id: UUID,
    body: VersionRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> AgentInfo:
    async with session.begin():
        return await container.agent_management.publish(session, principal, agent_id, body.expected_version)


@router.post('/{agent_id}/unpublish')
async def unpublish(
    agent_id: UUID,
    body: VersionRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> AgentInfo:
    async with session.begin():
        return await container.agents.unpublish(session, principal, agent_id, body.expected_version)


@router.delete('/{agent_id}')
async def remove(
    agent_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    _body: EmptyRequest | None = None,
) -> AgentInfo:
    async with session.begin():
        return await container.agent_management.remove(session, principal, agent_id)


@router.post('/{agent_id}/preview', status_code=202)
async def preview(
    agent_id: UUID,
    body: PreviewRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> RunSnapshot | PreparationInfo:
    async with session.begin():
        agent = await container.agents.get(session, principal, agent_id)
    if agent.knowledge_base_ids:
        return await container.agent_runs.prepare_knowledge_agent(principal, agent_id, body)
    async with session.begin():
        return await container.agent_runs.preview(session, principal, agent_id, body, container.settings.token_limit)


@router.get('/{agent_id}/api-access')
async def api_access(
    agent_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> dict[str, object]:
    return await container.agent_management.api_access(session, principal, agent_id)
