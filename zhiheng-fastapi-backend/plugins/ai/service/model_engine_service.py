import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timezone
from time import perf_counter
from types import MappingProxyType
from typing import ClassVar, NoReturn, Protocol, TypeVar

from plugins.ai.entity.vo.ai_model_engine_vo import (
    ConnectionProfileModel,
    ConnectionTestRequestModel,
    ConnectionTestResultModel,
    CredentialCreateModel,
    CredentialRotateModel,
    CredentialSummaryModel,
    DeploymentCreateModel,
    DeploymentModel,
    DeploymentUpdateModel,
    ModelEngineStatusModel,
    ProviderModel,
)
from plugins.ai.infrastructure.connection_probe_policy import prepare_connection_probe
from plugins.ai.infrastructure.litellm_management_adapter import LiteLLMManagementAdapter
from plugins.ai.infrastructure.litellm_management_errors import (
    LiteLLMManagementMisconfiguredError,
    LiteLLMManagementUpstreamInvalidError,
)
from plugins.ai.infrastructure.model_connection_resolver import (
    resolve_credential_connections,
    resolve_deployment_connection,
    safe_api_base,
)
from plugins.ai.infrastructure.rerank_compatibility import prepare_rerank_payload, restore_connection_parameters

ResultT = TypeVar('ResultT')
Clock = Callable[[], datetime]
Timer = Callable[[], float]
_MAX_REFERENCE_COUNT = 10_000_000_000
_ENGINE_ERROR_CONTRACTS = (
    (409, 'MODEL_ENGINE_CREDENTIAL_IN_USE'),
    (404, 'MODEL_ENGINE_DEPLOYMENT_NOT_FOUND'),
    (422, 'MODEL_ENGINE_REQUEST_INVALID'),
    (422, 'MODEL_ENGINE_CONNECTION_MISSING'),
    (409, 'MODEL_ENGINE_CONNECTION_CONFLICT'),
    (422, 'MODEL_ENGINE_CONNECTION_INVALID'),
    (422, 'MODEL_ENGINE_PROVIDER_MISMATCH'),
)
_ENGINE_ERROR_NAMES = (
    'credential_in_use',
    'deployment_not_found',
    'invalid_request',
    'connection_missing',
    'connection_conflict',
    'connection_invalid',
    'provider_mismatch',
)
_ENGINE_ERROR_VIEW = MappingProxyType(dict(zip(_ENGINE_ERROR_NAMES, _ENGINE_ERROR_CONTRACTS, strict=True)))
_ENGINE_ERROR_CONSTRUCTION_TOKEN = object()
_HEALTHY_LIVELINESS_STATUSES = frozenset({"I'm alive!", 'healthy', 'ok'})


class ManagementClient(Protocol):
    settings: object

    async def list_provider_fields(self) -> Mapping[str, object]: ...

    async def list_credentials(self) -> Mapping[str, object]: ...

    async def create_credential(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...

    async def rotate_credential(
        self,
        credential_name: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]: ...

    async def delete_credential(self, credential_name: str) -> Mapping[str, object]: ...

    async def list_deployments(self, *, model_alias: str | None = None) -> Mapping[str, object]: ...

    async def list_all_deployments(self) -> Mapping[str, object]: ...

    async def create_deployment(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...

    async def update_deployment(
        self,
        deployment_id: str,
        payload: Mapping[str, object],
    ) -> Mapping[str, object]: ...

    async def delete_deployment(self, deployment_id: str) -> Mapping[str, object]: ...

    async def test_connection(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...

    async def check_liveliness(self) -> Mapping[str, object]: ...


class RuntimeHealthClient(Protocol):
    settings: object

    async def check_liveliness(self) -> Mapping[str, object]: ...


class DeploymentReferenceQuery(Protocol):
    async def reference_counts(self) -> Mapping[str, int]: ...


def _engine_error_contract(kind_index: object) -> tuple[int, str]:
    if type(kind_index) is not int or not 0 <= kind_index < len(_ENGINE_ERROR_CONTRACTS):
        raise TypeError('无效的模型引擎错误分类')
    return _ENGINE_ERROR_CONTRACTS[kind_index]


def _validated_reference_count(count: object) -> int:
    if type(count) is not int or not 0 <= count <= _MAX_REFERENCE_COUNT:
        raise TypeError('无效的凭据引用计数')
    return count


def _trusted_engine_error_state(error: 'ModelEngineServiceError') -> tuple[int, int | None]:
    if type(error) is not ModelEngineServiceError:
        raise TypeError('无效的模型引擎错误实例')
    try:
        kind_index = object.__getattribute__(error, '_kind_index')
        reference_count = object.__getattribute__(error, '_reference_count')
        args = object.__getattribute__(error, 'args')
    except AttributeError:
        raise TypeError('无效的模型引擎错误实例') from None
    _engine_error_contract(kind_index)
    if args != ():
        raise TypeError('无效的模型引擎错误实例')
    if kind_index == 0:
        return kind_index, _validated_reference_count(reference_count)
    if reference_count is not None:
        raise TypeError('无效的模型引擎错误实例')
    return kind_index, None


def _new_model_engine_error(
    kind_index: int,
    reference_count: int | None = None,
    token: object = _ENGINE_ERROR_CONSTRUCTION_TOKEN,
) -> 'ModelEngineServiceError':
    return ModelEngineServiceError(kind_index, reference_count, token)


def _restore_model_engine_error(kind_index: int, reference_count: int | None) -> 'ModelEngineServiceError':
    return _new_model_engine_error(kind_index, reference_count)


class ModelEngineServiceError(Exception):
    """Engine 控制面的固定、不可变且脱敏业务错误。"""

    __slots__ = ('_kind_index', '_reference_count')
    _ERRORS: ClassVar = _ENGINE_ERROR_VIEW

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        raise TypeError('ModelEngineServiceError 不允许继承')

    def __init__(
        self,
        kind_index: int,
        reference_count: int | None = None,
        token: object | None = None,
    ) -> None:
        if token is not _ENGINE_ERROR_CONSTRUCTION_TOKEN:
            raise TypeError('ModelEngineServiceError 只能通过固定工厂创建')
        _engine_error_contract(kind_index)
        if kind_index == 0:
            reference_count = _validated_reference_count(reference_count)
        elif reference_count is not None:
            raise TypeError('非引用冲突错误不能携带引用数')
        object.__setattr__(self, '_kind_index', kind_index)
        object.__setattr__(self, '_reference_count', reference_count)
        object.__setattr__(self, 'args', ())

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise AttributeError('ModelEngineServiceError 实例不可修改')

    def __delattr__(self, name: str) -> NoReturn:
        raise AttributeError('ModelEngineServiceError 实例不可修改')

    @property
    def status_code(self) -> int:
        kind_index, _ = _trusted_engine_error_state(self)
        return _engine_error_contract(kind_index)[0]

    @property
    def code(self) -> str:
        kind_index, _ = _trusted_engine_error_state(self)
        return _engine_error_contract(kind_index)[1]

    @property
    def public_message(self) -> str:
        kind_index, reference_count = _trusted_engine_error_state(self)
        if kind_index == 0:
            return f'凭据正被 {reference_count} 个部署引用，不能删除'
        if kind_index == 1:
            return '引擎部署不存在'
        return {
            2: '模型引擎请求校验失败',
            3: '所选连接缺少可确定的服务地址，请新建完整连接',
            4: '所选连接引用了多个服务地址，请新建明确指定地址的连接',
            5: '所选连接的服务地址无效，请检查连接配置',
            6: '所选连接不属于当前服务商，请重新选择连接',
        }[kind_index]

    def __str__(self) -> str:
        return self.public_message

    def __repr__(self) -> str:
        return f'ModelEngineServiceError(code={self.code!r}, message={self.public_message!r})'

    def __reduce__(self) -> tuple[Callable[[int, int | None], 'ModelEngineServiceError'], tuple[int, int | None]]:
        return _restore_model_engine_error, _trusted_engine_error_state(self)

    def public_dict(self) -> dict[str, object]:
        kind_index, reference_count = _trusted_engine_error_state(self)
        result: dict[str, object] = {'code': self.code, 'message': self.public_message}
        if kind_index == 0:
            result['referencedDeploymentCount'] = reference_count
        return result

    @staticmethod
    def credential_in_use(count: int) -> 'ModelEngineServiceError':
        return _new_model_engine_error(0, _validated_reference_count(count))

    @staticmethod
    def deployment_not_found() -> 'ModelEngineServiceError':
        return _new_model_engine_error(1)

    @staticmethod
    def invalid_request() -> 'ModelEngineServiceError':
        return _new_model_engine_error(2)

    @staticmethod
    def connection_missing() -> 'ModelEngineServiceError':
        return _new_model_engine_error(3)

    @staticmethod
    def connection_conflict() -> 'ModelEngineServiceError':
        return _new_model_engine_error(4)

    @staticmethod
    def connection_invalid() -> 'ModelEngineServiceError':
        return _new_model_engine_error(5)

    @staticmethod
    def provider_mismatch() -> 'ModelEngineServiceError':
        return _new_model_engine_error(6)


class LiteLLMDeploymentReferenceQuery:
    """每次调用都从 LiteLLM 当前 Deployment 列表重算凭据引用。"""

    def __init__(
        self,
        client: ManagementClient | None,
        adapter: LiteLLMManagementAdapter,
    ) -> None:
        self._client = client
        self._adapter = adapter

    async def reference_counts(self) -> Mapping[str, int]:
        client = self._client
        if client is None or not _management_configured(client):
            raise LiteLLMManagementMisconfiguredError
        payload = await client.list_all_deployments()
        try:
            deployments = self._adapter.normalize_deployments(payload)
        finally:
            del payload
        counts: dict[str, int] = {}
        for deployment in deployments:
            if deployment.credential_name is not None:
                counts[deployment.credential_name] = counts.get(deployment.credential_name, 0) + 1
        return counts


class ModelEngineService:
    """编排 Platform 的 Engine 中立控制面操作。"""

    def __init__(
        self,
        client: ManagementClient | None,
        adapter: LiteLLMManagementAdapter,
        deployment_reference_query: DeploymentReferenceQuery,
        *,
        runtime_client: RuntimeHealthClient | None = None,
        clock: Clock | None = None,
        timer: Timer | None = None,
    ) -> None:
        self._client = client
        self._adapter = adapter
        self._deployment_reference_query = deployment_reference_query
        self._runtime_client = runtime_client
        self._clock = clock or _utc_now
        self._timer = timer or perf_counter

    async def list_providers(self) -> list[ProviderModel]:
        client = self._require_client()
        return await _load_providers(client, self._adapter)

    async def list_credentials(self) -> list[CredentialSummaryModel]:
        client = self._require_configured_client()
        providers = await _load_providers(client, self._adapter)
        credentials, counts = await asyncio.gather(
            _load_connection_credentials(client, providers),
            self._deployment_reference_query.reference_counts(),
        )
        return [
            credential.model_copy(update={'referenced_deployment_count': counts.get(credential.credential_name, 0)})
            for credential in credentials
        ]

    async def create_credential(self, request: CredentialCreateModel) -> None:
        client = self._require_configured_client()
        providers = await _load_providers(client, self._adapter)
        payload = self._build_payload(self._adapter.build_credential_create_payload, request, providers)
        operation = client.create_credential(payload)
        del request, providers, payload
        await operation

    async def rotate_credential(
        self,
        credential_name: str,
        request: CredentialRotateModel,
    ) -> None:
        client = self._require_configured_client()
        providers = await _load_providers(client, self._adapter)
        payload = self._build_payload(
            self._adapter.build_credential_rotate_payload,
            credential_name,
            request,
            providers,
        )
        operation = client.rotate_credential(credential_name, payload)
        del credential_name, request, providers, payload
        await operation

    async def delete_credential(self, credential_name: str) -> None:
        client = self._require_configured_client()
        counts = await self._deployment_reference_query.reference_counts()
        count = counts.get(credential_name, 0)
        if count > 0:
            raise ModelEngineServiceError.credential_in_use(count)
        await client.delete_credential(credential_name)

    async def list_deployments(self, *, model_alias: str | None = None) -> list[DeploymentModel]:
        client = self._require_configured_client()
        return await _load_deployments(client, self._adapter, model_alias=model_alias)

    async def get_deployment(self, deployment_id: str) -> DeploymentModel:
        deployments = await self.list_deployments()
        deployment = next((item for item in deployments if item.deployment_id == deployment_id), None)
        if deployment is None:
            raise ModelEngineServiceError.deployment_not_found()
        return deployment

    async def create_deployment(self, request: DeploymentCreateModel) -> DeploymentModel:
        client = self._require_configured_client()
        providers = await _load_providers(client, self._adapter)
        profile = await self._saved_connection(request.credential_name, request.provider, providers)
        payload = self._build_payload(self._adapter.build_deployment_create_payload, request, providers)
        _apply_connection_address(payload, profile)
        payload = prepare_rerank_payload(payload)
        operation = client.create_deployment(payload)
        del payload
        result = await operation
        try:
            return self._adapter.normalize_created_deployment(result, request, providers)
        finally:
            del result, request, providers

    async def update_deployment(
        self,
        deployment_id: str,
        request: DeploymentUpdateModel,
    ) -> None:
        client = self._require_configured_client()
        providers: Sequence[ProviderModel] = ()
        profile = None
        current_type = None
        current_provider = None
        if {'provider', 'credential_name'} & request.model_fields_set:
            providers = await _load_providers(client, self._adapter)
            profile, current_type, current_provider = await self._updated_connection(deployment_id, request, providers)
        payload = self._build_payload(self._adapter.build_deployment_update_payload, request, providers)
        if profile is not None:
            _apply_connection_address(payload, profile)
            if current_type == 'rerank':
                payload['litellm_params'].setdefault('custom_llm_provider', current_provider)
                payload = prepare_rerank_payload(payload, model_type=current_type)
        operation = client.update_deployment(deployment_id, payload)
        del deployment_id, request, providers, payload
        await operation

    async def delete_deployment(self, deployment_id: str) -> None:
        client = self._require_configured_client()
        await client.delete_deployment(deployment_id)

    async def test_connection(self, request: ConnectionTestRequestModel) -> ConnectionTestResultModel:
        client = self._require_configured_client()
        providers = await _load_providers(client, self._adapter)
        profile = None
        if request.credential_name is not None:
            profile = await self._saved_connection(request.credential_name, request.provider, providers)
            _validate_saved_values(request, profile)
        payload = self._build_payload(self._adapter.build_connection_test_payload, request, providers)
        if profile is not None:
            _apply_connection_address(payload, profile)
        payload = prepare_connection_probe(prepare_rerank_payload(payload))
        started_at = self._timer()
        operation = client.test_connection(payload)
        del request, providers, payload
        result = await operation
        latency_ms = _elapsed_ms(started_at, self._timer())
        try:
            return self._adapter.normalize_connection_test_result(result, latency_ms=latency_ms)
        finally:
            del result

    async def _saved_connection(
        self,
        credential_name: str,
        provider_name: str,
        providers: Sequence[ProviderModel],
    ) -> ConnectionProfileModel:
        credentials = await _load_connection_credentials(self._require_configured_client(), providers)
        profile = _require_connection_profile(_credential_profile(credentials, credential_name))
        _validate_connection_provider(profile, provider_name, providers)
        return profile

    async def _updated_connection(
        self,
        deployment_id: str,
        request: DeploymentUpdateModel,
        providers: Sequence[ProviderModel],
    ) -> tuple[ConnectionProfileModel, str | None, str]:
        current, credentials, old_profile, has_explicit_address, model_type = await _load_connection_update_context(
            self._require_configured_client(),
            self._adapter,
            deployment_id,
            providers,
        )
        credential_name = request.credential_name or current.credential_name
        profile = _require_connection_profile(_credential_profile(credentials, credential_name))
        provider_name = request.provider
        if provider_name is None:
            previous = _require_connection_profile(old_profile)
            specific_ids = [value for value in previous.provider_ids if value != 'CUSTOM_OPENAI']
            if not specific_ids and previous.api_base == profile.api_base:
                specific_ids = ['CUSTOM_OPENAI']
            if len(specific_ids) != 1:
                raise ModelEngineServiceError.provider_mismatch()
            provider_name = specific_ids[0]
        _validate_connection_provider(profile, provider_name, providers)
        # LiteLLM PATCH excludes None; it cannot clear an old api_base with a null value.
        if profile.api_base is None and has_explicit_address:
            raise ModelEngineServiceError.connection_missing()
        return profile, model_type, current.provider

    async def get_status(self) -> ModelEngineStatusModel:
        started_at = self._timer()
        checked_at = self._clock()
        runtime_configured = _runtime_configured(self._runtime_client)
        management_configured = self._client is not None and _management_configured(self._client)

        checks: dict[str, Awaitable[tuple[bool, object | None]]] = {}
        if runtime_configured:
            checks['runtime'] = _capture_liveliness(self._runtime_client.check_liveliness())  # type: ignore[union-attr]
        if management_configured:
            client = self._client
            if client is None:
                raise AssertionError('不可达')
            checks.update(
                {
                    'management': _capture_liveliness(client.check_liveliness()),
                    'deployments': _capture_check(_deployment_count(client, self._adapter)),
                    'credentials': _capture_check(_credential_count(client, self._adapter)),
                }
            )

        outcomes = dict(zip(checks, await asyncio.gather(*checks.values()), strict=True)) if checks else {}
        runtime_available = _check_succeeded(outcomes.get('runtime'))
        management_available = management_configured and all(
            _check_succeeded(outcomes.get(name)) for name in ('management', 'deployments', 'credentials')
        )
        return ModelEngineStatusModel(
            runtime_configured=runtime_configured,
            runtime_available=runtime_available,
            management_configured=management_configured,
            management_available=management_available,
            latency_ms=_elapsed_ms(started_at, self._timer()),
            last_checked_at=checked_at,
            deployment_count=_check_value(outcomes.get('deployments'), int),
            credential_count=_check_value(outcomes.get('credentials'), int),
        )

    def _require_client(self) -> ManagementClient:
        if self._client is None:
            raise LiteLLMManagementMisconfiguredError
        return self._client

    def _require_configured_client(self) -> ManagementClient:
        client = self._require_client()
        if not _management_configured(client):
            raise LiteLLMManagementMisconfiguredError
        return client

    @staticmethod
    def _build_payload(builder: Callable[..., dict[str, object]], *args: object) -> dict[str, object]:
        try:
            return builder(*args)
        except ValueError:
            raise ModelEngineServiceError.invalid_request() from None


def _credential_profile(
    credentials: Sequence[CredentialSummaryModel],
    credential_name: str | None,
) -> ConnectionProfileModel | None:
    credential = next((item for item in credentials if item.credential_name == credential_name), None)
    return credential.connection_profile if credential is not None else None


def _require_connection_profile(profile: ConnectionProfileModel | None) -> ConnectionProfileModel:
    if profile is None or profile.status == 'missing':
        raise ModelEngineServiceError.connection_missing()
    if profile.status == 'conflict':
        raise ModelEngineServiceError.connection_conflict()
    if profile.status == 'invalid':
        raise ModelEngineServiceError.connection_invalid()
    return profile


def _validate_connection_provider(
    profile: ConnectionProfileModel,
    provider_name: str,
    providers: Sequence[ProviderModel],
) -> None:
    selected = [item for item in providers if item.provider == provider_name]
    if not selected:
        selected = [item for item in providers if item.litellm_provider == provider_name]
    if len(selected) != 1 or selected[0].provider not in profile.provider_ids:
        raise ModelEngineServiceError.provider_mismatch()


async def _load_connection_update_context(
    client: ManagementClient,
    adapter: LiteLLMManagementAdapter,
    deployment_id: str,
    providers: Sequence[ProviderModel],
) -> tuple[DeploymentModel, list[CredentialSummaryModel], ConnectionProfileModel, bool, str | None]:
    deployments_payload = await client.list_all_deployments()
    credentials_payload = None
    parameters = None
    row = None
    try:
        deployments = adapter.normalize_deployments(deployments_payload)
        index = next((index for index, item in enumerate(deployments) if item.deployment_id == deployment_id), None)
        if index is None:
            raise ModelEngineServiceError.deployment_not_found()
        current = deployments[index]
        credentials_payload = await client.list_credentials()
        credentials = resolve_credential_connections(credentials_payload, deployments_payload, providers)
        row = deployments_payload.get('data', deployments_payload.get('items'))[index]
        parameters = restore_connection_parameters(row['litellm_params'], row['model_info'])
        mode = row['model_info'].get('mode')
        model_type = mode if isinstance(mode, str) and mode in {'chat', 'embedding', 'rerank'} else None
        profile, has_explicit_address = resolve_deployment_connection(
            current,
            parameters,
            _credential_profile(credentials, current.credential_name),
            providers,
        )
        return current, credentials, profile, has_explicit_address, model_type
    finally:
        del deployments_payload, credentials_payload, parameters, row


def _validate_saved_values(request: ConnectionTestRequestModel, profile: ConnectionProfileModel) -> None:
    # Saved connection tests may repeat the resolved address, but cannot replace saved authentication.
    invalid = any(key != 'api_base' for key in request.values)
    if 'api_base' in request.values:
        supplied_base = safe_api_base(request.values['api_base'].get_secret_value())
        invalid = invalid or supplied_base is None or supplied_base != profile.api_base
    del request, profile
    if invalid:
        raise ModelEngineServiceError.connection_conflict()


def _apply_connection_address(payload: dict[str, object], profile: ConnectionProfileModel) -> None:
    if profile.api_base is not None:
        payload['litellm_params']['api_base'] = profile.api_base


async def _load_connection_credentials(
    client: ManagementClient,
    providers: Sequence[ProviderModel],
) -> list[CredentialSummaryModel]:
    deployments_payload = await client.list_all_deployments()
    credentials_payload = None
    try:
        credentials_payload = await client.list_credentials()
        return resolve_credential_connections(credentials_payload, deployments_payload, providers)
    finally:
        del credentials_payload, deployments_payload


async def _load_providers(
    client: ManagementClient,
    adapter: LiteLLMManagementAdapter,
) -> list[ProviderModel]:
    payload = await client.list_provider_fields()
    try:
        return adapter.normalize_provider_fields(payload)
    finally:
        del payload


async def _load_credentials(
    client: ManagementClient,
    adapter: LiteLLMManagementAdapter,
) -> list[CredentialSummaryModel]:
    payload = await client.list_credentials()
    try:
        try:
            return adapter.normalize_credentials(payload)
        except LiteLLMManagementUpstreamInvalidError:
            deployments = await _load_deployments(client, adapter)
            provider_by_credential = _unique_credential_providers(deployments)
            return adapter.normalize_credentials(payload, provider_by_credential=provider_by_credential)
    finally:
        del payload


def _unique_credential_providers(deployments: Sequence[DeploymentModel]) -> dict[str, str]:
    providers: dict[str, set[str]] = {}
    for deployment in deployments:
        if deployment.credential_name:
            providers.setdefault(deployment.credential_name, set()).add(deployment.provider)
    return {name: next(iter(values)) for name, values in providers.items() if len(values) == 1}


async def _load_deployments(
    client: ManagementClient,
    adapter: LiteLLMManagementAdapter,
    *,
    model_alias: str | None = None,
) -> list[DeploymentModel]:
    payload = await client.list_deployments(model_alias=model_alias) if model_alias else await client.list_deployments()
    try:
        deployments = adapter.normalize_deployments(payload)
        return [item for item in deployments if item.model_alias == model_alias] if model_alias else deployments
    finally:
        del payload


async def _deployment_count(client: ManagementClient, adapter: LiteLLMManagementAdapter) -> int:
    return len(await _load_deployments(client, adapter))


async def _credential_count(client: ManagementClient, adapter: LiteLLMManagementAdapter) -> int:
    return len(await _load_credentials(client, adapter))


async def _capture_check(operation: Awaitable[ResultT]) -> tuple[bool, ResultT | None]:
    try:
        return True, await operation
    except Exception:
        return False, None


async def _capture_liveliness(operation: Awaitable[Mapping[str, object]]) -> tuple[bool, None]:
    payload: Mapping[str, object] | None = None
    try:
        payload = await operation
        status = payload.get('status') if all(isinstance(key, str) for key in payload) else None
        return type(status) is str and status in _HEALTHY_LIVELINESS_STATUSES, None
    except Exception:
        return False, None
    finally:
        del payload


def _management_configured(client: ManagementClient) -> bool:
    return bool(getattr(client.settings, 'management_configured', False))


def _runtime_configured(client: RuntimeHealthClient | None) -> bool:
    return client is not None and bool(getattr(client.settings, 'configured', False))


def _check_succeeded(outcome: tuple[bool, object | None] | None) -> bool:
    return outcome is not None and outcome[0]


def _check_value(
    outcome: tuple[bool, object | None] | None,
    value_type: type[ResultT],
) -> ResultT | None:
    if not _check_succeeded(outcome):
        return None
    value = outcome[1]
    return value if type(value) is value_type else None


def _elapsed_ms(started_at: float, finished_at: float) -> float:
    return round(max(0.0, finished_at - started_at) * 1000, 2)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
