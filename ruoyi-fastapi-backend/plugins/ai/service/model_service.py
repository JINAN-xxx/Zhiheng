import asyncio
import unicodedata
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
    ModelEmbeddingRequest,
    ModelEmbeddingResponse,
    ModelMessage,
    ModelRerankDocument,
    ModelRerankRequest,
    ModelRerankResponse,
    ModelRuntimeEngine,
    ModelStreamEvent,
)
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.domain.resource_id import validate_resource_id
from plugins.ai.entity.vo.ai_model_catalog_vo import ModelDefaultParametersModel

_CHAT_DEFAULT_PARAMETERS = frozenset(
    {
        'temperature',
        'top_p',
        'max_tokens',
        'presence_penalty',
        'frequency_penalty',
    }
)
_EMBEDDING_DEFAULT_PARAMETERS = frozenset({'dimensions'})
_RERANK_DEFAULT_PARAMETERS = frozenset({'top_n', 'return_documents'})
_DEFAULT_PARAMETERS_BY_TYPE = {
    'chat': _CHAT_DEFAULT_PARAMETERS,
    'embedding': _EMBEDDING_DEFAULT_PARAMETERS,
    'rerank': _RERANK_DEFAULT_PARAMETERS,
}
_REQUEST_PARAMETERS_BY_TYPE = {
    'chat': _CHAT_DEFAULT_PARAMETERS | {'stop', 'user', 'thinking_mode', 'reasoning_effort'},
    'embedding': _EMBEDDING_DEFAULT_PARAMETERS | {'user'},
    'rerank': _RERANK_DEFAULT_PARAMETERS,
}
_FORBIDDEN_TEXT_CATEGORIES = frozenset({'Cc', 'Zl', 'Zp'})
_INVALID_PARAMETER_MESSAGE = '模型参数无效'
_UNSUPPORTED_PARAMETER_MESSAGE = '不支持的模型参数'
_MAX_STOP_SEQUENCES = 4
_EMBEDDING_MAX_DIMENSIONS = 65536


def _freeze_parameters(parameters: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType(dict(parameters))


@dataclass(frozen=True)
class ResolvedModel:
    platform_model_id: str
    binding_id: str
    engine_type: str
    engine_model_snapshot: str
    model_type: str
    default_parameters: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))
    model_code: str | None = None

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
class ModelServiceEmbeddingResult:
    platform_model_id: str
    binding_id: str
    engine_type: str
    engine_model_snapshot: str
    response: ModelEmbeddingResponse


@dataclass(frozen=True)
class ModelServiceRerankResult:
    platform_model_id: str
    binding_id: str
    engine_type: str
    engine_model_snapshot: str
    response: ModelRerankResponse


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

    async def resolve_model(self, model_id: str, expected_type: str | None = None) -> ResolvedModel:
        validate_resource_id(model_id, 'mdl')
        if self._session_factory is not None:
            async with self._session_factory() as db:
                return await self._resolve_model_in_session(db, model_id, expected_type)
        return await self._resolve_model_in_session(self._db, model_id, expected_type)

    async def _resolve_model_in_session(
        self,
        db: AsyncSession,
        model_id: str,
        expected_type: str | None,
    ) -> ResolvedModel:
        self._require_clean_session(db)
        try:
            model = await self._model_dao.get_model_by_id(db, model_id)
            if model is None:
                raise ModelServiceError.not_found()
            if model.lifecycle_status != 'active':
                raise ModelServiceError.disabled()
            if expected_type is not None and model.model_type != expected_type:
                # 类型不匹配与未授权返回同一外部错误，避免泄露其他类型授权。
                raise ModelServiceError.not_found()
            binding = await self._binding_dao.get_current_binding(db, model_id)
            if binding is None or binding.binding_status != 'active':
                raise ModelServiceError.binding_missing()
            defaults = self._validate_parameters(model.model_type, model.default_parameters)
            resolved = ResolvedModel(
                platform_model_id=model.model_id,
                binding_id=binding.binding_id,
                engine_type=binding.engine_type,
                engine_model_snapshot=binding.engine_resource_id,
                model_type=model.model_type,
                default_parameters=defaults,
                model_code=model.model_code,
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
        resolved = await self.resolve_model(model_id, expected_type='chat')
        request = self._build_request(resolved, messages, parameters)
        response = await self._runtime.chat(request)
        return ModelServiceChatResult(
            platform_model_id=resolved.platform_model_id,
            binding_id=resolved.binding_id,
            engine_type=resolved.engine_type,
            engine_model_snapshot=resolved.engine_model_snapshot,
            response=response,
        )

    async def embed(
        self,
        model_id: str,
        inputs: Sequence[str],
        parameters: Mapping[str, object] | None = None,
    ) -> ModelServiceEmbeddingResult:
        resolved = await self.resolve_model(model_id, expected_type='embedding')
        request = self._build_embedding_request(resolved, inputs, parameters)
        response = await self._runtime.embed(request)
        return ModelServiceEmbeddingResult(
            platform_model_id=resolved.platform_model_id,
            binding_id=resolved.binding_id,
            engine_type=resolved.engine_type,
            engine_model_snapshot=resolved.engine_model_snapshot,
            response=response,
        )

    async def rerank(
        self,
        model_id: str,
        query: str,
        documents: Sequence[str],
        parameters: Mapping[str, object] | None = None,
    ) -> ModelServiceRerankResult:
        resolved = await self.resolve_model(model_id, expected_type='rerank')
        request = self._build_rerank_request(resolved, query, documents, parameters)
        response = await self._runtime.rerank(request)
        return ModelServiceRerankResult(
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
        resolved = await self.resolve_model(model_id, expected_type='chat')
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
        caller_parameters = ModelService._validate_parameters(
            resolved.model_type,
            {} if parameters is None else parameters,
            allow_request_extensions=True,
        )
        merged_parameters = {**resolved.default_parameters, **caller_parameters}
        return ModelChatRequest(
            engine_model=resolved.engine_model_snapshot,
            messages=messages,
            parameters=merged_parameters,
        )

    @staticmethod
    def _build_embedding_request(
        resolved: ResolvedModel,
        inputs: Sequence[str],
        parameters: Mapping[str, object] | None,
    ) -> ModelEmbeddingRequest:
        caller_parameters = ModelService._validate_parameters(
            resolved.model_type,
            {} if parameters is None else parameters,
            allow_request_extensions=True,
        )
        merged_parameters = {**resolved.default_parameters, **caller_parameters}
        return ModelEmbeddingRequest(
            engine_model=resolved.engine_model_snapshot,
            inputs=tuple(inputs),
            dimensions=merged_parameters.get('dimensions'),
            user=merged_parameters.get('user'),
        )

    @staticmethod
    def _build_rerank_request(
        resolved: ResolvedModel,
        query: str,
        documents: Sequence[str],
        parameters: Mapping[str, object] | None,
    ) -> ModelRerankRequest:
        caller_parameters = ModelService._validate_parameters(
            resolved.model_type,
            {} if parameters is None else parameters,
            allow_request_extensions=True,
        )
        merged_parameters = {**resolved.default_parameters, **caller_parameters}
        return ModelRerankRequest(
            engine_model=resolved.engine_model_snapshot,
            query=query,
            documents=tuple(ModelRerankDocument(text=document) for document in documents),
            top_n=merged_parameters.get('top_n'),
            return_documents=bool(merged_parameters.get('return_documents', False)),
        )

    @classmethod
    def _validate_parameters(
        cls,
        model_type: str,
        parameters: Mapping[str, Any],
        *,
        allow_request_extensions: bool = False,
    ) -> dict[str, object]:
        if model_type not in _DEFAULT_PARAMETERS_BY_TYPE:
            raise ValueError(_UNSUPPORTED_PARAMETER_MESSAGE)
        if not isinstance(parameters, Mapping):
            raise TypeError('模型参数必须是映射')
        if not allow_request_extensions:
            # 兼容旧目录把三种模型共用 VO 中的未设置字段全部存为 null。
            # 只忽略已知默认字段的 null，未知字段和非空的跨类型参数仍严格拒绝。
            parameters = {
                key: value
                for key, value in parameters.items()
                if value is not None or key not in ModelDefaultParametersModel.model_fields
            }
        allowed = (
            _REQUEST_PARAMETERS_BY_TYPE[model_type]
            if allow_request_extensions
            else _DEFAULT_PARAMETERS_BY_TYPE[model_type]
        )
        if set(parameters).difference(allowed):
            raise ValueError(_UNSUPPORTED_PARAMETER_MESSAGE)
        # 内部受限参数必须精确匹配固定值，显式 null 也需要进入校验。
        normalized = {
            key: value
            for key, value in parameters.items()
            if value is not None or key in {'thinking_mode', 'reasoning_effort'}
        }
        if model_type == 'chat':
            return cls._validate_chat_parameters(normalized)
        if model_type == 'embedding':
            return cls._validate_embedding_parameters(normalized)
        return cls._validate_rerank_parameters(normalized)

    @staticmethod
    def _validate_chat_parameters(parameters: Mapping[str, Any]) -> dict[str, object]:
        default_parameters = {key: value for key, value in parameters.items() if key in _CHAT_DEFAULT_PARAMETERS}
        aliased = {
            ModelDefaultParametersModel.model_fields[key].alias: value for key, value in default_parameters.items()
        }
        try:
            validated = ModelDefaultParametersModel.model_validate(aliased)
        except ValidationError:
            raise ValueError(_INVALID_PARAMETER_MESSAGE) from None
        result = validated.model_dump(exclude_none=True)
        if 'stop' in parameters:
            result['stop'] = ModelService._validate_stop(parameters['stop'])
        if 'user' in parameters:
            result['user'] = ModelService._validate_user(parameters['user'])
        if 'thinking_mode' in parameters:
            if parameters['thinking_mode'] != 'disabled':
                raise ValueError(_INVALID_PARAMETER_MESSAGE)
            result['thinking_mode'] = 'disabled'
        if 'reasoning_effort' in parameters:
            if parameters['reasoning_effort'] != 'low':
                raise ValueError(_INVALID_PARAMETER_MESSAGE)
            result['reasoning_effort'] = 'low'
        return result

    @staticmethod
    def _validate_embedding_parameters(parameters: Mapping[str, Any]) -> dict[str, object]:
        result: dict[str, object] = {}
        if 'dimensions' in parameters:
            dimensions = parameters['dimensions']
            if type(dimensions) is not int or not 1 <= dimensions <= _EMBEDDING_MAX_DIMENSIONS:
                raise ValueError(_INVALID_PARAMETER_MESSAGE)
            result['dimensions'] = dimensions
        if 'user' in parameters:
            result['user'] = ModelService._validate_user(parameters['user'])
        return result

    @staticmethod
    def _validate_rerank_parameters(parameters: Mapping[str, Any]) -> dict[str, object]:
        result: dict[str, object] = {}
        if 'top_n' in parameters:
            top_n = parameters['top_n']
            if type(top_n) is not int or top_n < 1:
                raise ValueError(_INVALID_PARAMETER_MESSAGE)
            result['top_n'] = top_n
        if 'return_documents' in parameters:
            return_documents = parameters['return_documents']
            if type(return_documents) is not bool:
                raise ValueError(_INVALID_PARAMETER_MESSAGE)
            result['return_documents'] = return_documents
        return result

    @staticmethod
    def _validate_stop(value: object) -> str | tuple[str, ...]:
        if isinstance(value, str):
            values = (value,)
            normalized: str | tuple[str, ...] = value
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            values = tuple(value)
            normalized = values
        else:
            raise ValueError(_INVALID_PARAMETER_MESSAGE)
        if not 1 <= len(values) <= _MAX_STOP_SEQUENCES or any(
            not ModelService._valid_public_text(item, 100) for item in values
        ):
            raise ValueError(_INVALID_PARAMETER_MESSAGE)
        return normalized

    @staticmethod
    def _validate_user(value: object) -> str:
        if not ModelService._valid_public_text(value, 128):
            raise ValueError(_INVALID_PARAMETER_MESSAGE)
        return value

    @staticmethod
    def _valid_public_text(value: object, max_length: int) -> bool:
        return (
            isinstance(value, str)
            and 1 <= len(value) <= max_length
            and not any(unicodedata.category(character) in _FORBIDDEN_TEXT_CATEGORIES for character in value)
        )

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
