from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol, TypeVar

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from common.vo import PageModel
from exceptions.exception import ServiceException
from plugins.ai.dao.ai_developer_api_dao import AiDeveloperApiDao
from plugins.ai.dao.ai_model_catalog_dao import AiModelCatalogDao
from plugins.ai.dao.ai_resource_binding_dao import AiResourceBindingDao
from plugins.ai.domain.model_runtime import ModelRuntimeEngine
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.domain.resource_id import new_resource_id
from plugins.ai.entity.do.ai_developer_api_do import AiModelRateCard
from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
from plugins.ai.entity.do.ai_resource_binding_do import AiResourceBinding
from plugins.ai.entity.vo.ai_developer_api_vo import RateCardCreateModel, RateCardModel
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
MODEL_TYPE_IMMUTABLE = '模型类型创建后不可修改，需要改变类型时必须新建模型编码'
MODEL_TYPE_FIELD_CONFLICT = '模型类型与能力或默认参数不匹配'
BINDING_ACTIVE_MODEL_CONFLICT = '已发布模型请先停用再修改运行时绑定'
BINDING_CREATE_VERSION_CONFLICT = '新建绑定不允许指定版本号'
BINDING_UPDATE_VERSION_REQUIRED = '更新绑定必须提供当前版本号'
BINDING_VERSION_CONFLICT = '绑定版本已变化，请刷新后重试'
BINDING_PUBLISH_CONFLICT = '运行时绑定已变化，请重新发布'
RUNTIME_ALIAS_MISSING = '运行时未发现绑定的模型别名'
MODEL_STATE_CONFLICT = '模型状态已变化，请刷新后重试'
MODEL_DISABLE_CONFLICT = '仅已发布模型可以停用'
MODEL_DELETE_CONFLICT = '仅草稿或已停用模型可以删除'
MODEL_REFERENCED = '模型已被对话、密钥授权、费率或调用记录引用，不能删除'
RATE_CARD_TYPE_CONFLICT = '模型类型与计费模式不匹配'


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
        rate_card_dao: type[AiDeveloperApiDao] = AiDeveloperApiDao,
    ) -> None:
        self._db = db
        self._runtime = runtime
        self._reference_checker = reference_checker or NoModelReferenceChecker()
        self._model_dao = model_dao
        self._binding_dao = binding_dao
        self._rate_card_dao = rate_card_dao

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

    async def list_rate_cards(self, model_id: str) -> list[RateCardModel]:
        await self._require_model(model_id)
        cards = await self._rate_card_dao.list_rate_cards_by_model(self._db, model_id)
        return [RateCardModel.model_validate(card) for card in cards]

    async def create_rate_card(
        self,
        model_id: str,
        payload: RateCardCreateModel,
        *,
        username: str,
        now: datetime,
    ) -> RateCardModel:
        model = await self._require_model(model_id)
        expected_billing_mode = 'search_unit' if model.model_type == 'rerank' else 'token'
        if payload.billing_mode != expected_billing_mode:
            await self._rollback_preserving_error()
            raise ServiceException(message=RATE_CARD_TYPE_CONFLICT)
        existing = await self._rate_card_dao.list_rate_cards_by_model(self._db, model_id)
        next_version = max((card.version for card in existing), default=0) + 1
        database_now = now.replace(tzinfo=None) if now.tzinfo is not None else now
        effective_from = (
            payload.effective_from.replace(tzinfo=None)
            if payload.effective_from is not None and payload.effective_from.tzinfo is not None
            else (payload.effective_from or database_now)
        )

        async def operation() -> RateCardModel:
            card = AiModelRateCard(
                rate_card_id=new_resource_id('rate'),
                platform_model_id=model_id,
                version=next_version,
                billing_mode=payload.billing_mode,
                input_price_per_token=payload.input_price_per_token,
                output_price_per_token=payload.output_price_per_token,
                cache_price_per_token=payload.cache_price_per_token,
                search_price_per_unit=payload.search_price_per_unit,
                currency=payload.currency,
                effective_from=effective_from,
                effective_to=None,
                status='active',
                created_by=username,
                created_at=database_now,
                updated_by=username,
                updated_at=database_now,
            )
            created = await self._rate_card_dao.create_rate_card(self._db, card)
            return RateCardModel.model_validate(created)

        return await self._run_write(operation)

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
            model = await self._require_locked_model(model_id)
            response_values = PlatformModelModel.model_validate(model).model_dump()
            requested_code = values.get('model_code')
            if requested_code is not None and requested_code != model.model_code:
                if model.published_at is not None:
                    raise ServiceException(message=MODEL_CODE_IMMUTABLE)
                existing = await self._model_dao.get_model_by_code(self._db, requested_code)
                if existing is not None and existing.model_id != model_id:
                    raise ServiceException(message=MODEL_CODE_CONFLICT)
            requested_type = values.get('model_type')
            if requested_type is not None and requested_type != model.model_type:
                raise ServiceException(message=MODEL_TYPE_IMMUTABLE)
            update_values = {**values, 'update_by': username, 'update_time': now}
            update_operation = (
                self._model_dao.update_unpublished_model
                if requested_code is not None and requested_code != model.model_code
                else self._model_dao.update_model
            )
            if await update_operation(self._db, model_id, update_values) != 1:
                raise ServiceException(message=MODEL_STATE_CONFLICT)
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
            model = await self._require_locked_model(model_id)
            if model.lifecycle_status == 'active':
                raise ServiceException(message=BINDING_ACTIVE_MODEL_CONFLICT)
            current = await self._binding_dao.get_current_binding_for_update(self._db, model_id)
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
            binding = await self._binding_dao.get_current_binding(self._db, model_id)
            if binding is None or binding.binding_status != 'active':
                raise ModelServiceError.binding_missing()
            model_snapshot = (model.lifecycle_status, model.published_at)
            binding_snapshot = self._binding_snapshot(binding)

            # Each command owns its transaction; release the read transaction before the network call.
            await self._db.rollback()

            visible_model_ids = await self._runtime.list_model_ids()
            if binding_snapshot[3] not in visible_model_ids:
                raise ServiceException(message=RUNTIME_ALIAS_MISSING)

            locked_model = await self._require_locked_model(model_id)
            if (locked_model.lifecycle_status, locked_model.published_at) != model_snapshot:
                raise ServiceException(message=MODEL_STATE_CONFLICT)
            locked_binding = await self._binding_dao.get_current_binding_for_update(self._db, model_id)
            if locked_binding is None or self._binding_snapshot(locked_binding) != binding_snapshot:
                raise ServiceException(message=BINDING_PUBLISH_CONFLICT)

            response_values = PlatformModelModel.model_validate(locked_model).model_dump()
            values: dict[str, Any] = {
                'lifecycle_status': 'active',
                'update_by': username,
                'update_time': now,
            }
            if locked_model.published_at is None:
                values['published_at'] = now
            if (
                await self._model_dao.publish_model_if_state(
                    self._db,
                    model_id,
                    expected_lifecycle_status=locked_model.lifecycle_status,
                    expected_published_at=locked_model.published_at,
                    values=values,
                )
                != 1
            ):
                raise ServiceException(message=MODEL_STATE_CONFLICT)
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
            model = await self._require_locked_model(model_id)
            response_values = PlatformModelModel.model_validate(model).model_dump()
            if model.lifecycle_status != 'active':
                raise ServiceException(message=MODEL_DISABLE_CONFLICT)
            values = {'lifecycle_status': 'disabled', 'update_by': username, 'update_time': now}
            if await self._model_dao.disable_model_if_active(self._db, model_id, values) != 1:
                raise ServiceException(message=MODEL_STATE_CONFLICT)
            return PlatformModelModel.model_validate({**response_values, **values})

        return await self._run_write(operation)

    async def delete_model(self, model_id: str) -> None:
        async def operation() -> None:
            model = await self._require_locked_model(model_id)
            removable_draft = model.lifecycle_status == 'draft' and model.published_at is None
            if not removable_draft and model.lifecycle_status != 'disabled':
                raise ServiceException(message=MODEL_DELETE_CONFLICT)
            if await self._reference_checker.has_model_references(model_id):
                raise ServiceException(message=MODEL_REFERENCED)
            await self._binding_dao.delete_binding_by_resource(self._db, model_id)
            if await self._model_dao.delete_removable_model(self._db, model_id) != 1:
                raise ServiceException(message=MODEL_STATE_CONFLICT)

        await self._run_write(operation, integrity_conflict=MODEL_REFERENCED)

    async def _get_model_response(self, model_id: str) -> PlatformModelModel:
        return PlatformModelModel.model_validate(await self._require_model(model_id))

    async def _require_model(self, model_id: str) -> AiModelCatalog:
        model = await self._model_dao.get_model_by_id(self._db, model_id)
        if model is None:
            raise ModelServiceError.not_found()
        return model

    async def _require_locked_model(self, model_id: str) -> AiModelCatalog:
        model = await self._model_dao.get_model_for_update(self._db, model_id)
        if model is None:
            raise ModelServiceError.not_found()
        return model

    @staticmethod
    def _binding_snapshot(binding: AiResourceBinding) -> tuple[str, int, str, str]:
        return (
            binding.binding_id,
            binding.version,
            binding.binding_status,
            binding.engine_resource_id,
        )

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
