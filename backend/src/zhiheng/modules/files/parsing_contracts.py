"""Document parsing policy and safe public status contracts."""

from typing import Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from zhiheng.shared.errors import AppError

PARSER_VERSION = 'native-2'
ATTACHMENT_PARSER_VERSION = 'native-3-attachments'
MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_TEXT_BYTES = 2_000_000
MAX_IMAGE_URL_BYTES = 2_000_000


class ParseResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    text: str
    metadata: dict[str, str | int]
    parser_version: str = PARSER_VERSION
    image_data: str | None = None
    source_sha256: str | None = None


class ParsePort(Protocol):
    async def parse(self, content: bytes, *, filename: str) -> ParseResult: ...


ParseTier = Literal['flash', 'basic', 'standard', 'advanced']


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
