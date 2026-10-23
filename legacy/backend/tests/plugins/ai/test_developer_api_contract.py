import pickle
import re
from copy import copy, deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from plugins.ai.domain import developer_api_errors
from plugins.ai.domain.developer_api_errors import DeveloperApiError
from plugins.ai.domain.resource_id import new_resource_id
from plugins.ai.entity.vo.ai_developer_api_vo import (
    ApplicationCreateModel,
    ApplicationModel,
    ApplicationPageQueryModel,
    ApplicationUpdateModel,
    DeveloperKeyCreatedModel,
    DeveloperKeyCreateModel,
    DeveloperKeyRotateModel,
    DeveloperKeySummaryModel,
    ModelGrantModel,
    QuotaPolicyModel,
    QuotaPolicyWriteModel,
)

RESOURCE_PREFIXES = ('app', 'key', 'grt', 'qta', 'rate', 'use', 'qbk')
VALID_MODEL_ID = 'mdl_123e4567e89b42d3a456426614174000'
SECOND_MODEL_ID = 'mdl_987e4567e89b42d3a456426614174111'
VALID_APPLICATION_ID = 'app_123e4567e89b42d3a456426614174000'
VALID_KEY_ID = 'key_123e4567e89b42d3a456426614174000'
VALID_GRANT_ID = 'grt_123e4567e89b42d3a456426614174000'
UTC_EXPIRY = datetime(2027, 8, 18, tzinfo=timezone.utc)
NOW = datetime(2026, 8, 18, 10, 0)
QUERY_PAGE_NUM = 2
QUERY_PAGE_SIZE = 50
HTTP_SERVICE_UNAVAILABLE = 503


@pytest.mark.parametrize('prefix', RESOURCE_PREFIXES)
def test_developer_api_resource_ids_use_compact_uuid4(prefix: str) -> None:
    resource_id = new_resource_id(prefix)

    assert re.fullmatch(rf'{prefix}_[0-9a-f]{{32}}', resource_id)


def test_application_request_models_are_strict_camel_case_and_forbid_extra_fields() -> None:
    created = ApplicationCreateModel.model_validate(
        {'applicationCode': 'billing-prod', 'applicationName': '  Billing API  '}
    )

    assert created.application_code == 'billing-prod'
    assert created.application_name == 'Billing API'
    assert created.model_dump(by_alias=True) == {
        'applicationCode': 'billing-prod',
        'applicationName': 'Billing API',
    }
    for payload in (
        {'application_code': 'billing-prod', 'applicationName': 'Billing API'},
        {'applicationCode': 'billing-prod', 'applicationName': 'Billing API', 'tenantId': 'tenant-secret'},
    ):
        with pytest.raises(ValidationError):
            ApplicationCreateModel.model_validate(payload)


@pytest.mark.parametrize('dangerous_name', ('\x00Billing', 'Billing\x85', 'Billing\u2028API', 'Billing\u2029API'))
def test_application_and_key_names_reject_control_characters_before_trimming(dangerous_name: str) -> None:
    with pytest.raises(ValidationError):
        ApplicationCreateModel.model_validate({'applicationCode': 'billing-prod', 'applicationName': dangerous_name})
    with pytest.raises(ValidationError):
        DeveloperKeyCreateModel.model_validate(
            {
                'keyName': dangerous_name,
                'modelIds': [VALID_MODEL_ID],
                'quota': {},
            }
        )


@pytest.mark.parametrize('application_code', ('', '.hidden', 'billing prod', 'billing/production', 'x' * 101))
def test_application_code_uses_stable_safe_identifier(application_code: str) -> None:
    with pytest.raises(ValidationError):
        ApplicationCreateModel.model_validate({'applicationCode': application_code, 'applicationName': 'Billing API'})


def test_application_update_requires_cas_version_and_rejects_immutable_fields() -> None:
    update = ApplicationUpdateModel.model_validate({'applicationName': 'New Name', 'expectedVersion': 2})

    assert update.model_dump(by_alias=True) == {'applicationName': 'New Name', 'expectedVersion': 2}
    for payload in (
        {'applicationName': 'New Name'},
        {'applicationCode': 'changed', 'applicationName': 'New Name', 'expectedVersion': 2},
        {'applicationName': None, 'expectedVersion': 2},
    ):
        with pytest.raises(ValidationError):
            ApplicationUpdateModel.model_validate(payload)


def test_application_query_parses_ascii_pagination_and_rejects_boolean() -> None:
    query = ApplicationPageQueryModel.model_validate({'pageNum': '2', 'pageSize': '50'})

    assert query.page_num == QUERY_PAGE_NUM
    assert query.page_size == QUERY_PAGE_SIZE
    with pytest.raises(ValidationError):
        ApplicationPageQueryModel.model_validate({'pageNum': True})


def test_quota_policy_accepts_decimal_strings_and_preserves_exact_values() -> None:
    quota = QuotaPolicyWriteModel.model_validate(
        {
            'rpmLimit': 60,
            'tpmLimit': 100000,
            'concurrencyLimit': 10,
            'monthlyBudget': '100.1250000000',
            'warningRatio': '0.8000',
            'hardLimitRatio': '1.0000',
            'currency': 'USD',
        }
    )

    assert quota.monthly_budget == Decimal('100.1250000000')
    assert quota.warning_ratio == Decimal('0.8000')
    assert quota.hard_limit_ratio == Decimal('1.0000')


@pytest.mark.parametrize(
    'payload',
    (
        {'rpmLimit': 0},
        {'tpmLimit': -1},
        {'concurrencyLimit': 0},
        {'monthlyBudget': '-0.01'},
        {'monthlyBudget': 1.5},
        {'warningRatio': '0'},
        {'warningRatio': '1.1'},
        {'warningRatio': '0.9', 'hardLimitRatio': '0.8'},
        {'currency': 'usd'},
    ),
)
def test_quota_policy_rejects_invalid_limits_money_and_ratios(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        QuotaPolicyWriteModel.model_validate(payload)


def test_key_create_requires_unique_model_grants_and_utc_expiry() -> None:
    created = DeveloperKeyCreateModel.model_validate(
        {
            'keyName': 'production',
            'modelIds': [VALID_MODEL_ID, SECOND_MODEL_ID],
            'expiresAt': UTC_EXPIRY.isoformat(),
            'quota': {'monthlyBudget': '100.00'},
        }
    )

    assert created.expires_at == UTC_EXPIRY
    assert created.model_ids == [VALID_MODEL_ID, SECOND_MODEL_ID]
    for model_ids in ([], [VALID_MODEL_ID, VALID_MODEL_ID]):
        with pytest.raises(ValidationError):
            DeveloperKeyCreateModel.model_validate({'keyName': 'production', 'modelIds': model_ids, 'quota': {}})
    with pytest.raises(ValidationError):
        DeveloperKeyCreateModel.model_validate(
            {
                'keyName': 'production',
                'modelIds': [VALID_MODEL_ID],
                'expiresAt': datetime(2027, 8, 18).isoformat(),
                'quota': {},
            }
        )
    with pytest.raises(ValidationError):
        DeveloperKeyCreateModel.model_validate(
            {
                'keyName': 'production',
                'modelIds': [VALID_MODEL_ID],
                'expiresAt': (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
                'quota': {},
            }
        )


@pytest.mark.parametrize('seconds', (-1, 604801, True))
def test_key_rotation_rejects_invalid_grace_period(seconds: object) -> None:
    with pytest.raises(ValidationError):
        DeveloperKeyRotateModel.model_validate({'gracePeriodSeconds': seconds})


def test_response_models_are_camel_case_and_only_created_response_can_hold_complete_key() -> None:
    quota = QuotaPolicyModel.model_validate(
        {
            'quotaId': 'qta_123e4567e89b42d3a456426614174000',
            'rpmLimit': 60,
            'tpmLimit': None,
            'concurrencyLimit': 10,
            'monthlyBudget': '100.00',
            'warningRatio': '0.8000',
            'hardLimitRatio': '1.0000',
            'currency': 'USD',
            'version': 1,
        }
    )
    grant = ModelGrantModel.model_validate({'grantId': VALID_GRANT_ID, 'modelId': VALID_MODEL_ID, 'status': 'active'})
    summary = DeveloperKeySummaryModel.model_validate(
        {
            'keyId': VALID_KEY_ID,
            'applicationId': VALID_APPLICATION_ID,
            'keyName': 'production',
            'maskedKey': 'sk-zh_key_1234...9f3c',
            'fingerprint': '9f3c',
            'status': 'active',
            'expiresAt': None,
            'retireAt': None,
            'lastUsedAt': None,
            'createdAt': NOW,
            'grants': [grant],
            'quota': quota,
        }
    )
    created = DeveloperKeyCreatedModel.model_validate(
        {**summary.model_dump(by_alias=True), 'apiKey': 'sk-zh_key_example_secret'}
    )

    assert created.api_key == 'sk-zh_key_example_secret'
    assert 'apiKey' not in summary.model_dump(by_alias=True)
    assert not {'secretDigest', 'digestVersion', 'pepper'} & summary.model_dump(by_alias=True).keys()
    assert 'sk-zh_key_example_secret' not in repr(summary)


def test_application_response_accepts_orm_style_names_and_serializes_camel_case() -> None:
    response = ApplicationModel.model_validate(
        {
            'application_id': VALID_APPLICATION_ID,
            'application_code': 'billing-prod',
            'application_name': 'Billing API',
            'owner_user_id': 7,
            'status': 'active',
            'version': 1,
            'disabled_at': None,
            'deleted_at': NOW,
            'created_by': 'alice',
            'created_at': NOW,
            'updated_by': 'alice',
            'updated_at': NOW,
        }
    )

    dumped = response.model_dump(by_alias=True)
    assert dumped['applicationId'] == VALID_APPLICATION_ID
    assert dumped['deletedAt'] == NOW


@pytest.mark.parametrize(
    ('factory_name', 'status_code', 'code'),
    (
        ('application_not_found', 404, 'APPLICATION_NOT_FOUND'),
        ('application_disabled', 409, 'APPLICATION_DISABLED'),
        ('application_code_conflict', 409, 'APPLICATION_CODE_CONFLICT'),
        ('api_key_not_found', 404, 'API_KEY_NOT_FOUND'),
        ('api_key_inactive', 409, 'API_KEY_INACTIVE'),
        ('model_grant_invalid', 409, 'MODEL_GRANT_INVALID'),
        ('quota_conflict', 409, 'QUOTA_VERSION_CONFLICT'),
        ('configuration_error', 503, 'DEVELOPER_API_MISCONFIGURED'),
        ('application_conflict', 409, 'APPLICATION_VERSION_CONFLICT'),
        ('application_not_disabled', 409, 'APPLICATION_NOT_DISABLED'),
        ('application_active', 409, 'APPLICATION_ACTIVE'),
    ),
)
def test_developer_api_errors_have_fixed_public_contract(
    factory_name: str,
    status_code: int,
    code: str,
) -> None:
    error = getattr(DeveloperApiError, factory_name)()

    assert error.status_code == status_code
    assert error.code == code
    assert error.public_message
    assert error.public_dict() == {'code': code, 'message': error.public_message}
    assert error.args == ()
    assert vars(error) == {}


def test_developer_api_error_is_immutable_non_subclassable_and_not_forgeable() -> None:
    error = DeveloperApiError.configuration_error()

    with pytest.raises(TypeError):
        DeveloperApiError(503, 'FORGED', 'pepper secret')
    with pytest.raises(AttributeError):
        error.code = 'FORGED'  # type: ignore[misc]
    with pytest.raises(TypeError):

        class ForgedError(DeveloperApiError):
            pass


def test_developer_api_error_survives_copy_and_pickle_without_mutable_contract() -> None:
    original = DeveloperApiError.model_grant_invalid()

    for restored in (copy(original), deepcopy(original), pickle.loads(pickle.dumps(original))):
        assert type(restored) is DeveloperApiError
        assert restored.public_dict() == original.public_dict()
        assert restored.public_dict() is not original.public_dict()


def test_replacing_error_views_does_not_change_fixed_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    forged = ((200, 'FORGED', 'pepper secret'),)
    monkeypatch.setattr(developer_api_errors, '_FIXED_ERROR_CONTRACTS', forged)
    monkeypatch.setattr(DeveloperApiError, '_ERRORS', {'configuration_error': forged[0]})

    error = DeveloperApiError.configuration_error()

    assert error.status_code == HTTP_SERVICE_UNAVAILABLE
    assert error.code == 'DEVELOPER_API_MISCONFIGURED'
