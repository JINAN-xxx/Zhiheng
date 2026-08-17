import json
import traceback
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx
import pytest

from exceptions.exception import ServiceException
from plugins.ai.config import LiteLLMSettings
from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
from plugins.ai.entity.do.ai_resource_binding_do import AiResourceBinding
from plugins.ai.entity.vo.ai_model_catalog_vo import ModelCatalogCreateModel, ResourceBindingUpsertModel
from plugins.ai.entity.vo.ai_model_engine_vo import (
    ConnectionTestRequestModel,
    CredentialCreateModel,
    DeploymentCreateModel,
)
from plugins.ai.infrastructure.litellm_management_adapter import LiteLLMManagementAdapter
from plugins.ai.infrastructure.litellm_management_client import LiteLLMManagementClient
from plugins.ai.infrastructure.litellm_management_errors import LiteLLMManagementUpstreamError
from plugins.ai.service.model_catalog_service import RUNTIME_ALIAS_MISSING, ModelCatalogService
from plugins.ai.service.model_engine_service import ModelEngineService

NOW = datetime(2026, 8, 17, 10, 30)
PRIVATE_API_KEY = 'private-api-key'
MANAGEMENT_KEY = 'management-secret'
RUNTIME_KEY = 'runtime-secret'
PRIVATE_HOST = 'management-private.invalid'
PRIVATE_BODY_MARKER = 'private-upstream-body'
BAD_GATEWAY_STATUS = 502
TWO_CALLS = 2
THREE_WRITES = 3
SENSITIVE_FAILURE_BODY = {
    'detail': (
        f'{PRIVATE_BODY_MARKER}: {PRIVATE_API_KEY} '
        f'https://{PRIVATE_HOST}/provider?token={RUNTIME_KEY}&management={MANAGEMENT_KEY}'
    )
}
EXPECTED_RETRY_TIMER_VALUES = [10.0, 10.0125, 10.025]
EXPECTED_LATENCY_MS = 12.5

PROVIDER_PAYLOAD = {
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
                },
                {
                    'key': 'organization',
                    'label': 'Organization',
                    'placeholder': 'org-123',
                    'tooltip': None,
                    'required': False,
                    'field_type': 'text',
                    'options': ['org-123', 'org-456'],
                    'default_value': 'org-123',
                },
            ],
        }
    ]
}

CREDENTIAL_REQUEST = {
    'credentialName': 'openai-main',
    'provider': 'OpenAI',
    'values': {'api_key': PRIVATE_API_KEY, 'organization': 'org-123'},
}
CREDENTIAL_BODY = {
    'credential_name': 'openai-main',
    'credential_info': {'custom_llm_provider': 'openai'},
    'credential_values': {'api_key': PRIVATE_API_KEY, 'organization': 'org-123'},
}
DEPLOYMENT_REQUEST = {
    'modelAlias': 'company-chat',
    'upstreamModel': 'openai/gpt-4.1-mini',
    'provider': 'OpenAI',
    'credentialName': 'openai-main',
    'timeout': 30.5,
    'streamTimeout': 45.0,
    'maxRetries': 2,
    'rpm': 100,
    'tpm': 20000,
    'description': 'Primary deployment',
}
DEPLOYMENT_BODY = {
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
CONNECTION_REQUEST = {
    'upstreamModel': 'openai/gpt-4.1-mini',
    'provider': 'OpenAI',
    'credentialName': 'openai-main',
    'values': {},
}
CONNECTION_BODY = {
    'litellm_params': {
        'model': 'openai/gpt-4.1-mini',
        'custom_llm_provider': 'openai',
        'litellm_credential_name': 'openai-main',
    },
    'model_info': {},
}
CATALOG_REQUEST = {
    'modelCode': 'company-chat',
    'displayName': 'Company Chat',
    'modelType': 'chat',
    'capabilities': {'tools': True, 'streaming': True},
    'contextWindow': 128000,
    'maxOutputTokens': 4096,
    'defaultParameters': {'temperature': 0.2, 'maxTokens': 2048},
}
BINDING_REQUEST = {
    'engineResourceId': 'company-chat',
    'bindingStatus': 'active',
    'engineMetadata': {},
}


class TrackingSession:
    def __init__(self) -> None:
        self.transaction_active = False
        self.commit_count = 0
        self.rollback_count = 0
        self.events: list[str] = []
        self._rollback_actions: list[Callable[[], None]] = []

    def touch(self, operation: str) -> None:
        self.transaction_active = True
        self.events.append(f'db:{operation}')

    def on_rollback(self, action: Callable[[], None]) -> None:
        self._rollback_actions.append(action)

    async def commit(self) -> None:
        self.events.append('db:commit')
        self.commit_count += 1
        self._rollback_actions.clear()
        self.transaction_active = False

    async def rollback(self) -> None:
        self.events.append('db:rollback')
        self.rollback_count += 1
        while self._rollback_actions:
            self._rollback_actions.pop()()
        self.transaction_active = False


class InMemoryModelDao:
    def __init__(self, *, create_failures: int = 0) -> None:
        self.models: dict[str, AiModelCatalog] = {}
        self.create_failures = create_failures
        self.create_calls = 0

    async def get_model_by_code(self, db: TrackingSession, model_code: str) -> AiModelCatalog | None:
        db.touch('get_model_by_code')
        return next((model for model in self.models.values() if model.model_code == model_code), None)

    async def create_model(self, db: TrackingSession, model: AiModelCatalog) -> AiModelCatalog:
        db.touch('create_model')
        self.create_calls += 1
        self.models[model.model_id] = model
        db.on_rollback(lambda: self.models.pop(model.model_id, None))
        if self.create_failures:
            self.create_failures -= 1
            raise RuntimeError('catalog create failed')
        return model

    async def get_model_by_id(self, db: TrackingSession, model_id: str) -> AiModelCatalog | None:
        db.touch('get_model_by_id')
        return self.models.get(model_id)

    async def get_model_for_update(self, db: TrackingSession, model_id: str) -> AiModelCatalog | None:
        db.touch('get_model_for_update')
        return self.models.get(model_id)

    async def publish_model_if_state(
        self,
        db: TrackingSession,
        model_id: str,
        *,
        expected_lifecycle_status: str,
        expected_published_at: datetime | None,
        values: dict[str, Any],
    ) -> int:
        db.touch('publish_model_if_state')
        model = self.models.get(model_id)
        if (
            model is None
            or model.lifecycle_status != expected_lifecycle_status
            or model.published_at != expected_published_at
        ):
            return 0
        for key, value in values.items():
            setattr(model, key, value)
        return 1


class InMemoryBindingDao:
    def __init__(self, *, create_failures: int = 0) -> None:
        self.bindings: dict[str, AiResourceBinding] = {}
        self.create_failures = create_failures
        self.create_calls = 0

    async def get_current_binding_for_update(
        self,
        db: TrackingSession,
        resource_id: str,
    ) -> AiResourceBinding | None:
        db.touch('get_current_binding_for_update')
        return self.bindings.get(resource_id)

    async def create_binding(self, db: TrackingSession, binding: AiResourceBinding) -> AiResourceBinding:
        db.touch('create_binding')
        self.create_calls += 1
        self.bindings[binding.resource_id] = binding
        db.on_rollback(lambda: self.bindings.pop(binding.resource_id, None))
        if self.create_failures:
            self.create_failures -= 1
            raise RuntimeError('binding create failed')
        return binding

    async def get_current_binding(
        self,
        db: TrackingSession,
        resource_id: str,
    ) -> AiResourceBinding | None:
        db.touch('get_current_binding')
        return self.bindings.get(resource_id)


class TransactionCheckingRuntime:
    def __init__(self, session: TrackingSession, aliases: set[str]) -> None:
        self._session = session
        self.aliases = aliases
        self.transaction_states: list[bool] = []
        self.calls = 0

    async def list_model_ids(self) -> set[str]:
        self.calls += 1
        self.transaction_states.append(self._session.transaction_active)
        assert not self._session.transaction_active
        self._session.events.append('external:runtime-list')
        return set(self.aliases)

    async def aclose(self) -> None:
        return None


class EmptyDeploymentReferenceQuery:
    async def reference_counts(self) -> dict[str, int]:
        return {}


class MonotonicTimer:
    def __init__(self) -> None:
        self._ticks = 0
        self.values: list[float] = []

    def __call__(self) -> float:
        value = 10.0 + self._ticks * 0.0125
        self._ticks += 1
        self.values.append(value)
        return value


class RecordedLiteLLM:
    def __init__(
        self,
        session: TrackingSession,
        *,
        connection_failures: int = 0,
        credential_failures: int = 0,
        deployment_failures: int = 0,
    ) -> None:
        self._session = session
        self.connection_failures = connection_failures
        self.credential_failures = credential_failures
        self.deployment_failures = deployment_failures
        self.requests: list[dict[str, object]] = []
        self.credentials: dict[str, dict[str, object]] = {}
        self.deployments: dict[str, dict[str, object]] = {}
        self.connection_test_calls = 0
        self.failure_response_bodies: list[dict[str, object]] = []

    async def handle(self, request: httpx.Request) -> httpx.Response:
        assert not self._session.transaction_active
        body = self._validate_request(request)
        authorization = request.headers.get('authorization')
        self.requests.append(
            {
                'method': request.method,
                'path': request.url.path,
                'authorization': authorization,
                'body': body,
                'transactionActive': self._session.transaction_active,
            }
        )
        self._session.events.append(f'external:http:{request.method}:{request.url.path}')

        if request.url.path == '/public/providers/fields':
            assert request.method == 'GET'
            assert authorization is None
            return httpx.Response(200, json=PROVIDER_PAYLOAD)

        assert authorization == f'Bearer {MANAGEMENT_KEY}'
        assert authorization != f'Bearer {RUNTIME_KEY}'
        if request.url.path == '/credentials':
            assert request.method == 'POST'
            assert body == CREDENTIAL_BODY
            if self.credential_failures:
                self.credential_failures -= 1
                return self._sensitive_failure_response()
            self.credentials['openai-main'] = body
            return httpx.Response(200, json={'status': 'success'})
        if request.url.path == '/model/new':
            assert request.method == 'POST'
            assert body == DEPLOYMENT_BODY
            if self.deployment_failures:
                self.deployment_failures -= 1
                return self._sensitive_failure_response()
            self.deployments['deployment-1'] = body
            return httpx.Response(200, json={'model_id': 'deployment-1'})
        if request.url.path == '/health/test_connection':
            assert request.method == 'POST'
            assert body == CONNECTION_BODY
            self.connection_test_calls += 1
            if self.connection_failures:
                self.connection_failures -= 1
                return self._sensitive_failure_response()
            return httpx.Response(200, json={'status': 'success'})
        raise AssertionError(f'unexpected management request: {request.method} {request.url.path}')

    def _sensitive_failure_response(self) -> httpx.Response:
        self.failure_response_bodies.append(SENSITIVE_FAILURE_BODY)
        return httpx.Response(BAD_GATEWAY_STATUS, json=SENSITIVE_FAILURE_BODY)

    @staticmethod
    def _validate_request(request: httpx.Request) -> object:
        assert request.headers['user-agent'] == LiteLLMManagementClient.USER_AGENT
        assert 'request-id' not in request.headers
        assert 'trace-id' not in request.headers
        assert request.url.query == b''

        full_url = str(request.url)
        headers = tuple(request.headers.multi_items())
        assert RUNTIME_KEY not in full_url
        assert MANAGEMENT_KEY not in full_url
        assert PRIVATE_API_KEY not in full_url
        for name, value in headers:
            assert RUNTIME_KEY not in name
            assert RUNTIME_KEY not in value
            assert PRIVATE_API_KEY not in name
            assert PRIVATE_API_KEY not in value

        protected_headers = {
            name: value
            for name, value in headers
            if 'auth' in name.lower() or 'key' in name.lower() or 'secret' in name.lower() or 'token' in name.lower()
        }
        is_public_request = request.url.path == '/public/providers/fields'
        expected_protected_headers = {} if is_public_request else {'authorization': f'Bearer {MANAGEMENT_KEY}'}
        assert protected_headers == expected_protected_headers
        management_key_locations = [
            (name, value) for name, value in headers if MANAGEMENT_KEY in name or MANAGEMENT_KEY in value
        ]
        assert management_key_locations == (
            [] if is_public_request else [('authorization', f'Bearer {MANAGEMENT_KEY}')]
        )

        body = json.loads(request.content) if request.content else None
        serialized_body = json.dumps(body, ensure_ascii=True) if body is not None else ''
        assert RUNTIME_KEY not in serialized_body
        if PRIVATE_API_KEY in serialized_body:
            assert request.url.path == '/credentials'
            assert body == CREDENTIAL_BODY
        return body


class IntegrationHarness:
    def __init__(
        self,
        *,
        binding_create_failures: int = 0,
        connection_failures: int = 0,
        credential_failures: int = 0,
        deployment_failures: int = 0,
        model_create_failures: int = 0,
        runtime_aliases: set[str] | None = None,
    ) -> None:
        self.session = TrackingSession()
        self.litellm = RecordedLiteLLM(
            self.session,
            connection_failures=connection_failures,
            credential_failures=credential_failures,
            deployment_failures=deployment_failures,
        )
        settings = LiteLLMSettings(
            LITELLM_BASE_URL=f'https://{PRIVATE_HOST}',
            LITELLM_RUNTIME_KEY=RUNTIME_KEY,
            LITELLM_MANAGEMENT_KEY=MANAGEMENT_KEY,
        )
        self.client = LiteLLMManagementClient(settings, transport=httpx.MockTransport(self.litellm.handle))
        self.timer = MonotonicTimer()
        self.engine = ModelEngineService(
            self.client,
            LiteLLMManagementAdapter(),
            EmptyDeploymentReferenceQuery(),
            timer=self.timer,
        )
        self.model_dao = InMemoryModelDao(create_failures=model_create_failures)
        self.binding_dao = InMemoryBindingDao(create_failures=binding_create_failures)
        self.runtime = TransactionCheckingRuntime(self.session, runtime_aliases or set())
        self.catalog = ModelCatalogService(
            self.session,  # type: ignore[arg-type]
            self.runtime,
            model_dao=self.model_dao,  # type: ignore[arg-type]
            binding_dao=self.binding_dao,  # type: ignore[arg-type]
        )

    async def close(self) -> None:
        await self.client.aclose()


async def create_engine_resources(harness: IntegrationHarness) -> tuple[object, object]:
    await harness.engine.create_credential(CredentialCreateModel.model_validate(CREDENTIAL_REQUEST))
    deployment = await harness.engine.create_deployment(DeploymentCreateModel.model_validate(DEPLOYMENT_REQUEST))
    connection = await harness.engine.test_connection(ConnectionTestRequestModel.model_validate(CONNECTION_REQUEST))
    return deployment, connection


async def create_platform_resources(harness: IntegrationHarness) -> tuple[object, object]:
    model = await create_platform_model(harness)
    binding = await create_platform_binding(harness, model.model_id)
    return model, binding


async def create_platform_binding(harness: IntegrationHarness, model_id: str) -> object:
    return await harness.catalog.upsert_binding(
        model_id,
        ResourceBindingUpsertModel.model_validate(BINDING_REQUEST),
        username='admin',
        now=NOW,
    )


async def create_platform_model(harness: IntegrationHarness) -> object:
    model = await harness.catalog.create_model(
        ModelCatalogCreateModel.model_validate(CATALOG_REQUEST),
        username='admin',
        now=NOW,
    )
    return model


def assert_stable_sanitized_error(error: LiteLLMManagementUpstreamError) -> None:
    assert error.status_code == BAD_GATEWAY_STATUS
    assert error.code == 'LITELLM_MANAGEMENT_UPSTREAM_ERROR'
    assert str(error) == 'LiteLLM 管理面上游调用失败'
    assert error.__cause__ is None
    assert error.__context__ is None

    forbidden = (PRIVATE_API_KEY, MANAGEMENT_KEY, RUNTIME_KEY, PRIVATE_HOST, PRIVATE_BODY_MARKER, 'detail')
    serialized = repr(
        (
            str(error),
            repr(error),
            vars(error),
            error.args,
            ''.join(traceback.format_exception(error)),
        )
    ).lower()
    for value in forbidden:
        assert value.lower() not in serialized

    current = error.__traceback__
    while current is not None:
        filename = current.tb_frame.f_code.co_filename.replace('\\', '/')
        if '/plugins/ai/' in filename and '/tests/' not in filename:
            for local in current.tb_frame.f_locals.values():
                local_repr = repr(local).lower()
                for value in forbidden:
                    assert value.lower() not in local_repr
                assert not isinstance(local, (httpx.Request, httpx.Response))
        current = current.tb_next


@pytest.mark.asyncio
async def test_full_control_plane_flow_uses_exact_management_contract_and_safe_snapshots() -> None:
    harness = IntegrationHarness(runtime_aliases={'company-chat'})
    try:
        deployment, connection = await create_engine_resources(harness)
        model, binding = await create_platform_resources(harness)
        published = await harness.catalog.publish_model(model.model_id, username='admin', now=NOW)

        assert deployment.model_dump(by_alias=True, mode='json') == {
            'deploymentId': 'deployment-1',
            'modelAlias': 'company-chat',
            'provider': 'openai',
            'upstreamModel': 'openai/gpt-4.1-mini',
            'credentialName': 'openai-main',
            'status': 'unknown',
            'latencyMs': None,
            'blocked': False,
            'createdAt': None,
            'updatedAt': None,
            'timeout': 30.5,
            'streamTimeout': 45.0,
            'maxRetries': 2,
            'rpm': 100,
            'tpm': 20000,
            'description': 'Primary deployment',
        }
        assert connection.model_dump(by_alias=True, mode='json') == {
            'success': True,
            'latencyMs': EXPECTED_LATENCY_MS,
            'message': '连接测试成功',
        }
        assert model.model_dump(by_alias=True, mode='json') == {
            'modelId': model.model_id,
            'modelCode': 'company-chat',
            'displayName': 'Company Chat',
            'modelType': 'chat',
            'capabilities': {'vision': False, 'reasoning': False, 'tools': True, 'streaming': True},
            'contextWindow': 128000,
            'maxOutputTokens': 4096,
            'defaultParameters': {
                'temperature': 0.2,
                'topP': None,
                'maxTokens': 2048,
                'presencePenalty': None,
                'frequencyPenalty': None,
            },
            'lifecycleStatus': 'draft',
            'publishedAt': None,
            'createBy': 'admin',
            'createTime': '2026-08-17T10:30:00',
            'updateBy': 'admin',
            'updateTime': '2026-08-17T10:30:00',
        }
        assert binding.model_dump(by_alias=True, mode='json') == {
            'bindingId': binding.binding_id,
            'resourceId': model.model_id,
            'engineResourceId': 'company-chat',
            'bindingStatus': 'active',
            'engineMetadata': {},
            'version': 1,
            'createTime': '2026-08-17T10:30:00',
            'updateTime': '2026-08-17T10:30:00',
        }
        assert published.model_dump(by_alias=True, mode='json') == {
            **model.model_dump(by_alias=True, mode='json'),
            'lifecycleStatus': 'active',
            'publishedAt': '2026-08-17T10:30:00',
        }
        assert harness.litellm.credentials == {'openai-main': CREDENTIAL_BODY}
        assert harness.litellm.deployments == {'deployment-1': DEPLOYMENT_BODY}
        assert harness.litellm.connection_test_calls == 1
        assert harness.litellm.requests == [
            {
                'method': 'GET',
                'path': '/public/providers/fields',
                'authorization': None,
                'body': None,
                'transactionActive': False,
            },
            {
                'method': 'POST',
                'path': '/credentials',
                'authorization': f'Bearer {MANAGEMENT_KEY}',
                'body': CREDENTIAL_BODY,
                'transactionActive': False,
            },
            {
                'method': 'GET',
                'path': '/public/providers/fields',
                'authorization': None,
                'body': None,
                'transactionActive': False,
            },
            {
                'method': 'POST',
                'path': '/model/new',
                'authorization': f'Bearer {MANAGEMENT_KEY}',
                'body': DEPLOYMENT_BODY,
                'transactionActive': False,
            },
            {
                'method': 'GET',
                'path': '/public/providers/fields',
                'authorization': None,
                'body': None,
                'transactionActive': False,
            },
            {
                'method': 'POST',
                'path': '/health/test_connection',
                'authorization': f'Bearer {MANAGEMENT_KEY}',
                'body': CONNECTION_BODY,
                'transactionActive': False,
            },
        ]
        assert harness.runtime.transaction_states == [False]
        assert harness.session.commit_count == THREE_WRITES
        assert harness.session.rollback_count == 1
        assert not harness.session.transaction_active
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_credential_failure_starts_empty_and_retry_completes_without_compensation() -> None:
    harness = IntegrationHarness(credential_failures=1, runtime_aliases={'company-chat'})
    try:
        with pytest.raises(LiteLLMManagementUpstreamError) as caught:
            await harness.engine.create_credential(CredentialCreateModel.model_validate(CREDENTIAL_REQUEST))

        assert_stable_sanitized_error(caught.value)
        assert harness.litellm.failure_response_bodies == [SENSITIVE_FAILURE_BODY]
        assert harness.litellm.credentials == {}
        assert harness.litellm.deployments == {}
        assert not any(request['method'] == 'DELETE' for request in harness.litellm.requests)

        deployment, _connection = await create_engine_resources(harness)
        model, _binding = await create_platform_resources(harness)
        published = await harness.catalog.publish_model(model.model_id, username='admin', now=NOW)

        assert published.lifecycle_status == 'active'
        assert harness.litellm.credentials == {'openai-main': CREDENTIAL_BODY}
        assert harness.litellm.deployments == {deployment.deployment_id: DEPLOYMENT_BODY}
        assert sum(request['path'] == '/credentials' for request in harness.litellm.requests) == TWO_CALLS
        assert sum(request['path'] == '/model/new' for request in harness.litellm.requests) == 1
        assert not any(request['method'] == 'DELETE' for request in harness.litellm.requests)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_connection_failure_retains_engine_resources_and_retry_does_not_recreate_them() -> None:
    harness = IntegrationHarness(connection_failures=1, runtime_aliases={'company-chat'})
    try:
        await harness.engine.create_credential(CredentialCreateModel.model_validate(CREDENTIAL_REQUEST))
        deployment = await harness.engine.create_deployment(DeploymentCreateModel.model_validate(DEPLOYMENT_REQUEST))

        with pytest.raises(LiteLLMManagementUpstreamError) as caught:
            await harness.engine.test_connection(ConnectionTestRequestModel.model_validate(CONNECTION_REQUEST))

        assert_stable_sanitized_error(caught.value)
        assert harness.litellm.failure_response_bodies == [SENSITIVE_FAILURE_BODY]
        assert harness.litellm.credentials == {'openai-main': CREDENTIAL_BODY}
        assert harness.litellm.deployments == {deployment.deployment_id: DEPLOYMENT_BODY}
        assert not any(request['method'] == 'DELETE' for request in harness.litellm.requests)

        connection = await harness.engine.test_connection(ConnectionTestRequestModel.model_validate(CONNECTION_REQUEST))
        model, _binding = await create_platform_resources(harness)
        published = await harness.catalog.publish_model(model.model_id, username='admin', now=NOW)

        assert connection.success is True
        assert connection.latency_ms == EXPECTED_LATENCY_MS
        assert harness.timer.values == EXPECTED_RETRY_TIMER_VALUES
        assert published.lifecycle_status == 'active'
        assert sum(request['path'] == '/credentials' for request in harness.litellm.requests) == 1
        assert sum(request['path'] == '/model/new' for request in harness.litellm.requests) == 1
        assert sum(request['path'] == '/health/test_connection' for request in harness.litellm.requests) == TWO_CALLS
        assert not any(request['method'] == 'DELETE' for request in harness.litellm.requests)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_catalog_create_failure_retains_engine_resources_and_retry_does_not_recreate_them() -> None:
    harness = IntegrationHarness(model_create_failures=1, runtime_aliases={'company-chat'})
    try:
        deployment, _connection = await create_engine_resources(harness)
        failure_event_start = len(harness.session.events)

        with pytest.raises(RuntimeError, match='catalog create failed'):
            await create_platform_model(harness)

        assert harness.session.events[failure_event_start:] == [
            'db:get_model_by_code',
            'db:create_model',
            'db:rollback',
        ]
        assert harness.session.rollback_count == 1
        assert not harness.session.transaction_active
        assert harness.litellm.credentials == {'openai-main': CREDENTIAL_BODY}
        assert harness.litellm.deployments == {deployment.deployment_id: DEPLOYMENT_BODY}
        assert harness.model_dao.models == {}
        assert not any(request['method'] == 'DELETE' for request in harness.litellm.requests)

        retry_event_start = len(harness.session.events)
        model = await create_platform_model(harness)
        assert harness.session.events[retry_event_start:] == [
            'db:get_model_by_code',
            'db:create_model',
            'db:commit',
        ]
        await create_platform_binding(harness, model.model_id)
        published = await harness.catalog.publish_model(model.model_id, username='admin', now=NOW)

        assert published.lifecycle_status == 'active'
        assert len(harness.model_dao.models) == 1
        assert harness.model_dao.create_calls == TWO_CALLS
        assert sum(request['path'] == '/credentials' for request in harness.litellm.requests) == 1
        assert sum(request['path'] == '/model/new' for request in harness.litellm.requests) == 1
        assert not any(request['method'] == 'DELETE' for request in harness.litellm.requests)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_binding_failure_retains_deployment_and_draft_model_without_recreating_them() -> None:
    harness = IntegrationHarness(binding_create_failures=1, runtime_aliases={'company-chat'})
    try:
        deployment, _connection = await create_engine_resources(harness)
        model = await create_platform_model(harness)
        failure_event_start = len(harness.session.events)

        with pytest.raises(RuntimeError, match='binding create failed'):
            await create_platform_binding(harness, model.model_id)

        assert harness.session.events[failure_event_start:] == [
            'db:get_model_for_update',
            'db:get_current_binding_for_update',
            'db:create_binding',
            'db:rollback',
        ]
        assert harness.session.rollback_count == 1
        assert not harness.session.transaction_active
        assert harness.litellm.deployments == {deployment.deployment_id: DEPLOYMENT_BODY}
        assert harness.model_dao.models[model.model_id].lifecycle_status == 'draft'
        assert harness.binding_dao.bindings == {}
        assert not any(request['method'] == 'DELETE' for request in harness.litellm.requests)

        retry_event_start = len(harness.session.events)
        binding = await create_platform_binding(harness, model.model_id)
        assert harness.session.events[retry_event_start:] == [
            'db:get_model_for_update',
            'db:get_current_binding_for_update',
            'db:create_binding',
            'db:commit',
        ]
        published = await harness.catalog.publish_model(model.model_id, username='admin', now=NOW)

        assert binding.engine_resource_id == deployment.model_alias
        assert published.lifecycle_status == 'active'
        assert harness.model_dao.create_calls == 1
        assert harness.binding_dao.create_calls == TWO_CALLS
        assert sum(request['path'] == '/model/new' for request in harness.litellm.requests) == 1
        assert not any(request['method'] == 'DELETE' for request in harness.litellm.requests)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_deployment_failure_retains_credential_and_retry_never_compensates() -> None:
    harness = IntegrationHarness(deployment_failures=1, runtime_aliases={'company-chat'})
    try:
        await harness.engine.create_credential(CredentialCreateModel.model_validate(CREDENTIAL_REQUEST))
        with pytest.raises(LiteLLMManagementUpstreamError) as caught:
            await harness.engine.create_deployment(DeploymentCreateModel.model_validate(DEPLOYMENT_REQUEST))

        assert_stable_sanitized_error(caught.value)
        assert harness.litellm.credentials == {'openai-main': CREDENTIAL_BODY}
        assert harness.litellm.deployments == {}
        assert not any(request['method'] == 'DELETE' for request in harness.litellm.requests)

        deployment = await harness.engine.create_deployment(DeploymentCreateModel.model_validate(DEPLOYMENT_REQUEST))
        connection = await harness.engine.test_connection(ConnectionTestRequestModel.model_validate(CONNECTION_REQUEST))
        model, _binding = await create_platform_resources(harness)
        published = await harness.catalog.publish_model(model.model_id, username='admin', now=NOW)

        assert deployment.deployment_id == 'deployment-1'
        assert connection.success is True
        assert published.lifecycle_status == 'active'
        assert harness.litellm.credentials == {'openai-main': CREDENTIAL_BODY}
        assert harness.litellm.deployments == {'deployment-1': DEPLOYMENT_BODY}
        assert sum(request['path'] == '/credentials' for request in harness.litellm.requests) == 1
        assert sum(request['path'] == '/model/new' for request in harness.litellm.requests) == TWO_CALLS
        assert not any(request['method'] == 'DELETE' for request in harness.litellm.requests)
    finally:
        await harness.close()


@pytest.mark.asyncio
async def test_publish_failure_retains_deployment_and_retry_never_compensates() -> None:
    harness = IntegrationHarness()
    try:
        deployment, _connection = await create_engine_resources(harness)
        model, binding = await create_platform_resources(harness)

        with pytest.raises(ServiceException) as caught:
            await harness.catalog.publish_model(model.model_id, username='admin', now=NOW)

        assert caught.value.message == RUNTIME_ALIAS_MISSING
        assert harness.litellm.deployments == {deployment.deployment_id: DEPLOYMENT_BODY}
        assert harness.model_dao.models[model.model_id].lifecycle_status == 'draft'
        assert harness.binding_dao.bindings[model.model_id].binding_id == binding.binding_id
        assert not any(request['method'] == 'DELETE' for request in harness.litellm.requests)

        harness.runtime.aliases.add('company-chat')
        published = await harness.catalog.publish_model(model.model_id, username='admin', now=NOW)

        assert published.lifecycle_status == 'active'
        assert published.published_at == NOW
        assert harness.litellm.deployments == {deployment.deployment_id: DEPLOYMENT_BODY}
        assert sum(request['path'] == '/model/new' for request in harness.litellm.requests) == 1
        assert not any(request['method'] == 'DELETE' for request in harness.litellm.requests)
        assert harness.runtime.calls == TWO_CALLS
        assert harness.runtime.transaction_states == [False, False]
        assert not harness.session.transaction_active
    finally:
        await harness.close()
