import copy
import importlib
import importlib.util
from collections.abc import Callable

import pytest

from plugins.ai.entity.vo.ai_model_engine_vo import ConnectionTestRequestModel, DeploymentCreateModel
from tests.plugins.ai.test_model_connection_resolution import SILICON, Client, credential, service


def probe_policy() -> Callable[[dict[str, object]], dict[str, object]]:
    name = 'plugins.ai.infrastructure.connection_probe_policy'
    assert importlib.util.find_spec(name), '连接探测策略尚未实现'
    return importlib.import_module(name).prepare_connection_probe


def test_siliconflow_qwen_probe_uses_non_thinking_without_mutating_configuration() -> None:
    payload = {
        'mode': 'chat',
        'model_info': {},
        'litellm_params': {
            'model': 'Qwen/Qwen3.8-27B',
            'custom_llm_provider': 'openai',
            'api_base': 'https://api.siliconflow.cn/v1',
            'api_key': 'sentinel-only-key',
        },
    }
    original = copy.deepcopy(payload)
    result = probe_policy()(payload)
    assert result['litellm_params']['extra_body'] == {'enable_thinking': False}
    assert result['litellm_params']['api_key'] == 'sentinel-only-key'
    assert payload == original


@pytest.mark.parametrize(
    'change',
    [
        {'model': 'deepseek-ai/DeepSeek-R1'},
        {'model': 'Qwen/Qwen3-235B-A22B-Thinking-2507'},
        {'api_base': 'https://example.com/v1'},
        {'api_base': 'https://api.siliconflow.cn.evil.test/v1'},
        {'api_base': 'https://api.siliconflow.cn/v1/chat/completions'},
        {'custom_llm_provider': 'other'},
    ],
)
def test_probe_options_are_scoped_to_verified_endpoint_and_model(change: dict[str, str]) -> None:
    payload = {
        'mode': 'chat',
        'litellm_params': {
            'model': 'Qwen/Qwen3.8-27B',
            'custom_llm_provider': 'openai',
            'api_base': 'https://api.siliconflow.cn/v1',
            **change,
        },
    }
    assert probe_policy()(payload) == payload


@pytest.mark.parametrize('mode', ['embedding', 'rerank', None])
def test_probe_options_do_not_change_other_model_modes(mode: str | None) -> None:
    payload = {
        'mode': mode,
        'litellm_params': {
            'model': 'Qwen/Qwen3.8-27B',
            'custom_llm_provider': 'openai',
            'api_base': 'https://api.siliconflow.cn/v1',
        },
    }
    assert probe_policy()(payload) == payload


def test_saved_connection_and_other_extra_body_fields_are_preserved() -> None:
    payload = {
        'mode': 'chat',
        'litellm_params': {
            'model': 'Qwen/Qwen3.8-27B',
            'custom_llm_provider': 'openai',
            'api_base': 'https://api.siliconflow.cn/v1/',
            'litellm_credential_name': 'saved-connection',
            'extra_body': {'custom': 'preserved', 'enable_thinking': True},
        },
    }
    result = probe_policy()(payload)
    assert result['litellm_params']['litellm_credential_name'] == 'saved-connection'
    assert result['litellm_params']['extra_body'] == {'custom': 'preserved', 'enable_thinking': False}
    assert payload['litellm_params']['extra_body']['enable_thinking'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('saved', [True, False])
async def test_service_applies_probe_options_after_resolving_address_but_not_to_deployment(saved: bool) -> None:
    client = Client([credential(api_base=SILICON, provider='OpenAI_Compatible')])
    original_credentials = copy.deepcopy(client.credentials)
    engine = service(client)
    request = ConnectionTestRequestModel(
        upstreamModel='Qwen/Qwen3.8-27B',
        provider='OpenAI_Compatible',
        modelType='chat',
        credentialName='saved' if saved else None,
        values={} if saved else {'api_key': 'test-key', 'api_base': SILICON},
    )
    result = await engine.test_connection(request)
    assert result.success
    assert client.sent[-1][1]['litellm_params']['extra_body'] == {'enable_thinking': False}
    await engine.create_deployment(
        DeploymentCreateModel(
            modelAlias='qwen-probe-test',
            upstreamModel='Qwen/Qwen3.8-27B',
            provider='OpenAI_Compatible',
            credentialName='saved',
        )
    )
    assert 'extra_body' not in client.sent[-1][1]['litellm_params']
    assert client.credentials == original_credentials
