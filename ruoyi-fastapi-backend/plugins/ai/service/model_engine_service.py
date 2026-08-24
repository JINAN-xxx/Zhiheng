import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timezone
from time import perf_counter
from types import MappingProxyType
from typing import ClassVar, NoReturn, Protocol, TypeVar

from plugins.ai.entity.vo.ai_model_engine_vo import (
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
from plugins.ai.infrastructure.litellm_management_adapter import LiteLLMManagementAdapter
from plugins.ai.infrastructure.litellm_management_errors import (
    LiteLLMManagementMisconfiguredError,
    LiteLLMManagementUpstreamInvalidError,
)

ResultT = TypeVar('ResultT')
Clock = Callable[[], datetime]
Timer = Callable[[], float]
_MAX_REFERENCE_COUNT = 10_000_000_000
_ENGINE_ERROR_CONTRACTS = (
    (409, 'MODEL_ENGINE_CREDENTIAL_IN_USE'),
    (404, 'MODEL_ENGINE_DEPLOYMENT_NOT_FOUND'),
    (422, 'MODEL_ENGINE_REQUEST_INVALID'),
)
_ENGINE_ERROR_NAMES = ('credential_in_use', 'deployment_not_found', 'invalid_request')
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

    async def list_deployments(self) -> Mapping[str, object]: ...

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
        return '模型引擎请求校验失败'

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
        deployments = await _load_deployments(client, self._adapter)
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
        credentials, counts = await asyncio.gather(
            _load_credentials(client, self._adapter),
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

    async def list_deployments(self) -> list[DeploymentModel]:
        client = self._require_configured_client()
        return await _load_deployments(client, self._adapter)

    async def get_deployment(self, deployment_id: str) -> DeploymentModel:
        deployments = await self.list_deployments()
        deployment = next((item for item in deployments if item.deployment_id == deployment_id), None)
        if deployment is None:
            raise ModelEngineServiceError.deployment_not_found()
        return deployment

    async def create_deployment(self, request: DeploymentCreateModel) -> DeploymentModel:
        client = self._require_configured_client()
        providers = await _load_providers(client, self._adapter)
        payload = self._build_payload(self._adapter.build_deployment_create_payload, request, providers)
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
        if 'provider' in request.model_fields_set:
            providers = await _load_providers(client, self._adapter)
        payload = self._build_payload(self._adapter.build_deployment_update_payload, request, providers)
        operation = client.update_deployment(deployment_id, payload)
        del deployment_id, request, providers, payload
        await operation

    async def delete_deployment(self, deployment_id: str) -> None:
        client = self._require_configured_client()
        await client.delete_deployment(deployment_id)

    async def test_connection(self, request: ConnectionTestRequestModel) -> ConnectionTestResultModel:
        client = self._require_configured_client()
        providers = await _load_providers(client, self._adapter)
        payload = self._build_payload(self._adapter.build_connection_test_payload, request, providers)
        started_at = self._timer()
        operation = client.test_connection(payload)
        del request, providers, payload
        result = await operation
        latency_ms = _elapsed_ms(started_at, self._timer())
        try:
            return self._adapter.normalize_connection_test_result(result, latency_ms=latency_ms)
        finally:
            del result

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
) -> list[DeploymentModel]:
    payload = await client.list_deployments()
    try:
        return adapter.normalize_deployments(payload)
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
