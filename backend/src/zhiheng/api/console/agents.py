from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query, Response

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
    AssistantConversationInfo,
    PlazaAssistant,
    PlazaAssistantPage,
    PlazaDepartment,
    PlazaPublishRequest,
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
    status: Literal['enabled', 'disabled'] | None = None,
) -> AgentPage:
    return await container.agents.list_agents(
        session, principal, page=page, page_size=page_size, search=search, status=status
    )


@router.get('/available')
async def available(
    container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> list[AgentSummary]:
    return await container.agent_management.available(session, principal)


@router.get('/plaza')
async def plaza_list(
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 12,
    search: Annotated[str, Query(max_length=100)] = '',
) -> PlazaAssistantPage:
    return await container.agents.plaza_page(session, principal, search=search, page=page, page_size=page_size)


@router.get('/plaza/departments')
async def plaza_departments(
    container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> list[PlazaDepartment]:
    return [
        PlazaDepartment(id=identifier, name=name)
        for identifier, name in await container.identity.active_departments(session)
    ]


@router.get('/plaza/history')
async def plaza_history(
    container: ContainerDependency, principal: PrincipalDependency, before: UUID | None = None
) -> list[AssistantConversationInfo]:
    return await container.assistant_conversations.plaza_history(principal, before=before)


@router.get('/plaza/{agent_id}')
async def plaza_get(
    agent_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> PlazaAssistant:
    return await container.agents.plaza_detail(session, principal, agent_id)


@router.put('/{agent_id}/plaza')
async def plaza_publish(
    agent_id: UUID,
    body: PlazaPublishRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> AgentInfo:
    async with session.begin():
        result = await container.agent_management.publish_plaza(session, principal, agent_id, body)
        await container.operations.record(
            session, principal=principal, action='agent.plaza.publish', resource_type='agent', resource_id=str(agent_id)
        )
        return result


@router.delete('/{agent_id}/plaza', status_code=204)
async def plaza_unpublish(
    agent_id: UUID,
    body: VersionRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> Response:
    async with session.begin():
        await container.agents.unpublish_plaza(session, principal, agent_id, body.expected_version)
        await container.operations.record(
            session, principal=principal, action='agent.plaza.withdraw', resource_type='agent', resource_id=str(agent_id)
        )
    return Response(status_code=204)


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


@router.post('/{agent_id}/enable')
async def enable(
    agent_id: UUID,
    body: VersionRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> AgentInfo:
    async with session.begin():
        return await container.agent_management.enable(session, principal, agent_id, body.expected_version)


@router.post('/{agent_id}/disable')
async def disable(
    agent_id: UUID,
    body: VersionRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> AgentInfo:
    async with session.begin():
        return await container.agents.set_enabled(session, principal, agent_id, body.expected_version, False)


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
        return await container.agent_runs.preview(session, principal, agent_id, body)
