"""Resolve saved connections without exposing keys or guessing from credential names."""

import re
from collections.abc import Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

from plugins.ai.entity.vo.ai_model_engine_vo import (
    ConnectionProfileModel,
    CredentialSummaryModel,
    DeploymentModel,
    ProviderModel,
)
from plugins.ai.infrastructure.litellm_management_adapter import LiteLLMManagementAdapter
from plugins.ai.infrastructure.litellm_management_errors import LiteLLMManagementUpstreamInvalidError
from plugins.ai.infrastructure.rerank_compatibility import restore_connection_parameters

_MAX_API_BASE_LENGTH = 2048
_MAX_PORT = 65535
_CONTROL_CHARACTER_LIMIT = 32
_HOST = re.compile(r'^[a-zA-Z0-9.-]+$')


def safe_api_base(value: object) -> str | None:
    """Only return an HTTP endpoint without credentials, query, fragment, or control characters."""
    if not isinstance(value, str) or not value or len(value) > _MAX_API_BASE_LENGTH:
        return None
    if any(character.isspace() or ord(character) < _CONTROL_CHARACTER_LIMIT for character in value) or any(
        c in value for c in '?#\\'
    ):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
            return None
        if '@' in parsed.netloc or '%' in parsed.netloc:
            return None
        host = parsed.hostname.lower()
        if ':' not in host and not _HOST.fullmatch(host):
            return None
        port = parsed.port
        if port is not None and not 1 <= port <= _MAX_PORT:
            return None
        authority = f'[{host}]' if ':' in host else host
        if port is not None and (parsed.scheme, port) not in {('https', 443), ('http', 80)}:
            authority += f':{port}'
        return urlunsplit((parsed.scheme, authority, parsed.path.rstrip('/'), '', ''))
    except ValueError:
        return None


def resolve_credential_connections(
    credentials_payload: object,
    deployments_payload: object,
    providers: Sequence[ProviderModel],
) -> list[CredentialSummaryModel]:
    result = _resolve_credential_connections(credentials_payload, deployments_payload, providers)
    del credentials_payload, deployments_payload, providers
    if result is None:
        raise LiteLLMManagementUpstreamInvalidError
    return result


def resolve_deployment_connection(
    deployment: DeploymentModel,
    parameters: Mapping[str, object],
    saved_profile: ConnectionProfileModel | None,
    providers: Sequence[ProviderModel],
) -> tuple[ConnectionProfileModel, bool]:
    """A deployed explicit endpoint takes precedence when validating an in-place connection change."""
    has_explicit_address = _has_address(parameters)
    if has_explicit_address:
        return (
            _profile({'credential_values': {'api_base': parameters['api_base']}}, deployment.provider, [], providers),
            True,
        )
    return saved_profile or _profile({}, deployment.provider, [], providers), False


def _rows(payload: object, keys: tuple[str, ...]) -> list | None:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, Mapping):
        return None
    present = [key for key in keys if key in payload]
    if len(present) != 1:
        return None
    items = payload[present[0]]
    return items if isinstance(items, list) else None


def _resolve_credential_connections(
    credentials_payload: object,
    deployments_payload: object,
    providers: Sequence[ProviderModel],
) -> list[CredentialSummaryModel] | None:
    try:
        raw_credentials = _rows(credentials_payload, ('credentials', 'data', 'items'))
        raw_deployments = _rows(deployments_payload, ('data', 'items'))
        if raw_credentials is None or raw_deployments is None:
            return None
        deployments = LiteLLMManagementAdapter.normalize_deployments(deployments_payload)
        referenced_providers: dict[str, set[str]] = {}
        for deployment in deployments:
            if deployment.credential_name:
                referenced_providers.setdefault(deployment.credential_name, set()).add(deployment.provider)
        inferred = {}
        for item in raw_credentials:
            name = item['credential_name']
            engines = referenced_providers.get(name, set())
            inferred[name] = next(iter(engines)) if len(engines) == 1 else 'unknown'
        credentials = LiteLLMManagementAdapter.normalize_credentials(
            credentials_payload,
            provider_by_credential=inferred,
        )
        return [
            credential.model_copy(
                update={
                    'connection_profile': _profile(
                        item,
                        credential.provider,
                        [
                            restore_connection_parameters(row['litellm_params'], row['model_info'])
                            for row in raw_deployments
                            if row['litellm_params'].get('litellm_credential_name') == credential.credential_name
                        ],
                        providers,
                    )
                }
            )
            for item, credential in zip(raw_credentials, credentials, strict=True)
        ]
    except Exception:
        return None


def _profile(
    credential: Mapping, engine: str, references: list[Mapping], providers: Sequence[ProviderModel]
) -> ConnectionProfileModel:
    values = credential.get('credential_values', {})
    if not isinstance(values, Mapping):
        return ConnectionProfileModel(status='invalid')
    addresses = []
    if _has_address(values):
        addresses.append(values['api_base'])
    else:
        addresses.extend(params['api_base'] for params in references if _has_address(params))
    normalized = [safe_api_base(address) for address in addresses]
    if any(address is None for address in normalized):
        return ConnectionProfileModel(status='invalid')
    unique = set(normalized)
    if len(unique) > 1:
        return ConnectionProfileModel(status='conflict')
    api_base = next(iter(unique)) if unique else None

    # Engine identity is enough only for native SDKs. "openai" names a shared protocol.
    canonical = next((item.litellm_provider for item in providers if item.provider == engine), engine)
    native_ids = [
        item.provider
        for item in providers
        if item.litellm_provider == canonical and canonical not in {'openai', 'custom_openai', 'openai_compatible'}
    ]
    if native_ids and canonical != 'deepseek':
        return ConnectionProfileModel(status='ready', api_base=api_base, provider_ids=native_ids)
    if api_base is None:
        info = credential.get('credential_info', {})
        declared_provider = info.get('provider')
        if declared_provider == 'OpenAI':
            api_base = 'https://api.openai.com/v1'
        elif native_ids:
            return ConnectionProfileModel(status='ready', provider_ids=native_ids)
        else:
            return ConnectionProfileModel(status='missing')
    return ConnectionProfileModel(status='ready', api_base=api_base, provider_ids=_endpoint_providers(api_base))


def _has_address(values: Mapping) -> bool:
    value = values.get('api_base')
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _endpoint_providers(api_base: str) -> list[str]:
    parsed = urlsplit(api_base)
    if parsed.scheme == 'https' and parsed.port is None:
        if parsed.hostname in {'api.siliconflow.cn', 'api-st.siliconflow.cn'} and parsed.path == '/v1':
            return ['OpenAI_Compatible', 'CUSTOM_OPENAI']
        if parsed.hostname == 'api.deepseek.com' and parsed.path in {'', '/v1'}:
            return ['Deepseek', 'CUSTOM_OPENAI']
        if parsed.hostname == 'api.openai.com' and parsed.path == '/v1':
            return ['OpenAI', 'CUSTOM_OPENAI']
    return ['CUSTOM_OPENAI']
