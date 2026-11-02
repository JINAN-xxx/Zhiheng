from typing import Annotated
from urllib.parse import unquote_to_bytes
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from zhiheng.api.contracts import EmptyRequest
from zhiheng.api.dependencies import ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.modules.skills.public import DraftInfo, SkillInfo, SkillPage, SkillStatus
from zhiheng.shared.errors import AppError

router = APIRouter(prefix='/api/skills', tags=['skills'])


class StatusRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: SkillStatus
    expected_version: int = Field(ge=1)


class ContentInfo(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content: str


@router.get('')
async def skills(
    request: Request,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    search: Annotated[str, Query(max_length=500)] = '',
    status: SkillStatus | None = None,
) -> SkillPage:
    # QueryParams uses replacement decoding; reject invalid original search bytes first.
    for parameter in request.scope['query_string'].split(b'&'):
        key, _, value = parameter.partition(b'=')
        if unquote_to_bytes(key.replace(b'+', b' ')) == b'search':
            try:
                unquote_to_bytes(value.replace(b'+', b' ')).decode('utf-8')
            except UnicodeError as error:
                raise AppError('invalid_skill_search', '技能搜索条件必须为有效UTF-8', 422) from error
    return await container.skills.list_skills(
        session, principal.id, page=page, page_size=page_size, search=search, status=status
    )


@router.get('/available')
async def available(
    container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> list[SkillInfo]:
    return await container.skills.available(session, principal.id)


@router.post('/import/prepare', status_code=201, openapi_extra={'x-transport-policy': 'binary_skill'})
async def prepare(
    request: Request,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
) -> DraftInfo:
    payload = await request.body()
    async with session.begin():
        return await container.skills.prepare(session, principal, payload, filename=filename)


@router.post('/import/{draft_id}/confirm', status_code=201)
async def confirm(
    draft_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    _body: EmptyRequest | None = None,
) -> SkillInfo:
    async with session.begin():
        return await container.skills.confirm(session, principal, draft_id)


@router.get('/{skill_id}')
async def get(
    skill_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> SkillInfo:
    return await container.skills.get(session, principal.id, skill_id)


@router.get('/{skill_id}/content')
async def content(
    skill_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> ContentInfo:
    return ContentInfo(content=await container.skills.content(session, principal.id, skill_id))


@router.patch('/{skill_id}/status')
async def status(
    skill_id: UUID,
    body: StatusRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> SkillInfo:
    async with session.begin():
        return await container.skills.set_status(session, principal, skill_id, **body.model_dump())


@router.delete('/{skill_id}')
async def remove(
    skill_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    _body: EmptyRequest | None = None,
) -> SkillInfo:
    async with session.begin():
        return await container.skill_management.remove(session, principal, skill_id)
