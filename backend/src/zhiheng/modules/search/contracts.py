"""Provider-neutral values for public web search; credentials never enter tool values."""

from datetime import datetime
from typing import Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from zhiheng.shared.errors import AppError

Freshness = Literal['noLimit', 'oneDay', 'oneWeek', 'oneMonth', 'oneYear']


class WebSearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')

    query: str = Field(min_length=1, max_length=1000)
    count: int = Field(default=5, ge=1, le=10, strict=True)
    freshness: Freshness = 'noLimit'

    @field_validator('query')
    @classmethod
    def valid_query(cls, value: str) -> str:
        value.encode('utf-8')
        if not value.strip() or '\x00' in value:
            raise ValueError('请输入有效的搜索关键词')
        return value.strip()


class WebSearchSource(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')

    id: str
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=4096)
    summary: str = Field(default='', max_length=2000)
    site_name: str = Field(default='', max_length=200)
    published_at: str | None = Field(default=None, max_length=100)

    @field_validator('url')
    @classmethod
    def valid_url(cls, value: str) -> str:
        address = urlsplit(value)
        if address.scheme not in {'http', 'https'} or not address.hostname or address.username or address.password:
            raise ValueError('搜索结果必须提供有效的公开网页链接')
        if any(character < ' ' for character in value):
            raise ValueError('网页链接不能包含控制字符')
        return value


class WebSearchResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')

    provider: Literal['bocha'] = 'bocha'
    query: str
    fetched_at: datetime
    results: tuple[WebSearchSource, ...] = Field(max_length=10)
    search_requests: Literal[1] = 1


class WebSearchError(AppError):
    def __init__(self, code: str, message: str, *, attempted: bool = False, uncertain: bool = False) -> None:
        super().__init__(code, message, 502 if attempted else 503)
        self.attempted, self.uncertain = attempted, uncertain


class WebSearch(Protocol):
    @property
    def available(self) -> bool: ...

    async def search(self, request: WebSearchRequest) -> WebSearchResult: ...
