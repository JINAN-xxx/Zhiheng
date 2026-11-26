from typing import Annotated
from urllib.parse import unquote_to_bytes
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from zhiheng.api.common.contracts import EmptyRequest
from zhiheng.api.common.dependencies import ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.modules.skills.public import DraftInfo, SkillCatalog, SkillInfo, SkillPage, SkillStatus
from zhiheng.shared.errors import AppError

router = APIRouter(prefix='/api/skills', tags=['skills'])


class StatusRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    status: SkillStatus
    expected_version: int = Field(ge=1)


class ContentInfo(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content: str


class PlatformSkillDetail(ContentInfo):
    skill: SkillInfo


class PublishRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(strict=True, ge=1)


class WithdrawRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision_id: UUID


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
    source: SkillCatalog | None = None,
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
        session, principal.id, page=page, page_size=page_size, search=search, status=status, source=source
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


@router.get('/platform/{skill_id}')
async def platform_detail(
    skill_id: UUID,
    response: Response,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> PlatformSkillDetail:
    skill, text = await container.skills.platform_detail(session, skill_id)
    response.headers['Cache-Control'] = 'no-store'
    return PlatformSkillDetail(skill=skill, content=text)


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


@router.get('/{skill_id}/references')
async def list_references(
    skill_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> dict[str, list[dict[str, object]]]:
    return await container.skill_management.references(session, principal, skill_id)


@router.put('/{skill_id}/platform')
async def publish_platform(
    skill_id: UUID,
    body: PublishRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> SkillInfo:
    async with session.begin():
        result = await container.skills.publish_platform(session, principal, skill_id, body.expected_version)
        await container.operations.record(
            session,
            principal=principal,
            action='skill.platform.publish',
            resource_type='skill',
            resource_id=str(skill_id),
        )
        return result


@router.delete('/{skill_id}/platform', status_code=204)
async def withdraw_platform(
    skill_id: UUID,
    body: WithdrawRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> Response:
    async with session.begin():
        await container.skills.withdraw_platform(session, principal, skill_id, body.expected_revision_id)
        await container.operations.record(
            session,
            principal=principal,
            action='skill.platform.withdraw',
            resource_type='skill',
            resource_id=str(skill_id),
        )
    return Response(status_code=204)


@router.patch('/{skill_id}/status')
async def status(
    skill_id: UUID,
    body: StatusRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> SkillInfo:
    async with session.begin():
        return await container.skill_commands.status(session, principal, skill_id, **body.model_dump())


@router.delete('/{skill_id}')
async def remove(
    skill_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    _body: EmptyRequest | None = None,
) -> SkillInfo:
    async with session.begin():
        return await container.skill_commands.remove(session, principal, skill_id)
