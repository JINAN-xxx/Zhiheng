import asyncio
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from plugins.ai.dao.ai_model_catalog_dao import AiModelCatalogDao
from plugins.ai.dao.ai_resource_binding_dao import AiResourceBindingDao
from plugins.ai.domain.model_runtime import (
    ModelChatRequest,
    ModelChatResponse,
    ModelMessage,
    ModelRuntimeEngine,
    ModelStreamEvent,
)
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.domain.resource_id import validate_resource_id
from plugins.ai.entity.vo.ai_model_catalog_vo import ModelDefaultParametersModel

_ALLOWED_PARAMETERS = frozenset(
    {
        'temperature',
        'top_p',
        'max_tokens',
        'presence_penalty',
        'frequency_penalty',
    }
)
_INVALID_PARAMETER_MESSAGE = '模型参数无效'
_UNSUPPORTED_PARAMETER_MESSAGE = '不支持的模型参数'


def _freeze_parameters(parameters: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(dict(parameters))


@dataclass(frozen=True)
class ResolvedModel:
    platform_model_id: str
    binding_id: str
    engine_type: str
    engine_model_snapshot: str
    default_parameters: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        object.__setattr__(self, 'default_parameters', _freeze_parameters(self.default_parameters))


@dataclass(frozen=True)
class ModelServiceChatResult:
    platform_model_id: str
    binding_id: str
    engine_type: str
    engine_model_snapshot: str
    response: ModelChatResponse


@dataclass(frozen=True)
class ModelServiceStreamResult:
    platform_model_id: str
    binding_id: str
    engine_type: str
    engine_model_snapshot: str
    _stream: AsyncIterator[ModelStreamEvent] = field(repr=False, compare=False)
    _iterator: AsyncIterator[ModelStreamEvent] = field(init=False, repr=False, compare=False)
    _operation_lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock, repr=False, compare=False)
    _closed: bool = field(init=False, default=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, '_iterator', self._stream.__aiter__())

    def __aiter__(self) -> 'ModelServiceStreamResult':
        return self

    async def __anext__(self) -> ModelStreamEvent:
        async with self._operation_lock:
            if self._closed:
                raise StopAsyncIteration
            try:
                return await anext(self._iterator)
            except StopAsyncIteration:
                await self._close_locked()
                raise
            except BaseException:
                await self._close_preserving_active_error()
                raise

    async def aclose(self) -> None:
        async with self._operation_lock:
            await self._close_locked()

    async def _close_locked(self) -> None:
        if self._closed:
            return
        close = getattr(self._iterator, 'aclose', None)
        if close is not None:
            await close()
        object.__setattr__(self, '_closed', True)

    async def _close_preserving_active_error(self) -> None:
        try:
            await self._close_locked()
        except BaseException:
            # Cleanup failures must not replace the active runtime error or cancellation.
            pass


class ModelService:
    """使用 Platform Model ID 调用可替换 Runtime Engine。

    数据库会话必须由本服务独占且只用于解析模型；调用方不得共享包含未提交写的会话。
    """

    def __init__(
        self,
        db: AsyncSession,
        runtime: ModelRuntimeEngine,
        *,
        session_factory: Callable[[], AsyncSession] | None = None,
        model_dao: type[AiModelCatalogDao] = AiModelCatalogDao,
        binding_dao: type[AiResourceBindingDao] = AiResourceBindingDao,
    ) -> None:
        self._db = db
        self._runtime = runtime
        self._session_factory = session_factory
        self._model_dao = model_dao
        self._binding_dao = binding_dao
        if self._session_factory is None:
            self._require_clean_session()

    async def resolve_model(self, model_id: str) -> ResolvedModel:
        validate_resource_id(model_id, 'mdl')
        if self._session_factory is not None:
            async with self._session_factory() as db:
                return await self._resolve_model_in_session(db, model_id)
        return await self._resolve_model_in_session(self._db, model_id)

    async def _resolve_model_in_session(self, db: AsyncSession, model_id: str) -> ResolvedModel:
        self._require_clean_session(db)
        try:
            model = await self._model_dao.get_model_by_id(db, model_id)
            if model is None:
                raise ModelServiceError.not_found()
            if model.lifecycle_status != 'active':
                raise ModelServiceError.disabled()
            binding = await self._binding_dao.get_current_binding(db, model_id)
            if binding is None or binding.binding_status != 'active':
                raise ModelServiceError.binding_missing()
            defaults = self._validate_parameters(model.default_parameters)
            resolved = ResolvedModel(
                platform_model_id=model.model_id,
                binding_id=binding.binding_id,
                engine_type=binding.engine_type,
                engine_model_snapshot=binding.engine_resource_id,
                default_parameters=defaults,
            )
        except BaseException:
            await self._rollback_preserving_active_error(db)
            raise
        if db.in_transaction():
            await db.rollback()
        return resolved

    async def chat(
        self,
        model_id: str,
        messages: Sequence[ModelMessage],
        parameters: Mapping[str, object] | None = None,
    ) -> ModelServiceChatResult:
        resolved = await self.resolve_model(model_id)
        request = self._build_request(resolved, messages, parameters)
        response = await self._runtime.chat(request)
        return ModelServiceChatResult(
            platform_model_id=resolved.platform_model_id,
            binding_id=resolved.binding_id,
            engine_type=resolved.engine_type,
            engine_model_snapshot=resolved.engine_model_snapshot,
            response=response,
        )

    async def stream_chat(
        self,
        model_id: str,
        messages: Sequence[ModelMessage],
        parameters: Mapping[str, object] | None = None,
    ) -> ModelServiceStreamResult:
        resolved = await self.resolve_model(model_id)
        request = self._build_request(resolved, messages, parameters)
        stream = self._runtime.stream_chat(request)
        return ModelServiceStreamResult(
            platform_model_id=resolved.platform_model_id,
            binding_id=resolved.binding_id,
            engine_type=resolved.engine_type,
            engine_model_snapshot=resolved.engine_model_snapshot,
            _stream=stream,
        )

    @staticmethod
    def _build_request(
        resolved: ResolvedModel,
        messages: Sequence[ModelMessage],
        parameters: Mapping[str, object] | None,
    ) -> ModelChatRequest:
        caller_parameters = ModelService._validate_parameters({} if parameters is None else parameters)
        merged_parameters = {**resolved.default_parameters, **caller_parameters}
        return ModelChatRequest(
            engine_model=resolved.engine_model_snapshot,
            messages=messages,
            parameters=merged_parameters,
        )

    @staticmethod
    def _validate_parameters(parameters: Mapping[str, Any]) -> dict[str, object]:
        if not isinstance(parameters, Mapping):
            raise TypeError('模型参数必须是映射')
        if set(parameters).difference(_ALLOWED_PARAMETERS):
            raise ValueError(_UNSUPPORTED_PARAMETER_MESSAGE)
        normalized = {key: value for key, value in parameters.items() if value is not None}
        aliased = {ModelDefaultParametersModel.model_fields[key].alias: value for key, value in normalized.items()}
        try:
            validated = ModelDefaultParametersModel.model_validate(aliased)
        except ValidationError:
            raise ValueError(_INVALID_PARAMETER_MESSAGE) from None
        return validated.model_dump(exclude_none=True)

    def _require_clean_session(self, db: AsyncSession | None = None) -> None:
        session = self._db if db is None else db
        if session.in_transaction():
            raise RuntimeError('Model Service 需要独占的只读数据库会话')

    async def _rollback_preserving_active_error(self, db: AsyncSession | None = None) -> None:
        session = self._db if db is None else db
        if not session.in_transaction():
            return
        try:
            await session.rollback()
        except BaseException:
            pass
