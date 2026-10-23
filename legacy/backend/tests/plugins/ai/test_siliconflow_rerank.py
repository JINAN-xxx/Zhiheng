from copy import deepcopy

import pytest

from plugins.ai.domain.model_runtime import ModelRerankDocument, ModelRerankRequest
from plugins.ai.entity.vo.ai_model_engine_vo import (
    ConnectionTestRequestModel,
    DeploymentCreateModel,
    DeploymentUpdateModel,
)
from plugins.ai.infrastructure.connection_test_diagnostics import classify_connection_failure
from plugins.ai.infrastructure.litellm_management_adapter import LiteLLMManagementAdapter
from plugins.ai.infrastructure.litellm_runtime_adapter import LiteLLMRuntimeAdapter
from tests.plugins.ai.test_embedding_rerank_adapter import RERANK_REQUEST, FakeRuntimeClient, _rerank_result
from tests.plugins.ai.test_model_engine_controller import FakeManagementClient, provider_payload, service

BASE = 'https://api.siliconflow.cn/v1'
MODEL = 'BAAI/bge-reranker-v2-m3'


def client() -> FakeManagementClient:
    providers = provider_payload()
    providers['items'][0]['provider'] = 'OpenAI_Compatible'
    providers['items'][0]['credential_fields'].append(
        {'key': 'api_base', 'label': 'API Base', 'required': True, 'field_type': 'text'}
    )
    credentials = {
        'credentials': [
            {
                'credential_name': 'siliconflow-saved',
                'credential_info': {'provider': 'OpenAI_Compatible', 'custom_llm_provider': 'openai'},
                'credential_values': {'api_key': 'must-never-leak', 'api_base': BASE},
            }
        ]
    }
    return FakeManagementClient(providers=providers, credentials=credentials, deployments={'data': []})


def deployed() -> dict:
    return {
        'model_name': 'bge-rerank',
        'litellm_params': {
            'model': MODEL,
            'custom_llm_provider': 'cohere',
            'api_base': BASE + '/rerank',
            'litellm_credential_name': 'siliconflow-saved',
            'return_documents': True,
        },
        'model_info': {'id': 'rerank-deployment', 'mode': 'rerank', 'zhiheng_provider': 'openai'},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize('saved', [False, True])
async def test_rerank_probe_uses_compatible_protocol_and_reuses_saved_credentials(saved: bool) -> None:
    gateway = client()
    request = ConnectionTestRequestModel.model_validate(
        {
            'upstreamModel': MODEL,
            'provider': 'OpenAI_Compatible',
            'modelType': 'rerank',
            **(
                {'credentialName': 'siliconflow-saved', 'values': {}}
                if saved
                else {'values': {'api_base': BASE, 'api_key': 'test-key'}}
            ),
        }
    )
    assert (await service(gateway).test_connection(request)).success
    payload = next(value for method, value in gateway.calls if method == 'test_connection')
    assert payload['mode'] == 'rerank'
    assert payload['litellm_params']['model'] == MODEL
    assert payload['litellm_params']['custom_llm_provider'] == 'cohere'
    assert payload['litellm_params']['api_base'] == BASE + '/rerank'
    assert payload['litellm_params']['return_documents'] is True
    if saved:
        assert payload['litellm_params']['litellm_credential_name'] == 'siliconflow-saved'
        assert 'api_key' not in payload['litellm_params']


@pytest.mark.asyncio
async def test_create_deployment_preserves_selected_type_and_public_provider() -> None:
    gateway = client()
    model = await service(gateway).create_deployment(
        DeploymentCreateModel.model_validate(
            {
                'modelAlias': 'bge-rerank',
                'upstreamModel': MODEL,
                'provider': 'OpenAI_Compatible',
                'credentialName': 'siliconflow-saved',
                'modelType': 'rerank',
            }
        )
    )
    payload = next(value for method, value in gateway.calls if method == 'create_deployment')
    assert payload['litellm_params'] == deployed()['litellm_params']
    assert payload['model_info'] == {'mode': 'rerank', 'zhiheng_provider': 'openai'}
    assert model.provider == 'openai'


@pytest.mark.asyncio
async def test_list_and_connection_change_keep_the_original_provider_and_endpoint() -> None:
    gateway = client()
    gateway.deployments = {'data': [deployed()]}
    engine = service(gateway)
    rows = await engine.list_deployments()
    assert rows[0].provider == 'openai'
    credentials = await engine.list_credentials()
    assert credentials[0].connection_profile.api_base == BASE
    assert 'OpenAI_Compatible' in credentials[0].connection_profile.provider_ids
    await engine.update_deployment('rerank-deployment', DeploymentUpdateModel(credentialName='siliconflow-saved'))
    _, payload = next(value for method, value in gateway.calls if method == 'update_deployment')
    assert payload['litellm_params']['custom_llm_provider'] == 'cohere'
    assert payload['litellm_params']['api_base'] == BASE + '/rerank'


def test_other_cohere_deployments_are_not_relabelled() -> None:
    item = deployed()
    item['model_info'].pop('zhiheng_provider')
    assert LiteLLMManagementAdapter.normalize_deployments({'data': [item]})[0].provider == 'cohere'
    item = deepcopy(deployed())
    item['litellm_params']['api_base'] = 'https://example.com/v1/rerank'
    assert LiteLLMManagementAdapter.normalize_deployments({'data': [item]})[0].provider == 'cohere'


@pytest.mark.parametrize('return_documents', [False, True])
def test_rerank_wire_payload_is_text_array_and_requests_non_null_documents(return_documents: bool) -> None:
    request = ModelRerankRequest(
        engine_model='bge-rerank',
        query='fruit',
        documents=(ModelRerankDocument(text='Apple'),),
        return_documents=return_documents,
    )
    payload = LiteLLMRuntimeAdapter._build_rerank_payload(request)
    assert payload['documents'] == ['Apple']
    assert payload['return_documents'] is True
    assert request.return_documents is return_documents


def test_unsupported_protocol_is_not_reported_as_a_network_outage() -> None:
    reason, _ = classify_connection_failure(
        {'result': {'error': 'litellm.APIConnectionError: Unsupported provider: openai'}}
    )
    assert reason == 'invalid_request'


@pytest.mark.asyncio
@pytest.mark.parametrize('model_type', ['chat', 'embedding', None])
async def test_other_model_types_keep_original_protocol(model_type: str | None) -> None:
    gateway = client()
    request = ConnectionTestRequestModel.model_validate(
        {
            'upstreamModel': MODEL,
            'provider': 'OpenAI_Compatible',
            'modelType': model_type,
            'values': {'api_base': BASE, 'api_key': 'test-key'},
        }
    )
    await service(gateway).test_connection(request)
    payload = next(value for method, value in gateway.calls if method == 'test_connection')
    assert payload['litellm_params']['custom_llm_provider'] == 'openai'
    assert payload['litellm_params']['api_base'] == BASE
    assert 'return_documents' not in payload['litellm_params']


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'base',
    [
        'https://example.com/v1',
        'https://api.siliconflow.cn.evil.invalid/v1',
        'http://api.siliconflow.cn/v1',
        'https://api.siliconflow.cn:9443/v1',
    ],
)
async def test_unknown_endpoints_do_not_trigger_protocol_rewrite(base: str) -> None:
    gateway = client()
    await service(gateway).test_connection(
        ConnectionTestRequestModel.model_validate(
            {
                'upstreamModel': MODEL,
                'provider': 'OpenAI_Compatible',
                'modelType': 'rerank',
                'values': {'api_base': base, 'api_key': 'test-key'},
            }
        )
    )
    payload = next(value for method, value in gateway.calls if method == 'test_connection')
    assert payload['litellm_params']['custom_llm_provider'] == 'openai'
    assert payload['litellm_params']['api_base'] == base


@pytest.mark.asyncio
async def test_zero_search_units_keeps_tokens_and_uses_existing_estimated_unit_policy() -> None:
    result = _rerank_result()
    result.payload['meta'] = {'tokens': {'input_tokens': 17}, 'billed_units': {'search_units': 0}}
    response = await LiteLLMRuntimeAdapter(FakeRuntimeClient(rerank_result=result)).rerank(RERANK_REQUEST)
    assert response.input_tokens == result.payload['meta']['tokens']['input_tokens']
    assert response.search_units is None


@pytest.mark.parametrize('units', [-1, False, '0', 0.0])
def test_invalid_search_units_are_still_rejected(units: object) -> None:
    valid, _, _ = LiteLLMRuntimeAdapter._normalize_rerank_usage({'billed_units': {'search_units': units}})
    assert not valid
