"""Platform knowledge defaults are managed independently from personal libraries."""

from fastapi import APIRouter

from zhiheng.api.common.dependencies import ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.modules.knowledge.public import (
    KnowledgeDefaultsInfo,
    KnowledgeDefaultsPatch,
    ParsingSettingsInfo,
    ParsingSettingsPatch,
    ParsingStatusInfo,
)

router = APIRouter(prefix='/api/knowledge/settings', tags=['knowledge'])


@router.get('')
async def get(
    container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> KnowledgeDefaultsInfo:
    return await container.knowledge_defaults.get(session, principal)


@router.patch('')
async def patch(
    body: KnowledgeDefaultsPatch,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> KnowledgeDefaultsInfo:
    async with session.begin():
        result = await container.knowledge_defaults.patch(session, principal, body)
        await container.operations.record(
            session,
            principal=principal,
            action='knowledge.defaults.update',
            resource_type='knowledge_defaults',
            resource_id='1',
        )
        return result


@router.get('/parsing')
async def parsing_get(
    container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> ParsingSettingsInfo:
    return await container.parsing_settings.get(session, principal)


@router.patch('/parsing')
async def parsing_update(
    body: ParsingSettingsPatch,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> ParsingSettingsInfo:
    async with session.begin():
        result = await container.parsing_settings.update(session, principal, body)
        await container.operations.record(
            session,
            principal=principal,
            action='knowledge.parsing.update',
            resource_type='parsing_settings',
            resource_id='1',
        )
        return result


@router.get('/parsing/status')
async def parsing_status(container: ContainerDependency, principal: PrincipalDependency) -> ParsingStatusInfo:
    return await container.document_parsing.status(principal)
