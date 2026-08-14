import importlib
import importlib.util
import json
import traceback
from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

MODULES = (
    'plugins.ai.entity.vo.ai_model_engine_vo',
    'plugins.ai.infrastructure.litellm_management_adapter',
)
SENSITIVE_KEYS = {
    'api_base',
    'api_key',
    'authorization',
    'client_secret',
    'credential_values',
    'password',
    'secret',
    'token',
    'vertex_credentials',
}
SECRET_VALUES = {
    'credential-value-secret',
    'nested-secret',
    'private-api-base',
    'private-api-key',
    'private-token',
}
BAD_GATEWAY_STATUS = 502


def load_contract() -> tuple[Any, Any]:
    missing = [module for module in MODULES if importlib.util.find_spec(module) is None]
    if missing:
        pytest.fail(f'Task2 模块尚未实现: {", ".join(missing)}')
    vo = importlib.import_module(MODULES[0])
    adapter_module = importlib.import_module(MODULES[1])
    return vo, adapter_module


@pytest.fixture
def real_provider_payload() -> dict[str, object]:
    return {
        'items': [
            {
                'provider': 'OpenAI',
                'provider_display_name': 'OpenAI',
                'litellm_provider': 'openai',
                'default_model_placeholder': 'gpt-4.1-mini',
                'credential_fields': [
                    {
                        'key': 'api_key',
                        'label': 'API Key',
                        'placeholder': None,
                        'tooltip': 'Provider API key',
                        'required': True,
                        'field_type': 'password',
                        'options': None,
                        'default_value': None,
                        'untrusted_field_extension': 'must-not-leak',
                    },
                    {
                        'key': 'organization',
                        'label': 'Organization',
                        'placeholder': 'org-123',
                        'tooltip': None,
                        'required': False,
                        'field_type': 'future-widget',
                        'options': ['org-123', 'org-456'],
                        'default_value': 'org-123',
                    },
                    {
                        'key': 'future_credential',
                        'label': 'Future credential',
                        'placeholder': None,
                        'tooltip': None,
                        'required': False,
                        'field_type': 'text',
                        'options': None,
                        'default_value': None,
                    },
                ],
                'logo': 'must-not-leak',
                'credential_values': {'api_key': 'private-api-key'},
            }
        ]
    }


@pytest.fixture
def deployment_payload() -> dict[str, object]:
    return {
        'data': [
            {
                'model_name': 'company-chat',
                'blocked': False,
                'litellm_params': {
                    'model': 'openai/gpt-4.1-mini',
                    'custom_llm_provider': 'openai',
                    'litellm_credential_name': 'openai-main',
                    'timeout': 30.5,
                    'stream_timeout': 45,
                    'max_retries': 2,
                    'rpm': 100,
                    'tpm': 20000,
                    'api_key': 'private-api-key',
                    'api_base': 'private-api-base',
                    'future_param': {'secret': 'nested-secret'},
                },
                'model_info': {
                    'id': 'deployment-1',
                    'status': 'healthy',
                    'latency_ms': 18.25,
                    'description': 'Primary deployment',
                    'created_at': '2026-08-11T10:00:00Z',
                    'updated_at': '2026-08-12T11:30:00+00:00',
                    'api_key': 'private-api-key',
                    'provider_credentials': {'token': 'private-token'},
                    'future_info': 'must-not-leak',
                },
                'unknown_top_level': {'authorization': 'private-token'},
            }
        ],
        'total_count': 1,
        'current_page': 1,
        'total_pages': 1,
        'size': 50,
        'unknown_page_extension': 'must-not-leak',
    }


def dump_alias(model: Any) -> dict[str, object]:
    return model.model_dump(mode='json', by_alias=True)


def assert_response_is_sanitized(value: object) -> None:
    serialized = json.dumps(value, ensure_ascii=True, sort_keys=True).lower()
    for key in SENSITIVE_KEYS:
        assert key not in serialized
    for secret in SECRET_VALUES:
        assert secret not in serialized


def assert_upstream_invalid(error: Exception, *forbidden: str) -> None:
    assert type(error).__name__ == 'LiteLLMManagementUpstreamInvalidError'
    assert error.status_code == BAD_GATEWAY_STATUS
    assert error.code == 'LITELLM_MANAGEMENT_UPSTREAM_INVALID'
    assert str(error) == 'LiteLLM 管理面返回了无效数据'
    assert error.__cause__ is None
    assert error.__context__ is None
    serialized = repr(
        (
            str(error),
            repr(error),
            vars(error),
            error.args,
            ''.join(traceback.format_exception(error)),
        )
    ).lower()
    for secret in forbidden:
        assert secret.lower() not in serialized

    current = error.__traceback__
    while current is not None:
        filename = current.tb_frame.f_code.co_filename.replace('\\', '/')
        if '/plugins/ai/' in filename and '/tests/' not in filename:
            for value in current.tb_frame.f_locals.values():
                local_repr = repr(value).lower()
                for secret in forbidden:
                    assert secret.lower() not in local_repr
        current = current.tb_next


def test_provider_fixture_is_normalized_to_exact_safe_platform_shape(real_provider_payload: dict[str, object]) -> None:
    _, adapter_module = load_contract()

    providers = adapter_module.LiteLLMManagementAdapter.normalize_provider_fields(real_provider_payload)

    assert [dump_alias(provider) for provider in providers] == [
        {
            'provider': 'OpenAI',
            'displayName': 'OpenAI',
            'litellmProvider': 'openai',
            'defaultModelPlaceholder': 'gpt-4.1-mini',
            'credentialFields': [
                {
                    'fieldKey': 'api_key',
                    'label': 'API Key',
                    'placeholder': None,
                    'tooltip': 'Provider API key',
                    'required': True,
                    'fieldType': 'password',
                    'options': None,
                    'defaultValue': None,
                },
                {
                    'fieldKey': 'organization',
                    'label': 'Organization',
                    'placeholder': 'org-123',
                    'tooltip': None,
                    'required': False,
                    'fieldType': 'text',
                    'options': ['org-123', 'org-456'],
                    'defaultValue': 'org-123',
                },
                {
                    'fieldKey': 'future_credential',
                    'label': 'Future credential',
                    'placeholder': None,
                    'tooltip': None,
                    'required': False,
                    'fieldType': 'text',
                    'options': None,
                    'defaultValue': None,
                },
            ],
        }
    ]
    serialized = repr([dump_alias(provider) for provider in providers])
    for secret in SECRET_VALUES:
        assert secret not in serialized


def test_real_oracle_provider_allows_and_normalizes_multiline_pem_placeholder() -> None:
    _, adapter_module = load_contract()
    pem_placeholder = '-----BEGIN RSA PRIVATE KEY-----\r\n...\r\n-----END RSA PRIVATE KEY-----'
    payload = {
        'items': [
            {
                'provider': 'Oracle',
                'provider_display_name': 'Oracle Cloud Infrastructure (OCI)',
                'litellm_provider': 'oci',
                'credential_fields': [
                    {
                        'key': 'oci_key',
                        'label': 'OCI Private Key (PEM)',
                        'placeholder': pem_placeholder,
                        'tooltip': 'Paste the full PEM-encoded private key.',
                        'required': True,
                        'field_type': 'textarea',
                        'options': None,
                        'default_value': None,
                    }
                ],
                'default_model_placeholder': 'oci/xai.grok-3',
            }
        ]
    }

    field = adapter_module.LiteLLMManagementAdapter.normalize_provider_fields(payload)[0].credential_fields[0]

    assert field.placeholder == ('-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----')


@pytest.mark.parametrize('forbidden', ('\x00', '\x85', '\u2028', '\u2029', '\r'))
def test_multiline_field_placeholder_still_rejects_unsafe_controls(forbidden: str) -> None:
    _, adapter_module = load_contract()
    payload = {
        'items': [
            {
                'provider': 'Oracle',
                'provider_display_name': 'Oracle',
                'litellm_provider': 'oci',
                'credential_fields': [
                    {
                        'key': 'oci_key',
                        'label': 'OCI Key',
                        'placeholder': f'BEGIN{forbidden}END',
                        'field_type': 'textarea',
                    }
                ],
            }
        ]
    }

    with pytest.raises(Exception) as raised:
        adapter_module.LiteLLMManagementAdapter.normalize_provider_fields(payload)

    assert_upstream_invalid(raised.value)


@pytest.mark.parametrize(
    'field_type',
    ('text', 'password', 'select', 'upload', 'textarea'),
)
def test_provider_preserves_only_known_field_types(field_type: str) -> None:
    _, adapter_module = load_contract()
    payload = {
        'items': [
            {
                'provider': 'P',
                'provider_display_name': 'Provider',
                'litellm_provider': 'provider',
                'credential_fields': [{'key': 'api_key', 'label': 'Key', 'required': False, 'field_type': field_type}],
            }
        ]
    }

    field = adapter_module.LiteLLMManagementAdapter.normalize_provider_fields(payload)[0].credential_fields[0]

    assert field.field_type == field_type


@pytest.mark.parametrize('bad_key', (None, '', '1api_key', 'api-key', 'api key', 'api\x00key', 'a' * 129))
def test_provider_rejects_missing_or_unsafe_field_key(bad_key: object) -> None:
    _, adapter_module = load_contract()
    payload = {
        'items': [
            {
                'provider': 'P',
                'provider_display_name': 'Provider',
                'litellm_provider': 'provider',
                'credential_fields': [{'key': bad_key, 'label': 'Key'}],
            }
        ]
    }

    with pytest.raises(Exception) as raised:
        adapter_module.LiteLLMManagementAdapter.normalize_provider_fields(payload)

    assert_upstream_invalid(raised.value)


@pytest.mark.parametrize(
    ('path', 'bad_value'),
    (
        ('provider', 'bad\x00provider'),
        ('provider_display_name', 'x' * 257),
        ('litellm_provider', 123),
        ('default_model_placeholder', 'bad\nmodel'),
    ),
)
def test_provider_rejects_control_characters_wrong_types_and_overlong_text(path: str, bad_value: object) -> None:
    _, adapter_module = load_contract()
    item: dict[str, object] = {
        'provider': 'P',
        'provider_display_name': 'Provider',
        'litellm_provider': 'provider',
        'default_model_placeholder': 'provider/model',
        'credential_fields': [],
    }
    item[path] = bad_value

    with pytest.raises(Exception) as raised:
        adapter_module.LiteLLMManagementAdapter.normalize_provider_fields({'items': [item]})

    assert_upstream_invalid(raised.value)


@pytest.mark.parametrize(
    ('field_name', 'value'),
    (
        ('options', [{'value': 'nested-secret'}]),
        ('options', [[['too-deep']]]),
        ('options', ['x' * 513]),
        ('default_value', {'secret': 'nested-secret'}),
        ('default_value', float('inf')),
    ),
)
def test_provider_rejects_unsafe_options_and_default_values(field_name: str, value: object) -> None:
    _, adapter_module = load_contract()
    field: dict[str, object] = {'key': 'api_key', 'label': 'Key'}
    field[field_name] = value
    payload = {
        'items': [
            {
                'provider': 'P',
                'provider_display_name': 'Provider',
                'litellm_provider': 'provider',
                'credential_fields': [field],
            }
        ]
    }

    with pytest.raises(Exception) as raised:
        adapter_module.LiteLLMManagementAdapter.normalize_provider_fields(payload)

    assert_upstream_invalid(raised.value, 'nested-secret')


@pytest.mark.parametrize('top_key', ('data', 'items', 'credentials'))
def test_credential_list_accepts_documented_and_real_litellm_top_level_shapes(top_key: str) -> None:
    _, adapter_module = load_contract()
    item = {
        'credential_name': 'openai-main',
        'provider': 'openai',
        'referenced_deployment_count': 2,
        'created_at': '2026-08-11T10:00:00Z',
        'updated_at': '2026-08-12T11:30:00+00:00',
        'credential_values': {'api_key': 'private-api-key'},
        'api_key': 'private-api-key',
        'unknown': 'must-not-leak',
    }

    credentials = adapter_module.LiteLLMManagementAdapter.normalize_credentials({top_key: [item]})

    assert [dump_alias(credential) for credential in credentials] == [
        {
            'credentialName': 'openai-main',
            'provider': 'openai',
            'referencedDeploymentCount': 2,
            'createdAt': '2026-08-11T10:00:00Z',
            'updatedAt': '2026-08-12T11:30:00Z',
        }
    ]
    assert_response_is_sanitized([dump_alias(credential) for credential in credentials])


def test_real_litellm_credential_shape_extracts_provider_only_from_safe_info() -> None:
    _, adapter_module = load_contract()
    payload = {
        'success': True,
        'credentials': [
            {
                'credential_name': 'bedrock-main',
                'credential_info': {
                    'custom_llm_provider': 'bedrock',
                    'description': 'must-not-leak',
                    'client_secret': 'nested-secret',
                },
                'credential_values': {'aws_secret_access_key': 'credential-value-secret'},
            }
        ],
        'unknown': {'token': 'private-token'},
    }

    credentials = adapter_module.LiteLLMManagementAdapter.normalize_credentials(payload)

    assert [dump_alias(credential) for credential in credentials] == [
        {
            'credentialName': 'bedrock-main',
            'provider': 'bedrock',
            'referencedDeploymentCount': 0,
            'createdAt': None,
            'updatedAt': None,
        }
    ]
    assert_response_is_sanitized([dump_alias(credential) for credential in credentials])


@pytest.mark.parametrize(
    'bad_name',
    ('', 'with space', 'with/slash', 'bad\x00name', 'a' * 129, 123),
)
def test_credential_response_rejects_unsafe_name(bad_name: object) -> None:
    _, adapter_module = load_contract()
    payload = {'items': [{'credential_name': bad_name, 'provider': 'openai'}]}

    with pytest.raises(Exception) as raised:
        adapter_module.LiteLLMManagementAdapter.normalize_credentials(payload)

    assert_upstream_invalid(raised.value)


@pytest.mark.parametrize(
    'date_value',
    ('not-a-date', '2026-08-11T10:00:00Z\nsecret', '2' * 65, 1_723_000_000),
)
def test_credential_response_rejects_invalid_date_boundaries(date_value: object) -> None:
    _, adapter_module = load_contract()
    payload = {
        'items': [
            {
                'credential_name': 'openai-main',
                'provider': 'openai',
                'created_at': date_value,
            }
        ]
    }

    with pytest.raises(Exception) as raised:
        adapter_module.LiteLLMManagementAdapter.normalize_credentials(payload)

    assert_upstream_invalid(raised.value)


def test_deployment_fixture_is_normalized_to_exact_safe_platform_shape(
    deployment_payload: dict[str, object],
) -> None:
    _, adapter_module = load_contract()

    deployments = adapter_module.LiteLLMManagementAdapter.normalize_deployments(deployment_payload)

    assert [dump_alias(deployment) for deployment in deployments] == [
        {
            'deploymentId': 'deployment-1',
            'modelAlias': 'company-chat',
            'provider': 'openai',
            'upstreamModel': 'openai/gpt-4.1-mini',
            'credentialName': 'openai-main',
            'status': 'healthy',
            'latencyMs': 18.25,
            'blocked': False,
            'createdAt': '2026-08-11T10:00:00Z',
            'updatedAt': '2026-08-12T11:30:00Z',
            'timeout': 30.5,
            'streamTimeout': 45.0,
            'maxRetries': 2,
            'rpm': 100,
            'tpm': 20000,
            'description': 'Primary deployment',
        }
    ]
    assert_response_is_sanitized([dump_alias(deployment) for deployment in deployments])


def test_deployment_accepts_items_shape_and_provider_from_model_info() -> None:
    _, adapter_module = load_contract()
    payload = {
        'items': [
            {
                'model_name': 'company-chat',
                'litellm_params': {'model': 'openai/gpt-4.1-mini'},
                'model_info': {'id': 'deployment-1', 'litellm_provider': 'openai'},
            }
        ]
    }

    deployment = adapter_module.LiteLLMManagementAdapter.normalize_deployments(payload)[0]

    assert deployment.provider == 'openai'
    assert deployment.status == 'unknown'
    assert deployment.blocked is False


@pytest.mark.parametrize(
    'payload',
    (
        {},
        {'data': None},
        {'items': {}},
        {'data': ['not-an-object']},
        {'data': [{1: 'non-string-key'}]},
        {
            'data': [
                {
                    'model_name': 'alias',
                    'litellm_params': {'model': 'provider/model'},
                    'model_info': {},
                }
            ]
        },
    ),
)
def test_malformed_deployment_responses_raise_fixed_upstream_invalid(payload: object) -> None:
    _, adapter_module = load_contract()

    with pytest.raises(Exception) as raised:
        adapter_module.LiteLLMManagementAdapter.normalize_deployments(payload)

    assert_upstream_invalid(raised.value)


def test_malformed_upstream_traceback_does_not_retain_raw_secret() -> None:
    _, adapter_module = load_contract()
    payload = {
        'credentials': [
            {
                'credential_name': 'bad name',
                'provider': 'openai',
                'credential_values': {'api_key': 'credential-value-secret'},
                'nested': {'token': 'private-token'},
            }
        ]
    }

    with pytest.raises(Exception) as raised:
        adapter_module.LiteLLMManagementAdapter.normalize_credentials(payload)

    assert_upstream_invalid(raised.value, 'credential-value-secret', 'private-token')


def test_request_models_are_strict_camel_case_and_forbid_escape_hatches() -> None:
    vo, _ = load_contract()

    credential = vo.CredentialCreateModel.model_validate(
        {'credentialName': 'openai-main', 'provider': 'OpenAI', 'values': {'api_key': 'private-api-key'}}
    )
    deployment = vo.DeploymentCreateModel.model_validate(
        {
            'modelAlias': 'company-chat',
            'upstreamModel': 'openai/gpt-4.1-mini',
            'provider': 'OpenAI',
            'credentialName': 'openai-main',
            'timeout': 30,
        }
    )

    assert credential.credential_name == 'openai-main'
    assert 'private-api-key' not in repr(credential)
    assert deployment.model_alias == 'company-chat'
    forbidden_inputs = (
        (vo.CredentialCreateModel, {'credential_name': 'openai-main', 'provider': 'OpenAI', 'values': {}}),
        (
            vo.CredentialCreateModel,
            {
                'credentialName': 'openai-main',
                'provider': 'OpenAI',
                'values': {},
                'credentialInfo': {'arbitrary': True},
            },
        ),
        (
            vo.DeploymentCreateModel,
            {
                'modelAlias': 'company-chat',
                'upstreamModel': 'openai/model',
                'provider': 'OpenAI',
                'credentialName': 'openai-main',
                'litellmParams': {'api_key': 'private-api-key'},
            },
        ),
        (
            vo.DeploymentCreateModel,
            {
                'modelAlias': 'company-chat',
                'upstreamModel': 'openai/model',
                'provider': 'OpenAI',
                'credentialName': 'openai-main',
                'modelInfo': {'id': 'caller-controlled'},
            },
        ),
    )
    for model_type, request in forbidden_inputs:
        with pytest.raises(ValidationError):
            model_type.model_validate(request)


def test_credential_request_validation_does_not_echo_secret_and_copies_input() -> None:
    vo, _ = load_contract()
    source = {'api_key': 'private-api-key'}
    request = vo.CredentialCreateModel.model_validate(
        {'credentialName': 'openai-main', 'provider': 'OpenAI', 'values': source}
    )
    source['api_key'] = 'changed-after-validation'

    assert request.values['api_key'].get_secret_value() == 'private-api-key'
    assert 'private-api-key' not in repr(request)
    assert 'private-api-key' not in request.model_dump_json()

    with pytest.raises(ValidationError) as raised:
        vo.CredentialCreateModel.model_validate(
            {'credentialName': 'openai-main', 'provider': 'OpenAI', 'values': {'api_key': object()}}
        )
    assert 'api_key' not in str(raised.value)
    assert 'private-api-key' not in str(raised.value)


def test_credential_validation_error_details_do_not_retain_original_values() -> None:
    vo, _ = load_contract()

    with pytest.raises(ValidationError) as raised:
        vo.CredentialCreateModel.model_validate(
            {
                'credentialName': 'openai-main',
                'provider': 'OpenAI',
                'values': {'api_key': 'private-api-key', 'bad-key': 'private-token'},
            }
        )

    serialized = repr(raised.value.errors()).lower()
    assert 'api_key' not in serialized
    assert 'bad-key' not in serialized
    assert 'private-api-key' not in serialized
    assert 'private-token' not in serialized


def test_credential_payload_uses_dynamic_provider_and_explicit_allowlist(
    real_provider_payload: dict[str, object],
) -> None:
    vo, adapter_module = load_contract()
    providers = adapter_module.LiteLLMManagementAdapter.normalize_provider_fields(real_provider_payload)
    request = vo.CredentialCreateModel.model_validate(
        {
            'credentialName': 'openai-main',
            'provider': 'OpenAI',
            'values': {'api_key': 'private-api-key', 'organization': 'org-123'},
        }
    )

    payload = adapter_module.LiteLLMManagementAdapter.build_credential_create_payload(request, providers)

    assert payload == {
        'credential_name': 'openai-main',
        'credential_info': {'custom_llm_provider': 'openai'},
        'credential_values': {'api_key': 'private-api-key', 'organization': 'org-123'},
    }


def test_credential_rotate_payload_cannot_extend_credential_info(
    real_provider_payload: dict[str, object],
) -> None:
    vo, adapter_module = load_contract()
    providers = adapter_module.LiteLLMManagementAdapter.normalize_provider_fields(real_provider_payload)
    request = vo.CredentialRotateModel.model_validate({'provider': 'OpenAI', 'values': {'api_key': 'private-api-key'}})

    payload = adapter_module.LiteLLMManagementAdapter.build_credential_rotate_payload('openai-main', request, providers)

    assert payload == {
        'credential_name': 'openai-main',
        'credential_info': {'custom_llm_provider': 'openai'},
        'credential_values': {'api_key': 'private-api-key'},
    }


def test_textarea_credential_payload_preserves_multiline_pem_secret_exactly() -> None:
    vo, adapter_module = load_contract()
    providers = adapter_module.LiteLLMManagementAdapter.normalize_provider_fields(
        {
            'items': [
                {
                    'provider': 'Oracle',
                    'provider_display_name': 'Oracle',
                    'litellm_provider': 'oci',
                    'credential_fields': [
                        {
                            'key': 'oci_key',
                            'label': 'OCI Key',
                            'required': True,
                            'field_type': 'textarea',
                        }
                    ],
                }
            ]
        }
    )
    secret = '-----BEGIN KEY-----\r\nprivate-body\r\n-----END KEY-----\r\n'
    request = vo.CredentialCreateModel.model_validate(
        {'credentialName': 'oci-main', 'provider': 'Oracle', 'values': {'oci_key': secret}}
    )

    payload = adapter_module.LiteLLMManagementAdapter.build_credential_create_payload(request, providers)

    assert payload['credential_values'] == {'oci_key': secret}


@pytest.mark.parametrize('field_type', ('textarea', 'upload'))
def test_multiline_credential_values_reject_unsafe_controls(field_type: str) -> None:
    vo, adapter_module = load_contract()
    providers = adapter_module.LiteLLMManagementAdapter.normalize_provider_fields(
        {
            'items': [
                {
                    'provider': 'Oracle',
                    'provider_display_name': 'Oracle',
                    'litellm_provider': 'oci',
                    'credential_fields': [
                        {
                            'key': 'oci_key',
                            'label': 'OCI Key',
                            'required': True,
                            'field_type': field_type,
                        }
                    ],
                }
            ]
        }
    )
    request = vo.CredentialCreateModel.model_validate(
        {
            'credentialName': 'oci-main',
            'provider': 'Oracle',
            'values': {'oci_key': 'BEGIN\nprivate\x00body\nEND\n'},
        }
    )

    with pytest.raises(ValueError) as raised:
        adapter_module.LiteLLMManagementAdapter.build_credential_create_payload(request, providers)

    assert str(raised.value) == '凭据字段校验失败'


@pytest.mark.parametrize(
    'values',
    (
        {},
        {'api_key': '   '},
        {'api_key': 'x' * 8193},
        {'api_key': 'private-api-key', 'organization': 'not-an-option'},
        {'api_key': 'private-api-key', 'future_credential': 'private-token'},
        {'api_key': 'private-api-key', 'caller_extension': 'private-token'},
    ),
)
def test_credential_payload_rejects_missing_invalid_or_unsupported_dynamic_values(
    real_provider_payload: dict[str, object],
    values: dict[str, str],
) -> None:
    vo, adapter_module = load_contract()
    if values.get('organization') == 'not-an-option':
        provider_item = real_provider_payload['items'][0]  # type: ignore[index]
        organization_field = provider_item['credential_fields'][1]  # type: ignore[index]
        organization_field['field_type'] = 'select'
    providers = adapter_module.LiteLLMManagementAdapter.normalize_provider_fields(real_provider_payload)
    request = vo.CredentialCreateModel.model_validate(
        {'credentialName': 'openai-main', 'provider': 'OpenAI', 'values': values}
    )

    with pytest.raises(ValueError) as raised:
        adapter_module.LiteLLMManagementAdapter.build_credential_create_payload(request, providers)

    serialized = str(raised.value)
    assert serialized == '凭据字段校验失败'
    for value in values.values():
        assert value not in serialized


def test_deployment_create_payload_is_exact_and_never_accepts_sensitive_extensions() -> None:
    vo, adapter_module = load_contract()
    request = vo.DeploymentCreateModel.model_validate(
        {
            'modelAlias': 'company-chat',
            'upstreamModel': 'openai/gpt-4.1-mini',
            'provider': 'openai',
            'credentialName': 'openai-main',
            'timeout': 30.5,
            'streamTimeout': 45,
            'maxRetries': 2,
            'rpm': 100,
            'tpm': 20000,
            'description': 'Primary deployment',
        }
    )

    payload = adapter_module.LiteLLMManagementAdapter.build_deployment_create_payload(request)

    assert payload == {
        'model_name': 'company-chat',
        'litellm_params': {
            'model': 'openai/gpt-4.1-mini',
            'custom_llm_provider': 'openai',
            'litellm_credential_name': 'openai-main',
            'timeout': 30.5,
            'stream_timeout': 45.0,
            'max_retries': 2,
            'rpm': 100,
            'tpm': 20000,
        },
        'model_info': {'description': 'Primary deployment'},
    }
    assert 'api_key' not in repr(payload)
    assert 'api_base' not in repr(payload)
    assert 'id' not in payload['model_info']

    with pytest.raises(ValidationError):
        vo.DeploymentCreateModel.model_validate(
            {
                'modelAlias': 'company-chat',
                'upstreamModel': 'openai/gpt-4.1-mini',
                'provider': 'openai',
                'credentialName': 'openai-main',
                'blocked': True,
            }
        )


def test_deployment_update_payload_preserves_supported_explicit_null_clears() -> None:
    vo, adapter_module = load_contract()
    request = vo.DeploymentUpdateModel.model_validate(
        {
            'timeout': None,
            'streamTimeout': None,
            'maxRetries': None,
            'rpm': None,
            'tpm': None,
            'description': None,
        }
    )

    payload = adapter_module.LiteLLMManagementAdapter.build_deployment_update_payload(request)

    assert payload == {
        'litellm_params': {
            'timeout': None,
            'stream_timeout': None,
            'max_retries': None,
            'rpm': None,
            'tpm': None,
        },
        'model_info': {'description': None},
    }


def test_deployment_update_payload_supports_blocked_only_on_patch() -> None:
    vo, adapter_module = load_contract()
    request = vo.DeploymentUpdateModel.model_validate({'blocked': True})

    assert adapter_module.LiteLLMManagementAdapter.build_deployment_update_payload(request) == {'blocked': True}


@pytest.mark.parametrize('field', ('modelAlias', 'upstreamModel', 'provider', 'credentialName', 'blocked'))
def test_deployment_update_rejects_null_for_identity_and_boolean_fields(field: str) -> None:
    vo, _ = load_contract()

    with pytest.raises(ValidationError):
        vo.DeploymentUpdateModel.model_validate({field: None})


@pytest.mark.parametrize(
    'payload',
    (
        {},
        {'timeout': 0},
        {'timeout': 600.1},
        {'streamTimeout': True},
        {'maxRetries': -1},
        {'maxRetries': 11},
        {'rpm': 0},
        {'tpm': 10_000_000_001},
        {'description': 'x' * 2001},
        {'modelAlias': '   '},
    ),
)
def test_deployment_update_enforces_nonempty_patch_and_numeric_boundaries(payload: dict[str, object]) -> None:
    vo, _ = load_contract()

    with pytest.raises(ValidationError):
        vo.DeploymentUpdateModel.model_validate(payload)


def test_connection_and_status_models_are_camel_case_whitelists() -> None:
    vo, _ = load_contract()
    connection = vo.ConnectionTestRequestModel.model_validate(
        {
            'upstreamModel': 'openai/gpt-4.1-mini',
            'provider': 'openai',
            'credentialName': 'openai-main',
            'values': {'api_key': 'private-api-key'},
        }
    )
    result = vo.ConnectionTestResultModel(
        success=True,
        latency_ms=12.5,
        message='连接成功',
    )
    status = vo.ModelEngineStatusModel(
        runtime_configured=True,
        runtime_available=True,
        management_configured=True,
        management_available=False,
        latency_ms=None,
        last_checked_at=datetime(2026, 8, 11, tzinfo=timezone.utc),
        deployment_count=None,
        credential_count=2,
    )

    assert 'private-api-key' not in repr(connection)
    assert dump_alias(result) == {'success': True, 'latencyMs': 12.5, 'message': '连接成功'}
    assert dump_alias(status) == {
        'runtimeConfigured': True,
        'runtimeAvailable': True,
        'managementConfigured': True,
        'managementAvailable': False,
        'latencyMs': None,
        'lastCheckedAt': '2026-08-11T00:00:00Z',
        'deploymentCount': None,
        'credentialCount': 2,
    }
    for model_type, request in (
        (
            vo.ConnectionTestRequestModel,
            {
                'upstreamModel': 'openai/model',
                'provider': 'openai',
                'credentialName': 'openai-main',
                'values': {},
                'apiBase': 'private-api-base',
            },
        ),
        (vo.ConnectionTestResultModel, {'success': True, 'latencyMs': 1, 'rawResponse': {'token': 'x'}}),
        (
            vo.ModelEngineStatusModel,
            {
                'runtimeConfigured': True,
                'runtimeAvailable': True,
                'managementConfigured': True,
                'managementAvailable': True,
                'databaseUrl': 'private-api-base',
            },
        ),
    ):
        with pytest.raises(ValidationError):
            model_type.model_validate(request)


def test_connection_payload_validates_dynamic_values_and_is_an_exact_litellm_request(
    real_provider_payload: dict[str, object],
) -> None:
    vo, adapter_module = load_contract()
    providers = adapter_module.LiteLLMManagementAdapter.normalize_provider_fields(real_provider_payload)
    request = vo.ConnectionTestRequestModel.model_validate(
        {
            'upstreamModel': 'openai/gpt-4.1-mini',
            'provider': 'OpenAI',
            'credentialName': None,
            'values': {'api_key': 'private-api-key', 'organization': 'org-123'},
        }
    )

    payload = adapter_module.LiteLLMManagementAdapter.build_connection_test_payload(request, providers)

    assert payload == {
        'litellm_params': {
            'model': 'openai/gpt-4.1-mini',
            'custom_llm_provider': 'openai',
            'api_key': 'private-api-key',
            'organization': 'org-123',
        },
        'model_info': {},
    }


def test_connection_payload_allows_saved_credential_without_reposting_values(
    real_provider_payload: dict[str, object],
) -> None:
    vo, adapter_module = load_contract()
    providers = adapter_module.LiteLLMManagementAdapter.normalize_provider_fields(real_provider_payload)
    request = vo.ConnectionTestRequestModel.model_validate(
        {
            'upstreamModel': 'openai/gpt-4.1-mini',
            'provider': 'OpenAI',
            'credentialName': 'openai-main',
            'values': {},
        }
    )

    payload = adapter_module.LiteLLMManagementAdapter.build_connection_test_payload(request, providers)

    assert payload == {
        'litellm_params': {
            'model': 'openai/gpt-4.1-mini',
            'custom_llm_provider': 'openai',
            'litellm_credential_name': 'openai-main',
        },
        'model_info': {},
    }


@pytest.mark.parametrize(
    ('model_type_name', 'payload'),
    (
        (
            'DeploymentCreateModel',
            {
                'modelAlias': 'company\x00chat',
                'upstreamModel': 'openai/model',
                'provider': 'openai',
                'credentialName': 'openai-main',
            },
        ),
        (
            'ConnectionTestRequestModel',
            {
                'upstreamModel': 'openai/model\nother',
                'provider': 'openai',
                'credentialName': 'openai-main',
                'values': {},
            },
        ),
    ),
)
def test_request_identifiers_reject_control_characters(model_type_name: str, payload: dict[str, object]) -> None:
    vo, _ = load_contract()

    with pytest.raises(ValidationError):
        getattr(vo, model_type_name).model_validate(payload)


def test_connection_dynamic_value_failure_does_not_retain_secret_in_traceback(
    real_provider_payload: dict[str, object],
) -> None:
    vo, adapter_module = load_contract()
    providers = adapter_module.LiteLLMManagementAdapter.normalize_provider_fields(real_provider_payload)
    request = vo.ConnectionTestRequestModel.model_validate(
        {
            'upstreamModel': 'openai/gpt-4.1-mini',
            'provider': 'OpenAI',
            'credentialName': None,
            'values': {'api_key': 'private-api-key', 'future_credential': 'private-token'},
        }
    )

    with pytest.raises(ValueError) as raised:
        adapter_module.LiteLLMManagementAdapter.build_connection_test_payload(request, providers)

    serialized = repr((str(raised.value), raised.value.__cause__, raised.value.__context__)).lower()
    assert serialized == "('凭据字段校验失败', none, none)"
    current = raised.value.__traceback__
    while current is not None:
        filename = current.tb_frame.f_code.co_filename.replace('\\', '/')
        if '/plugins/ai/' in filename and '/tests/' not in filename:
            local_repr = repr(current.tb_frame.f_locals).lower()
            assert 'private-api-key' not in local_repr
            assert 'private-token' not in local_repr
        current = current.tb_next
