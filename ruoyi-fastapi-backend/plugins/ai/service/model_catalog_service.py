from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol, TypeVar

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from common.vo import PageModel
from exceptions.exception import ServiceException
from plugins.ai.dao.ai_model_catalog_dao import AiModelCatalogDao
from plugins.ai.dao.ai_resource_binding_dao import AiResourceBindingDao
from plugins.ai.domain.model_runtime import ModelRuntimeEngine
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.domain.resource_id import new_resource_id
from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
from plugins.ai.entity.do.ai_resource_binding_do import AiResourceBinding
from plugins.ai.entity.vo.ai_model_catalog_vo import (
    ModelCatalogCreateModel,
    ModelCatalogPageQueryModel,
    ModelCatalogUpdateModel,
    PlatformModelDetailModel,
    PlatformModelModel,
    ResourceBindingModel,
    ResourceBindingUpsertModel,
)

ResultT = TypeVar('ResultT')

MODEL_CODE_CONFLICT = '模型编码已存在'
MODEL_CODE_IMMUTABLE = '已发布模型不允许修改模型编码'
BINDING_CREATE_VERSION_CONFLICT = '新建绑定不允许指定版本号'
BINDING_UPDATE_VERSION_REQUIRED = '更新绑定必须提供当前版本号'
BINDING_VERSION_CONFLICT = '绑定版本已变化，请刷新后重试'
RUNTIME_ALIAS_MISSING = '运行时未发现绑定的模型别名'
MODEL_DISABLE_CONFLICT = '仅已发布模型可以停用'
MODEL_DELETE_CONFLICT = '仅从未发布的草稿可以删除'
MODEL_REFERENCED = '模型已被对话引用，不能删除'


class ModelReferenceChecker(Protocol):
    async def has_model_references(self, model_id: str) -> bool: ...


class NoModelReferenceChecker:
    """在 Chat 新表接入前使用的显式无引用实现。"""

    async def has_model_references(self, model_id: str) -> bool:
        del model_id
        return False


class ModelCatalogService:
    """管理 Platform Model Catalog 与当前 Runtime Binding 的生命周期。"""

    def __init__(
        self,
        db: AsyncSession,
        runtime: ModelRuntimeEngine,
        reference_checker: ModelReferenceChecker | None = None,
        *,
        model_dao: type[AiModelCatalogDao] = AiModelCatalogDao,
        binding_dao: type[AiResourceBindingDao] = AiResourceBindingDao,
    ) -> None:
        self._db = db
        self._runtime = runtime
        self._reference_checker = reference_checker or NoModelReferenceChecker()
        self._model_dao = model_dao
        self._binding_dao = binding_dao

    async def list_models(
        self,
        query: ModelCatalogPageQueryModel,
        *,
        is_page: bool = False,
    ) -> PageModel | list[dict[str, Any]]:
        result = await self._model_dao.list_models(self._db, query, is_page=is_page)
        if isinstance(result, PageModel):
            return result.model_copy(update={'rows': self._safe_model_rows(result.rows)})
        return self._safe_model_rows(result)

    async def get_model(self, model_id: str) -> PlatformModelDetailModel:
        model = await self._require_model(model_id)
        binding = await self._binding_dao.get_current_binding(self._db, model_id)
        model_response = PlatformModelModel.model_validate(model)
        return PlatformModelDetailModel.model_validate(
            {
                **model_response.model_dump(),
                'binding': ResourceBindingModel.model_validate(binding) if binding is not None else None,
            }
        )

    async def create_model(
        self,
        payload: ModelCatalogCreateModel,
        *,
        username: str,
        now: datetime,
    ) -> PlatformModelModel:
        async def operation() -> PlatformModelModel:
            if await self._model_dao.get_model_by_code(self._db, payload.model_code) is not None:
                raise ServiceException(message=MODEL_CODE_CONFLICT)
            values = payload.model_dump(mode='python')
            model = AiModelCatalog(
                model_id=new_resource_id('mdl'),
                **values,
                lifecycle_status='draft',
                published_at=None,
                create_by=username,
                create_time=now,
                update_by=username,
                update_time=now,
            )
            created = await self._model_dao.create_model(self._db, model)
            return PlatformModelModel.model_validate(created)

        return await self._run_write(operation, integrity_conflict=MODEL_CODE_CONFLICT)

    async def update_model(
        self,
        model_id: str,
        payload: ModelCatalogUpdateModel,
        *,
        username: str,
        now: datetime,
    ) -> PlatformModelModel:
        values = payload.model_dump(exclude_unset=True, mode='python')
        if not values:
            return await self._run_without_write(lambda: self._get_model_response(model_id))

        async def operation() -> PlatformModelModel:
            model = await self._require_model(model_id)
            response_values = PlatformModelModel.model_validate(model).model_dump()
            requested_code = values.get('model_code')
            if requested_code is not None and requested_code != model.model_code:
                if model.published_at is not None:
                    raise ServiceException(message=MODEL_CODE_IMMUTABLE)
                existing = await self._model_dao.get_model_by_code(self._db, requested_code)
                if existing is not None and existing.model_id != model_id:
                    raise ServiceException(message=MODEL_CODE_CONFLICT)
            update_values = {**values, 'update_by': username, 'update_time': now}
            if await self._model_dao.update_model(self._db, model_id, update_values) != 1:
                raise ModelServiceError.not_found()
            return PlatformModelModel.model_validate({**response_values, **update_values})

        return await self._run_write(operation, integrity_conflict=MODEL_CODE_CONFLICT)

    async def upsert_binding(
        self,
        model_id: str,
        payload: ResourceBindingUpsertModel,
        *,
        username: str,
        now: datetime,
    ) -> ResourceBindingModel:
        async def operation() -> ResourceBindingModel:
            await self._require_model(model_id)
            current = await self._binding_dao.get_current_binding(self._db, model_id)
            values = payload.model_dump(exclude={'expected_version'}, mode='python')
            if current is None:
                if payload.expected_version is not None:
                    raise ServiceException(message=BINDING_CREATE_VERSION_CONFLICT)
                binding = AiResourceBinding(
                    binding_id=new_resource_id('bnd'),
                    resource_type='model',
                    resource_id=model_id,
                    engine_type='litellm',
                    **values,
                    version=1,
                    create_by=username,
                    create_time=now,
                    update_by=username,
                    update_time=now,
                )
                created = await self._binding_dao.create_binding(self._db, binding)
                return ResourceBindingModel.model_validate(created)
            if payload.expected_version is None:
                raise ServiceException(message=BINDING_UPDATE_VERSION_REQUIRED)
            response_values = ResourceBindingModel.model_validate(current).model_dump()
            update_values = {**values, 'update_by': username, 'update_time': now}
            updated = await self._binding_dao.update_binding_with_version(
                self._db,
                current.binding_id,
                expected_version=payload.expected_version,
                values=update_values,
            )
            if updated != 1:
                raise ServiceException(message=BINDING_VERSION_CONFLICT)
            return ResourceBindingModel.model_validate(
                {**response_values, **update_values, 'version': payload.expected_version + 1}
            )

        return await self._run_write(operation)

    async def publish_model(
        self,
        model_id: str,
        *,
        username: str,
        now: datetime,
    ) -> PlatformModelModel:
        async def operation() -> PlatformModelModel:
            model = await self._require_model(model_id)
            response_values = PlatformModelModel.model_validate(model).model_dump()
            binding = await self._binding_dao.get_current_binding(self._db, model_id)
            if binding is None or binding.binding_status != 'active':
                raise ModelServiceError.binding_missing()

            visible_model_ids = await self._runtime.list_model_ids()
            if binding.engine_resource_id not in visible_model_ids:
                raise ServiceException(message=RUNTIME_ALIAS_MISSING)

            values: dict[str, Any] = {
                'lifecycle_status': 'active',
                'update_by': username,
                'update_time': now,
            }
            if model.published_at is None:
                values['published_at'] = now
            if await self._model_dao.update_model(self._db, model_id, values) != 1:
                raise ModelServiceError.not_found()
            return PlatformModelModel.model_validate({**response_values, **values})

        return await self._run_write(operation)

    async def disable_model(
        self,
        model_id: str,
        *,
        username: str,
        now: datetime,
    ) -> PlatformModelModel:
        async def operation() -> PlatformModelModel:
            model = await self._require_model(model_id)
            response_values = PlatformModelModel.model_validate(model).model_dump()
            if model.lifecycle_status != 'active':
                raise ServiceException(message=MODEL_DISABLE_CONFLICT)
            values = {'lifecycle_status': 'disabled', 'update_by': username, 'update_time': now}
            if await self._model_dao.update_model(self._db, model_id, values) != 1:
                raise ModelServiceError.not_found()
            return PlatformModelModel.model_validate({**response_values, **values})

        return await self._run_write(operation)

    async def delete_draft(self, model_id: str) -> None:
        async def operation() -> None:
            model = await self._require_model(model_id)
            if model.lifecycle_status != 'draft' or model.published_at is not None:
                raise ServiceException(message=MODEL_DELETE_CONFLICT)
            if await self._reference_checker.has_model_references(model_id):
                raise ServiceException(message=MODEL_REFERENCED)
            await self._binding_dao.delete_binding_by_resource(self._db, model_id)
            if await self._model_dao.delete_model(self._db, model_id) != 1:
                raise ModelServiceError.not_found()

        await self._run_write(operation)

    async def _get_model_response(self, model_id: str) -> PlatformModelModel:
        return PlatformModelModel.model_validate(await self._require_model(model_id))

    async def _require_model(self, model_id: str) -> AiModelCatalog:
        model = await self._model_dao.get_model_by_id(self._db, model_id)
        if model is None:
            raise ModelServiceError.not_found()
        return model

    @staticmethod
    def _safe_model_rows(rows: list[Any]) -> list[dict[str, Any]]:
        return [PlatformModelModel.model_validate(row).model_dump(by_alias=True, mode='json') for row in rows]

    async def _run_write(
        self,
        operation: Callable[[], Awaitable[ResultT]],
        *,
        integrity_conflict: str | None = None,
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
            if integrity_conflict is None:
                raise ServiceException(message='数据约束冲突')
            raise ServiceException(message=integrity_conflict)
        return result

    async def _run_without_write(self, operation: Callable[[], Awaitable[ResultT]]) -> ResultT:
        try:
            result = await operation()
        except BaseException:
            await self._rollback_preserving_error()
            raise
        await self._db.rollback()
        return result

    async def _rollback_preserving_error(self) -> None:
        try:
            await self._db.rollback()
        except Exception:
            pass
