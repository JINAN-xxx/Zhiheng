import pytest

from plugins.ai.infrastructure.litellm_management_adapter import LiteLLMManagementAdapter


@pytest.mark.parametrize(
    ('error', 'reason', 'message_part'),
    [
        ('Timeout exceeded', 'timeout', '超时'),
        ({'type': 'APIConnectionError', 'message': 'Connection error'}, 'network', '网络'),
        ('Error code: 401 - AuthenticationError: invalid api key sk-private', 'authentication', 'API Key'),
        ({'status_code': 403, 'message': 'Forbidden'}, 'permission', '权限'),
        ('Error code: 402 - Insufficient balance', 'quota', '余额'),
        ('RateLimitError: 429 Too Many Requests', 'rate_limit', '频率'),
        ('Error code: 404 - model does not exist', 'model_not_found', '模型名称'),
        ('BadRequestError: unsupported parameter', 'invalid_request', '模型类型'),
        ('Error code: 503 - Service unavailable', 'upstream_unavailable', '暂时不可用'),
        ('private-key-and-host-no-known-error', 'unknown', '连接测试失败'),
    ],
)
def test_connection_failure_returns_fixed_reason_without_upstream_content(
    error: object, reason: str, message_part: str
) -> None:
    result = LiteLLMManagementAdapter.normalize_connection_test_result(
        {
            'status': 'error',
            'result': {'error': error, 'api_key': 'credential-secret', 'api_base': 'https://private-host'},
        },
        latency_ms=60000,
    )
    assert result.success is False
    assert result.failure_reason == reason
    assert message_part in result.message
    serialized = result.model_dump_json()
    for secret in ('sk-private', 'credential-secret', 'private-host', 'private-key'):
        assert secret not in serialized


def test_success_does_not_infer_failures_from_unrelated_model_fields() -> None:
    result = LiteLLMManagementAdapter.normalize_connection_test_result(
        {'status': 'success', 'result': {'model': 'timeout-401-model'}},
        latency_ms=10,
    )
    assert result.success
    assert result.failure_reason is None


def test_failure_classifier_only_uses_error_fields_and_bounds_untrusted_text() -> None:
    result = LiteLLMManagementAdapter.normalize_connection_test_result(
        {'status': 'error', 'result': {'model': '401-timeout', 'error': 'x' * 10000 + 'Timeout exceeded'}},
        latency_ms=10,
    )
    assert result.failure_reason == 'unknown'
