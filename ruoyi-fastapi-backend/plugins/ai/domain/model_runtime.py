from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from math import isfinite
from types import MappingProxyType
from typing import Literal, Protocol

_MESSAGE_ROLES = frozenset({'system', 'developer', 'user', 'assistant'})


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


class ModelRuntimeEngine(Protocol):
    async def list_model_ids(self) -> set[str]: ...

    async def chat(self, request: ModelChatRequest) -> ModelChatResponse: ...

    def stream_chat(self, request: ModelChatRequest) -> AsyncIterator[ModelStreamEvent]: ...

    async def aclose(self) -> None: ...
