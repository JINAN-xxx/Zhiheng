import re
from datetime import datetime

import pytest
from pydantic import ValidationError

from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.domain.resource_id import new_resource_id, validate_resource_id
from plugins.ai.entity.vo.ai_model_catalog_vo import (
    ModelCapabilitiesModel,
    ModelCatalogCreateModel,
    ModelCatalogPageQueryModel,
    ModelCatalogUpdateModel,
    ModelDefaultParametersModel,
    PlatformModelDetailModel,
    PlatformModelModel,
    ResourceBindingUpsertModel,
)

RESOURCE_PREFIXES = ('mdl', 'bnd', 'ses', 'msg', 'run')
GENERATED_ID_SAMPLE_SIZE = 100
TOP_P = 0.95
QUERY_PAGE_NUM = 2
QUERY_PAGE_SIZE = 50


@pytest.mark.parametrize('prefix', RESOURCE_PREFIXES)
def test_resource_id_generation_uses_allowed_prefix_and_compact_uuid(prefix: str) -> None:
    resource_id = new_resource_id(prefix)

    assert re.fullmatch(rf'{prefix}_[0-9a-f]{{32}}', resource_id)
    assert validate_resource_id(resource_id, prefix) == resource_id


def test_resource_id_generation_is_unique() -> None:
    generated = {new_resource_id('mdl') for _ in range(GENERATED_ID_SAMPLE_SIZE)}

    assert len(generated) == GENERATED_ID_SAMPLE_SIZE


@pytest.mark.parametrize('prefix', ('model', 'litellm', '', 'MDL'))
def test_resource_id_generation_rejects_unsupported_prefix(prefix: str) -> None:
    with pytest.raises(ValueError):
        new_resource_id(prefix)


@pytest.mark.parametrize('prefix', (None, 1, b'mdl'))
def test_resource_id_generation_rejects_non_string_prefix(prefix: object) -> None:
    with pytest.raises(TypeError):
        new_resource_id(prefix)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ('value', 'prefix'),
    (
        ('run_' + 'a' * 32, 'mdl'),
        ('mdl_' + 'A' * 32, 'mdl'),
        ('mdl_' + 'a' * 31, 'mdl'),
        ('mdl_' + 'a' * 33, 'mdl'),
        ('mdl_' + 'g' * 32, 'mdl'),
        ('mdl-' + 'a' * 32, 'mdl'),
    ),
)
def test_resource_id_validation_rejects_wrong_type_case_length_or_format(value: str, prefix: str) -> None:
    with pytest.raises(ValueError):
        validate_resource_id(value, prefix)


@pytest.mark.parametrize(
    ('value', 'prefix'),
    (
        (None, 'mdl'),
        (1, 'mdl'),
        ('mdl_' + 'a' * 32, None),
        ('mdl_' + 'a' * 32, 1),
    ),
)
def test_resource_id_validation_rejects_non_string_arguments(value: object, prefix: object) -> None:
    with pytest.raises(TypeError):
        validate_resource_id(value, prefix)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ('factory_name', 'status_code', 'code'),
    (
        ('not_found', 404, 'MODEL_NOT_FOUND'),
        ('disabled', 409, 'MODEL_DISABLED'),
        ('binding_missing', 503, 'MODEL_BINDING_MISSING'),
        ('runtime_misconfigured', 503, 'MODEL_RUNTIME_MISCONFIGURED'),
        ('runtime_unavailable', 503, 'MODEL_RUNTIME_UNAVAILABLE'),
        ('runtime_timeout', 504, 'MODEL_RUNTIME_TIMEOUT'),
        ('runtime_auth_failed', 502, 'MODEL_RUNTIME_AUTH_FAILED'),
        ('provider_auth_failed', 502, 'MODEL_PROVIDER_AUTH_FAILED'),
        ('provider_rate_limited', 502, 'MODEL_PROVIDER_RATE_LIMITED'),
        ('runtime_error', 502, 'MODEL_RUNTIME_ERROR'),
    ),
)
def test_model_service_errors_have_stable_public_contract(
    factory_name: str,
    status_code: int,
    code: str,
) -> None:
    error = getattr(ModelServiceError, factory_name)()

    assert error.status_code == status_code
    assert error.code == code
    assert error.public_message
    assert error.public_dict() == {'code': code, 'message': error.public_message}


def test_model_service_error_not_found_uses_platform_message() -> None:
    assert ModelServiceError.not_found().public_dict() == {
        'code': 'MODEL_NOT_FOUND',
        'message': '平台模型不存在',
    }


def test_model_service_error_does_not_retain_upstream_details() -> None:
    error = ModelServiceError.runtime_auth_failed()
    serialized_state = repr((error.__dict__, error.args, error.public_dict())).lower()

    assert set(error.__dict__) == {'status_code', 'code', 'public_message'}
    assert 'https://' not in serialized_state
    assert 'authorization' not in serialized_state
    assert 'api_key' not in serialized_state
    assert 'secret' not in serialized_state
    assert 'upstream_body' not in serialized_state


def _valid_create_payload() -> dict[str, object]:
    return {
        'modelCode': 'company.reasoning-v1',
        'displayName': '企业推理模型',
        'modelType': 'chat',
        'capabilities': {
            'vision': True,
            'reasoning': True,
            'tools': False,
            'streaming': True,
        },
        'contextWindow': 128000,
        'maxOutputTokens': 8192,
        'defaultParameters': {
            'temperature': 0.2,
            'topP': TOP_P,
            'maxTokens': 4096,
            'presencePenalty': 0,
            'frequencyPenalty': -0.1,
        },
    }


def test_model_catalog_create_accepts_camel_case_and_serializes_nested_models_as_camel_case() -> None:
    request = ModelCatalogCreateModel.model_validate(_valid_create_payload())

    assert request.model_code == 'company.reasoning-v1'
    assert request.capabilities.reasoning is True
    assert request.default_parameters.top_p == TOP_P
    assert request.model_dump(by_alias=True) == _valid_create_payload()


@pytest.mark.parametrize('field', ('apiKey', 'baseUrl', 'litellmModelId', 'engineType', 'provider'))
def test_model_catalog_create_rejects_runtime_and_provider_fields(field: str) -> None:
    payload = {**_valid_create_payload(), field: 'must-not-cross-platform-boundary'}

    with pytest.raises(ValidationError):
        ModelCatalogCreateModel.model_validate(payload)


@pytest.mark.parametrize(
    'model_code',
    (
        '',
        '-starts-with-separator',
        '.starts-with-separator',
        '_starts-with-separator',
        'contains space',
        'contains/slash',
        '含中文',
        'a' * 101,
    ),
)
def test_model_catalog_create_rejects_unstable_model_code(model_code: str) -> None:
    payload = {**_valid_create_payload(), 'modelCode': model_code}

    with pytest.raises(ValidationError):
        ModelCatalogCreateModel.model_validate(payload)


@pytest.mark.parametrize('model_code', ('a', '1', 'A_model.1-beta', 'model_company_reasoning'))
def test_model_catalog_create_accepts_stable_model_code(model_code: str) -> None:
    payload = {**_valid_create_payload(), 'modelCode': model_code}

    assert ModelCatalogCreateModel.model_validate(payload).model_code == model_code


@pytest.mark.parametrize('display_name', ('', '   ', None))
def test_model_catalog_create_requires_non_blank_display_name(display_name: object) -> None:
    payload = {**_valid_create_payload(), 'displayName': display_name}

    with pytest.raises(ValidationError):
        ModelCatalogCreateModel.model_validate(payload)


@pytest.mark.parametrize('model_type', ('completion', 'image', 'CHAT', ''))
def test_model_catalog_models_reject_unknown_model_type(model_type: str) -> None:
    payload = {**_valid_create_payload(), 'modelType': model_type}

    with pytest.raises(ValidationError):
        ModelCatalogCreateModel.model_validate(payload)


@pytest.mark.parametrize('model_type', ('chat', 'embedding'))
def test_model_catalog_models_accept_supported_model_type(model_type: str) -> None:
    payload = {**_valid_create_payload(), 'modelType': model_type}

    assert ModelCatalogCreateModel.model_validate(payload).model_type == model_type


@pytest.mark.parametrize('field', ('contextWindow', 'maxOutputTokens'))
@pytest.mark.parametrize('value', (0, -1, 1.5, '1024', True))
def test_model_catalog_create_requires_strict_positive_integer_limits(field: str, value: object) -> None:
    payload = {**_valid_create_payload(), field: value}

    with pytest.raises(ValidationError):
        ModelCatalogCreateModel.model_validate(payload)


@pytest.mark.parametrize('unknown_key', ('audio', 'embeddings', 'litellmCapability'))
def test_capabilities_reject_unknown_keys(unknown_key: str) -> None:
    with pytest.raises(ValidationError):
        ModelCapabilitiesModel.model_validate({unknown_key: True})


@pytest.mark.parametrize('value', ('true', 1, 0, None))
def test_capabilities_require_boolean_values(value: object) -> None:
    with pytest.raises(ValidationError):
        ModelCapabilitiesModel.model_validate({'vision': value})


@pytest.mark.parametrize(
    ('field', 'value'),
    (
        ('temperature', -0.1),
        ('temperature', 2.1),
        ('topP', -0.1),
        ('topP', 1.1),
        ('maxTokens', 0),
        ('maxTokens', -1),
        ('maxTokens', 1.5),
        ('presencePenalty', -2.1),
        ('presencePenalty', 2.1),
        ('frequencyPenalty', -2.1),
        ('frequencyPenalty', 2.1),
    ),
)
def test_default_parameters_enforce_common_ranges(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        ModelDefaultParametersModel.model_validate({field: value})


@pytest.mark.parametrize('unknown_key', ('seed', 'stop', 'responseFormat', 'apiKey', 'baseUrl'))
def test_default_parameters_reject_unknown_keys(unknown_key: str) -> None:
    with pytest.raises(ValidationError):
        ModelDefaultParametersModel.model_validate({unknown_key: 'not-allowed'})


def test_model_catalog_update_and_page_query_are_strict_camel_case_models() -> None:
    update = ModelCatalogUpdateModel.model_validate(
        {
            'displayName': '更新后的名称',
            'defaultParameters': {'temperature': 1.0},
        }
    )
    query = ModelCatalogPageQueryModel.model_validate(
        {
            'modelCode': 'company',
            'modelType': 'chat',
            'lifecycleStatus': 'active',
            'pageNum': QUERY_PAGE_NUM,
            'pageSize': QUERY_PAGE_SIZE,
        }
    )

    assert update.display_name == '更新后的名称'
    assert update.model_dump(by_alias=True, exclude_none=True) == {
        'displayName': '更新后的名称',
        'defaultParameters': {'temperature': 1.0},
    }
    assert query.page_num == QUERY_PAGE_NUM
    assert query.page_size == QUERY_PAGE_SIZE

    with pytest.raises(ValidationError):
        ModelCatalogUpdateModel.model_validate({'unknownField': True})
    with pytest.raises(ValidationError):
        ModelCatalogPageQueryModel.model_validate({'pageSize': 101})


def test_resource_binding_upsert_uses_engine_alias_without_accepting_engine_type() -> None:
    binding = ResourceBindingUpsertModel.model_validate(
        {
            'engineResourceId': 'company-reasoning',
            'bindingStatus': 'active',
            'engineMetadata': {},
            'expectedVersion': 1,
        }
    )

    assert binding.engine_resource_id == 'company-reasoning'
    assert binding.binding_status == 'active'
    assert binding.expected_version == 1
    assert 'engine_type' not in type(binding).model_fields

    with pytest.raises(ValidationError):
        ResourceBindingUpsertModel.model_validate(
            {'engineResourceId': 'company-reasoning', 'engineType': 'litellm'}
        )


@pytest.mark.parametrize('field', ('expectedVersion',))
@pytest.mark.parametrize('value', (0, -1, 1.5, '1', True))
def test_resource_binding_upsert_requires_strict_positive_expected_version(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        ResourceBindingUpsertModel.model_validate({'engineResourceId': 'company-reasoning', field: value})


def test_platform_model_response_is_from_attributes_and_does_not_expose_runtime_credentials() -> None:
    class CatalogRecord:
        model_id = 'mdl_' + 'a' * 32
        model_code = 'company.reasoning-v1'
        display_name = '企业推理模型'
        model_type = 'chat'
        capabilities = {'reasoning': True, 'streaming': True}
        context_window = 128000
        max_output_tokens = 8192
        default_parameters = {'temperature': 0.2, 'top_p': TOP_P}
        lifecycle_status = 'active'
        published_at = datetime(2026, 8, 12, 9, 30)
        create_by = 'admin'
        create_time = datetime(2026, 8, 12, 9, 0)
        update_by = 'admin'
        update_time = datetime(2026, 8, 12, 9, 30)
        api_key = 'secret'
        base_url = 'https://internal.example.com'
        litellm_model_id = 'deployment-secret'

    response = PlatformModelModel.model_validate(CatalogRecord())
    data = response.model_dump(by_alias=True, mode='json')

    assert data['modelId'] == CatalogRecord.model_id
    assert data['defaultParameters']['topP'] == TOP_P
    assert {'apiKey', 'baseUrl', 'litellmModelId', 'engineType'}.isdisjoint(data)


def test_platform_model_detail_serializes_binding_with_entity_field_names() -> None:
    detail = PlatformModelDetailModel.model_validate(
        {
            **_valid_create_payload(),
            'modelId': 'mdl_' + 'a' * 32,
            'lifecycleStatus': 'draft',
            'createBy': 'admin',
            'createTime': '2026-08-12T09:00:00',
            'updateBy': 'admin',
            'updateTime': '2026-08-12T09:00:00',
            'binding': {
                'bindingId': 'bnd_' + 'b' * 32,
                'resourceId': 'mdl_' + 'a' * 32,
                'engineResourceId': 'company-reasoning',
                'bindingStatus': 'active',
                'engineMetadata': {},
                'version': 1,
                'createTime': '2026-08-12T09:00:00',
                'updateTime': '2026-08-12T09:00:00',
            },
        }
    )
    data = detail.model_dump(by_alias=True, mode='json')

    assert data['binding']['engineResourceId'] == 'company-reasoning'
    assert data['binding']['bindingStatus'] == 'active'
    assert data['binding']['engineMetadata'] == {}
    assert data['binding']['version'] == 1
    assert {'apiKey', 'baseUrl', 'litellmModelId', 'engineType'}.isdisjoint(data['binding'])
