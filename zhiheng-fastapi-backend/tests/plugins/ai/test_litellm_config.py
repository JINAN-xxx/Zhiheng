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
    'AI_PUBLIC_API_ENABLED',
    'AI_API_KEY_PEPPER_CURRENT_VERSION',
    'AI_API_KEY_PEPPER_V1',
    'AI_API_AUTH_CACHE_TTL_SECONDS',
    'AI_API_MAX_REQUEST_BYTES',
    'AI_EMBEDDING_MAX_INPUTS',
    'AI_EMBEDDING_MAX_INPUT_BYTES',
    'AI_RERANK_MAX_DOCUMENTS',
    'AI_RERANK_MAX_QUERY_BYTES',
    'AI_RERANK_MAX_DOCUMENT_BYTES',
    'AI_RUNTIME_MAX_JSON_RESPONSE_BYTES',
)
VALID_PEPPER = 'developer-api-pepper-value-at-least-32-characters'
CUSTOM_API_AUTH_CACHE_TTL_SECONDS = 10
CUSTOM_API_MAX_REQUEST_BYTES = 2_097_152


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
    assert settings.public_api_enabled is False
    assert settings.developer_api_configured is False


def test_developer_api_settings_read_versioned_pepper_without_exposing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_litellm_environment(monkeypatch)
    monkeypatch.setenv('AI_PUBLIC_API_ENABLED', 'true')
    monkeypatch.setenv('AI_API_KEY_PEPPER_CURRENT_VERSION', 'v1')
    monkeypatch.setenv('AI_API_KEY_PEPPER_V1', VALID_PEPPER)
    monkeypatch.setenv('AI_API_AUTH_CACHE_TTL_SECONDS', '10')
    monkeypatch.setenv('AI_API_MAX_REQUEST_BYTES', '2097152')

    settings = LiteLLMSettings()

    assert settings.public_api_enabled is True
    assert settings.api_key_pepper_current_version == 'v1'
    assert settings.api_key_pepper_v1.get_secret_value() == VALID_PEPPER
    assert settings.api_auth_cache_ttl_seconds == CUSTOM_API_AUTH_CACHE_TTL_SECONDS
    assert settings.api_max_request_bytes == CUSTOM_API_MAX_REQUEST_BYTES
    assert settings.developer_api_configured is True
    assert VALID_PEPPER not in repr(settings)


@pytest.mark.parametrize(
    ('version', 'pepper'),
    (
        ('', VALID_PEPPER),
        ('v2', VALID_PEPPER),
        ('v1', ''),
        ('v1', 'short-pepper'),
        ('v1', ' ' * 40),
    ),
)
def test_developer_api_settings_fail_closed_for_unknown_or_weak_pepper(version: str, pepper: str) -> None:
    settings = LiteLLMSettings(
        AI_API_KEY_PEPPER_CURRENT_VERSION=version,
        AI_API_KEY_PEPPER_V1=pepper,
    )

    assert settings.developer_api_configured is False


@pytest.mark.parametrize(
    ('field', 'value'),
    (
        ('AI_API_AUTH_CACHE_TTL_SECONDS', -1),
        ('AI_API_AUTH_CACHE_TTL_SECONDS', 16),
        ('AI_API_MAX_REQUEST_BYTES', 0),
        ('AI_API_MAX_REQUEST_BYTES', 10_485_761),
        ('AI_EMBEDDING_MAX_INPUTS', 0),
        ('AI_EMBEDDING_MAX_INPUTS', 2048),
        ('AI_EMBEDDING_MAX_INPUT_BYTES', 0),
        ('AI_EMBEDDING_MAX_INPUT_BYTES', 2_097_152),
        ('AI_RERANK_MAX_DOCUMENTS', 0),
        ('AI_RERANK_MAX_QUERY_BYTES', 0),
        ('AI_RERANK_MAX_DOCUMENT_BYTES', 2_097_152),
        ('AI_RUNTIME_MAX_JSON_RESPONSE_BYTES', 1023),
        ('AI_RUNTIME_MAX_JSON_RESPONSE_BYTES', 268_435_457),
    ),
)
def test_developer_api_settings_reject_unsafe_numeric_bounds(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        LiteLLMSettings(**{field: value})


def test_gateway_settings_read_environment_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Embedding/Rerank 网关配置从进程环境读取并采用文档化默认值。"""
    clear_litellm_environment(monkeypatch)

    settings = LiteLLMSettings()

    assert settings.embedding_max_inputs == 256
    assert settings.embedding_max_input_bytes == 32768
    assert settings.rerank_max_documents == 256
    assert settings.rerank_max_query_bytes == 8192
    assert settings.rerank_max_document_bytes == 32768
    assert settings.runtime_max_json_response_bytes == 33_554_432


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
