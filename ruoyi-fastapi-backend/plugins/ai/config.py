from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LiteLLMSettings(BaseSettings):
    """AI 插件访问 LiteLLM Runtime 的进程级配置。"""

    base_url: str = Field('http://127.0.0.1:14000', validation_alias='LITELLM_BASE_URL')
    runtime_key: SecretStr = Field(SecretStr(''), validation_alias='LITELLM_RUNTIME_KEY')
    connect_timeout_seconds: float = Field(5, gt=0, validation_alias='LITELLM_CONNECT_TIMEOUT_SECONDS')
    request_timeout_seconds: float = Field(600, gt=0, validation_alias='LITELLM_REQUEST_TIMEOUT_SECONDS')
    verify_ssl: bool = Field(True, validation_alias='LITELLM_VERIFY_SSL')

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
