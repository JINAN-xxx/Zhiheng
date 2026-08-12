import pickle
import re
import traceback
from copy import copy, deepcopy
from datetime import datetime
from typing import Annotated

import pytest
from fastapi import FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import ValidationError

from plugins.ai.domain import model_service_errors
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.domain.resource_id import new_resource_id, validate_resource_id
from plugins.ai.entity.vo.ai_model_catalog_vo import (
    EngineMetadataModel,
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
MAX_ENGINE_RESOURCE_ID_LENGTH = 255
HTTP_OK = 200
HTTP_UNPROCESSABLE_CONTENT = 422
HTTP_NOT_FOUND = 404
MODEL_SERVICE_ERROR_COUNT = 10
RUNTIME_AUTH_FAILED_INDEX = 6
VALID_UUID4_HEX = '123e4567e89b42d3a456426614174000'
SECOND_VALID_UUID4_HEX = '987e4567e89b42d3a456426614174111'
UUID1_HEX = '123e4567e89b12d3a456426614174000'
NCS_VARIANT_HEX = '123e4567e89b42d37456426614174000'


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
        ('run_' + VALID_UUID4_HEX, 'mdl'),
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


@pytest.mark.parametrize('uuid_hex', (UUID1_HEX, NCS_VARIANT_HEX))
def test_resource_id_validation_rejects_non_uuid4_or_non_rfc_4122_values(uuid_hex: str) -> None:
    with pytest.raises(ValueError):
        validate_resource_id(f'mdl_{uuid_hex}', 'mdl')


@pytest.mark.parametrize(
    ('value', 'prefix'),
    (
        (None, 'mdl'),
        (1, 'mdl'),
        ('mdl_' + VALID_UUID4_HEX, None),
        ('mdl_' + VALID_UUID4_HEX, 1),
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
    assert error.__cause__ is None
    assert error.__context__ is None


def test_model_service_error_not_found_uses_platform_message() -> None:
    assert ModelServiceError.not_found().public_dict() == {
        'code': 'MODEL_NOT_FOUND',
        'message': '平台模型不存在',
    }


def test_model_service_error_does_not_retain_upstream_details() -> None:
    error = ModelServiceError.runtime_auth_failed()
    serialized_state = repr((error.__dict__, error.args, error.public_dict())).lower()

    assert vars(error) == {}
    assert error.args == ()
    assert object.__getattribute__(error, '_kind_index') == RUNTIME_AUTH_FAILED_INDEX
    assert 'https://' not in serialized_state
    assert 'authorization' not in serialized_state
    assert 'api_key' not in serialized_state
    assert 'secret' not in serialized_state
    assert 'upstream_body' not in serialized_state


def test_model_service_error_rejects_direct_construction_with_untrusted_details() -> None:
    sensitive_message = 'Authorization: Bearer secret at https://runtime.internal upstream_body'

    with pytest.raises(TypeError) as raised:
        ModelServiceError(502, 'UNTRUSTED_ERROR', sensitive_message)

    assert sensitive_message not in str(raised.value)


def test_model_service_error_contract_mapping_is_read_only() -> None:
    assert len(ModelServiceError._ERRORS) == MODEL_SERVICE_ERROR_COUNT
    with pytest.raises(TypeError):
        ModelServiceError._ERRORS['forged'] = (200, 'FORGED', 'Authorization: Bearer secret')
    with pytest.raises(TypeError):
        ModelServiceError._ERRORS['not_found'] = (200, 'FORGED', 'Authorization: Bearer secret')
    with pytest.raises(TypeError):
        del ModelServiceError._ERRORS['not_found']


def test_replacing_model_service_error_mapping_does_not_change_fixed_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ModelServiceError,
        '_ERRORS',
        {'not_found': (200, 'FORGED', 'Authorization: Bearer secret')},
    )

    error = ModelServiceError.not_found()

    assert error.status_code == HTTP_NOT_FOUND
    assert error.public_dict() == {'code': 'MODEL_NOT_FOUND', 'message': '平台模型不存在'}


def test_replacing_module_error_contracts_does_not_change_fixed_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    forged_contracts = ((200, 'FORGED', 'Authorization: Bearer secret'),)
    monkeypatch.setattr(model_service_errors, '_FIXED_ERROR_CONTRACTS', forged_contracts)
    monkeypatch.setattr(model_service_errors, '_ERROR_VIEW', {'not_found': forged_contracts[0]})

    error = ModelServiceError.not_found()

    assert error.status_code == HTTP_NOT_FOUND
    assert error.public_dict() == {'code': 'MODEL_NOT_FOUND', 'message': '平台模型不存在'}


def test_model_service_error_has_no_dynamic_category_constructor() -> None:
    assert not hasattr(ModelServiceError, '_create')

    for dynamic_category in ('forged', 0, 9, -1, 10):
        with pytest.raises(TypeError):
            ModelServiceError(dynamic_category)


def test_model_service_error_cannot_be_subclassed_to_override_constructor_or_contract() -> None:
    with pytest.raises(TypeError):

        class ForgedModelServiceError(ModelServiceError):
            _ERRORS = {'forged': (200, 'FORGED', 'Authorization: Bearer secret')}

            def __init__(self) -> None:
                Exception.__init__(self, 'Authorization: Bearer secret')


@pytest.mark.parametrize(
    ('attribute', 'value'),
    (
        ('status_code', 200),
        ('code', 'FORGED'),
        ('public_message', 'Authorization: Bearer secret'),
        ('args', ('Authorization: Bearer secret',)),
        ('_kind_index', 99),
    ),
)
def test_model_service_error_rejects_attribute_mutation(attribute: str, value: object) -> None:
    error = ModelServiceError.runtime_error()

    with pytest.raises((AttributeError, TypeError)):
        setattr(error, attribute, value)


@pytest.mark.parametrize('attribute', ('status_code', 'code', 'public_message', 'args', '_kind_index'))
def test_model_service_error_rejects_attribute_deletion(attribute: str) -> None:
    error = ModelServiceError.runtime_error()

    with pytest.raises((AttributeError, TypeError)):
        delattr(error, attribute)


def test_low_level_model_service_error_without_trusted_index_cannot_render() -> None:
    forged = Exception.__new__(ModelServiceError)
    Exception.__init__(forged, 'Authorization: Bearer secret at https://runtime.internal upstream_body')

    for operation in (str, repr, lambda error: error.public_dict()):
        with pytest.raises(TypeError):
            operation(forged)


@pytest.mark.parametrize(
    'operation',
    (
        copy,
        deepcopy,
        lambda error: pickle.loads(pickle.dumps(error)),
    ),
)
def test_model_service_error_copy_and_pickle_restore_only_fixed_contract(operation: object) -> None:
    original = ModelServiceError.provider_rate_limited()
    restored = operation(original)

    assert restored is not original
    assert type(restored) is ModelServiceError
    assert restored.public_dict() == original.public_dict()
    assert vars(restored) == vars(original)
    assert restored.__cause__ is None
    assert restored.__context__ is None


@pytest.mark.asyncio
async def test_model_service_error_translation_drops_sensitive_async_upstream_exception_chain() -> None:
    async def fail_with_sensitive_upstream() -> None:
        raise RuntimeError('Authorization: Bearer secret at https://runtime.internal upstream_body')

    with pytest.raises(ModelServiceError) as raised:
        await model_service_errors.call_with_model_service_error(
            fail_with_sensitive_upstream,
            ModelServiceError.runtime_error,
        )

    error = raised.value
    serialized_public_state = repr(
        (error.__dict__, error.args, str(error), repr(error), error.public_dict())
    ).lower()
    formatted_traceback = ''.join(traceback.format_exception(type(error), error, error.__traceback__)).lower()

    assert vars(error) == {}
    assert error.args == ()
    assert error.__cause__ is None
    assert error.__context__ is None
    assert 'authorization' not in serialized_public_state
    assert 'secret' not in serialized_public_state
    assert 'https://' not in serialized_public_state
    assert 'upstream_body' not in serialized_public_state
    assert 'authorization' not in formatted_traceback
    assert 'secret' not in formatted_traceback
    assert 'https://' not in formatted_traceback
    assert 'upstream_body' not in formatted_traceback


@pytest.mark.asyncio
async def test_model_service_error_helper_rebuilds_polluted_exact_instance() -> None:
    async def fail_with_sensitive_upstream() -> None:
        raise RuntimeError('Authorization: Bearer upstream-secret at https://runtime.internal upstream_body')

    forged = Exception.__new__(ModelServiceError)
    object.__setattr__(forged, '_kind_index', 9)
    Exception.__init__(forged, 'Authorization: Bearer forged-secret at https://forged.internal body')
    forged.__dict__.update(code='FORGED', public_message='Authorization: Bearer forged-secret')

    with pytest.raises(ModelServiceError) as raised:
        await model_service_errors.call_with_model_service_error(fail_with_sensitive_upstream, lambda: forged)

    error = raised.value
    serialized_error = repr((vars(error), error.args, str(error), repr(error), error.public_dict())).lower()

    assert error is not forged
    assert vars(error) == {}
    assert error.args == ()
    assert error.public_dict() == {'code': 'MODEL_RUNTIME_ERROR', 'message': '模型运行时调用失败'}
    assert error.__cause__ is None
    assert error.__context__ is None
    assert 'authorization' not in serialized_error
    assert 'secret' not in serialized_error
    assert 'https://' not in serialized_error
    assert 'body' not in serialized_error


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


@pytest.mark.parametrize(
    'payload',
    (
        {
            'model_code': 'company.reasoning-v1',
            'displayName': '企业推理模型',
            'modelType': 'chat',
        },
        {
            'modelCode': 'company.reasoning-v1',
            'display_name': '企业推理模型',
            'modelType': 'chat',
        },
        {
            'modelCode': 'company.reasoning-v1',
            'displayName': '企业推理模型',
            'model_type': 'chat',
        },
        {
            'modelCode': 'company.reasoning-v1',
            'displayName': '企业推理模型',
            'modelType': 'chat',
            'defaultParameters': {'top_p': TOP_P},
        },
    ),
)
def test_model_catalog_create_rejects_snake_case_request_fields(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ModelCatalogCreateModel.model_validate(payload)


@pytest.mark.parametrize(
    ('model_type', 'payload'),
    (
        (ModelCatalogUpdateModel, {'display_name': '更新后的名称'}),
        (ModelCatalogPageQueryModel, {'page_num': QUERY_PAGE_NUM}),
        (ResourceBindingUpsertModel, {'engine_resource_id': 'company-reasoning'}),
        (ModelCapabilitiesModel, {'vision': True, 'reasoning_mode': True}),
        (ModelDefaultParametersModel, {'top_p': TOP_P}),
    ),
)
def test_all_request_models_reject_snake_case_fields(model_type: type, payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        model_type.model_validate(payload)


@pytest.mark.parametrize(
    'model_type',
    (
        ModelCatalogCreateModel,
        ModelCatalogUpdateModel,
        ModelCatalogPageQueryModel,
        ResourceBindingUpsertModel,
        ModelCapabilitiesModel,
        ModelDefaultParametersModel,
        EngineMetadataModel,
    ),
)
def test_all_request_models_validate_by_camel_case_alias_only(model_type: type) -> None:
    assert model_type.model_config['validate_by_alias'] is True
    assert model_type.model_config['validate_by_name'] is False


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


def test_model_catalog_page_query_accepts_standard_http_query_integer_strings() -> None:
    app = FastAPI()

    @app.get('/models')
    async def list_models(query: Annotated[ModelCatalogPageQueryModel, Query()]) -> dict[str, int]:
        return {'pageNum': query.page_num, 'pageSize': query.page_size}

    response = TestClient(app).get(
        '/models',
        params={'pageNum': str(QUERY_PAGE_NUM), 'pageSize': str(QUERY_PAGE_SIZE)},
    )

    assert response.status_code == HTTP_OK
    assert response.json() == {'pageNum': QUERY_PAGE_NUM, 'pageSize': QUERY_PAGE_SIZE}


@pytest.mark.parametrize('field', ('pageNum', 'pageSize'))
@pytest.mark.parametrize('value', ('0', '-1', '1.5', 'true', 'not-a-number'))
def test_model_catalog_page_query_rejects_invalid_http_query_integers(field: str, value: str) -> None:
    app = FastAPI()

    @app.get('/models')
    async def list_models(query: Annotated[ModelCatalogPageQueryModel, Query()]) -> dict[str, int]:
        return {'pageNum': query.page_num, 'pageSize': query.page_size}

    response = TestClient(app).get('/models', params={field: value})

    assert response.status_code == HTTP_UNPROCESSABLE_CONTENT


@pytest.mark.parametrize('value', (True, False, 1.5, 0, -1, '1.5', 'true', 'not-a-number'))
def test_model_catalog_page_query_rejects_non_positive_or_non_integer_python_values(value: object) -> None:
    with pytest.raises(ValidationError):
        ModelCatalogPageQueryModel.model_validate({'pageNum': value})


@pytest.mark.parametrize(
    'field',
    ('modelCode', 'displayName', 'modelType', 'capabilities', 'defaultParameters'),
)
def test_model_catalog_update_rejects_explicit_null_for_non_nullable_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        ModelCatalogUpdateModel.model_validate({field: None})


@pytest.mark.parametrize('field', ('contextWindow', 'maxOutputTokens'))
def test_model_catalog_update_accepts_explicit_null_for_clearable_limits(field: str) -> None:
    update = ModelCatalogUpdateModel.model_validate({field: None})

    assert update.model_dump(by_alias=True, exclude_unset=True) == {field: None}


def test_model_catalog_update_keeps_empty_patch_semantics() -> None:
    update = ModelCatalogUpdateModel.model_validate({})

    assert update.model_dump(by_alias=True, exclude_unset=True) == {}


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


@pytest.mark.parametrize('engine_resource_id', ('', ' ', '   \t\n'))
def test_resource_binding_upsert_rejects_blank_engine_resource_id(engine_resource_id: str) -> None:
    with pytest.raises(ValidationError):
        ResourceBindingUpsertModel.model_validate({'engineResourceId': engine_resource_id})


def test_resource_binding_upsert_strips_engine_resource_id_and_accepts_maximum_length() -> None:
    engine_resource_id = 'm' * MAX_ENGINE_RESOURCE_ID_LENGTH
    binding = ResourceBindingUpsertModel.model_validate({'engineResourceId': f'  {engine_resource_id}  '})

    assert binding.engine_resource_id == engine_resource_id
    assert binding.model_dump(by_alias=True, exclude_unset=True) == {'engineResourceId': engine_resource_id}


def test_resource_binding_upsert_rejects_engine_resource_id_over_maximum_length() -> None:
    with pytest.raises(ValidationError):
        ResourceBindingUpsertModel.model_validate(
            {'engineResourceId': 'm' * (MAX_ENGINE_RESOURCE_ID_LENGTH + 1)}
        )


@pytest.mark.parametrize(
    'engine_resource_id',
    (
        'model\x00alias',
        'model\nalias',
        'model\ralias',
        'model\talias',
        'model\x1falias',
        'model\x7falias',
        'model\x80alias',
        'model\x85alias',
        'model\u2028alias',
        'model\u2029alias',
    ),
)
def test_resource_binding_upsert_rejects_control_characters(engine_resource_id: str) -> None:
    with pytest.raises(ValidationError):
        ResourceBindingUpsertModel.model_validate({'engineResourceId': engine_resource_id})


@pytest.mark.parametrize('field', ('expectedVersion',))
@pytest.mark.parametrize('value', (0, -1, 1.5, '1', True))
def test_resource_binding_upsert_requires_strict_positive_expected_version(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        ResourceBindingUpsertModel.model_validate({'engineResourceId': 'company-reasoning', field: value})


def test_platform_model_response_is_from_attributes_and_does_not_expose_runtime_credentials() -> None:
    class CatalogRecord:
        model_id = 'mdl_' + VALID_UUID4_HEX
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
            'modelId': 'mdl_' + VALID_UUID4_HEX,
            'lifecycleStatus': 'draft',
            'createBy': 'admin',
            'createTime': '2026-08-12T09:00:00',
            'updateBy': 'admin',
            'updateTime': '2026-08-12T09:00:00',
            'binding': {
                'bindingId': 'bnd_' + SECOND_VALID_UUID4_HEX,
                'resourceId': 'mdl_' + VALID_UUID4_HEX,
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


@pytest.mark.parametrize(
    'model_id',
    (
        'bnd_' + VALID_UUID4_HEX,
        'mdl_' + UUID1_HEX,
        'mdl_' + NCS_VARIANT_HEX,
        'mdl_' + 'a' * 32,
    ),
)
def test_platform_model_response_rejects_invalid_model_resource_id(model_id: str) -> None:
    payload = {
        **_valid_create_payload(),
        'modelId': model_id,
        'lifecycleStatus': 'draft',
        'createBy': 'admin',
        'createTime': '2026-08-12T09:00:00',
        'updateBy': 'admin',
        'updateTime': '2026-08-12T09:00:00',
    }

    with pytest.raises(ValidationError):
        PlatformModelModel.model_validate(payload)


@pytest.mark.parametrize(
    ('binding_id', 'resource_id'),
    (
        ('mdl_' + SECOND_VALID_UUID4_HEX, 'mdl_' + VALID_UUID4_HEX),
        ('bnd_' + UUID1_HEX, 'mdl_' + VALID_UUID4_HEX),
        ('bnd_' + SECOND_VALID_UUID4_HEX, 'bnd_' + VALID_UUID4_HEX),
        ('bnd_' + SECOND_VALID_UUID4_HEX, 'mdl_' + NCS_VARIANT_HEX),
    ),
)
def test_platform_model_detail_rejects_invalid_binding_resource_ids(binding_id: str, resource_id: str) -> None:
    payload = {
        **_valid_create_payload(),
        'modelId': 'mdl_' + VALID_UUID4_HEX,
        'lifecycleStatus': 'draft',
        'createBy': 'admin',
        'createTime': '2026-08-12T09:00:00',
        'updateBy': 'admin',
        'updateTime': '2026-08-12T09:00:00',
        'binding': {
            'bindingId': binding_id,
            'resourceId': resource_id,
            'engineResourceId': 'company-reasoning',
            'bindingStatus': 'active',
            'engineMetadata': {},
            'version': 1,
            'createTime': '2026-08-12T09:00:00',
            'updateTime': '2026-08-12T09:00:00',
        },
    }

    with pytest.raises(ValidationError):
        PlatformModelDetailModel.model_validate(payload)
