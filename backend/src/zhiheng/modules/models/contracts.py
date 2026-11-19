import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from http import HTTPStatus
from typing import Literal, Protocol, get_args
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    ValidationError,
    computed_field,
    field_validator,
    model_serializer,
    model_validator,
)

from zhiheng.modules.models.capacity import SAFETY_MARGIN, CapacityStatus, capacity_evidence_status
from zhiheng.shared.errors import AppError
from zhiheng.shared.numbers import MAX_SAFE_INTEGER
from zhiheng.shared.text import storage_text

MAX_IMAGE_SNAPSHOT_BYTES = 2_000_000
MAX_CONTEXT_IMAGE_BYTES = 16 * 1024 * 1024
MAX_TRIAL_PROMPT_CHARS = 4000
MAX_TRIAL_DOCUMENT_CHARS = 1000
MAX_TRIAL_DOCUMENTS = 8
IMAGE_TOKEN_RESERVATION = 2048
MIN_HTTP_STATUS = 100
MAX_HTTP_STATUS = 599


@dataclass(frozen=True)
class ModelImage:
    data_url: str
    estimated_tokens: int = IMAGE_TOKEN_RESERVATION


@dataclass(frozen=True)
class ModelMessage:
    role: Literal['system', 'developer', 'user', 'assistant']
    content: str
    images: tuple[ModelImage, ...] = ()


@dataclass(frozen=True)
class ModelDelta:
    text: str = ''
    input_tokens: int | None = None
    output_tokens: int | None = None
    finish_reason: Literal['stop', 'length'] | None = None
    result: dict[str, object] | None = None
    search_units: int | None = None
    cache_tokens: int | None = None
    reasoning_tokens: int | None = None
    reasoning: str = ''
    activity: bool = False


class ModelProvider(Protocol):
    async def validate(self) -> None: ...

    def stream(self, messages: tuple[ModelMessage, ...]) -> AsyncIterator[ModelDelta]: ...


class ModelToolCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')

    id: str = Field(min_length=1, max_length=200)
    name: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z_][A-Za-z0-9_-]*$')
    arguments: str = Field(default='{}', max_length=100000)

    @field_validator('id')
    @classmethod
    def valid_identity(cls, value: str) -> str:
        value.encode('utf-8')
        if any(character < ' ' for character in value):
            raise ValueError('Invalid tool call identity')
        return value


class AgentModelMessage(BaseModel):
    """Tool-aware wire values for trusted internal Agent executions only."""

    model_config = ConfigDict(frozen=True, extra='forbid')

    role: Literal['system', 'developer', 'user', 'assistant', 'tool']
    content: str = Field(default='', max_length=2000000)
    tool_calls: tuple[ModelToolCall, ...] = Field(default=(), max_length=20)
    tool_call_id: str | None = Field(default=None, min_length=1, max_length=200)
    reasoning_content: str = Field(default='', max_length=200000)

    @model_validator(mode='after')
    def valid_tool_message(self) -> 'AgentModelMessage':
        if (self.role == 'tool') != (self.tool_call_id is not None):
            raise ValueError('Tool results require their original tool call identity')
        if self.tool_calls and self.role != 'assistant':
            raise ValueError('Only assistant messages can request tools')
        return self


class AgentModelProvider(Protocol):
    def stream_agent(
        self, messages: tuple[AgentModelMessage, ...], tools: tuple[dict[str, object], ...]
    ) -> AsyncIterator[ModelDelta]: ...


ProviderFailureReason = Literal[
    'unknown',
    'http_error',
    'insufficient_balance',
    'transport_error',
    'timeout',
    'connect_timeout',
    'api_error',
    'invalid_response',
    'incomplete_stream',
    'invalid_content',
    'invalid_reasoning',
    'unsupported_finish_reason',
    'invalid_options',
    'invalid_request',
    'projection_limit',
]
ProviderExceptionType = Literal[
    'APIStatusError',
    'APITimeoutError',
    'APIConnectionError',
    'APIError',
    'HTTPStatusError',
    'TimeoutException',
    'HTTPError',
    'TimeoutError',
    'ValueError',
    'KeyError',
]
ProviderRateLimitMetric = Literal['unknown', 'rpm', 'rph', 'rpd', 'tpm', 'tpd', 'ipm', 'ipd']


class ProviderError(AppError):
    def __init__(
        self,
        *,
        definitive_rejection: bool = False,
        reason: ProviderFailureReason = 'unknown',
        upstream_status: int | None = None,
        upstream_error_type: ProviderExceptionType | None = None,
        rate_limit_metric: ProviderRateLimitMetric = 'unknown',
    ) -> None:
        self.definitive_rejection = definitive_rejection
        # Internal diagnostics are allowlisted even when a caller bypasses typing.
        # Never accept exception text, response bodies, or arbitrary class names.
        self.reason = reason if type(reason) is str and reason in get_args(ProviderFailureReason) else 'unknown'
        self.upstream_status = (
            upstream_status
            if type(upstream_status) is int and MIN_HTTP_STATUS <= upstream_status <= MAX_HTTP_STATUS
            else None
        )
        self.upstream_error_type = (
            upstream_error_type
            if type(upstream_error_type) is str and upstream_error_type in get_args(ProviderExceptionType)
            else None
        )
        self.rate_limit_metric: ProviderRateLimitMetric = (
            rate_limit_metric
            if self.upstream_status == HTTPStatus.TOO_MANY_REQUESTS
            and type(rate_limit_metric) is str
            and rate_limit_metric in get_args(ProviderRateLimitMetric)
            else 'unknown'
        )
        super().__init__(
            'provider_rejected' if definitive_rejection else 'provider_unavailable', '模型调用失败，请稍后重试。', 502
        )


class ModelTrialInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    prompt: str = Field(min_length=1, max_length=MAX_TRIAL_PROMPT_CHARS)
    documents: tuple[str, ...] = Field(default=(), max_length=MAX_TRIAL_DOCUMENTS)

    @field_validator('prompt')
    @classmethod
    def clean_prompt(cls, value: str) -> str:
        value.encode('utf-8')
        if not value.strip() or '\x00' in value:
            raise ValueError('请输入有效的试用内容')
        return value.strip()

    @field_validator('documents')
    @classmethod
    def clean_documents(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        result = []
        for value in values:
            value.encode('utf-8')
            if not value.strip() or '\x00' in value or len(value) > MAX_TRIAL_DOCUMENT_CHARS:
                raise ValueError('每份候选资料需为 1 至 1000 个字符')
            result.append(value.strip())
        return tuple(result)


class ModelOptions(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    provider_output_default: bool = False
    temperature: float | None = None
    top_p: float | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None
    stop: str | tuple[str, ...] | None = None
    user: str | None = None
    dimensions: int | None = None
    top_n: int | None = None
    return_documents: bool | None = None
    response_format: Literal['json_object'] | None = None
    reasoning_effort: Literal['none', 'minimal', 'low', 'medium', 'high'] | None = None

    @model_serializer(mode='wrap')
    def serialize_existing_options(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        raw = handler(self)
        if not isinstance(raw, dict) or any(not isinstance(key, str) for key in raw):
            raise ValueError('Invalid options serialization')
        result: dict[str, object] = dict(raw)
        if not self.provider_output_default:
            result.pop('provider_output_default', None)
        # Optional additions must not invalidate already published vector
        # generations whose schema-7 binding contains the original fields.
        if self.response_format is None:
            result.pop('response_format', None)
        if self.reasoning_effort is None:
            result.pop('reasoning_effort', None)
        return result


class ModelBinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')

    model_id: UUID
    connection_id: UUID
    upstream_model: str
    max_output_tokens: int = Field(strict=True, ge=1, le=MAX_SAFE_INTEGER)
    context_window_tokens: int | None = Field(default=None, strict=True, ge=256, le=2097152)
    version: Literal[1] = 1
    connection_revision_id: UUID | None = None
    model_type: Literal['chat', 'embedding', 'rerank'] = 'chat'
    vision_enabled: bool = Field(default=False, strict=True)
    options: ModelOptions = Field(default_factory=ModelOptions)

    @property
    def output_limit_tokens(self) -> int | None:
        return None if self.options.provider_output_default else self.max_output_tokens

    @property
    def output_reservation_tokens(self) -> int:
        # A provisional billing estimate does not become a provider generation limit.
        return min(4096, self.max_output_tokens) if self.options.provider_output_default else self.max_output_tokens

    @model_serializer(mode='wrap')
    def serialize_compatible_binding(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        raw = handler(self)
        if not isinstance(raw, dict):
            raise ValueError('Invalid binding serialization')
        result: dict[str, object] = dict(raw)
        if self.context_window_tokens is None:
            result.pop('context_window_tokens', None)
        return result

    @model_validator(mode='after')
    def supported_vision(self) -> 'ModelBinding':
        if self.vision_enabled and self.model_type != 'chat':
            raise ValueError('Vision requires chat model')
        return self


class ProviderFactory(Protocol):
    def __call__(
        self, *, base_url: str, api_key: str, binding: ModelBinding, timeout_seconds: float
    ) -> ModelProvider: ...


class ModelRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')

    binding: ModelBinding
    messages: tuple[ModelMessage, ...]
    execution_timeout_seconds: float | None = Field(default=None, ge=0.1, le=600)
    execution_deadline: datetime | None = None
    required_modules: tuple[str, ...] = ()
    parent_run_id: UUID | None = None
    parent_attempt_id: UUID | None = None
    agent_messages: tuple[AgentModelMessage, ...] = ()
    tools: tuple[dict[str, object], ...] = Field(default=(), max_length=30)

    @model_validator(mode='before')
    @classmethod
    def restore_agent_messages(cls, value: object) -> object:
        if not isinstance(value, dict) or 'agent_messages_json' not in value:
            return value
        restored = dict(value)
        encoded = restored.pop('agent_messages_json')
        if not isinstance(encoded, str) or 'agent_messages' in restored:
            raise ValueError('Invalid encoded Agent messages')
        restored['agent_messages'] = json.loads(encoded)
        return restored

    @model_serializer(mode='wrap')
    def serialize_agent_additions(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        raw = handler(self)
        if not isinstance(raw, dict):
            raise ValueError('Invalid model request serialization')
        result: dict[str, object] = dict(raw)
        if self.agent_messages:
            # JSONB cannot store NUL or unpaired surrogates. Keep executable
            # messages in an ASCII JSON string; only the display transcript is escaped.
            result.pop('agent_messages', None)
            result['agent_messages_json'] = json.dumps(
                [message.model_dump(mode='json') for message in self.agent_messages], ensure_ascii=True
            )
            result['messages'] = [
                {'role': message.role, 'content': storage_text(message.content), 'images': []}
                for message in self.messages
            ]
        else:
            result.pop('agent_messages', None)
        if not self.tools:
            result.pop('tools', None)
        if self.parent_run_id is None:
            result.pop('parent_run_id', None)
        if self.parent_attempt_id is None:
            result.pop('parent_attempt_id', None)
        return result

    @model_validator(mode='after')
    def valid_agent_request(self) -> 'ModelRequest':
        if (self.parent_run_id is None) != (self.parent_attempt_id is None):
            raise ValueError('A child model call requires its complete parent execution identity')
        if (self.agent_messages or self.tools) and self.binding.model_type != 'chat':
            raise ValueError('Agent tools require a chat model')
        if self.tools and not self.agent_messages:
            raise ValueError('Agent tools require explicit Agent messages')
        return self

    @field_validator('messages')
    @classmethod
    def validate_images(cls, messages: tuple[ModelMessage, ...]) -> tuple[ModelMessage, ...]:
        for message in messages:
            for image in message.images:
                if (
                    message.role != 'user'
                    or not image.data_url.startswith('data:image/jpeg;base64,')
                    or len(image.data_url) > MAX_IMAGE_SNAPSHOT_BYTES
                    or image.estimated_tokens != IMAGE_TOKEN_RESERVATION
                ):
                    raise ValueError('Invalid managed image snapshot')
        return messages

    @model_validator(mode='after')
    def require_vision_capability(self) -> 'ModelRequest':
        if any(message.images for message in self.messages) and not self.binding.vision_enabled:
            raise ValueError('Model does not support vision')
        if self.binding.model_type != 'chat' and (
            self.binding.options.response_format is not None or self.binding.options.reasoning_effort is not None
        ):
            raise ValueError('Chat generation controls require chat model')
        return self


class ConnectionOption(BaseModel):
    id: UUID
    name: str
    enabled: bool


class ConnectionInfo(BaseModel):
    id: UUID
    name: str
    provider: Literal['openai_compatible'] = 'openai_compatible'
    model_count: int | None = None
    base_url: str
    has_api_key: bool = True
    enabled: bool = True


class ModelAvailability(BaseModel):
    """Platform model/connection state; no claim about upstream reachability."""

    model_config = ConfigDict(frozen=True)
    available: bool
    reason: (
        Literal['model_disabled', 'model_deleted', 'connection_missing', 'connection_disabled', 'connection_deleted']
        | None
    ) = None


class ModelInfo(BaseModel):
    id: UUID
    name: str
    connection_id: UUID
    upstream_model: str
    max_output_tokens: int
    context_window_tokens: int | None = None
    capacity_note: str = ''
    availability: ModelAvailability | None = None
    enabled: bool
    model_type: Literal['chat', 'embedding', 'rerank'] = 'chat'
    vision_enabled: bool = Field(default=False, strict=True)
    capabilities: tuple[Literal['chat', 'stream', 'embedding', 'rerank', 'vision'], ...] = ('chat', 'stream')

    @computed_field  # type: ignore[prop-decorator]  # read-only Pydantic serialization property.
    @property
    def capacity_status(self) -> CapacityStatus:
        return capacity_evidence_status(self.model_type, self.context_window_tokens, self.capacity_note)


class ModelConfiguration(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    name: str = Field(strict=True, min_length=1, max_length=80)
    connection_id: UUID
    upstream_model: str = Field(strict=True, min_length=1, max_length=200)
    max_output_tokens: int = Field(default=512, strict=True, ge=1, le=MAX_SAFE_INTEGER)
    context_window_tokens: int | None = Field(default=None, strict=True, ge=256, le=2097152)
    capacity_note: str = Field(default='', strict=True, max_length=2000)
    model_type: Literal['chat', 'embedding', 'rerank'] = 'chat'
    vision_enabled: bool = Field(default=False, strict=True)

    @field_validator('name', 'upstream_model', 'capacity_note')
    @classmethod
    def valid_text(cls, value: str) -> str:
        value.encode('utf-8')
        value = value.strip()
        if '\x00' in value:
            raise ValueError('文本不能包含空字符')
        return value

    @field_validator('name', 'upstream_model')
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value:
            raise ValueError('名称和上游模型标识不能为空')
        return value

    @classmethod
    def from_fields(cls, values: dict[str, object]) -> 'ModelConfiguration':
        try:
            return cls.model_validate(values)
        except ValidationError as error:
            fields = sorted({str(item['loc'][0]) for item in error.errors() if item['loc']})
            raise AppError(
                'invalid_model_configuration',
                '模型配置字段无效，请检查名称、模型标识、容量和依据',
                422,
                details={'fields': fields},
            ) from error

    @model_validator(mode='after')
    def valid_context_capacity(self) -> 'ModelConfiguration':
        if self.vision_enabled and self.model_type != 'chat':
            raise AppError('unsupported_capability', '仅聊天模型支持vision能力', 422)
        if (
            self.model_type == 'chat'
            and self.context_window_tokens is not None
            and self.max_output_tokens + SAFETY_MARGIN >= self.context_window_tokens
        ):
            raise AppError(
                'invalid_model_capacity',
                '最大输出与256 Token安全余量占满上下文窗口，请调整输出上限或窗口',
                422,
                details={
                    'max_output_tokens': self.max_output_tokens,
                    'context_window_tokens': self.context_window_tokens,
                    'safety_margin_tokens': SAFETY_MARGIN,
                },
            )
        return self


@dataclass(frozen=True)
class ModelSaveCheck:
    configuration: ModelConfiguration
    binding: ModelBinding
    previous: ModelInfo | None
    enabled: bool | None
    provider: ModelProvider | None
    required_actions: tuple[str, ...] = ('update',)


class RateCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    billing_mode: Literal['token', 'search_unit']
    input_price: Decimal = Field(default=Decimal(0), ge=0, max_digits=30, decimal_places=12)
    output_price: Decimal = Field(default=Decimal(0), ge=0, max_digits=30, decimal_places=12)
    cache_price: Decimal = Field(default=Decimal(0), ge=0, max_digits=30, decimal_places=12)
    search_price: Decimal = Field(default=Decimal(0), ge=0, max_digits=30, decimal_places=12)
    currency: str = Field(default='USD', pattern=r'^[A-Z]{3}$')

    @field_validator('input_price', 'output_price', 'cache_price', 'search_price', mode='before')
    @classmethod
    def exact_price(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError('Decimal strings are required')
        return value


class RateInfo(RateCreate):
    @field_validator('input_price', 'output_price', 'cache_price', 'search_price', mode='before')
    @classmethod
    def exact_price(cls, value: object) -> object:
        return value

    id: UUID
    model_id: UUID
    version: int
    created_at: datetime
