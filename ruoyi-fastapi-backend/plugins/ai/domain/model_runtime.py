import unicodedata
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from math import isfinite
from types import MappingProxyType
from typing import Literal, Protocol

_MESSAGE_ROLES = frozenset({'system', 'developer', 'user', 'assistant'})
_FORBIDDEN_TEXT_CATEGORIES = frozenset({'Cc', 'Zl', 'Zp'})
_EMBEDDING_MAX_INPUTS = 256
_EMBEDDING_MAX_INPUT_BYTES = 32768
_EMBEDDING_MAX_DIMENSIONS = 65536
_RERANK_MAX_DOCUMENTS = 256
_RERANK_MAX_QUERY_BYTES = 8192
_RERANK_MAX_DOCUMENT_BYTES = 32768
_MAX_USER_LENGTH = 128


def _contains_forbidden_text(value: str) -> bool:
    return any(unicodedata.category(character) in _FORBIDDEN_TEXT_CATEGORIES for character in value)


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        frozen_items: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError('模型请求对象的键必须是字符串')
            frozen_items[key] = _freeze(item)
        return MappingProxyType(frozen_items)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and isfinite(value):
        return value
    raise TypeError('模型请求只允许 JSON 安全值')


@dataclass(frozen=True)
class ModelMessage:
    role: str
    content: object

    def __post_init__(self) -> None:
        if self.role not in _MESSAGE_ROLES:
            raise ValueError('不支持的模型消息角色')
        if not isinstance(self.content, str) and not (
            isinstance(self.content, Sequence)
            and not isinstance(self.content, (str, bytes, bytearray))
            and all(isinstance(block, Mapping) for block in self.content)
        ):
            raise TypeError('模型消息内容必须是文本或内容块序列')
        object.__setattr__(self, 'content', _freeze(self.content))


@dataclass(frozen=True)
class ModelChatRequest:
    engine_model: str
    messages: Sequence[ModelMessage]
    parameters: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.engine_model, str)
            or not self.engine_model
            or self.engine_model != self.engine_model.strip()
        ):
            raise ValueError('引擎模型标识必须是无首尾空白的非空字符串')
        messages = tuple(self.messages)
        if not messages:
            raise ValueError('模型请求至少需要一条消息')
        if any(not isinstance(message, ModelMessage) for message in messages):
            raise TypeError('模型请求消息必须使用 ModelMessage')
        if not isinstance(self.parameters, Mapping):
            raise TypeError('模型参数必须是映射')
        object.__setattr__(self, 'messages', messages)
        object.__setattr__(self, 'parameters', _freeze(self.parameters))


@dataclass(frozen=True)
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    raw_cost: Decimal | None = None

    def __post_init__(self) -> None:
        token_counts = (
            self.input_tokens,
            self.output_tokens,
            self.reasoning_tokens,
            self.cache_read_tokens,
            self.cache_write_tokens,
        )
        if any(type(value) is not int or value < 0 for value in token_counts):
            raise ValueError('模型用量必须使用非负整数')
        if self.raw_cost is not None and (
            not isinstance(self.raw_cost, Decimal) or not self.raw_cost.is_finite() or self.raw_cost < 0
        ):
            raise ValueError('模型原始成本必须是有限非负 Decimal')


@dataclass(frozen=True)
class ModelChatResponse:
    text: str
    reasoning: str | None = None
    usage: ModelUsage = field(default_factory=ModelUsage)
    request_id: str | None = None
    finish_reason: Literal['stop', 'length', 'content_filter', 'tool_calls', 'function_call'] | None = None


@dataclass(frozen=True)
class ModelStreamEvent:
    event_type: Literal['meta', 'content', 'reasoning', 'usage', 'done']
    delta: str | None = None
    usage: ModelUsage | None = None
    request_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, str):
            raise TypeError('模型流事件类型必须是字符串')
        if self.event_type in {'content', 'reasoning'}:
            if not isinstance(self.delta, str) or not self.delta:
                raise ValueError('文本流事件必须携带非空增量')
            if self.usage is not None or self.request_id is not None:
                raise ValueError('文本流事件不能携带其他字段')
            return
        if self.event_type == 'usage':
            if not isinstance(self.usage, ModelUsage):
                raise TypeError('用量流事件必须携带规范用量')
            if self.delta is not None or self.request_id is not None:
                raise ValueError('用量流事件不能携带其他字段')
            return
        if self.event_type == 'meta':
            if not isinstance(self.request_id, str) or not self.request_id:
                raise ValueError('元数据流事件必须携带请求 ID')
            if self.delta is not None or self.usage is not None:
                raise ValueError('元数据流事件不能携带其他字段')
            return
        if self.event_type == 'done':
            if self.delta is not None or self.usage is not None or self.request_id is not None:
                raise ValueError('完成流事件不能携带其他字段')
            return
        raise ValueError('不支持的模型流事件类型')


def _valid_engine_model(value: object) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or _contains_forbidden_text(value):
        raise ValueError('引擎模型标识必须是无首尾空白且不含控制字符的非空字符串')
    return value


def _valid_plain_text(value: object, max_bytes: int, *, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode('utf-8')) > max_bytes
        or _contains_forbidden_text(value)
    ):
        raise ValueError(f'{field_name}必须是安全且不超限的文本')
    return value


def _valid_user(value: object) -> str | None:
    if value is None:
        return None
    return _valid_plain_text(value, _MAX_USER_LENGTH, field_name='用户标识')


def _valid_dimensions(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 1 <= value <= _EMBEDDING_MAX_DIMENSIONS:
        raise ValueError('向量维度必须是 1 到 65536 的整数')
    return value


def _valid_top_n(value: object, document_count: int) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 1 <= value <= document_count:
        raise ValueError('top_n 必须是 1 到文档数量的整数')
    return value


def _valid_raw_cost(value: object) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError('原始成本必须是有限非负 Decimal')
    return value


def _valid_float_vector(values: Sequence[object]) -> tuple[float, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        raise TypeError('向量必须是有限浮点数序列')
    normalized = tuple(values)
    if not normalized or any(type(item) is not float or not isfinite(item) for item in normalized):
        raise ValueError('向量必须是非空且全部为有限浮点数')
    return normalized


@dataclass(frozen=True)
class ModelEmbeddingRequest:
    engine_model: str
    inputs: Sequence[str]
    dimensions: int | None = None
    user: str | None = None

    def __post_init__(self) -> None:
        engine_model = _valid_engine_model(self.engine_model)
        inputs = tuple(self.inputs)
        if not 1 <= len(inputs) <= _EMBEDDING_MAX_INPUTS:
            raise ValueError('Embedding 输入数量必须在 1 到 256 之间')
        validated_inputs = tuple(
            _valid_plain_text(item, _EMBEDDING_MAX_INPUT_BYTES, field_name='Embedding 输入') for item in inputs
        )
        dimensions = _valid_dimensions(self.dimensions)
        user = _valid_user(self.user)
        object.__setattr__(self, 'engine_model', engine_model)
        object.__setattr__(self, 'inputs', validated_inputs)
        object.__setattr__(self, 'dimensions', dimensions)
        object.__setattr__(self, 'user', user)


@dataclass(frozen=True)
class ModelEmbeddingVector:
    index: int
    values: Sequence[float]

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise ValueError('向量索引必须是非负整数')
        object.__setattr__(self, 'values', _valid_float_vector(self.values))


@dataclass(frozen=True)
class ModelEmbeddingResponse:
    vectors: Sequence[ModelEmbeddingVector]
    input_tokens: int | None = None
    raw_cost: Decimal | None = None

    def __post_init__(self) -> None:
        vectors = tuple(self.vectors)
        if not vectors or any(not isinstance(vector, ModelEmbeddingVector) for vector in vectors):
            raise ValueError('Embedding 响应至少需要一个向量')
        if len({len(vector.values) for vector in vectors}) != 1:
            raise ValueError('Embedding 响应所有向量维度必须一致')
        indexes = [vector.index for vector in vectors]
        if sorted(indexes) != list(range(len(vectors))):
            raise ValueError('Embedding 响应索引必须唯一且覆盖 0..n-1')
        if self.input_tokens is not None and (type(self.input_tokens) is not int or self.input_tokens < 0):
            raise ValueError('Embedding 输入 Token 必须是可空非负整数')
        object.__setattr__(self, 'vectors', vectors)
        object.__setattr__(self, 'raw_cost', _valid_raw_cost(self.raw_cost))


@dataclass(frozen=True)
class ModelRerankDocument:
    text: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, 'text', _valid_plain_text(self.text, _RERANK_MAX_DOCUMENT_BYTES, field_name='文档文本')
        )


@dataclass(frozen=True)
class ModelRerankResult:
    index: int
    relevance_score: float

    def __post_init__(self) -> None:
        if type(self.index) is not int or self.index < 0:
            raise ValueError('Rerank 结果索引必须是非负整数')
        if type(self.relevance_score) is not float or not isfinite(self.relevance_score):
            raise ValueError('Rerank 相关性分数必须是有限浮点数')


@dataclass(frozen=True)
class ModelRerankRequest:
    engine_model: str
    query: str
    documents: Sequence[ModelRerankDocument]
    top_n: int | None = None
    return_documents: bool = False

    def __post_init__(self) -> None:
        engine_model = _valid_engine_model(self.engine_model)
        query = _valid_plain_text(self.query, _RERANK_MAX_QUERY_BYTES, field_name='查询文本')
        documents = tuple(self.documents)
        if not 1 <= len(documents) <= _RERANK_MAX_DOCUMENTS:
            raise ValueError('Rerank 文档数量必须在 1 到 256 之间')
        if any(not isinstance(document, ModelRerankDocument) for document in documents):
            raise TypeError('Rerank 文档必须使用 ModelRerankDocument')
        top_n = _valid_top_n(self.top_n, len(documents))
        if type(self.return_documents) is not bool:
            raise TypeError('return_documents 必须是布尔值')
        object.__setattr__(self, 'engine_model', engine_model)
        object.__setattr__(self, 'query', query)
        object.__setattr__(self, 'documents', documents)
        object.__setattr__(self, 'top_n', top_n)


@dataclass(frozen=True)
class ModelRerankResponse:
    request_id: str
    results: Sequence[ModelRerankResult]
    input_tokens: int | None = None
    search_units: int | None = None
    raw_cost: Decimal | None = None

    def __post_init__(self) -> None:
        results = tuple(self.results)
        if not results or any(not isinstance(result, ModelRerankResult) for result in results):
            raise ValueError('Rerank 响应至少需要一个结果')
        indexes = [result.index for result in results]
        if len(set(indexes)) != len(indexes):
            raise ValueError('Rerank 响应索引必须唯一')
        if self.input_tokens is not None and (type(self.input_tokens) is not int or self.input_tokens < 0):
            raise ValueError('Rerank 输入 Token 必须是可空非负整数')
        if self.search_units is not None and (type(self.search_units) is not int or self.search_units < 1):
            raise ValueError('Rerank Search Unit 必须是可空正整数')
        if not isinstance(self.request_id, str) or not self.request_id:
            raise ValueError('Rerank 请求 ID 必须是非空字符串')
        object.__setattr__(self, 'results', results)
        object.__setattr__(self, 'raw_cost', _valid_raw_cost(self.raw_cost))


class ModelRuntimeEngine(Protocol):
    async def list_model_ids(self) -> set[str]: ...

    async def chat(self, request: ModelChatRequest) -> ModelChatResponse: ...

    def stream_chat(self, request: ModelChatRequest) -> AsyncIterator[ModelStreamEvent]: ...

    async def embed(self, request: ModelEmbeddingRequest) -> ModelEmbeddingResponse: ...

    async def rerank(self, request: ModelRerankRequest) -> ModelRerankResponse: ...

    async def aclose(self) -> None: ...
