import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Generic, TypeVar, cast

from plugins.ai.entity.vo.ai_model_engine_vo import (
    ConnectionTestRequestModel,
    CredentialCreateModel,
    CredentialRotateModel,
    CredentialSummaryModel,
    DeploymentCreateModel,
    DeploymentModel,
    DeploymentUpdateModel,
    ProviderFieldModel,
    ProviderModel,
)
from plugins.ai.infrastructure.litellm_management_errors import LiteLLMManagementUpstreamInvalidError

ALLOWED_CREDENTIAL_KEYS = frozenset(
    {
        'api_base',
        'api_key',
        'api_version',
        'aws_access_key_id',
        'aws_bedrock_runtime_endpoint',
        'aws_profile_name',
        'aws_region_name',
        'aws_role_name',
        'aws_secret_access_key',
        'aws_session_name',
        'aws_session_token',
        'aws_web_identity_token',
        'azure_ad_token',
        'base_model',
        'client_id',
        'client_secret',
        'oci_compartment_id',
        'oci_fingerprint',
        'oci_key',
        'oci_region',
        'oci_tenancy',
        'oci_user',
        'organization',
        'project_id',
        'space_id',
        'tenant_id',
        'token',
        'vertex_credentials',
        'vertex_location',
        'vertex_project',
        'zen_api_key',
    }
)
_FIELD_TYPES = frozenset({'text', 'password', 'select', 'upload', 'textarea'})
_FIELD_KEY_PATTERN = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,127}$')
_IDENTIFIER_PATTERN = re.compile(r'^[A-Za-z][A-Za-z0-9._-]{0,127}$')
_CREDENTIAL_NAME_PATTERN = re.compile(r'^[A-Za-z0-9._-]{1,128}$')
_FORBIDDEN_TEXT_CATEGORIES = frozenset({'Cc', 'Zl', 'Zp'})
_MAX_CREDENTIAL_VALUE_LENGTH = 8192
_MAX_PROVIDER_OPTIONS = 100
_MAX_DATETIME_TEXT_LENGTH = 64

ValueT = TypeVar('ValueT')


@dataclass(frozen=True)
class _ParseOutcome(Generic[ValueT]):
    value: ValueT | None


@dataclass(frozen=True)
class _CredentialPayloadOutcome:
    payload: dict[str, object] | None


class LiteLLMManagementAdapter:
    """把 LiteLLM 管理面数据收敛为 Platform 的受控契约。"""

    @staticmethod
    def normalize_provider_fields(payload: object) -> list[ProviderModel]:
        outcome = _normalize_provider_fields(payload)
        del payload
        if outcome.value is None:
            _raise_upstream_invalid()
        return list(cast('tuple[ProviderModel, ...]', outcome.value))

    @staticmethod
    def normalize_credentials(payload: object) -> list[CredentialSummaryModel]:
        outcome = _normalize_credentials(payload)
        del payload
        if outcome.value is None:
            _raise_upstream_invalid()
        return list(cast('tuple[CredentialSummaryModel, ...]', outcome.value))

    @staticmethod
    def normalize_deployments(payload: object) -> list[DeploymentModel]:
        outcome = _normalize_deployments(payload)
        del payload
        if outcome.value is None:
            _raise_upstream_invalid()
        return list(cast('tuple[DeploymentModel, ...]', outcome.value))

    @staticmethod
    def build_credential_create_payload(
        request: CredentialCreateModel,
        providers: Sequence[ProviderModel],
    ) -> dict[str, object]:
        outcome = _build_credential_payload(
            credential_name=request.credential_name,
            provider_name=request.provider,
            values=request.values,
            providers=providers,
            require_required=True,
        )
        del request, providers
        if outcome.payload is None:
            _raise_credential_validation_error()
        return cast('dict[str, object]', outcome.payload)

    @staticmethod
    def build_credential_rotate_payload(
        credential_name: str,
        request: CredentialRotateModel,
        providers: Sequence[ProviderModel],
    ) -> dict[str, object]:
        outcome = _build_credential_payload(
            credential_name=credential_name,
            provider_name=request.provider,
            values=request.values,
            providers=providers,
            require_required=False,
        )
        del credential_name, request, providers
        if outcome.payload is None:
            _raise_credential_validation_error()
        return cast('dict[str, object]', outcome.payload)

    @staticmethod
    def build_deployment_create_payload(
        request: DeploymentCreateModel,
        providers: Sequence[ProviderModel],
    ) -> dict[str, object]:
        outcome = _build_deployment_create_payload(request, providers)
        del request, providers
        if outcome.payload is None:
            _raise_deployment_provider_validation_error()
        return cast('dict[str, object]', outcome.payload)

    @staticmethod
    def build_deployment_update_payload(
        request: DeploymentUpdateModel,
        providers: Sequence[ProviderModel],
    ) -> dict[str, object]:
        outcome = _build_deployment_update_payload(request, providers)
        del request, providers
        if outcome.payload is None:
            _raise_deployment_provider_validation_error()
        return cast('dict[str, object]', outcome.payload)

    @staticmethod
    def build_connection_test_payload(
        request: ConnectionTestRequestModel,
        providers: Sequence[ProviderModel],
    ) -> dict[str, object]:
        outcome = _build_connection_test_payload(request, providers)
        del request, providers
        if outcome.payload is None:
            _raise_credential_validation_error()
        return cast('dict[str, object]', outcome.payload)


def _build_deployment_create_payload(
    request: DeploymentCreateModel,
    providers: Sequence[ProviderModel],
) -> _CredentialPayloadOutcome:
    try:
        provider = _find_provider(request.provider, providers)
        if provider is None:
            return _CredentialPayloadOutcome(payload=None)
        litellm_params: dict[str, object] = {
            'model': request.upstream_model,
            'custom_llm_provider': provider.litellm_provider,
            'litellm_credential_name': request.credential_name,
        }
        _set_if_not_none(litellm_params, 'timeout', request.timeout)
        _set_if_not_none(litellm_params, 'stream_timeout', request.stream_timeout)
        _set_if_not_none(litellm_params, 'max_retries', request.max_retries)
        _set_if_not_none(litellm_params, 'rpm', request.rpm)
        _set_if_not_none(litellm_params, 'tpm', request.tpm)
        model_info: dict[str, object] = {}
        _set_if_not_none(model_info, 'description', request.description)
        return _CredentialPayloadOutcome(
            payload={
                'model_name': request.model_alias,
                'litellm_params': litellm_params,
                'model_info': model_info,
            }
        )
    except Exception:
        return _CredentialPayloadOutcome(payload=None)


def _build_deployment_update_payload(
    request: DeploymentUpdateModel,
    providers: Sequence[ProviderModel],
) -> _CredentialPayloadOutcome:
    try:
        fields = request.model_fields_set
        payload: dict[str, object] = {}
        if 'model_alias' in fields:
            payload['model_name'] = request.model_alias

        litellm_params: dict[str, object] = {}
        parameter_fields = (
            ('upstream_model', 'model'),
            ('credential_name', 'litellm_credential_name'),
            ('timeout', 'timeout'),
            ('stream_timeout', 'stream_timeout'),
            ('max_retries', 'max_retries'),
            ('rpm', 'rpm'),
            ('tpm', 'tpm'),
        )
        for field_name, upstream_name in parameter_fields:
            if field_name in fields:
                litellm_params[upstream_name] = getattr(request, field_name)
        if 'provider' in fields:
            provider = _find_provider(cast('str', request.provider), providers)
            if provider is None:
                return _CredentialPayloadOutcome(payload=None)
            litellm_params['custom_llm_provider'] = provider.litellm_provider
        if litellm_params:
            payload['litellm_params'] = litellm_params
        if 'description' in fields:
            payload['model_info'] = {'description': request.description}
        if 'blocked' in fields:
            payload['blocked'] = request.blocked
        return _CredentialPayloadOutcome(payload=payload)
    except Exception:
        return _CredentialPayloadOutcome(payload=None)


def _normalize_provider_fields(payload: object) -> _ParseOutcome[tuple[ProviderModel, ...]]:
    try:
        items = _extract_items(payload, ('items',))
        if items is None:
            return _ParseOutcome(value=None)
        providers: list[ProviderModel] = []
        seen_providers: set[str] = set()
        for item in items:
            if not _is_string_key_mapping(item):
                return _ParseOutcome(value=None)
            provider = _safe_identifier(item.get('provider'))
            display_name = _safe_text(item.get('provider_display_name'), maximum=256)
            litellm_provider = _safe_identifier(item.get('litellm_provider'))
            placeholder = _safe_optional_text(item.get('default_model_placeholder'), maximum=512)
            raw_fields = item.get('credential_fields')
            if provider is None or display_name is None or litellm_provider is None or not isinstance(raw_fields, list):
                return _ParseOutcome(value=None)
            if item.get('default_model_placeholder') is not None and placeholder is None:
                return _ParseOutcome(value=None)
            if provider in seen_providers:
                return _ParseOutcome(value=None)
            seen_providers.add(provider)

            fields: list[ProviderFieldModel] = []
            seen_fields: set[str] = set()
            for raw_field in raw_fields:
                field = _normalize_provider_field(raw_field)
                if field is None or field.field_key in seen_fields:
                    return _ParseOutcome(value=None)
                seen_fields.add(field.field_key)
                fields.append(field)
            providers.append(
                ProviderModel(
                    provider=provider,
                    display_name=display_name,
                    litellm_provider=litellm_provider,
                    default_model_placeholder=placeholder,
                    credential_fields=fields,
                )
            )
        return _ParseOutcome(value=tuple(providers))
    except Exception:
        return _ParseOutcome(value=None)


def _normalize_provider_field(raw_field: object) -> ProviderFieldModel | None:
    if not _is_string_key_mapping(raw_field):
        return None
    field_key = raw_field.get('key')
    label = _safe_text(raw_field.get('label'), maximum=256)
    if not isinstance(field_key, str) or _FIELD_KEY_PATTERN.fullmatch(field_key) is None or label is None:
        return None
    placeholder = _safe_field_placeholder(raw_field.get('placeholder'), maximum=512)
    tooltip = _safe_optional_text(raw_field.get('tooltip'), maximum=2000)
    if raw_field.get('placeholder') is not None and placeholder is None:
        return None
    if raw_field.get('tooltip') is not None and tooltip is None:
        return None

    required = raw_field.get('required', False)
    if type(required) is not bool:
        return None
    raw_field_type = raw_field.get('field_type', 'text')
    if not isinstance(raw_field_type, str) or _safe_text(raw_field_type, maximum=64) is None:
        return None
    field_type = raw_field_type if raw_field_type in _FIELD_TYPES else 'text'
    valid_options, options = _safe_options(raw_field.get('options'))
    valid_default, default_value = _safe_simple_value(raw_field.get('default_value'))
    if not valid_options or not valid_default:
        return None
    return ProviderFieldModel(
        field_key=field_key,
        label=label,
        placeholder=placeholder,
        tooltip=tooltip,
        required=required,
        field_type=field_type,
        options=options,
        default_value=default_value,
    )


def _normalize_credentials(payload: object) -> _ParseOutcome[tuple[CredentialSummaryModel, ...]]:
    try:
        items = payload if isinstance(payload, list) else _extract_items(payload, ('data', 'items', 'credentials'))
        if items is None:
            return _ParseOutcome(value=None)
        credentials: list[CredentialSummaryModel] = []
        seen_names: set[str] = set()
        for item in items:
            if not _is_string_key_mapping(item):
                return _ParseOutcome(value=None)
            name = item.get('credential_name')
            if not isinstance(name, str) or _CREDENTIAL_NAME_PATTERN.fullmatch(name) is None or name in seen_names:
                return _ParseOutcome(value=None)
            seen_names.add(name)
            raw_info = item.get('credential_info', {})
            if not _is_string_key_mapping(raw_info):
                return _ParseOutcome(value=None)
            provider_value = item.get('provider')
            if provider_value is None:
                provider_value = raw_info.get('custom_llm_provider', raw_info.get('provider'))
            provider = _safe_identifier(provider_value)
            if provider is None:
                return _ParseOutcome(value=None)
            count = item.get('referenced_deployment_count', item.get('referencedDeploymentCount'))
            if count is not None and (type(count) is not int or count < 0):
                return _ParseOutcome(value=None)
            valid_created, created_at = _safe_datetime(item.get('created_at', item.get('createdAt')))
            valid_updated, updated_at = _safe_datetime(item.get('updated_at', item.get('updatedAt')))
            if not valid_created or not valid_updated:
                return _ParseOutcome(value=None)
            credentials.append(
                CredentialSummaryModel(
                    credential_name=name,
                    provider=provider,
                    referenced_deployment_count=count,
                    created_at=created_at,
                    updated_at=updated_at,
                )
            )
        return _ParseOutcome(value=tuple(credentials))
    except Exception:
        return _ParseOutcome(value=None)


def _normalize_deployments(payload: object) -> _ParseOutcome[tuple[DeploymentModel, ...]]:
    try:
        items = _extract_items(payload, ('data', 'items'))
        if items is None:
            return _ParseOutcome(value=None)
        deployments: list[DeploymentModel] = []
        seen_ids: set[str] = set()
        for item in items:
            deployment = _normalize_deployment(item)
            if deployment is None or deployment.deployment_id in seen_ids:
                return _ParseOutcome(value=None)
            seen_ids.add(deployment.deployment_id)
            deployments.append(deployment)
        return _ParseOutcome(value=tuple(deployments))
    except Exception:
        return _ParseOutcome(value=None)


def _normalize_deployment(item: object) -> DeploymentModel | None:
    if not _is_string_key_mapping(item):
        return None
    raw_params = item.get('litellm_params')
    raw_info = item.get('model_info')
    if not _is_string_key_mapping(raw_params) or not _is_string_key_mapping(raw_info):
        return None

    deployment_id = _safe_text(raw_info.get('id'), maximum=255)
    model_alias = _safe_text(item.get('model_name'), maximum=255)
    upstream_model = _safe_text(raw_params.get('model'), maximum=512)
    provider_value = raw_params.get('custom_llm_provider')
    if provider_value is None:
        provider_value = raw_info.get('litellm_provider')
    provider = _safe_identifier(provider_value)
    credential_value = raw_params.get('litellm_credential_name')
    credential_name = None
    if credential_value is not None:
        if not isinstance(credential_value, str) or _CREDENTIAL_NAME_PATTERN.fullmatch(credential_value) is None:
            return None
        credential_name = credential_value
    if deployment_id is None or model_alias is None or upstream_model is None or provider is None:
        return None

    raw_status = raw_info.get('status', item.get('status', 'unknown'))
    status = _safe_text(raw_status, maximum=64)
    if status is None:
        return None
    valid_latency, latency_ms = _safe_optional_number(raw_info.get('latency_ms', item.get('latency_ms')), 0, None)
    blocked_value = item.get('blocked', raw_info.get('blocked', False))
    if not valid_latency or type(blocked_value) is not bool:
        return None
    valid_created, created_at = _safe_datetime(raw_info.get('created_at', item.get('created_at')))
    valid_updated, updated_at = _safe_datetime(raw_info.get('updated_at', item.get('updated_at')))
    valid_timeout, timeout = _safe_optional_number(raw_params.get('timeout'), 0, 600, exclusive_minimum=True)
    valid_stream_timeout, stream_timeout = _safe_optional_number(
        raw_params.get('stream_timeout'), 0, 600, exclusive_minimum=True
    )
    valid_retries, max_retries = _safe_optional_integer(raw_params.get('max_retries'), 0, 10)
    valid_rpm, rpm = _safe_optional_integer(raw_params.get('rpm'), 1, 10_000_000_000)
    valid_tpm, tpm = _safe_optional_integer(raw_params.get('tpm'), 1, 10_000_000_000)
    description = _safe_optional_text(raw_info.get('description'), maximum=2000)
    if raw_info.get('description') is not None and description is None:
        return None
    if not all(
        (
            valid_created,
            valid_updated,
            valid_timeout,
            valid_stream_timeout,
            valid_retries,
            valid_rpm,
            valid_tpm,
        )
    ):
        return None
    return DeploymentModel(
        deployment_id=deployment_id,
        model_alias=model_alias,
        provider=provider,
        upstream_model=upstream_model,
        credential_name=credential_name,
        status=status,
        latency_ms=latency_ms,
        blocked=blocked_value,
        created_at=created_at,
        updated_at=updated_at,
        timeout=timeout,
        stream_timeout=stream_timeout,
        max_retries=max_retries,
        rpm=rpm,
        tpm=tpm,
        description=description,
    )


def _build_credential_payload(
    *,
    credential_name: object,
    provider_name: str,
    values: Mapping[str, object],
    providers: Sequence[ProviderModel],
    require_required: bool,
) -> _CredentialPayloadOutcome:
    try:
        if (
            not isinstance(credential_name, str)
            or _CREDENTIAL_NAME_PATTERN.fullmatch(credential_name) is None
            or not values
        ):
            return _CredentialPayloadOutcome(payload=None)
        provider = _find_provider(provider_name, providers)
        credential_values = _validated_credential_values(
            values,
            provider,
            require_required=require_required,
        )
        if provider is None or credential_values is None:
            return _CredentialPayloadOutcome(payload=None)
        return _CredentialPayloadOutcome(
            payload={
                'credential_name': credential_name,
                'credential_info': {'custom_llm_provider': provider.litellm_provider},
                'credential_values': credential_values,
            }
        )
    except Exception:
        return _CredentialPayloadOutcome(payload=None)


def _build_connection_test_payload(
    request: ConnectionTestRequestModel,
    providers: Sequence[ProviderModel],
) -> _CredentialPayloadOutcome:
    try:
        provider = _find_provider(request.provider, providers)
        values = _validated_credential_values(
            request.values,
            provider,
            require_required=request.credential_name is None,
        )
        if provider is None or values is None:
            return _CredentialPayloadOutcome(payload=None)
        litellm_params: dict[str, object] = {
            'model': request.upstream_model,
            'custom_llm_provider': provider.litellm_provider,
        }
        if request.credential_name is not None:
            litellm_params['litellm_credential_name'] = request.credential_name
        litellm_params.update(values)
        return _CredentialPayloadOutcome(
            payload={
                'litellm_params': litellm_params,
                'model_info': {},
            }
        )
    except Exception:
        return _CredentialPayloadOutcome(payload=None)


def _find_provider(provider_name: str, providers: Sequence[ProviderModel]) -> ProviderModel | None:
    exact = [provider for provider in providers if provider.provider == provider_name]
    if len(exact) == 1:
        return exact[0]
    canonical = [provider for provider in providers if provider.litellm_provider == provider_name]
    return canonical[0] if len(canonical) == 1 else None


def _validated_credential_values(
    values: Mapping[str, object],
    provider: ProviderModel | None,
    *,
    require_required: bool,
) -> dict[str, str] | None:
    if provider is None:
        return None
    fields = {field.field_key: field for field in provider.credential_fields}
    supported_keys = set(fields).intersection(ALLOWED_CREDENTIAL_KEYS)
    if any(key not in supported_keys for key in values):
        return None
    if require_required and any(field.required and key not in values for key, field in fields.items()):
        return None

    validated: dict[str, str] = {}
    for key, secret_value in values.items():
        get_secret_value = getattr(secret_value, 'get_secret_value', None)
        if get_secret_value is None:
            return None
        raw_value = get_secret_value()
        if not isinstance(raw_value, str) or len(raw_value) > _MAX_CREDENTIAL_VALUE_LENGTH:
            return None
        field = fields[key]
        if field.field_type in {'textarea', 'upload'}:
            if not _is_safe_multiline_secret(raw_value):
                return None
        elif not raw_value.strip() or raw_value != raw_value.strip() or _contains_forbidden_text(raw_value):
            return None
        if field.field_type == 'select' and field.options is not None and raw_value not in field.options:
            return None
        validated[key] = raw_value
    return validated


def _extract_items(payload: object, allowed_keys: tuple[str, ...]) -> list[object] | None:
    if not _is_string_key_mapping(payload):
        return None
    present = [key for key in allowed_keys if key in payload]
    if len(present) != 1:
        return None
    items = payload[present[0]]
    return items if isinstance(items, list) else None


def _is_string_key_mapping(value: object) -> bool:
    return isinstance(value, Mapping) and all(isinstance(key, str) for key in value)


def _contains_forbidden_text(value: str) -> bool:
    return any(unicodedata.category(character) in _FORBIDDEN_TEXT_CATEGORIES for character in value)


def _safe_text(value: object, *, maximum: int) -> str | None:
    if not isinstance(value, str) or _contains_forbidden_text(value):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > maximum:
        return None
    return normalized


def _safe_optional_text(value: object, *, maximum: int) -> str | None:
    return None if value is None else _safe_text(value, maximum=maximum)


def _safe_field_placeholder(value: object, *, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    for index, character in enumerate(value):
        if character == '\n':
            continue
        if character == '\r':
            if index + 1 < len(value) and value[index + 1] == '\n':
                continue
            return None
        if unicodedata.category(character) in _FORBIDDEN_TEXT_CATEGORIES:
            return None
    normalized = value.replace('\r\n', '\n').strip()
    if not normalized or len(normalized) > maximum:
        return None
    return normalized


def _is_safe_multiline_secret(value: str) -> bool:
    if not value.strip():
        return False
    for index, character in enumerate(value):
        if character == '\n':
            continue
        if character == '\r':
            if index + 1 < len(value) and value[index + 1] == '\n':
                continue
            return False
        if unicodedata.category(character) in _FORBIDDEN_TEXT_CATEGORIES:
            return False
    return True


def _safe_identifier(value: object) -> str | None:
    normalized = _safe_text(value, maximum=128)
    if normalized is None or _IDENTIFIER_PATTERN.fullmatch(normalized) is None:
        return None
    return normalized


def _safe_options(value: object) -> tuple[bool, list[object] | None]:
    if value is None:
        return True, None
    if not isinstance(value, list) or len(value) > _MAX_PROVIDER_OPTIONS:
        return False, None
    options: list[object] = []
    for option in value:
        valid, normalized = _safe_simple_value(option)
        if not valid:
            return False, None
        options.append(normalized)
    return True, options


def _safe_simple_value(value: object) -> tuple[bool, object]:
    if value is None or type(value) is bool or type(value) is int:
        return True, value
    if type(value) is float:
        return (math.isfinite(value), value)
    if isinstance(value, str):
        normalized = _safe_optional_text(value, maximum=512)
        return (normalized is not None, normalized)
    return False, None


def _safe_datetime(value: object) -> tuple[bool, datetime | None]:
    if value is None:
        return True, None
    if isinstance(value, datetime):
        return True, value
    if not isinstance(value, str) or len(value) > _MAX_DATETIME_TEXT_LENGTH or _contains_forbidden_text(value):
        return False, None
    try:
        parsed = datetime.fromisoformat(value[:-1] + '+00:00' if value.endswith('Z') else value)
    except ValueError:
        return False, None
    return True, parsed


def _safe_optional_number(
    value: object,
    minimum: float,
    maximum: float | None,
    *,
    exclusive_minimum: bool = False,
) -> tuple[bool, float | None]:
    if value is None:
        return True, None
    if type(value) not in {int, float}:
        return False, None
    normalized = float(value)
    if not math.isfinite(normalized):
        return False, None
    if exclusive_minimum and normalized <= minimum:
        return False, None
    if not exclusive_minimum and normalized < minimum:
        return False, None
    if maximum is not None and normalized > maximum:
        return False, None
    return True, normalized


def _safe_optional_integer(value: object, minimum: int, maximum: int) -> tuple[bool, int | None]:
    if value is None:
        return True, None
    if type(value) is not int or value < minimum or value > maximum:
        return False, None
    return True, value


def _set_if_not_none(payload: dict[str, object], key: str, value: object) -> None:
    if value is not None:
        payload[key] = value


def _raise_upstream_invalid() -> None:
    raise LiteLLMManagementUpstreamInvalidError from None


def _raise_credential_validation_error() -> None:
    raise ValueError('凭据字段校验失败') from None


def _raise_deployment_provider_validation_error() -> None:
    raise ValueError('模型供应商校验失败') from None
