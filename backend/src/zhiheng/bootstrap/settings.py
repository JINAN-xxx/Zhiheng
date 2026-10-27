"""Explicit instance settings; legacy environment files are never discovered."""

from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='ZHIHENG_', env_file=None, extra='forbid')

    files_root: Path = Path('/var/lib/zhiheng/files')
    milvus_uri: str | None = None
    milvus_token: SecretStr = SecretStr('')
    milvus_database: str = 'default'
    database_url: SecretStr
    redis_url: SecretStr
    credential_key: SecretStr
    cookie_secure: bool = True
    session_ttl_seconds: int = Field(default=86400, ge=300, le=604800)
    allowed_origins: tuple[str, ...] = ()
    token_limit: int = Field(default=100000, ge=1)
    provider_timeout_seconds: float = Field(default=60, ge=1, le=600)
    lease_seconds: int = Field(default=30, ge=3, le=300)
    worker_queue: str = Field(default='zhiheng:core:queue', min_length=1, max_length=100)

    @field_validator('database_url')
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        if urlsplit(value.get_secret_value()).scheme != 'postgresql+asyncpg':
            raise ValueError('database_url must use postgresql+asyncpg')
        return value

    @field_validator('redis_url')
    @classmethod
    def validate_redis_url(cls, value: SecretStr) -> SecretStr:
        if urlsplit(value.get_secret_value()).scheme not in {'redis', 'rediss'}:
            raise ValueError('redis_url must use redis or rediss')
        return value

    @field_validator('credential_key')
    @classmethod
    def validate_credential_key(cls, value: SecretStr) -> SecretStr:
        try:
            Fernet(value.get_secret_value().encode())
        except (ValueError, TypeError) as exc:
            raise ValueError('credential_key must be a valid Fernet key') from exc
        return value

    @property
    def worker_health_key(self) -> str:
        return f'{self.worker_queue}:heartbeat'
