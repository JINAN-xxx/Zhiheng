from collections.abc import Awaitable
from typing import Annotated, TypeVar

from fastapi import File, Path, Query, Response, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from common.annotation.log_annotation import AuditedHTTPException
from common.aspect.db_seesion import DBSessionDependency
from common.aspect.interface_auth import UserInterfaceAuthDependency
from common.aspect.pre_auth import CurrentUserDependency, PreAuthDependency
from common.router import APIRouterPro
from common.vo import DataResponseModel, PageResponseModel, ResponseBaseModel
from module_admin.entity.vo.user_vo import CurrentUserModel
from plugins.agent_extension.config import AgentSkillSettings
from plugins.agent_extension.domain.skill_errors import SkillPackageError, SkillServiceError, SkillStorageError
from plugins.agent_extension.domain.skill_package import SkillPackageLimits, SkillPackageParser
from plugins.agent_extension.entity.vo.skill_vo import (
    AvailableSkillModel,
    SkillContentModel,
    SkillDetailModel,
    SkillDraftModel,
    SkillId,
    SkillModel,
    SkillPageQueryModel,
    SkillStatusUpdateModel,
)
from plugins.agent_extension.infrastructure.skill_storage import SkillStorage
from plugins.agent_extension.service.skill_service import SkillService
from utils.response_util import ResponseUtil

ResultT = TypeVar('ResultT')

skill_controller = APIRouterPro(
    prefix='/agent_extension/skills',
    order_num=25,
    tags=['智能体扩展-技能管理'],
    dependencies=[PreAuthDependency()],
)


def _operator(current_user: CurrentUserModel) -> str:
    if current_user.user is None or not current_user.user.user_name:
        raise AuditedHTTPException(
            status_code=401,
            detail={'code': 'SKILL_UNAUTHENTICATED', 'message': '用户未登录'},
            audit_code='SKILL_UNAUTHENTICATED',
            audit_message='用户未登录',
        )
    return current_user.user.user_name


def _skill_service(query_db: AsyncSession) -> SkillService:
    settings = AgentSkillSettings()
    parser = SkillPackageParser(
        SkillPackageLimits(
            upload_bytes=settings.upload_bytes,
            extracted_bytes=settings.extracted_bytes,
            file_count=settings.file_count,
            skill_md_bytes=settings.skill_md_bytes,
        )
    )
    return SkillService(
        query_db,
        SkillStorage(settings.data_dir, parser=parser, draft_ttl_seconds=settings.draft_ttl_seconds),
        settings,
    )


async def _call(operation: Awaitable[ResultT]) -> ResultT:
    try:
        return await operation
    except SkillServiceError as error:
        status_code = 404 if error.code == 'SKILL_NOT_FOUND' else 409 if 'CONFLICT' in error.code else 422
        detail = error.public_dict()
    except (SkillPackageError, SkillStorageError) as error:
        status_code = 422
        detail = {'code': 'SKILL_PACKAGE_INVALID', 'message': str(error)}
    raise AuditedHTTPException(
        status_code=status_code,
        detail=detail,
        audit_code=detail['code'],
        audit_message=detail['message'],
    ) from None


@skill_controller.get(
    '',
    response_model=PageResponseModel[SkillModel],
    dependencies=[UserInterfaceAuthDependency('agent_extension:skill:list')],
)
async def list_skills(
    query: Annotated[SkillPageQueryModel, Query()],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    result = await _call(_skill_service(query_db).list_skills(query, is_page=True))
    return ResponseUtil.success(model_content=result)


@skill_controller.get(
    '/available',
    response_model=DataResponseModel[list[AvailableSkillModel]],
    dependencies=[UserInterfaceAuthDependency('agent_extension:skill:use')],
)
async def list_available_skills(query_db: Annotated[AsyncSession, DBSessionDependency()]) -> Response:
    result = await _call(_skill_service(query_db).list_available_skills())
    return ResponseUtil.success(data=result)


@skill_controller.get(
    '/{skill_id}',
    response_model=DataResponseModel[SkillDetailModel],
    dependencies=[UserInterfaceAuthDependency('agent_extension:skill:query')],
)
async def get_skill(
    skill_id: Annotated[SkillId, Path(description='技能ID')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    result = await _call(_skill_service(query_db).get_skill(skill_id))
    return ResponseUtil.success(data=result)


@skill_controller.get(
    '/{skill_id}/content',
    response_model=DataResponseModel[SkillContentModel],
    dependencies=[UserInterfaceAuthDependency('agent_extension:skill:query')],
)
async def get_skill_content(
    skill_id: Annotated[SkillId, Path(description='技能ID')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    result = await _call(_skill_service(query_db).get_content(skill_id))
    return ResponseUtil.success(data=result)


@skill_controller.post(
    '/import/prepare',
    response_model=DataResponseModel[SkillDraftModel],
    dependencies=[UserInterfaceAuthDependency('agent_extension:skill:add')],
)
async def prepare_skill_import(
    file: Annotated[UploadFile, File(description='SKILL.md 或 ZIP 技能包')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    settings = AgentSkillSettings()
    content = await file.read(settings.upload_bytes + 1)
    await file.close()
    if len(content) > settings.upload_bytes:
        await _call(_raise_upload_too_large())
    result = await _call(
        _skill_service(query_db).prepare_upload(
            content,
            filename=file.filename or '',
            operator=_operator(current_user),
        )
    )
    return ResponseUtil.success(data=result)


async def _raise_upload_too_large() -> None:
    raise SkillServiceError('SKILL_PACKAGE_INVALID', '技能包超过上传大小限制')


@skill_controller.post(
    '/import/{draft_id}/confirm',
    response_model=DataResponseModel[SkillModel],
    dependencies=[UserInterfaceAuthDependency('agent_extension:skill:add')],
)
async def confirm_skill_import(
    draft_id: Annotated[str, Path(min_length=38, max_length=38)],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    result = await _call(_skill_service(query_db).confirm_install(draft_id, operator=_operator(current_user)))
    return ResponseUtil.success(data=result)


@skill_controller.put(
    '/{skill_id}/status',
    response_model=DataResponseModel[SkillModel],
    dependencies=[UserInterfaceAuthDependency('agent_extension:skill:edit')],
)
async def update_skill_status(
    skill_id: Annotated[SkillId, Path(description='技能ID')],
    payload: SkillStatusUpdateModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    result = await _call(_skill_service(query_db).update_status(skill_id, payload, operator=_operator(current_user)))
    return ResponseUtil.success(data=result)


@skill_controller.delete(
    '/{skill_id}',
    response_model=ResponseBaseModel,
    dependencies=[UserInterfaceAuthDependency('agent_extension:skill:remove')],
)
async def delete_skill(
    skill_id: Annotated[SkillId, Path(description='技能ID')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    await _call(_skill_service(query_db).delete_skill(skill_id, operator=_operator(current_user)))
    return ResponseUtil.success()
