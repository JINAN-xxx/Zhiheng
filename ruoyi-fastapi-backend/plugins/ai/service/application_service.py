from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol, TypeVar

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from common.vo import PageModel
from plugins.ai.dao.ai_developer_api_dao import AiDeveloperApiDao
from plugins.ai.domain.developer_api_errors import DeveloperApiError
from plugins.ai.domain.resource_id import new_resource_id
from plugins.ai.entity.do.ai_developer_api_do import AiApplication
from plugins.ai.entity.vo.ai_developer_api_vo import (
    ApplicationCreateModel,
    ApplicationModel,
    ApplicationPageQueryModel,
    ApplicationUpdateModel,
)

ResultT = TypeVar('ResultT')


class ApplicationDao(Protocol):
    async def create_application(self, db: AsyncSession, application: AiApplication) -> AiApplication: ...

    async def get_application_by_id(self, db: AsyncSession, application_id: str) -> AiApplication | None: ...

    async def get_application_by_code(self, db: AsyncSession, application_code: str) -> AiApplication | None: ...

    async def get_application_for_update(self, db: AsyncSession, application_id: str) -> AiApplication | None: ...

    async def list_applications(
        self,
        db: AsyncSession,
        query_object: ApplicationPageQueryModel,
        *,
        owner_user_id: int | None,
        is_page: bool = False,
    ) -> PageModel | list[dict[str, Any]]: ...

    async def update_application_with_version(
        self,
        db: AsyncSession,
        application_id: str,
        *,
        expected_version: int,
        values: dict[str, Any],
    ) -> int: ...

    async def disable_application_if_active(
        self,
        db: AsyncSession,
        application_id: str,
        values: dict[str, Any],
    ) -> int: ...


class ApplicationService:
    """管理 Developer API Application 的所有权与生命周期。"""

    def __init__(
        self,
        db: AsyncSession,
        *,
        application_dao: ApplicationDao = AiDeveloperApiDao,
    ) -> None:
        self._db = db
        self._application_dao = application_dao

    async def list_applications(
        self,
        query: ApplicationPageQueryModel,
        *,
        actor_user_id: int,
        is_admin: bool,
        is_page: bool = True,
    ) -> PageModel | list[dict[str, Any]]:
        result = await self._application_dao.list_applications(
            self._db,
            query,
            owner_user_id=None if is_admin else actor_user_id,
            is_page=is_page,
        )
        if isinstance(result, PageModel):
            return result.model_copy(update={'rows': self._safe_application_rows(result.rows)})
        return self._safe_application_rows(result)

    async def get_application(
        self,
        application_id: str,
        *,
        actor_user_id: int,
        is_admin: bool,
    ) -> ApplicationModel:
        application = await self._application_dao.get_application_by_id(self._db, application_id)
        self._require_access(application, actor_user_id=actor_user_id, is_admin=is_admin)
        return ApplicationModel.model_validate(application)

    async def create_application(
        self,
        payload: ApplicationCreateModel,
        *,
        owner_user_id: int,
        username: str,
        now: datetime,
    ) -> ApplicationModel:
        async def operation() -> ApplicationModel:
            if await self._application_dao.get_application_by_code(self._db, payload.application_code) is not None:
                raise DeveloperApiError.application_code_conflict()
            application = AiApplication(
                application_id=new_resource_id('app'),
                application_code=payload.application_code,
                application_name=payload.application_name,
                owner_user_id=owner_user_id,
                tenant_id=None,
                project_id=None,
                status='active',
                version=1,
                disabled_at=None,
                created_by=username,
                created_at=now,
                updated_by=username,
                updated_at=now,
            )
            created = await self._application_dao.create_application(self._db, application)
            return ApplicationModel.model_validate(created)

        return await self._run_write(operation, integrity_conflict=True)

    async def update_application(
        self,
        application_id: str,
        payload: ApplicationUpdateModel,
        *,
        actor_user_id: int,
        is_admin: bool,
        username: str,
        now: datetime,
    ) -> ApplicationModel:
        async def operation() -> ApplicationModel:
            application = await self._application_dao.get_application_for_update(self._db, application_id)
            self._require_access(application, actor_user_id=actor_user_id, is_admin=is_admin)
            if application.status != 'active':
                raise DeveloperApiError.application_disabled()
            values = {
                'application_name': payload.application_name,
                'updated_by': username,
                'updated_at': now,
            }
            snapshot = ApplicationModel.model_validate(application).model_dump()
            updated = await self._application_dao.update_application_with_version(
                self._db,
                application_id,
                expected_version=payload.expected_version,
                values=values,
            )
            if updated != 1:
                raise DeveloperApiError.application_conflict()
            return ApplicationModel.model_validate(
                {
                    **snapshot,
                    **values,
                    'version': payload.expected_version + 1,
                }
            )

        return await self._run_write(operation)

    async def disable_application(
        self,
        application_id: str,
        *,
        actor_user_id: int,
        is_admin: bool,
        username: str,
        now: datetime,
    ) -> ApplicationModel:
        async def operation() -> ApplicationModel:
            application = await self._application_dao.get_application_for_update(self._db, application_id)
            self._require_access(application, actor_user_id=actor_user_id, is_admin=is_admin)
            if application.status != 'active':
                raise DeveloperApiError.application_disabled()
            values = {
                'disabled_at': now,
                'updated_by': username,
                'updated_at': now,
            }
            snapshot = ApplicationModel.model_validate(application).model_dump()
            if await self._application_dao.disable_application_if_active(self._db, application_id, values) != 1:
                raise DeveloperApiError.application_conflict()
            return ApplicationModel.model_validate(
                {
                    **snapshot,
                    **values,
                    'status': 'disabled',
                    'version': snapshot['version'] + 1,
                }
            )

        return await self._run_write(operation)

    @staticmethod
    def _require_access(
        application: AiApplication | None,
        *,
        actor_user_id: int,
        is_admin: bool,
    ) -> None:
        if application is None or (not is_admin and application.owner_user_id != actor_user_id):
            raise DeveloperApiError.application_not_found()

    @staticmethod
    def _safe_application_rows(rows: list[Any]) -> list[dict[str, Any]]:
        return [ApplicationModel.model_validate(row).model_dump(by_alias=True, mode='json') for row in rows]

    async def _run_write(
        self,
        operation: Callable[[], Awaitable[ResultT]],
        *,
        integrity_conflict: bool = False,
    ) -> ResultT:
        integrity_failed = False
        try:
            result = await operation()
            await self._db.commit()
        except IntegrityError:
            integrity_failed = True
            await self._rollback_preserving_error()
        except BaseException:
            await self._rollback_preserving_error()
            raise
        if integrity_failed:
            if integrity_conflict:
                raise DeveloperApiError.application_code_conflict()
            raise DeveloperApiError.application_conflict()
        return result

    async def _rollback_preserving_error(self) -> None:
        try:
            await self._db.rollback()
        except Exception:
            pass
