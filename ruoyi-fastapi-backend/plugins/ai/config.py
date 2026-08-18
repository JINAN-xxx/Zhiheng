from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MINIMUM_API_KEY_PEPPER_LENGTH = 32


class LiteLLMSettings(BaseSettings):
    """AI 插件访问 LiteLLM Runtime 的进程级配置。"""

    base_url: str = Field('http://127.0.0.1:14000', validation_alias='LITELLM_BASE_URL')
    runtime_key: SecretStr = Field(SecretStr(''), validation_alias='LITELLM_RUNTIME_KEY')
    management_key: SecretStr = Field(SecretStr(''), validation_alias='LITELLM_MANAGEMENT_KEY')
    connect_timeout_seconds: float = Field(5, gt=0, validation_alias='LITELLM_CONNECT_TIMEOUT_SECONDS')
    request_timeout_seconds: float = Field(600, gt=0, validation_alias='LITELLM_REQUEST_TIMEOUT_SECONDS')
    verify_ssl: bool = Field(True, validation_alias='LITELLM_VERIFY_SSL')
    public_api_enabled: bool = Field(False, validation_alias='AI_PUBLIC_API_ENABLED')
    api_key_pepper_current_version: str = Field('', validation_alias='AI_API_KEY_PEPPER_CURRENT_VERSION')
    api_key_pepper_v1: SecretStr = Field(SecretStr(''), validation_alias='AI_API_KEY_PEPPER_V1')
    api_auth_cache_ttl_seconds: int = Field(15, ge=0, le=15, validation_alias='AI_API_AUTH_CACHE_TTL_SECONDS')
    api_max_request_bytes: int = Field(
        1_048_576,
        ge=1,
        le=10_485_760,
        validation_alias='AI_API_MAX_REQUEST_BYTES',
    )

    model_config = SettingsConfigDict(case_sensitive=False, extra='ignore')

    @field_validator('base_url')
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        """基础地址仅接受包含主机名的 HTTP(S) URL。"""
        normalized = value.strip().rstrip('/')
        parsed = urlparse(normalized)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
            raise ValueError('LiteLLM 基础地址必须是包含主机名的 HTTP(S) URL')
        return normalized

    @property
    def configured(self) -> bool:
        """返回 Runtime Key 是否已配置。"""
        return bool(self.runtime_key.get_secret_value().strip())

    @property
    def management_configured(self) -> bool:
        """返回 Management Key 是否已配置。"""
        return bool(self.management_key.get_secret_value().strip())

    @property
    def developer_api_configured(self) -> bool:
        """返回当前Developer API Key摘要版本是否安全可用。"""
        pepper = self.api_key_pepper_v1.get_secret_value().strip()
        return self.api_key_pepper_current_version == 'v1' and len(pepper) >= MINIMUM_API_KEY_PEPPER_LENGTH
