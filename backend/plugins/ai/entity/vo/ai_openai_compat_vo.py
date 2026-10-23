import re
import unicodedata
from math import isfinite
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

_FORBIDDEN_SINGLE_LINE_CATEGORIES = frozenset({'Cc', 'Zl', 'Zp'})
_MODEL_CODE_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
MAX_STOP_SEQUENCES = 4
EMBEDDING_MAX_INPUTS = 256
EMBEDDING_MAX_INPUT_BYTES = 32768
EMBEDDING_MAX_DIMENSIONS = 65536
RERANK_MAX_DOCUMENTS = 256
RERANK_MAX_QUERY_BYTES = 8192
RERANK_MAX_DOCUMENT_BYTES = 32768


def _reject_unsafe_single_line_text(value: object) -> object:
    if isinstance(value, str) and any(
        unicodedata.category(character) in _FORBIDDEN_SINGLE_LINE_CATEGORIES for character in value
    ):
        raise ValueError('value contains unsupported control characters')
    return value


SafeModelCode = Annotated[
    str,
    BeforeValidator(_reject_unsafe_single_line_text),
    StringConstraints(strict=True, min_length=1, max_length=100, pattern=_MODEL_CODE_PATTERN),
]
SafeCallerId = Annotated[
    str,
    BeforeValidator(_reject_unsafe_single_line_text),
    StringConstraints(strict=True, min_length=1, max_length=128),
]
SafeStopSequence = Annotated[
    str,
    BeforeValidator(_reject_unsafe_single_line_text),
    StringConstraints(strict=True, min_length=1, max_length=100),
]


def _valid_utf8_bounded_text(value: object, max_bytes: int) -> str:
    if not isinstance(value, str):
        raise ValueError('text must be a string')
    if not value or len(value.encode('utf-8')) > max_bytes:
        raise ValueError('text exceeds the allowed byte size')
    return value


EmbeddingInput = Annotated[
    str,
    BeforeValidator(_reject_unsafe_single_line_text),
    BeforeValidator(lambda value: _valid_utf8_bounded_text(value, EMBEDDING_MAX_INPUT_BYTES)),
    StringConstraints(strict=True, min_length=1),
]
RerankQuery = Annotated[
    str,
    BeforeValidator(_reject_unsafe_single_line_text),
    BeforeValidator(lambda value: _valid_utf8_bounded_text(value, RERANK_MAX_QUERY_BYTES)),
    StringConstraints(strict=True, min_length=1),
]
RerankDocumentText = Annotated[
    str,
    BeforeValidator(_reject_unsafe_single_line_text),
    BeforeValidator(lambda value: _valid_utf8_bounded_text(value, RERANK_MAX_DOCUMENT_BYTES)),
    StringConstraints(strict=True, min_length=1),
]


class _StrictRequestModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class _PublicResponseModel(BaseModel):
    model_config = ConfigDict(extra='ignore', strict=True)


class OpenAIModelModel(_PublicResponseModel):
    id: SafeModelCode
    object: Literal['model'] = 'model'
    created: Annotated[int, Field(strict=True, ge=0)]
    owned_by: Literal['zhiheng'] = 'zhiheng'


class OpenAIModelListResponseModel(_PublicResponseModel):
    object: Literal['list'] = 'list'
    data: list[OpenAIModelModel]


class OpenAIChatMessageModel(_StrictRequestModel):
    role: Literal['system', 'developer', 'user', 'assistant']
    content: Annotated[str, StringConstraints(strict=True, min_length=1)]


class OpenAIChatCompletionRequestModel(_StrictRequestModel):
    model: SafeModelCode
    messages: list[OpenAIChatMessageModel] = Field(min_length=1, max_length=100)
    stream: bool = False
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, gt=0)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    stop: SafeStopSequence | list[SafeStopSequence] | None = Field(default=None)
    user: SafeCallerId | None = None

    @model_validator(mode='after')
    def validate_first_release_contract(self) -> 'OpenAIChatCompletionRequestModel':
        if isinstance(self.stop, list) and not 1 <= len(self.stop) <= MAX_STOP_SEQUENCES:
            raise ValueError('stop must contain between one and four values')
        return self


class OpenAIAssistantMessageModel(_PublicResponseModel):
    role: Literal['assistant'] = 'assistant'
    content: str


class OpenAIChatCompletionChoiceModel(_PublicResponseModel):
    index: Annotated[int, Field(strict=True, ge=0)]
    message: OpenAIAssistantMessageModel
    finish_reason: Literal['stop', 'length']


class OpenAIUsageModel(_PublicResponseModel):
    prompt_tokens: Annotated[int, Field(strict=True, ge=0)]
    completion_tokens: Annotated[int, Field(strict=True, ge=0)]
    total_tokens: Annotated[int, Field(strict=True, ge=0)]


class OpenAIChatCompletionResponseModel(_PublicResponseModel):
    id: Annotated[str, StringConstraints(strict=True, pattern=r'^chatcmpl_[0-9a-f]{32}$')]
    object: Literal['chat.completion'] = 'chat.completion'
    created: Annotated[int, Field(strict=True, ge=0)]
    model: SafeModelCode
    choices: list[OpenAIChatCompletionChoiceModel] = Field(min_length=1)
    usage: OpenAIUsageModel


class OpenAIChatCompletionChunkDeltaModel(_PublicResponseModel):
    role: Literal['assistant'] | None = None
    content: str | None = None

    @model_validator(mode='after')
    def validate_delta_shape(self) -> 'OpenAIChatCompletionChunkDeltaModel':
        if self.role is not None and self.content is not None:
            raise ValueError('chunk delta cannot contain role and content together')
        return self


class OpenAIChatCompletionChunkChoiceModel(_PublicResponseModel):
    index: Annotated[int, Field(strict=True, ge=0)]
    delta: OpenAIChatCompletionChunkDeltaModel
    finish_reason: Literal['stop', 'length'] | None = None


class OpenAIChatCompletionChunkModel(_PublicResponseModel):
    id: Annotated[str, StringConstraints(strict=True, pattern=r'^chatcmpl_[0-9a-f]{32}$')]
    object: Literal['chat.completion.chunk'] = 'chat.completion.chunk'
    created: Annotated[int, Field(strict=True, ge=0)]
    model: SafeModelCode
    choices: list[OpenAIChatCompletionChunkChoiceModel] = Field(min_length=1)


class OpenAIEmbeddingRequestModel(_StrictRequestModel):
    model: SafeModelCode
    input: str | list[EmbeddingInput] = Field(min_length=1)
    encoding_format: Literal['float'] | None = None
    dimensions: Annotated[int, Field(strict=True, ge=1, le=EMBEDDING_MAX_DIMENSIONS)] | None = None
    user: SafeCallerId | None = None

    @field_validator('input', mode='before')
    @classmethod
    def normalize_input(cls, value: object) -> object:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            if not 1 <= len(value) <= EMBEDDING_MAX_INPUTS:
                raise ValueError('input must contain between one and 256 values')
            return value
        raise ValueError('input must be a string or a list of strings')

    @model_validator(mode='after')
    def validate_input_text(self) -> 'OpenAIEmbeddingRequestModel':
        if isinstance(self.input, list):
            for item in self.input:
                if not item:
                    raise ValueError('input items must be non-empty strings')
        return self


class OpenAIEmbeddingDataModel(_PublicResponseModel):
    object: Literal['embedding'] = 'embedding'
    index: Annotated[int, Field(strict=True, ge=0)]
    embedding: list[float]

    @field_validator('embedding')
    @classmethod
    def validate_finite_embedding(cls, value: list[float]) -> list[float]:
        if not value or any(not isinstance(item, float) or not isfinite(item) for item in value):
            raise ValueError('embedding must be a non-empty list of finite floats')
        return value


class OpenAIEmbeddingUsageModel(_PublicResponseModel):
    prompt_tokens: Annotated[int, Field(strict=True, ge=0)]
    total_tokens: Annotated[int, Field(strict=True, ge=0)]


class OpenAIEmbeddingResponseModel(_PublicResponseModel):
    object: Literal['list'] = 'list'
    data: list[OpenAIEmbeddingDataModel] = Field(min_length=1)
    model: SafeModelCode
    usage: OpenAIEmbeddingUsageModel


class RerankDocumentModel(_StrictRequestModel):
    text: RerankDocumentText


class OpenAIRerankRequestModel(_StrictRequestModel):
    model: SafeModelCode
    query: RerankQuery
    documents: list[RerankDocumentText | RerankDocumentModel] = Field(min_length=1, max_length=RERANK_MAX_DOCUMENTS)
    top_n: Annotated[int, Field(strict=True, ge=1, le=RERANK_MAX_DOCUMENTS)] | None = None
    return_documents: bool | None = None

    @model_validator(mode='after')
    def validate_top_n(self) -> 'OpenAIRerankRequestModel':
        if self.top_n is not None and self.top_n > len(self.documents):
            raise ValueError('top_n must not exceed the document count')
        return self


class RerankResultDocumentModel(_PublicResponseModel):
    text: str


class RerankResultModel(_PublicResponseModel):
    index: Annotated[int, Field(strict=True, ge=0)]
    relevance_score: float
    document: RerankResultDocumentModel | None = None

    @field_validator('relevance_score')
    @classmethod
    def validate_finite_score(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError('relevance_score must be finite')
        return value


class RerankMetaModel(_PublicResponseModel):
    billed_units: dict[str, int] = Field(default_factory=dict)
    tokens: dict[str, int] = Field(default_factory=dict)


class OpenAIRerankResponseModel(_PublicResponseModel):
    id: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=255)]
    results: list[RerankResultModel] = Field(min_length=1)
    meta: RerankMetaModel
