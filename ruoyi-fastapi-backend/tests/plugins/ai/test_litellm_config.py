import pytest
from pydantic import ValidationError

from plugins.ai.config import LiteLLMSettings
from plugins.ai.infrastructure.litellm_errors import (
    LiteLLMAuthenticationError,
    LiteLLMError,
    LiteLLMMisconfiguredError,
    LiteLLMTimeoutError,
    LiteLLMUnavailableError,
    LiteLLMUpstreamError,
)

DEFAULT_CONNECT_TIMEOUT_SECONDS = 5
DEFAULT_REQUEST_TIMEOUT_SECONDS = 600
CUSTOM_CONNECT_TIMEOUT_SECONDS = 2.5
CUSTOM_REQUEST_TIMEOUT_SECONDS = 30
LITELLM_ENVIRONMENT_KEYS = (
    'LITELLM_BASE_URL',
    'LITELLM_RUNTIME_KEY',
    'LITELLM_MANAGEMENT_KEY',
    'LITELLM_CONNECT_TIMEOUT_SECONDS',
    'LITELLM_REQUEST_TIMEOUT_SECONDS',
    'LITELLM_VERIFY_SSL',
)


def clear_litellm_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """清除 LiteLLM 相关环境变量，隔离配置测试。"""
    for key in LITELLM_ENVIRONMENT_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_litellm_settings_use_safe_local_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """未配置环境变量时使用本地地址和空运行时密钥。"""
    clear_litellm_environment(monkeypatch)

    settings = LiteLLMSettings()

    assert settings.base_url == 'http://127.0.0.1:14000'
    assert settings.runtime_key.get_secret_value() == ''
    assert settings.management_key.get_secret_value() == ''
    assert settings.configured is False
    assert settings.management_configured is False
    assert settings.connect_timeout_seconds == DEFAULT_CONNECT_TIMEOUT_SECONDS
    assert settings.request_timeout_seconds == DEFAULT_REQUEST_TIMEOUT_SECONDS
    assert settings.verify_ssl is True


def test_litellm_settings_read_environment_without_exposing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """配置从进程环境读取，SecretStr 的展示不包含真实密钥。"""
    clear_litellm_environment(monkeypatch)
    monkeypatch.setenv('LITELLM_BASE_URL', 'https://proxy.example.com/')
    monkeypatch.setenv('LITELLM_RUNTIME_KEY', 'runtime-secret')
    monkeypatch.setenv('LITELLM_MANAGEMENT_KEY', 'management-secret')
    monkeypatch.setenv('LITELLM_CONNECT_TIMEOUT_SECONDS', '2.5')
    monkeypatch.setenv('LITELLM_REQUEST_TIMEOUT_SECONDS', '30')
    monkeypatch.setenv('LITELLM_VERIFY_SSL', 'false')

    settings = LiteLLMSettings()

    assert settings.base_url == 'https://proxy.example.com'
    assert settings.runtime_key.get_secret_value() == 'runtime-secret'
    assert settings.management_key.get_secret_value() == 'management-secret'
    assert settings.configured is True
    assert settings.management_configured is True
    assert settings.connect_timeout_seconds == CUSTOM_CONNECT_TIMEOUT_SECONDS
    assert settings.request_timeout_seconds == CUSTOM_REQUEST_TIMEOUT_SECONDS
    assert settings.verify_ssl is False
    assert 'runtime-secret' not in repr(settings)
    assert 'management-secret' not in repr(settings)


def test_litellm_management_key_supports_validation_alias() -> None:
    settings = LiteLLMSettings(LITELLM_MANAGEMENT_KEY='management-secret')

    assert settings.management_key.get_secret_value() == 'management-secret'
    assert settings.management_configured is True
    assert 'management-secret' not in repr(settings)


def test_litellm_blank_management_key_is_not_configured() -> None:
    settings = LiteLLMSettings(LITELLM_MANAGEMENT_KEY='   ')

    assert settings.management_configured is False


@pytest.mark.parametrize(
    'url_value',
    ('localhost:14000', 'ftp://proxy.example.com', 'http:///missing-host'),
)
def test_litellm_settings_reject_invalid_base_url(url_value: str) -> None:
    """基础地址必须是包含主机名的 HTTP(S) URL。"""
    with pytest.raises(ValidationError):
        LiteLLMSettings(LITELLM_BASE_URL=url_value)


@pytest.mark.parametrize(
    'field',
    ('LITELLM_CONNECT_TIMEOUT_SECONDS', 'LITELLM_REQUEST_TIMEOUT_SECONDS'),
)
def test_litellm_settings_reject_non_positive_timeouts(field: str) -> None:
    """建连和请求超时必须大于零。"""
    with pytest.raises(ValidationError):
        LiteLLMSettings(**{field: 0})


@pytest.mark.parametrize(
    ('error_type', 'status_code', 'code'),
    (
        (LiteLLMMisconfiguredError, 503, 'LITELLM_MISCONFIGURED'),
        (LiteLLMUnavailableError, 503, 'LITELLM_UNAVAILABLE'),
        (LiteLLMTimeoutError, 504, 'LITELLM_TIMEOUT'),
        (LiteLLMAuthenticationError, 502, 'LITELLM_AUTHENTICATION_FAILED'),
        (LiteLLMUpstreamError, 502, 'LITELLM_UPSTREAM_ERROR'),
    ),
)
def test_litellm_errors_have_stable_public_contract(
    error_type: type[LiteLLMError],
    status_code: int,
    code: str,
) -> None:
    """内部异常向控制器提供稳定且脱敏的错误契约。"""
    error = error_type()

    assert error.status_code == status_code
    assert error.code == code
    assert error.message
    assert 'secret' not in error.message.lower()
