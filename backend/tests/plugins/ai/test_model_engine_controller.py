import asyncio
import inspect
import json
from collections.abc import Callable, Generator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Response
from fastapi.routing import APIRoute
from starlette.requests import Request
from starlette.status import (
    HTTP_200_OK,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_422_UNPROCESSABLE_CONTENT,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from common.annotation import log_annotation
from common.annotation.log_annotation import Log
from common.aspect.data_scope import GetDataScope
from common.context import RequestContext
from module_admin.entity.vo.user_vo import CurrentUserModel, UserInfoModel
from plugins.ai.controller import ai_model_engine_controller as controller_module
from plugins.ai.controller.ai_model_engine_controller import (
    ai_model_engine_controller,
    create_engine_credential,
    create_engine_deployment,
    delete_engine_credential,
    delete_engine_deployment,
    get_engine_deployment,
    get_model_engine_status,
    list_engine_credentials,
    list_engine_deployments,
    list_engine_providers,
    rotate_engine_credential,
    update_engine_deployment,
)
from plugins.ai.controller.ai_model_engine_controller import (
    test_engine_connection as call_engine_connection,
)
from plugins.ai.entity.vo.ai_model_engine_vo import (
    ConnectionTestRequestModel,
    CredentialCreateModel,
    CredentialRotateModel,
    DeploymentCreateModel,
    DeploymentUpdateModel,
)
from plugins.ai.infrastructure.litellm_errors import LiteLLMUnavailableError
from plugins.ai.infrastructure.litellm_management_adapter import LiteLLMManagementAdapter
from plugins.ai.infrastructure.litellm_management_errors import (
    LiteLLMManagementMisconfiguredError,
    LiteLLMManagementUnavailableError,
)
from plugins.ai.service.model_engine_service import (
    DeploymentReferenceQuery,
    LiteLLMDeploymentReferenceQuery,
    ModelEngineService,
    ModelEngineServiceError,
)

NOW = datetime(2026, 8, 14, 8, 30, tzinfo=timezone.utc)
CREDENTIAL_NAME = 'openai-main'
DEPLOYMENT_ID = 'deployment-1'
EXPECTED_ROUTE_COUNT = 12
REFERENCED_DEPLOYMENTS = 2
FRESH_REFERENCE_QUERY_CALLS = 2


def provider_payload() -> dict[str, object]:
    return {
        'items': [
            {
                'provider': 'OpenAI',
                'provider_display_name': 'OpenAI',
                'litellm_provider': 'openai',
                'default_model_placeholder': 'openai/gpt-4.1-mini',
                'credential_fields': [
                    {
                        'key': 'api_key',
                        'label': 'API Key',
                        'required': True,
                        'field_type': 'password',
                    }
                ],
            }
        ]
    }


def credential_payload(*, upstream_count: int | None = None) -> dict[str, object]:
    item: dict[str, object] = {
        'credential_name': CREDENTIAL_NAME,
        'credential_info': {'custom_llm_provider': 'openai', 'provider': 'OpenAI'},
        'credential_values': {'api_key': 'must-never-leak'},
    }
    if upstream_count is not None:
        item['referenced_deployment_count'] = upstream_count
    return {'credentials': [item]}


def deployment_item(
    deployment_id: str = DEPLOYMENT_ID,
    *,
    credential_name: str | None = CREDENTIAL_NAME,
) -> dict[str, object]:
    params: dict[str, object] = {
        'model': 'openai/gpt-4.1-mini',
        'custom_llm_provider': 'openai',
    }
    if credential_name is not None:
        params['litellm_credential_name'] = credential_name
    return {
        'model_name': 'company-chat',
        'litellm_params': params,
        'model_info': {'id': deployment_id, 'status': 'healthy'},
        'blocked': False,
    }


def deployment_payload(*items: Mapping[str, object]) -> dict[str, object]:
    return {'data': list(items or (deployment_item(),))}


class FakeManagementClient:
    def __init__(
        self,
        *,
        configured: bool = True,
        providers: Mapping[str, object] | None = None,
        credentials: Mapping[str, object] | None = None,
        deployments: Mapping[str, object] | None = None,
    ) -> None:
        self.settings = SimpleNamespace(management_configured=configured)
        self.providers = providers or provider_payload()
        self.credentials = credentials or credential_payload()
        self.deployments = deployments or deployment_payload()
        self.calls: list[tuple[str, object]] = []
        self.errors: dict[str, Exception] = {}
        self.responses: dict[str, Mapping[str, object]] = {}

    async def _result(self, method: str, payload: object = None) -> Mapping[str, object]:
        self.calls.append((method, payload))
        if method in self.errors:
            raise self.errors[method]
        if method in self.responses:
            return self.responses[method]
        if method == 'list_provider_fields':
            return self.providers
        if method == 'list_credentials':
            return self.credentials
        if method == 'list_deployments':
            return self.deployments
        if method == 'create_deployment':
            return {'model_id': 'created-deployment'}
        if method == 'test_connection':
            return {'status': 'success', 'result': {'api_key': 'must-never-leak'}}
        if method == 'check_liveliness':
            return {'status': "I'm alive!"}
        return {'success': True}

    async def list_provider_fields(self) -> Mapping[str, object]:
        return await self._result('list_provider_fields')

    async def list_credentials(self) -> Mapping[str, object]:
        return await self._result('list_credentials')

    async def create_credential(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        return await self._result('create_credential', payload)

    async def rotate_credential(self, name: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        return await self._result('rotate_credential', (name, payload))

    async def delete_credential(self, name: str) -> Mapping[str, object]:
        return await self._result('delete_credential', name)

    async def list_deployments(self, model_alias: str | None = None) -> Mapping[str, object]:
        return await self._result('list_deployments', model_alias)

    async def list_all_deployments(self) -> Mapping[str, object]:
        return await self._result('list_deployments')

    async def create_deployment(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        return await self._result('create_deployment', payload)

    async def update_deployment(self, deployment_id: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        return await self._result('update_deployment', (deployment_id, payload))

    async def delete_deployment(self, deployment_id: str) -> Mapping[str, object]:
        return await self._result('delete_deployment', deployment_id)

    async def test_connection(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        return await self._result('test_connection', payload)

    async def check_liveliness(self) -> Mapping[str, object]:
        return await self._result('check_liveliness')


class FakeRuntimeClient:
    def __init__(
        self,
        *,
        configured: bool = True,
        error: BaseException | None = None,
        response: Mapping[str, object] | None = None,
    ) -> None:
        self.settings = SimpleNamespace(configured=configured)
        self.error = error
        self.response = response if response is not None else {'status': 'healthy'}
        self.calls = 0

    async def check_liveliness(self) -> Mapping[str, object]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.response


class FakeReferenceQuery(DeploymentReferenceQuery):
    def __init__(self, results: list[Mapping[str, int]]) -> None:
        self.results = list(results)
        self.calls = 0

    async def reference_counts(self) -> Mapping[str, int]:
        self.calls += 1
        return self.results.pop(0)


def service(
    client: FakeManagementClient | None,
    *,
    reference_query: DeploymentReferenceQuery | None = None,
    runtime: FakeRuntimeClient | None = None,
) -> ModelEngineService:
    if reference_query is None:
        reference_query = LiteLLMDeploymentReferenceQuery(client, LiteLLMManagementAdapter())
    return ModelEngineService(
        client,
        LiteLLMManagementAdapter(),
        reference_query,
        runtime_client=runtime,
        clock=lambda: NOW,
        timer=lambda: 10.0,
    )


@pytest.mark.asyncio
async def test_service_uses_provider_mapping_and_exact_safe_write_payloads() -> None:
    client = FakeManagementClient()
    engine = service(client)
    credential = CredentialCreateModel.model_validate(
        {'credentialName': CREDENTIAL_NAME, 'provider': 'OpenAI', 'values': {'api_key': 'private-key'}}
    )
    deployment = DeploymentCreateModel.model_validate(
        {
            'modelAlias': 'company-chat',
            'upstreamModel': 'openai/gpt-4.1-mini',
            'provider': 'OpenAI',
            'credentialName': CREDENTIAL_NAME,
            'maxRetries': 2,
        }
    )

    await engine.create_credential(credential)
    created_deployment = await engine.create_deployment(deployment)

    assert client.calls == [
        ('list_provider_fields', None),
        (
            'create_credential',
            {
                'credential_name': CREDENTIAL_NAME,
                'credential_info': {'custom_llm_provider': 'openai', 'provider': 'OpenAI'},
                'credential_values': {'api_key': 'private-key'},
            },
        ),
        ('list_provider_fields', None),
        ('list_deployments', None),
        ('list_credentials', None),
        (
            'create_deployment',
            {
                'model_name': 'company-chat',
                'litellm_params': {
                    'model': 'openai/gpt-4.1-mini',
                    'custom_llm_provider': 'openai',
                    'litellm_credential_name': CREDENTIAL_NAME,
                    'max_retries': 2,
                    'api_base': 'https://api.openai.com/v1',
                },
                'model_info': {},
            },
        ),
    ]
    assert created_deployment.model_dump(by_alias=True, mode='json') == {
        'deploymentId': 'created-deployment',
        'modelAlias': 'company-chat',
        'provider': 'openai',
        'upstreamModel': 'openai/gpt-4.1-mini',
        'credentialName': CREDENTIAL_NAME,
        'status': 'unknown',
        'latencyMs': None,
        'blocked': False,
        'createdAt': None,
        'updatedAt': None,
        'timeout': None,
        'streamTimeout': None,
        'maxRetries': 2,
        'rpm': None,
        'tpm': None,
        'description': None,
    }
    assert 'must-never-leak' not in repr(created_deployment)


@pytest.mark.asyncio
async def test_credential_list_recomputes_references_and_ignores_upstream_count() -> None:
    client = FakeManagementClient(credentials=credential_payload(upstream_count=999))
    references = FakeReferenceQuery([{CREDENTIAL_NAME: REFERENCED_DEPLOYMENTS}])

    result = await service(client, reference_query=references).list_credentials()

    assert len(result) == 1
    assert result[0].referenced_deployment_count == REFERENCED_DEPLOYMENTS
    assert references.calls == 1
    assert 'must-never-leak' not in repr(result)


@pytest.mark.asyncio
async def test_default_reference_query_counts_only_exact_credential_names() -> None:
    client = FakeManagementClient(
        deployments=deployment_payload(
            deployment_item('deployment-1'),
            deployment_item('deployment-2'),
            deployment_item('deployment-3', credential_name='other'),
            deployment_item('deployment-4', credential_name=None),
        )
    )
    query = LiteLLMDeploymentReferenceQuery(client, LiteLLMManagementAdapter())

    assert await query.reference_counts() == {CREDENTIAL_NAME: 2, 'other': 1}


@pytest.mark.asyncio
async def test_credential_delete_uses_fresh_count_and_returns_sanitized_conflict() -> None:
    secret = 'provider-secret-that-must-not-leak'
    client = FakeManagementClient()
    references = FakeReferenceQuery([{}, {CREDENTIAL_NAME: 3}])
    engine = service(client, reference_query=references)

    first = await engine.list_credentials()
    assert first[0].referenced_deployment_count == 0

    with pytest.raises(ModelEngineServiceError) as exc_info:
        await engine.delete_credential(CREDENTIAL_NAME)

    assert exc_info.value.status_code == HTTP_409_CONFLICT
    assert exc_info.value.public_dict() == {
        'code': 'MODEL_ENGINE_CREDENTIAL_IN_USE',
        'message': '凭据正被 3 个部署引用，不能删除',
        'referencedDeploymentCount': 3,
    }
    assert secret not in json.dumps(exc_info.value.public_dict(), ensure_ascii=False)
    assert ('delete_credential', CREDENTIAL_NAME) not in client.calls
    assert references.calls == FRESH_REFERENCE_QUERY_CALLS


@pytest.mark.parametrize(
    ('factory_name', 'status_code', 'code', 'message'),
    (
        ('deployment_not_found', 404, 'MODEL_ENGINE_DEPLOYMENT_NOT_FOUND', '引擎部署不存在'),
        ('invalid_request', 422, 'MODEL_ENGINE_REQUEST_INVALID', '模型引擎请求校验失败'),
        ('connection_missing', 422, 'MODEL_ENGINE_CONNECTION_MISSING', '所选连接缺少可确定的服务地址，请新建完整连接'),
        (
            'connection_conflict',
            409,
            'MODEL_ENGINE_CONNECTION_CONFLICT',
            '所选连接引用了多个服务地址，请新建明确指定地址的连接',
        ),
        ('connection_invalid', 422, 'MODEL_ENGINE_CONNECTION_INVALID', '所选连接的服务地址无效，请检查连接配置'),
        ('provider_mismatch', 422, 'MODEL_ENGINE_PROVIDER_MISMATCH', '所选连接不属于当前服务商，请重新选择连接'),
    ),
)
def test_model_engine_errors_have_fixed_immutable_contracts(
    factory_name: str,
    status_code: int,
    code: str,
    message: str,
) -> None:
    error = getattr(ModelEngineServiceError, factory_name)()

    assert type(error) is ModelEngineServiceError
    assert error.status_code == status_code
    assert error.code == code
    assert error.public_message == message
    assert str(error) == message
    assert error.args == ()
    assert vars(error) == {}
    assert error.public_dict() == {'code': code, 'message': message}
    for field, value in (
        ('status_code', 200),
        ('code', 'ATTACKER_CODE'),
        ('public_message', 'private-secret'),
        ('args', ('private-secret',)),
    ):
        with pytest.raises(AttributeError):
            setattr(error, field, value)
        with pytest.raises(AttributeError):
            delattr(error, field)


def test_credential_in_use_error_validates_count_and_returns_fresh_safe_details() -> None:
    error = ModelEngineServiceError.credential_in_use(REFERENCED_DEPLOYMENTS)

    first = error.public_dict()
    first['message'] = 'polluted-secret'
    assert error.public_dict() == {
        'code': 'MODEL_ENGINE_CREDENTIAL_IN_USE',
        'message': '凭据正被 2 个部署引用，不能删除',
        'referencedDeploymentCount': REFERENCED_DEPLOYMENTS,
    }
    assert 'polluted-secret' not in repr(error)
    for invalid_count in (-1, True, 1.5, '2', None):
        with pytest.raises(TypeError):
            ModelEngineServiceError.credential_in_use(invalid_count)  # type: ignore[arg-type]


def test_model_engine_error_rejects_arbitrary_construction_and_inheritance() -> None:
    secret = 'arbitrary-private-message'

    with pytest.raises(TypeError):
        ModelEngineServiceError(418, 'ATTACKER_CODE', secret, {'secret': secret})  # type: ignore[call-arg]
    with pytest.raises(TypeError):

        class ForgedModelEngineServiceError(ModelEngineServiceError):
            pass


def test_low_level_forged_model_engine_error_is_never_treated_as_canonical() -> None:
    secret = 'forged-private-message'
    forged = ModelEngineServiceError.__new__(ModelEngineServiceError)
    Exception.__init__(forged)
    object.__setattr__(forged, '_kind_index', 0)
    object.__setattr__(forged, '_reference_count', secret)
    object.__setattr__(forged, 'args', (secret,))

    with pytest.raises(TypeError, match='无效的模型引擎错误实例'):
        forged.public_dict()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'operation',
    ('list_credentials', 'list_deployments', 'get_deployment', 'create_credential'),
)
async def test_protected_operations_fail_fast_when_management_is_unconfigured(operation: str) -> None:
    client = FakeManagementClient(configured=False)
    engine = service(client)

    with pytest.raises(LiteLLMManagementMisconfiguredError):
        if operation == 'get_deployment':
            await engine.get_deployment(DEPLOYMENT_ID)
        elif operation == 'create_credential':
            await engine.create_credential(
                CredentialCreateModel.model_validate(
                    {'credentialName': CREDENTIAL_NAME, 'provider': 'OpenAI', 'values': {'api_key': 'secret'}}
                )
            )
        else:
            await getattr(engine, operation)()

    assert client.calls == []


@pytest.mark.asyncio
async def test_public_provider_list_works_without_management_key() -> None:
    client = FakeManagementClient(configured=False)

    providers = await service(client).list_providers()

    assert providers[0].provider == 'OpenAI'
    assert client.calls == [('list_provider_fields', None)]


@pytest.mark.asyncio
async def test_deployment_list_filters_alias_upstream_and_rechecks_normalized_matches() -> None:
    unrelated = {**deployment_item('unrelated'), 'model_name': 'company-chat-other'}
    client = FakeManagementClient(
        deployments=deployment_payload(unrelated, deployment_item('matching-one'), deployment_item('matching-two'))
    )

    deployments = await service(client).list_deployments(model_alias='company-chat')

    assert client.calls == [('list_deployments', 'company-chat')]
    assert [deployment.deployment_id for deployment in deployments] == ['matching-one', 'matching-two']
    assert {deployment.model_alias for deployment in deployments} == {'company-chat'}


@pytest.mark.asyncio
async def test_deployment_list_alias_filter_returns_empty_list_for_no_exact_match() -> None:
    client = FakeManagementClient()

    assert await service(client).list_deployments(model_alias='missing-model') == []
    assert client.calls == [('list_deployments', 'missing-model')]


@pytest.mark.asyncio
async def test_deployment_detail_returns_exact_item_and_not_found_is_stable() -> None:
    engine = service(FakeManagementClient())

    detail = await engine.get_deployment(DEPLOYMENT_ID)
    assert detail.deployment_id == DEPLOYMENT_ID

    with pytest.raises(ModelEngineServiceError) as exc_info:
        await engine.get_deployment('missing-deployment')
    assert exc_info.value.status_code == HTTP_404_NOT_FOUND
    assert exc_info.value.public_dict() == {
        'code': 'MODEL_ENGINE_DEPLOYMENT_NOT_FOUND',
        'message': '引擎部署不存在',
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('model_type', ('chat', 'embedding', 'rerank'))
async def test_connection_test_forwards_selected_type_and_returns_only_fixed_sanitized_result(model_type: str) -> None:
    client = FakeManagementClient()
    request = ConnectionTestRequestModel.model_validate(
        {
            'upstreamModel': 'openai/gpt-4.1-mini',
            'provider': 'OpenAI',
            'modelType': model_type,
            'values': {'api_key': 'connection-secret'},
        }
    )

    result = await service(client).test_connection(request)

    assert result.model_dump(by_alias=True) == {
        'success': True,
        'latencyMs': 0.0,
        'message': '连接测试成功',
        'failureReason': None,
    }
    assert 'connection-secret' not in repr(result)
    assert client.calls[-1] == (
        'test_connection',
        {
            'litellm_params': {
                'model': 'openai/gpt-4.1-mini',
                'custom_llm_provider': 'openai',
                'api_key': 'connection-secret',
            },
            'model_info': {},
            'mode': model_type,
        },
    )


@pytest.mark.asyncio
async def test_connection_test_maps_http_200_error_status_without_upstream_result() -> None:
    client = FakeManagementClient()
    client.responses['test_connection'] = {
        'status': 'error',
        'result': 'provider rejected connection-secret at private-host',
    }
    request = ConnectionTestRequestModel.model_validate(
        {
            'upstreamModel': 'openai/gpt-4.1-mini',
            'provider': 'OpenAI',
            'values': {'api_key': 'connection-secret'},
        }
    )

    result = await service(client).test_connection(request)

    assert result.model_dump(by_alias=True) == {
        'success': False,
        'latencyMs': 0.0,
        'message': '连接测试失败，请检查模型类型、名称和连接配置',
        'failureReason': 'unknown',
    }
    assert 'provider rejected' not in repr(result)
    assert 'connection-secret' not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize('response', ({}, {'status': 'unknown'}, {'status': True}))
async def test_connection_test_rejects_malformed_http_200_status(response: Mapping[str, object]) -> None:
    client = FakeManagementClient()
    client.responses['test_connection'] = response
    request = ConnectionTestRequestModel.model_validate(
        {
            'upstreamModel': 'openai/gpt-4.1-mini',
            'provider': 'OpenAI',
            'credentialName': CREDENTIAL_NAME,
            'values': {},
        }
    )

    with pytest.raises(Exception) as exc_info:
        await service(client).test_connection(request)

    assert getattr(exc_info.value, 'code', None) == 'LITELLM_MANAGEMENT_UPSTREAM_INVALID'


@pytest.mark.asyncio
async def test_failed_onboarding_steps_never_trigger_compensating_deletes() -> None:
    client = FakeManagementClient()
    engine = service(client)
    credential = CredentialCreateModel.model_validate(
        {'credentialName': CREDENTIAL_NAME, 'provider': 'OpenAI', 'values': {'api_key': 'private-key'}}
    )
    deployment = DeploymentCreateModel.model_validate(
        {
            'modelAlias': 'company-chat',
            'upstreamModel': 'openai/gpt-4.1-mini',
            'provider': 'OpenAI',
            'credentialName': CREDENTIAL_NAME,
        }
    )

    await engine.create_credential(credential)
    client.errors['create_deployment'] = LiteLLMManagementUnavailableError()
    with pytest.raises(LiteLLMManagementUnavailableError):
        await engine.create_deployment(deployment)
    assert not any(call[0] == 'delete_credential' for call in client.calls)

    client.errors.pop('create_deployment')
    await engine.create_deployment(deployment)
    with pytest.raises(RuntimeError, match='platform publish failed'):
        raise RuntimeError('platform publish failed')
    assert not any(call[0] == 'delete_deployment' for call in client.calls)


@pytest.mark.asyncio
async def test_status_checks_run_concurrently_and_isolate_each_failure() -> None:
    started: set[str] = set()
    release = asyncio.Event()

    class ConcurrentManagementClient(FakeManagementClient):
        async def _wait(self, name: str, result: Mapping[str, object]) -> Mapping[str, object]:
            started.add(name)
            await asyncio.wait_for(release.wait(), timeout=1)
            if name == 'credentials':
                raise LiteLLMManagementUnavailableError
            return result

        async def check_liveliness(self) -> Mapping[str, object]:
            return await self._wait('management', {'status': 'healthy'})

        async def list_credentials(self) -> Mapping[str, object]:
            return await self._wait('credentials', credential_payload())

        async def list_deployments(self) -> Mapping[str, object]:
            return await self._wait('deployments', deployment_payload())

    class ConcurrentRuntimeClient(FakeRuntimeClient):
        async def check_liveliness(self) -> Mapping[str, object]:
            started.add('runtime')
            await asyncio.wait_for(release.wait(), timeout=1)
            return {'status': 'healthy'}

    engine = service(ConcurrentManagementClient(), runtime=ConcurrentRuntimeClient())
    task = asyncio.create_task(engine.get_status())
    for _ in range(20):
        if started == {'runtime', 'management', 'credentials', 'deployments'}:
            break
        await asyncio.sleep(0)
    assert started == {'runtime', 'management', 'credentials', 'deployments'}
    release.set()
    status = await task

    assert status.runtime_configured is True
    assert status.runtime_available is True
    assert status.management_configured is True
    assert status.management_available is False
    assert status.deployment_count == 1
    assert status.credential_count is None
    assert status.last_checked_at == NOW


@pytest.mark.asyncio
async def test_status_misconfigured_short_circuits_network_and_still_returns_safe_state() -> None:
    management = FakeManagementClient(configured=False)
    runtime = FakeRuntimeClient(configured=False)

    status = await service(management, runtime=runtime).get_status()

    assert status.model_dump(by_alias=True) == {
        'runtimeConfigured': False,
        'runtimeAvailable': False,
        'managementConfigured': False,
        'managementAvailable': False,
        'latencyMs': 0.0,
        'lastCheckedAt': NOW,
        'deploymentCount': None,
        'credentialCount': None,
    }
    assert management.calls == []
    assert runtime.calls == 0


@pytest.mark.asyncio
async def test_status_runtime_failure_does_not_hide_management_results() -> None:
    management = FakeManagementClient()
    runtime = FakeRuntimeClient(error=LiteLLMUnavailableError('private runtime detail'))

    status = await service(management, runtime=runtime).get_status()

    assert status.runtime_configured is True
    assert status.runtime_available is False
    assert status.management_configured is True
    assert status.management_available is True
    assert status.deployment_count == 1
    assert status.credential_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('healthy_status', ("I'm alive!", 'healthy', 'ok'))
async def test_status_accepts_only_explicit_healthy_liveliness_values(healthy_status: str) -> None:
    management = FakeManagementClient()
    management.responses['check_liveliness'] = {'status': healthy_status}
    runtime = FakeRuntimeClient(response={'status': healthy_status})

    status = await service(management, runtime=runtime).get_status()

    assert status.runtime_available is True
    assert status.management_available is True
    assert status.deployment_count == 1
    assert status.credential_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'unhealthy_response',
    (
        {'status': 'error'},
        {'status': 'unhealthy'},
        {'status': 'shutting_down'},
        {'status': 'HEALTHY'},
        {'status': True},
        {'status': None},
        {},
    ),
)
async def test_http_200_unhealthy_or_malformed_liveliness_is_not_available(
    unhealthy_response: Mapping[str, object],
) -> None:
    management = FakeManagementClient()
    management.responses['check_liveliness'] = unhealthy_response
    runtime = FakeRuntimeClient(response=unhealthy_response)

    status = await service(management, runtime=runtime).get_status()

    assert status.runtime_available is False
    assert status.management_available is False
    assert status.deployment_count == 1
    assert status.credential_count == 1
    assert {call[0] for call in management.calls} == {
        'check_liveliness',
        'list_deployments',
        'list_credentials',
    }


@pytest.mark.asyncio
async def test_status_liveliness_cancellation_is_never_downgraded_to_unavailable() -> None:
    runtime = FakeRuntimeClient(error=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        await service(FakeManagementClient(), runtime=runtime).get_status()


def build_request(
    path: str,
    method: str = 'GET',
    *,
    body: bytes = b'',
    path_params: dict[str, str] | None = None,
) -> Request:
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        sent = True
        return {'type': 'http.request', 'body': body, 'more_body': False}

    headers = [(b'user-agent', b'pytest')]
    if body:
        headers.append((b'content-type', b'application/json'))
    return Request(
        {
            'type': 'http',
            'method': method,
            'path': path,
            'headers': headers,
            'path_params': path_params or {},
            'query_string': b'',
            'client': ('127.0.0.1', 12345),
            'app': FastAPI(),
        },
        receive=receive,
    )


def decode_response(response: Response) -> dict[str, Any]:
    return json.loads(response.body)


def current_user() -> CurrentUserModel:
    return CurrentUserModel(
        permissions=['*:*:*'],
        roles=[],
        user=UserInfoModel(userName='engine-admin', dept=None, role=[]),
    )


class FakeControllerService:
    error: Exception | None = None
    calls: list[tuple[str, tuple[object, ...]]] = []

    @classmethod
    def reset(cls) -> None:
        cls.error = None
        cls.calls = []

    async def _result(self, method: str, *args: object) -> object:
        self.calls.append((method, args))
        if self.error is not None:
            raise self.error
        if method in {'list_providers', 'list_credentials', 'list_deployments'}:
            return []
        if method == 'get_status':
            return {
                'runtimeConfigured': False,
                'runtimeAvailable': False,
                'managementConfigured': False,
                'managementAvailable': False,
                'latencyMs': 0,
                'lastCheckedAt': NOW,
                'deploymentCount': None,
                'credentialCount': None,
            }
        if method == 'test_connection':
            return {'success': True, 'latencyMs': 1, 'message': '连接测试成功'}
        if method == 'get_deployment':
            return LiteLLMManagementAdapter.normalize_deployments(deployment_payload())[0]
        if method == 'create_deployment':
            return LiteLLMManagementAdapter.normalize_deployments(
                deployment_payload(deployment_item('created-deployment'))
            )[0]
        return None

    async def list_deployments(self, model_alias: str | None = None) -> object:
        if model_alias is None:
            return await self._result('list_deployments')
        return await self._result('list_deployments', model_alias)

    def __getattr__(self, name: str) -> Callable[..., Any]:
        async def operation(*args: object) -> object:
            return await self._result(name, *args)

        return operation


@pytest.fixture(autouse=True)
def controller_context(monkeypatch: pytest.MonkeyPatch) -> Generator[list[object], None, None]:
    operation_logs: list[object] = []
    user_token = RequestContext.set_current_user(current_user())
    patterns_token = RequestContext.set_current_exclude_patterns([])

    async def capture_operation_log(_request: Request, operation_log: object, _source: str) -> None:
        operation_logs.append(operation_log)

    monkeypatch.setattr(log_annotation.LogQueueService, 'enqueue_operation_log', capture_operation_log)
    FakeControllerService.reset()
    fake_service = FakeControllerService()
    monkeypatch.setattr(controller_module, 'get_model_engine_service', lambda _request: fake_service)
    try:
        yield operation_logs
    finally:
        RequestContext.reset_current_user(user_token)
        RequestContext.reset_current_exclude_patterns(patterns_token)


def route_map() -> dict[tuple[str, str], APIRoute]:
    return {
        (next(iter(route.methods)), route.path): route
        for route in ai_model_engine_controller.routes
        if isinstance(route, APIRoute) and len(route.methods) == 1
    }


def dependency_calls(route: APIRoute) -> list[Callable[..., object]]:
    pending = list(route.dependant.dependencies)
    calls: list[Callable[..., object]] = []
    while pending:
        dependant = pending.pop()
        if dependant.call is not None:
            calls.append(dependant.call)
        pending.extend(dependant.dependencies)
    return calls


def log_decorator(route: APIRoute) -> Log | None:
    closure = route.endpoint.__closure__ or ()
    return next((cell.cell_contents for cell in closure if isinstance(cell.cell_contents, Log)), None)


def test_routes_use_exact_methods_permissions_login_db_session_and_no_data_scope() -> None:
    expected_permissions = {
        ('GET', '/ai/model-engine/providers'): 'ai:engine:credential:list',
        ('GET', '/ai/model-engine/credentials'): 'ai:engine:credential:list',
        ('POST', '/ai/model-engine/credentials'): 'ai:engine:credential:add',
        ('PUT', '/ai/model-engine/credentials/{credential_name}/values'): 'ai:engine:credential:rotate',
        ('DELETE', '/ai/model-engine/credentials/{credential_name}'): 'ai:engine:credential:remove',
        ('GET', '/ai/model-engine/deployments'): 'ai:engine:deployment:list',
        ('GET', '/ai/model-engine/deployments/{deployment_id}'): 'ai:engine:deployment:query',
        ('POST', '/ai/model-engine/deployments'): 'ai:engine:deployment:add',
        ('PATCH', '/ai/model-engine/deployments/{deployment_id}'): 'ai:engine:deployment:edit',
        ('DELETE', '/ai/model-engine/deployments/{deployment_id}'): 'ai:engine:deployment:remove',
        ('POST', '/ai/model-engine/test-connection'): 'ai:model:test',
        ('GET', '/ai/model-engine/status'): 'ai:runtime:query',
    }
    assert len(expected_permissions) == EXPECTED_ROUTE_COUNT
    routes = route_map()
    assert set(routes) == set(expected_permissions)

    for route_key, expected_permission in expected_permissions.items():
        calls = dependency_calls(routes[route_key])
        assert any(type(call).__name__ == 'PreAuth' for call in calls)
        assert [call.perm for call in calls if type(call).__name__ == 'CheckUserInterfaceAuth'] == [expected_permission]
        assert any(getattr(call, '__name__', '') == 'get_db' for call in calls)
        assert not any(isinstance(call, GetDataScope) for call in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('query_alias', 'expected_status', 'expected_alias'),
    (
        (None, HTTP_200_OK, None),
        ('company-chat', HTTP_200_OK, 'company-chat'),
        ('  company-chat  ', HTTP_200_OK, 'company-chat'),
        ('provider/model&region=china', HTTP_200_OK, 'provider/model&region=china'),
        ('', HTTP_422_UNPROCESSABLE_CONTENT, None),
        ('   ', HTTP_422_UNPROCESSABLE_CONTENT, None),
        ('x' * 256, HTTP_422_UNPROCESSABLE_CONTENT, None),
        ('model\nother', HTTP_422_UNPROCESSABLE_CONTENT, None),
        ('model\u2028other', HTTP_422_UNPROCESSABLE_CONTENT, None),
    ),
)
async def test_deployment_list_query_validates_and_forwards_optional_model_alias(
    query_alias: str | None,
    expected_status: int,
    expected_alias: str | None,
) -> None:
    app = FastAPI()
    app.include_router(ai_model_engine_controller)

    def isolated_dependency() -> object:
        return object()

    for dependency in dependency_calls(route_map()[('GET', '/ai/model-engine/deployments')]):
        app.dependency_overrides[dependency] = isolated_dependency

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get(
            '/ai/model-engine/deployments',
            params={} if query_alias is None else {'modelAlias': query_alias},
        )

    assert response.status_code == expected_status
    if expected_status == HTTP_422_UNPROCESSABLE_CONTENT:
        assert FakeControllerService.calls == []
    else:
        assert response.json()['data'] == []
        expected_args = () if expected_alias is None else (expected_alias,)
        assert FakeControllerService.calls == [('list_deployments', expected_args)]


def test_controller_has_no_client_state_adapter_or_litellm_payload_assembly() -> None:
    source = Path(controller_module.__file__).read_text(encoding='utf-8')
    for forbidden in (
        'request.app.state',
        'MANAGEMENT_CLIENT_STATE_NAME',
        'LiteLLMManagementClient',
        'LiteLLMManagementAdapter',
        'litellm_params',
        'credential_values',
        'DataScopeDependency',
    ):
        assert forbidden not in source


def test_sensitive_write_routes_exclude_the_entire_json_body_from_audit() -> None:
    routes = route_map()
    for route_key in (
        ('POST', '/ai/model-engine/credentials'),
        ('PUT', '/ai/model-engine/credentials/{credential_name}/values'),
        ('POST', '/ai/model-engine/test-connection'),
    ):
        decorator = log_decorator(routes[route_key])
        assert decorator is not None
        assert decorator.request_log_mode == 'exclude'
        assert decorator.request_exclude_fields == ('json_body',)


@pytest.mark.asyncio
async def test_controller_maps_management_misconfigured_to_stable_503() -> None:
    FakeControllerService.error = LiteLLMManagementMisconfiguredError()

    with pytest.raises(HTTPException) as exc_info:
        await list_engine_credentials(build_request('/ai/model-engine/credentials'), object())

    assert exc_info.value.status_code == HTTP_503_SERVICE_UNAVAILABLE
    assert exc_info.value.detail == {
        'code': 'LITELLM_MANAGEMENT_MISCONFIGURED',
        'message': 'LiteLLM 管理面尚未完成配置',
    }
    assert exc_info.value.__cause__ is None


@pytest.mark.asyncio
async def test_controller_maps_credential_reference_conflict_with_count_only() -> None:
    FakeControllerService.error = ModelEngineServiceError.credential_in_use(2)

    with pytest.raises(HTTPException) as exc_info:
        await delete_engine_credential(
            build_request(
                f'/ai/model-engine/credentials/{CREDENTIAL_NAME}',
                'DELETE',
                path_params={'credential_name': CREDENTIAL_NAME},
            ),
            CREDENTIAL_NAME,
            object(),
            current_user(),
        )

    assert exc_info.value.status_code == HTTP_409_CONFLICT
    assert exc_info.value.detail == {
        'code': 'MODEL_ENGINE_CREDENTIAL_IN_USE',
        'message': '凭据正被 2 个部署引用，不能删除',
        'referencedDeploymentCount': 2,
    }


@pytest.mark.asyncio
async def test_actual_sensitive_audit_never_contains_credential_values(
    controller_context: list[object],
) -> None:
    secret = 'audit-secret-must-never-appear'
    body = json.dumps({'credentialName': CREDENTIAL_NAME, 'provider': 'OpenAI', 'values': {'api_key': secret}}).encode()
    payload = CredentialCreateModel.model_validate(json.loads(body))

    response = await create_engine_credential(
        build_request('/ai/model-engine/credentials', 'POST', body=body),
        payload,
        object(),
        current_user(),
    )

    assert response.status_code == HTTP_200_OK
    assert len(controller_context) == 1
    operation_log = controller_context[0]
    assert operation_log.oper_param == ''
    assert secret not in operation_log.oper_param
    assert secret not in operation_log.json_result


@pytest.mark.asyncio
async def test_controller_delegates_all_operations_without_building_upstream_payloads() -> None:
    database = object()
    user = current_user()
    credential = CredentialCreateModel.model_validate(
        {'credentialName': CREDENTIAL_NAME, 'provider': 'OpenAI', 'values': {'api_key': 'secret'}}
    )
    rotate = CredentialRotateModel.model_validate({'provider': 'OpenAI', 'values': {'api_key': 'new-secret'}})
    deployment = DeploymentCreateModel.model_validate(
        {
            'modelAlias': 'company-chat',
            'upstreamModel': 'openai/gpt-4.1-mini',
            'provider': 'OpenAI',
            'credentialName': CREDENTIAL_NAME,
        }
    )
    update = DeploymentUpdateModel.model_validate({'blocked': True})
    connection = ConnectionTestRequestModel.model_validate(
        {
            'upstreamModel': 'openai/gpt-4.1-mini',
            'provider': 'OpenAI',
            'credentialName': CREDENTIAL_NAME,
            'values': {},
        }
    )

    await list_engine_providers(build_request('/ai/model-engine/providers'), database)
    await list_engine_credentials(build_request('/ai/model-engine/credentials'), database)
    await create_engine_credential(build_request('/ai/model-engine/credentials', 'POST'), credential, database, user)
    await rotate_engine_credential(
        build_request(
            f'/ai/model-engine/credentials/{CREDENTIAL_NAME}/values',
            'PUT',
            path_params={'credential_name': CREDENTIAL_NAME},
        ),
        CREDENTIAL_NAME,
        rotate,
        database,
        user,
    )
    await delete_engine_credential(
        build_request(
            f'/ai/model-engine/credentials/{CREDENTIAL_NAME}',
            'DELETE',
            path_params={'credential_name': CREDENTIAL_NAME},
        ),
        CREDENTIAL_NAME,
        database,
        user,
    )
    await list_engine_deployments(build_request('/ai/model-engine/deployments'), database)
    await get_engine_deployment(build_request(f'/ai/model-engine/deployments/{DEPLOYMENT_ID}'), DEPLOYMENT_ID, database)
    create_deployment_response = await create_engine_deployment(
        build_request('/ai/model-engine/deployments', 'POST'), deployment, database, user
    )
    await update_engine_deployment(
        build_request(
            f'/ai/model-engine/deployments/{DEPLOYMENT_ID}',
            'PATCH',
            path_params={'deployment_id': DEPLOYMENT_ID},
        ),
        DEPLOYMENT_ID,
        update,
        database,
        user,
    )
    await delete_engine_deployment(
        build_request(
            f'/ai/model-engine/deployments/{DEPLOYMENT_ID}',
            'DELETE',
            path_params={'deployment_id': DEPLOYMENT_ID},
        ),
        DEPLOYMENT_ID,
        database,
        user,
    )
    await call_engine_connection(
        build_request('/ai/model-engine/test-connection', 'POST'),
        connection,
        database,
        user,
    )
    await get_model_engine_status(build_request('/ai/model-engine/status'), database)

    assert [name for name, _args in FakeControllerService.calls] == [
        'list_providers',
        'list_credentials',
        'create_credential',
        'rotate_credential',
        'delete_credential',
        'list_deployments',
        'get_deployment',
        'create_deployment',
        'update_deployment',
        'delete_deployment',
        'test_connection',
        'get_status',
    ]
    assert FakeControllerService.calls[2][1] == (credential,)
    assert FakeControllerService.calls[3][1] == (CREDENTIAL_NAME, rotate)
    assert FakeControllerService.calls[8][1] == (DEPLOYMENT_ID, update)
    assert decode_response(create_deployment_response)['data']['deploymentId'] == 'created-deployment'


def test_service_dependency_contract_is_explicit_and_controller_uses_provider() -> None:
    signature = inspect.signature(ModelEngineService)
    assert list(signature.parameters)[:3] == ['client', 'adapter', 'deployment_reference_query']
    source = Path(controller_module.__file__).read_text(encoding='utf-8')
    assert 'get_model_engine_service(request)' in source
