import copy
import json
from collections.abc import Callable, Mapping, Sequence
from types import SimpleNamespace

import httpx
import pytest

from plugins.ai.config import LiteLLMSettings
from plugins.ai.entity.vo.ai_model_engine_vo import (
    ConnectionTestRequestModel,
    CredentialCreateModel,
    DeploymentCreateModel,
    DeploymentUpdateModel,
)
from plugins.ai.infrastructure.litellm_management_adapter import LiteLLMManagementAdapter
from plugins.ai.infrastructure.litellm_management_client import LiteLLMManagementClient
from plugins.ai.infrastructure.litellm_management_errors import (
    LiteLLMManagementTimeoutError,
    LiteLLMManagementUnavailableError,
    LiteLLMManagementUpstreamInvalidError,
)
from plugins.ai.service.model_engine_service import (
    LiteLLMDeploymentReferenceQuery,
    ModelEngineService,
    ModelEngineServiceError,
)
from tests.plugins.ai.test_litellm_management_client import assert_sanitized_management_error

PAGE_OVERFLOW_COUNT = 51
SILICON = 'https://api.siliconflow.cn/v1'
DEEPSEEK = 'https://api.deepseek.com'
OPENAI = 'https://api.openai.com/v1'
SECRET = 'saved-secret-never-return'
PROVIDERS = [
    ('OpenAI', 'openai'),
    ('OpenAI_Compatible', 'openai'),
    ('CUSTOM_OPENAI', 'openai'),
    ('Deepseek', 'deepseek'),
    ('Anthropic', 'anthropic'),
]


def credential(
    name: str = 'saved', *, api_base: object = None, provider: str | None = None, engine: str = 'openai'
) -> dict[str, object]:
    values = {'api_key': SECRET}
    if api_base is not None:
        values['api_base'] = api_base
    info = {'custom_llm_provider': engine}
    if provider:
        info['provider'] = provider
    return {'credential_name': name, 'credential_values': values, 'credential_info': info}


def deployment(
    api_base: object = None, *, name: str = 'saved', number: int = 1, engine: str = 'openai'
) -> dict[str, object]:
    params = {'model': 'Qwen/example', 'custom_llm_provider': engine, 'litellm_credential_name': name}
    if api_base is not None:
        params['api_base'] = api_base
    return {'model_name': f'alias-{number}', 'model_info': {'id': f'id-{number}'}, 'litellm_params': params}


class Client:
    settings = SimpleNamespace(management_configured=True)

    def __init__(self, credentials: list[dict[str, object]], deployments: Sequence[dict[str, object]] = ()) -> None:
        self.credentials = {'credentials': credentials}
        self.deployments = {'data': list(deployments)}
        self.sent = []
        self.reads: list[str] = []
        self.deployment_error = None

    async def list_provider_fields(self) -> dict[str, object]:
        self.reads.append('providers')
        return {
            'items': [
                {
                    'provider': name,
                    'provider_display_name': name,
                    'litellm_provider': engine,
                    'credential_fields': [
                        {'key': 'api_key', 'label': 'API Key', 'field_type': 'password', 'required': True},
                        {'key': 'api_base', 'label': 'API Base', 'field_type': 'text', 'required': False},
                    ],
                }
                for name, engine in PROVIDERS
            ]
        }

    async def list_credentials(self) -> dict[str, object]:
        self.reads.append('credentials')
        return copy.deepcopy(self.credentials)

    async def list_all_deployments(self) -> dict[str, object]:
        self.reads.append('deployments')
        if self.deployment_error:
            raise self.deployment_error
        return copy.deepcopy(self.deployments)

    async def list_deployments(self, **kwargs: object) -> dict[str, object]:
        return await self.list_all_deployments()

    async def test_connection(self, payload: Mapping[str, object]) -> dict[str, object]:
        self.sent.append(('test', payload))
        return {'status': 'success'}

    async def create_deployment(self, payload: Mapping[str, object]) -> dict[str, object]:
        self.sent.append(('create', payload))
        return {'model_id': 'created'}

    async def update_deployment(self, deployment_id: str, payload: Mapping[str, object]) -> dict[str, object]:
        self.sent.append(('update', payload))
        return {'model_id': deployment_id}

    async def create_credential(self, payload: Mapping[str, object]) -> dict[str, object]:
        self.sent.append(('credential', payload))
        self.credentials['credentials'].append(payload)
        return {'status': 'success'}


def service(client: Client) -> ModelEngineService:
    adapter = LiteLLMManagementAdapter()
    return ModelEngineService(client, adapter, LiteLLMDeploymentReferenceQuery(client, adapter))


def request(
    provider: str = 'OpenAI_Compatible', *, values: dict[str, str] | None = None, saved: bool = True
) -> ConnectionTestRequestModel:
    payload = {'provider': provider, 'upstreamModel': 'deepseek-ai/DeepSeek-V3.2-Pro', 'values': values or {}}
    if saved:
        payload['credentialName'] = 'saved'
    return ConnectionTestRequestModel.model_validate(payload)


@pytest.mark.asyncio
async def test_legacy_saved_connection_reuses_unique_deployment_address_for_list_test_and_create() -> None:
    client = Client([credential('saved')], [deployment(SILICON)])
    original = copy.deepcopy((client.credentials, client.deployments))
    engine = service(client)
    rows = await engine.list_credentials()
    assert rows[0].model_dump(by_alias=True)['connectionProfile'] == {
        'status': 'ready',
        'apiBase': SILICON,
        'providerIds': ['OpenAI_Compatible', 'CUSTOM_OPENAI'],
    }
    assert SECRET not in repr(rows)
    await engine.test_connection(request())
    await engine.create_deployment(
        DeploymentCreateModel.model_validate(
            {
                'provider': 'OpenAI_Compatible',
                'upstreamModel': 'deepseek-ai/DeepSeek-V3.2-Pro',
                'credentialName': 'saved',
                'modelAlias': 'new-model',
            }
        )
    )
    for _, payload in client.sent:
        assert payload['litellm_params']['api_base'] == SILICON
        assert payload['litellm_params']['litellm_credential_name'] == 'saved'
        assert 'api_key' not in payload['litellm_params']
    assert (client.credentials, client.deployments) == original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('api_base', 'allowed'),
    [
        (SILICON, ['OpenAI_Compatible', 'CUSTOM_OPENAI']),
        ('https://api-st.siliconflow.cn/v1', ['OpenAI_Compatible', 'CUSTOM_OPENAI']),
        (DEEPSEEK, ['Deepseek', 'CUSTOM_OPENAI']),
        (DEEPSEEK + '/v1/', ['Deepseek', 'CUSTOM_OPENAI']),
        (OPENAI, ['OpenAI', 'CUSTOM_OPENAI']),
        ('https://custom.example/v2', ['CUSTOM_OPENAI']),
        ('http://localhost:8080/v1', ['CUSTOM_OPENAI']),
        ('https://api.siliconflow.cn.evil.example/v1', ['CUSTOM_OPENAI']),
    ],
)
async def test_explicit_safe_address_defines_allowed_provider_ids(api_base: str, allowed: list[str]) -> None:
    rows = await service(Client([credential(api_base=api_base)])).list_credentials()
    assert rows[0].connection_profile.status == 'ready'
    assert rows[0].connection_profile.provider_ids == allowed
    assert rows[0].connection_profile.api_base == api_base.rstrip('/')


@pytest.mark.asyncio
async def test_explicit_credential_address_has_priority_over_historical_deployments() -> None:
    client = Client([credential(api_base=DEEPSEEK)], [deployment(SILICON)])
    await service(client).test_connection(request('Deepseek'))
    assert client.sent[0][1]['litellm_params']['api_base'] == DEEPSEEK


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('credentials', 'deployments', 'status', 'code'),
    [
        ([credential('saved')], [], 'missing', 'MODEL_ENGINE_CONNECTION_MISSING'),
        (
            [credential('siliconflow-named-but-not-proof'), credential('saved')],
            [],
            'missing',
            'MODEL_ENGINE_CONNECTION_MISSING',
        ),
        (
            [credential('saved')],
            [deployment(SILICON), deployment(DEEPSEEK, number=2)],
            'conflict',
            'MODEL_ENGINE_CONNECTION_CONFLICT',
        ),
        (
            [credential(api_base='https://user:private-password@api.siliconflow.cn/v1')],
            [],
            'invalid',
            'MODEL_ENGINE_CONNECTION_INVALID',
        ),
        (
            [credential(api_base='https://api.siliconflow.cn/v1?key=query-private')],
            [],
            'invalid',
            'MODEL_ENGINE_CONNECTION_INVALID',
        ),
        (
            [credential(api_base='https://api.siliconflow.cn/v1#private-fragment')],
            [],
            'invalid',
            'MODEL_ENGINE_CONNECTION_INVALID',
        ),
        ([credential(api_base='file:///private-address')], [], 'invalid', 'MODEL_ENGINE_CONNECTION_INVALID'),
        ([credential(api_base='not-an-endpoint')], [deployment(SILICON)], 'invalid', 'MODEL_ENGINE_CONNECTION_INVALID'),
        (
            [credential('saved')],
            [deployment('https://user:private-password@host/v1')],
            'invalid',
            'MODEL_ENGINE_CONNECTION_INVALID',
        ),
    ],
)
async def test_unusable_connection_has_safe_profile_and_blocks_both_operations(
    credentials: list[dict[str, object]], deployments: list[dict[str, object]], status: str, code: str
) -> None:
    client = Client(credentials, deployments)
    engine = service(client)
    rows = await engine.list_credentials()
    profile = next(item for item in rows if item.credential_name == 'saved').connection_profile
    assert profile.model_dump(by_alias=True) == {'status': status, 'apiBase': None, 'providerIds': []}
    for operation in (
        engine.test_connection(request()),
        engine.create_deployment(
            DeploymentCreateModel.model_validate(
                {
                    'provider': 'OpenAI_Compatible',
                    'upstreamModel': 'model',
                    'credentialName': 'saved',
                    'modelAlias': 'new',
                }
            )
        ),
    ):
        with pytest.raises(ModelEngineServiceError) as caught:
            await operation
        assert caught.value.code == code
        assert 'private' not in repr(caught.value)
        assert SECRET not in repr(caught.value)
    assert not client.sent


@pytest.mark.asyncio
async def test_cross_provider_reuse_and_saved_endpoint_override_are_rejected() -> None:
    client = Client([credential(api_base=SILICON)])
    engine = service(client)
    for connection_request in (
        request('Deepseek'),
        request('OpenAI'),
        request(values={'api_base': DEEPSEEK}),
        request(values={'api_key': 'replace-saved-key'}),
    ):
        with pytest.raises(ModelEngineServiceError) as caught:
            await engine.test_connection(connection_request)
        assert caught.value.code in {'MODEL_ENGINE_PROVIDER_MISMATCH', 'MODEL_ENGINE_CONNECTION_CONFLICT'}
    assert not client.sent


@pytest.mark.asyncio
async def test_identical_safe_saved_address_may_be_supplied_without_overriding_credentials() -> None:
    client = Client([credential(api_base=SILICON)])
    await service(client).test_connection(request(values={'api_base': SILICON + '/'}))
    assert client.sent[0][1]['litellm_params']['api_base'] == SILICON


@pytest.mark.asyncio
async def test_native_provider_defaults_and_new_explicit_openai_identity_remain_usable() -> None:
    client = Client([credential(engine='anthropic'), credential('official', provider='OpenAI')])
    rows = await service(client).list_credentials()
    assert rows[0].connection_profile.model_dump(by_alias=True) == {
        'status': 'ready',
        'apiBase': None,
        'providerIds': ['Anthropic'],
    }
    assert rows[1].connection_profile.model_dump(by_alias=True) == {
        'status': 'ready',
        'apiBase': OPENAI,
        'providerIds': ['OpenAI', 'CUSTOM_OPENAI'],
    }


@pytest.mark.asyncio
async def test_new_credential_preserves_platform_provider_identity_for_later_resolution() -> None:
    client = Client([])
    engine = service(client)
    await engine.create_credential(
        CredentialCreateModel.model_validate(
            {
                'credentialName': 'official',
                'provider': 'OpenAI',
                'values': {'api_key': SECRET},
            }
        )
    )
    assert client.sent[0][1]['credential_info']['provider'] == 'OpenAI'
    rows = await engine.list_credentials()
    assert rows[0].connection_profile.api_base == OPENAI


@pytest.mark.asyncio
async def test_unsaved_values_can_still_be_tested_without_loading_connections() -> None:
    client = Client([])
    client.deployment_error = AssertionError('temporary connection does not need saved state')
    await service(client).test_connection(request(values={'api_key': 'temporary', 'api_base': SILICON}, saved=False))
    assert client.sent[0][1]['litellm_params']['api_key'] == 'temporary'


@pytest.mark.asyncio
async def test_incomplete_deployment_read_fails_closed_before_testing_saved_connection() -> None:
    client = Client([credential()], [deployment(SILICON)])
    client.deployment_error = LiteLLMManagementUnavailableError()
    with pytest.raises(LiteLLMManagementUnavailableError):
        await service(client).test_connection(request())
    assert not client.sent


def http_client(handler: Callable[[httpx.Request], httpx.Response]) -> LiteLLMManagementClient:
    return LiteLLMManagementClient(
        LiteLLMSettings(LITELLM_BASE_URL='https://management.example', LITELLM_MANAGEMENT_KEY='management-secret'),
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_all_deployments_reads_beyond_fifty_and_includes_late_conflict() -> None:
    pages = []
    items = [deployment(SILICON, number=number) for number in range(51)]
    items[-1]['litellm_params']['api_base'] = DEEPSEEK

    def handler(req: httpx.Request) -> httpx.Response:
        page = int(req.url.params.get('page', '1'))
        size = int(req.url.params.get('size', '50'))
        pages.append(page)
        return httpx.Response(
            200,
            json={
                'data': items[(page - 1) * size : page * size],
                'total_count': 51,
                'current_page': page,
                'total_pages': 2,
            },
        )

    client = http_client(handler)
    try:
        response = await client.list_all_deployments()
    finally:
        await client.aclose()
    assert len(response['data']) == PAGE_OVERFLOW_COUNT
    assert response['data'][-1]['litellm_params']['api_base'] == DEEPSEEK
    assert pages == [1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize('broken', ['repeat', 'empty', 'wrong_total', 'wrong_page'])
async def test_all_deployments_rejects_incomplete_or_inconsistent_pagination(broken: str) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        page = int(req.url.params.get('page', '1'))
        items = [deployment(SILICON, number=number) for number in range(50)]
        if page == 1:
            return httpx.Response(200, json={'data': items, 'total_count': 51, 'current_page': 1, 'total_pages': 2})
        return httpx.Response(
            200,
            json={
                'data': items
                if broken == 'repeat'
                else ([] if broken == 'empty' else [deployment(DEEPSEEK, number=51)]),
                'total_count': 52 if broken == 'wrong_total' else 51,
                'current_page': 1 if broken == 'wrong_page' else page,
                'total_pages': 2,
            },
        )

    client = http_client(handler)
    try:
        with pytest.raises(LiteLLMManagementUpstreamInvalidError):
            await client.list_all_deployments()
    finally:
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('unset', [None, '', '   '])
async def test_unset_address_can_use_unique_reference_or_native_sdk_default(unset: object) -> None:
    legacy = credential()
    legacy['credential_values']['api_base'] = unset
    native = credential('native', engine='anthropic')
    native['credential_values']['api_base'] = unset
    client = Client([legacy, native], [deployment(SILICON), deployment(unset, number=2)])
    rows = await service(client).list_credentials()
    assert rows[0].connection_profile.api_base == SILICON
    assert rows[0].connection_profile.status == 'ready'
    assert rows[1].connection_profile.status == 'ready'
    assert rows[1].connection_profile.api_base is None


@pytest.mark.asyncio
async def test_providerless_legacy_credential_uses_reference_and_orphan_stays_missing() -> None:
    legacy = credential()
    legacy['credential_info'] = {'description': 'not a provider declaration'}
    orphan = credential('orphan')
    orphan['credential_info'] = {}
    rows = await service(Client([legacy, orphan], [deployment(SILICON)])).list_credentials()
    assert rows[0].connection_profile.api_base == SILICON
    assert rows[1].connection_profile.status == 'missing'


@pytest.mark.asyncio
async def test_explicit_native_platform_provider_identity_supports_default_endpoint() -> None:
    native = credential()
    native['credential_info'] = {'provider': 'Anthropic'}
    rows = await service(Client([native])).list_credentials()
    assert rows[0].connection_profile.model_dump(by_alias=True) == {
        'status': 'ready',
        'apiBase': None,
        'providerIds': ['Anthropic'],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'api_base',
    [
        'https://api.openai.com/v1?access_token=private-secret',
        'https://api.openai.com/v1#private-secret',
        'https://user:private-secret@api.openai.com/v1',
        'https://api.openai.com:invalid/v1',
        'https://api.openai.com:0/v1',
        'https://api.openai.com/v1\nprivate-secret',
        '********',
    ],
)
async def test_invalid_native_or_reliably_declared_address_never_falls_back_to_sdk_default(api_base: str) -> None:
    rows = await service(
        Client(
            [
                credential('official', api_base=api_base, provider='OpenAI'),
                credential('native', api_base=api_base, engine='anthropic'),
            ]
        )
    ).list_credentials()
    assert all(row.connection_profile.status == 'invalid' for row in rows)
    serialized = json.dumps([row.model_dump(by_alias=True, mode='json') for row in rows])
    assert 'private-secret' not in serialized
    assert SECRET not in serialized


@pytest.mark.asyncio
async def test_late_page_transport_failure_does_not_return_partial_deployments_or_retain_keys() -> None:

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.params['page'] == '1':
            rows = [deployment(SILICON, number=number) for number in range(50)]
            rows[0]['litellm_params']['api_key'] = 'credential-value-secret'
            return httpx.Response(200, json={'data': rows, 'total_count': 51, 'current_page': 1, 'total_pages': 2})
        raise httpx.ReadTimeout('private-body management-secret at proxy.internal', request=req)

    client = http_client(handler)
    try:
        with pytest.raises(LiteLLMManagementTimeoutError) as caught:
            await client.list_all_deployments()
        assert_sanitized_management_error(caught.value)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_custom_openai_engine_requires_an_explicit_or_inferred_address() -> None:
    client = Client([credential(engine='custom_openai')])
    original_providers = client.list_provider_fields

    async def fields() -> dict[str, object]:
        payload = await original_providers()
        for item in payload['items']:
            if item['provider'] == 'CUSTOM_OPENAI':
                item['litellm_provider'] = 'custom_openai'
        return payload

    client.list_provider_fields = fields
    rows = await service(client).list_credentials()
    assert rows[0].connection_profile.status == 'missing'
    with pytest.raises(ModelEngineServiceError) as caught:
        await service(client).test_connection(request('CUSTOM_OPENAI'))
    assert caught.value.code == 'MODEL_ENGINE_CONNECTION_MISSING'
    assert not client.sent


@pytest.mark.asyncio
async def test_deepseek_platform_identity_still_classifies_explicit_endpoint() -> None:
    rows = await service(Client([credential(engine='Deepseek', api_base=DEEPSEEK)])).list_credentials()
    assert rows[0].connection_profile.provider_ids == ['Deepseek', 'CUSTOM_OPENAI']


@pytest.mark.asyncio
async def test_update_credential_within_same_provider_replaces_the_saved_endpoint() -> None:
    alternate = 'https://api-st.siliconflow.cn/v1'
    client = Client([credential(api_base=SILICON), credential('new', api_base=alternate)], [deployment(SILICON)])
    await service(client).update_deployment('id-1', DeploymentUpdateModel.model_validate({'credentialName': 'new'}))
    assert client.sent == [('update', {'litellm_params': {'litellm_credential_name': 'new', 'api_base': alternate}})]


@pytest.mark.asyncio
async def test_update_provider_and_credential_together_replaces_old_provider_endpoint() -> None:
    client = Client([credential(api_base=SILICON), credential('new', api_base=DEEPSEEK)], [deployment(SILICON)])
    await service(client).update_deployment(
        'id-1',
        DeploymentUpdateModel.model_validate(
            {
                'provider': 'Deepseek',
                'credentialName': 'new',
            }
        ),
    )
    assert client.sent == [
        (
            'update',
            {
                'litellm_params': {
                    'custom_llm_provider': 'deepseek',
                    'litellm_credential_name': 'new',
                    'api_base': DEEPSEEK,
                }
            },
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize('patch', [{'credentialName': 'new'}, {'provider': 'Deepseek'}])
async def test_update_rejects_implicit_cross_provider_or_mismatched_credential_without_writing(
    patch: dict[str, str],
) -> None:
    client = Client([credential(api_base=SILICON), credential('new', api_base=DEEPSEEK)], [deployment(SILICON)])
    with pytest.raises(ModelEngineServiceError) as caught:
        await service(client).update_deployment('id-1', DeploymentUpdateModel.model_validate(patch))
    assert caught.value.code == 'MODEL_ENGINE_PROVIDER_MISMATCH'
    assert not client.sent


@pytest.mark.asyncio
async def test_update_implicit_provider_uses_deployed_endpoint_instead_of_conflicting_credential_metadata() -> None:
    client = Client([credential(api_base=DEEPSEEK), credential('new', api_base=DEEPSEEK)], [deployment(SILICON)])
    with pytest.raises(ModelEngineServiceError) as caught:
        await service(client).update_deployment('id-1', DeploymentUpdateModel.model_validate({'credentialName': 'new'}))
    assert caught.value.code == 'MODEL_ENGINE_PROVIDER_MISMATCH'
    assert not client.sent


@pytest.mark.asyncio
async def test_update_custom_endpoint_change_requires_explicit_provider() -> None:
    first = 'https://first.example/v1'
    second = 'https://second.example/v1'
    client = Client([credential(api_base=first), credential('new', api_base=second)], [deployment(first)])
    with pytest.raises(ModelEngineServiceError) as caught:
        await service(client).update_deployment('id-1', DeploymentUpdateModel.model_validate({'credentialName': 'new'}))
    assert caught.value.code == 'MODEL_ENGINE_PROVIDER_MISMATCH'
    assert not client.sent
    await service(client).update_deployment(
        'id-1',
        DeploymentUpdateModel.model_validate(
            {
                'credentialName': 'new',
                'provider': 'CUSTOM_OPENAI',
            }
        ),
    )
    assert client.sent[0][1]['litellm_params']['api_base'] == second


@pytest.mark.asyncio
async def test_update_cannot_keep_old_explicit_endpoint_when_new_native_connection_uses_default() -> None:
    client = Client([credential(api_base=SILICON), credential('native', engine='anthropic')], [deployment(SILICON)])
    with pytest.raises(ModelEngineServiceError) as caught:
        await service(client).update_deployment(
            'id-1',
            DeploymentUpdateModel.model_validate(
                {
                    'credentialName': 'native',
                    'provider': 'Anthropic',
                }
            ),
        )
    assert caught.value.code == 'MODEL_ENGINE_CONNECTION_MISSING'
    assert not client.sent


@pytest.mark.asyncio
async def test_update_timeout_keeps_original_patch_without_loading_connections() -> None:
    client = Client([])
    client.deployment_error = AssertionError('timeout-only patch must not read connections')
    await service(client).update_deployment('id-1', DeploymentUpdateModel.model_validate({'timeout': 120}))
    assert client.sent == [('update', {'litellm_params': {'timeout': 120.0}})]
    assert not client.reads


@pytest.mark.asyncio
async def test_update_connection_uses_complete_deployment_pages_and_never_returns_first_page_not_found() -> None:
    items = [deployment(SILICON, number=number) for number in range(PAGE_OVERFLOW_COUNT)]
    page_reads = []
    updates = []
    provider_payload = await Client([]).list_provider_fields()

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == '/public/providers/fields':
            return httpx.Response(200, json=provider_payload)
        if req.url.path == '/credentials':
            return httpx.Response(
                200, json={'credentials': [credential(api_base=SILICON), credential('new', api_base=DEEPSEEK)]}
            )
        if req.url.path == '/v2/model/info':
            page = int(req.url.params.get('page', '1'))
            page_reads.append(page)
            return httpx.Response(
                200,
                json={
                    'data': items[(page - 1) * 50 : page * 50],
                    'total_count': PAGE_OVERFLOW_COUNT,
                    'current_page': page,
                    'total_pages': 2,
                    'size': 50,
                },
            )
        assert req.method == 'PATCH'
        assert req.url.path == '/model/id-50/update'
        updates.append(json.loads(req.content))
        return httpx.Response(200, json={'status': 'success'})

    client = http_client(handler)
    try:
        adapter = LiteLLMManagementAdapter()
        engine = ModelEngineService(client, adapter, LiteLLMDeploymentReferenceQuery(client, adapter))
        await engine.update_deployment(
            'id-50',
            DeploymentUpdateModel.model_validate(
                {
                    'provider': 'Deepseek',
                    'credentialName': 'new',
                }
            ),
        )
    finally:
        await client.aclose()
    assert page_reads == [1, 2]
    assert updates[0]['litellm_params']['api_base'] == DEEPSEEK


@pytest.mark.asyncio
async def test_update_connection_failed_full_read_does_not_send_patch() -> None:
    client = Client([credential(api_base=SILICON)], [deployment(SILICON)])
    client.deployment_error = LiteLLMManagementUnavailableError()
    with pytest.raises(LiteLLMManagementUnavailableError):
        await service(client).update_deployment(
            'id-1', DeploymentUpdateModel.model_validate({'provider': 'OpenAI_Compatible'})
        )
    assert not client.sent
