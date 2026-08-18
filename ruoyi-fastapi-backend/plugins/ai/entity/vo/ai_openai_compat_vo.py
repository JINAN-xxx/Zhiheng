import re
import unicodedata
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, model_validator

_FORBIDDEN_SINGLE_LINE_CATEGORIES = frozenset({'Cc', 'Zl', 'Zp'})
_MODEL_CODE_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
MAX_STOP_SEQUENCES = 4


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
        if self.stream:
            raise ValueError('streaming is not available in this release')
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
