"""Explicit instance settings; legacy environment files are never discovered."""

from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import Fernet
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='ZHIHENG_', env_file=None, extra='forbid')

    files_s3_endpoint: str = 'http://file-objects:9000'
    files_s3_bucket: str = Field(
        default='zhiheng-files', min_length=3, max_length=63, pattern=r'^[a-z0-9][a-z0-9-]+[a-z0-9]$'
    )
    files_s3_access_key: SecretStr = SecretStr('')
    files_s3_secret_key: SecretStr = SecretStr('')
    files_s3_region: str = Field(default='us-east-1', min_length=1, max_length=100)
    files_s3_timeout_seconds: float = Field(default=30, ge=1, le=120)
    sandbox_url: str = ''
    sandbox_api_key: SecretStr = SecretStr('')
    sandbox_image: str = 'zhiheng-workspace-runtime:0.2.0'
    sandbox_cpu: str = '1'
    sandbox_memory_mb: int = Field(default=1024, ge=256, le=8192)
    sandbox_run_seconds: int = Field(default=600, ge=60, le=3600)
    sandbox_ready_seconds: int = Field(default=60, ge=5, le=180)
    sandbox_pip_index_url: str = 'https://pypi.org/simple'
    bocha_api_key: SecretStr = SecretStr('')
    web_search_timeout_seconds: float = Field(default=15, ge=1, le=30)
    milvus_uri: str | None = None
    milvus_token: SecretStr = SecretStr('')
    milvus_database: str = 'default'
    graph_working_root: Path = Path('/tmp/zhiheng-graph')
    graph_database_url: SecretStr | None = None
    graph_runner_limit: int = Field(default=2, ge=1, le=4)
    database_url: SecretStr
    redis_url: SecretStr
    credential_key: SecretStr
    cookie_secure: bool = True
    session_ttl_seconds: int = Field(default=86400, ge=300, le=604800)
    allowed_origins: tuple[str, ...] = ()
    trusted_login_proxy_ips: frozenset[str] = frozenset()
    resource_rpm_limit: int = Field(default=120, ge=1, le=120)
    knowledge_rerank_batch_tokens: int = Field(default=48000, ge=1, le=48000)
    chat_context_input_tokens: int | None = Field(default=None, ge=256, le=2097152)
    chat_summary_trigger_tokens: int | None = Field(default=None, ge=256, le=2097152)
    chat_summary_keep_tokens: int | None = Field(default=None, ge=64, le=2097151)
    chat_summary_input_tokens: int | None = Field(default=None, ge=256, le=2097152)
    chat_summary_output_tokens: int | None = Field(default=None, ge=64, le=4096)
    provider_timeout_seconds: float = Field(default=60, ge=1, le=600)
    provider_connect_timeout_seconds: float = Field(default=10, ge=0.1, le=60)
    provider_first_response_timeout_seconds: float | None = Field(default=None, ge=0.1, le=600)
    provider_idle_timeout_seconds: float | None = Field(default=None, ge=0.1, le=600)
    provider_total_timeout_seconds: float = Field(default=600, ge=1, le=3600)
    knowledge_query_timeout_seconds: float = Field(default=60, ge=1, le=600)
    knowledge_rerank_timeout_seconds: float = Field(default=20, ge=0.1, le=600)
    knowledge_embedding_timeout_seconds: float = Field(default=30, ge=0.1, le=600)
    lease_seconds: int = Field(default=30, ge=3, le=300)
    worker_queue: str = Field(default='zhiheng:core:queue', min_length=1, max_length=100)
    worker_generation_slots: int = Field(default=15, ge=1)
    worker_workspace_slots: int = Field(default=2, ge=1)

    @property
    def model_first_response_seconds(self) -> float:
        return self.provider_first_response_timeout_seconds or self.provider_timeout_seconds

    @property
    def model_idle_seconds(self) -> float:
        return self.provider_idle_timeout_seconds or self.provider_timeout_seconds

    @property
    def model_transport_seconds(self) -> float:
        # The native executor owns stage deadlines; the socket watchdog is a backstop.
        return max(self.model_first_response_seconds, self.model_idle_seconds) + 5

    @field_validator('trusted_login_proxy_ips')
    @classmethod
    def validate_trusted_login_proxy_ips(cls, proxies: frozenset[str]) -> frozenset[str]:
        addresses = [ip_address(value) for value in proxies]
        if any(address.is_unspecified or address.is_multicast or '%' in str(address) for address in addresses):
            raise ValueError('Specify individual trusted proxy IP addresses')
        return frozenset(str(address) for address in addresses)

    @field_validator('sandbox_url')
    @classmethod
    def validate_sandbox_url(cls, value: str) -> str:
        if value:
            address = urlsplit(value)
            if (
                address.scheme not in {'http', 'https'}
                or not address.hostname
                or address.username
                or address.password
                or address.path not in {'', '/'}
                or address.query
                or address.fragment
            ):
                raise ValueError('sandbox_url must be an HTTP(S) origin without credentials or a path')
        return value.rstrip('/')

    @field_validator('sandbox_pip_index_url')
    @classmethod
    def validate_sandbox_pip_index_url(cls, value: str) -> str:
        address = urlsplit(value)
        if (
            address.scheme != 'https'
            or not address.hostname
            or address.username
            or address.password
            or address.query
            or address.fragment
        ):
            raise ValueError('sandbox_pip_index_url must be a trusted HTTPS index without embedded credentials')
        return value

    @model_validator(mode='after')
    def valid_file_storage(self) -> 'Settings':
        if bool(self.files_s3_access_key.get_secret_value()) != bool(self.files_s3_secret_key.get_secret_value()):
            raise ValueError('S3 file storage requires a complete application credential pair')
        if self.files_s3_endpoint:
            address = urlsplit(self.files_s3_endpoint)
            if (
                address.scheme not in {'http', 'https'}
                or not address.hostname
                or address.username is not None
                or address.password is not None
                or address.path not in {'', '/'}
                or address.query
                or address.fragment
            ):
                raise ValueError('S3 endpoint must be an HTTP(S) origin without credentials or a path')
        return self

    @model_validator(mode='after')
    def valid_chat_memory(self) -> 'Settings':
        if (
            self.chat_summary_trigger_tokens is not None
            and self.chat_summary_keep_tokens is not None
            and self.chat_summary_trigger_tokens <= self.chat_summary_keep_tokens
        ):
            raise ValueError('Chat summary trigger tokens must exceed retained tokens')
        return self

    @field_validator(
        'chat_summary_trigger_tokens',
        'chat_summary_keep_tokens',
        'chat_summary_input_tokens',
        'chat_summary_output_tokens',
        mode='before',
    )
    @classmethod
    def automatic_chat_summary(cls, value: object) -> object:
        if isinstance(value, str) and value.strip().lower() in {'', 'auto'}:
            return None
        return value

    @field_validator('graph_database_url', mode='before')
    @classmethod
    def empty_graph_database_url(cls, value: object) -> object:
        return None if value == '' else value

    @field_validator('graph_database_url')
    @classmethod
    def validate_graph_database_url(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and urlsplit(value.get_secret_value()).scheme != 'postgresql':
            raise ValueError('graph_database_url must use postgresql')
        return value

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

    @property
    def worker_heartbeats_key(self) -> str:
        return f'{self.worker_queue}:heartbeats'

    @property
    def worker_short_queue(self) -> str:
        return f'{self.worker_queue}:short'

    @property
    def worker_knowledge_queue(self) -> str:
        return f'{self.worker_queue}:knowledge'

    @property
    def worker_retrieval_queue(self) -> str:
        return f'{self.worker_queue}:retrieval'

    @property
    def worker_workspace_queue(self) -> str:
        return f'{self.worker_queue}:workspace'

    @property
    def worker_queues(self) -> tuple[str, ...]:
        return (
            self.worker_queue,
            self.worker_short_queue,
            self.worker_knowledge_queue,
            self.worker_retrieval_queue,
            self.worker_workspace_queue,
        )

    @property
    def worker_arq_health_keys(self) -> tuple[str, ...]:
        return tuple(f'{queue}:arq-health' for queue in self.worker_queues)
