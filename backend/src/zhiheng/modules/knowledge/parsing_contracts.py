"""Document parsing policy and safe public status contracts."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from zhiheng.modules.knowledge.contracts import ParseResult
from zhiheng.shared.errors import AppError

ParseTier = Literal['flash', 'basic', 'standard', 'advanced']
PARSE_TERMINAL = frozenset({'completed', 'failed', 'cancelled', 'submission_unknown'})


def endpoint(value: str | None) -> str | None:
    if not value or not value.strip():
        return None
    value = value.strip().rstrip('/')
    parts = urlsplit(value)
    if (
        parts.scheme not in {'http', 'https'}
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError('请填写不包含密钥或查询参数的 HTTP(S) 服务地址')
    _ = parts.port
    return value


class ParsingSettingsPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(strict=True, ge=1)
    local_url: str | None = Field(default=None, max_length=2048)
    local_api_key: SecretStr | None = Field(default=None, max_length=4096)
    clear_local_key: bool = False
    cloud_enabled: bool = False
    cloud_url: str | None = Field(default=None, max_length=2048)
    cloud_api_key: SecretStr | None = Field(default=None, max_length=4096)
    clear_cloud_key: bool = False
    tier: ParseTier = 'standard'
    timeout_seconds: int = Field(default=900, strict=True, ge=30, le=7200)
    queue_timeout_seconds: int = Field(default=120, strict=True, ge=5, le=900)
    _urls = field_validator('local_url', 'cloud_url')(endpoint)


class ParsingSettingsInfo(BaseModel):
    local_url: str | None
    has_local_key: bool
    cloud_enabled: bool
    cloud_url: str | None
    has_cloud_key: bool
    tier: ParseTier
    timeout_seconds: int
    queue_timeout_seconds: int
    version: int


class ParsingServiceStatus(BaseModel):
    configured: bool
    available: bool
    version: str | None = None
    device: str | None = None


class ParsingStatusInfo(BaseModel):
    local: ParsingServiceStatus
    cloud: ParsingServiceStatus | None = None
    cloud_enabled: bool
    native_available: bool = True


@dataclass(frozen=True)
class ParsingClaim:
    id: UUID
    parent_id: UUID
    owner_id: UUID
    file_id: UUID
    source_sha256: str
    source_version: int
    source_acl_version: int
    config: dict[str, object]
    engine: str
    phase: str
    task_id: str | None
    upstream_file_id: str | None
    artifact_id: UUID | None
    token: UUID
    created_at: datetime
    deadline: datetime
    engine_version: str | None
    parser_version: str | None


class ParsingPending(AppError):
    def __init__(self) -> None:
        super().__init__('document_parsing_pending', '文档正在解析', 202)


class MinerUFailure(AppError):
    def __init__(self, code: str, *, recoverable: bool = False, unknown: bool = False) -> None:
        super().__init__(code, '文档解析服务暂时不可用，原文件已保留', 503)
        self.recoverable = recoverable
        self.unknown = unknown


class MinerUClientPort(Protocol):
    base: str

    async def health(self) -> ParsingServiceStatus: ...
    async def create(self, filename: str, size: int, sha256: str, tier: str) -> dict[str, object]: ...
    async def get(self, task_id: str) -> dict[str, object]: ...
    async def cancel(self, task_id: str) -> None: ...
    async def upload(self, target: dict[str, object], content: bytes) -> None: ...
    async def download(self, value: object) -> bytes: ...


class MinerUClientFactory(Protocol):
    def __call__(self, base_url: str, api_key: str = '') -> MinerUClientPort: ...


class DocumentSourceValidator(Protocol):
    async def __call__(self, content: bytes, *, filename: str) -> None: ...


class MinerUResultDecoder(Protocol):
    def __call__(self, content: bytes, *, engine: str, version: str, parser_version: str, tier: str) -> ParseResult: ...
