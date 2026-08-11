import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, Response
from fastapi.routing import APIRoute
from starlette.requests import Request

from plugins.ai.controller.ai_runtime_controller import (
    ai_runtime_controller,
    get_ai_runtime_health,
    get_ai_runtime_models,
)
from plugins.ai.infrastructure.litellm_errors import (
    LiteLLMAuthenticationError,
    LiteLLMUnavailableError,
)

BAD_GATEWAY_STATUS = 502
SERVICE_UNAVAILABLE_STATUS = 503


class FakeRuntimeClient:
    """诊断控制器测试客户端。"""

    def __init__(
        self,
        *,
        configured: bool = True,
        liveliness_error: Exception | None = None,
        models_error: Exception | None = None,
        models_response: dict[str, object] | None = None,
    ) -> None:
        self.settings = SimpleNamespace(configured=configured)
        self.liveliness_error = liveliness_error
        self.models_error = models_error
        self.models_response = models_response or {'object': 'list', 'data': []}
        self.liveliness_call_count = 0

    async def check_liveliness(self) -> dict[str, object]:
        self.liveliness_call_count += 1
        if self.liveliness_error is not None:
            raise self.liveliness_error
        return {'status': 'healthy'}

    async def list_models(self) -> dict[str, object]:
        if self.models_error is not None:
            raise self.models_error
        return self.models_response


def build_request(client: FakeRuntimeClient | None) -> Request:
    """构造带应用状态的 Starlette Request。"""
    app = FastAPI()
    if client is not None:
        app.state.ai_litellm_runtime_client = client
    return Request(
        {
            'type': 'http',
            'method': 'GET',
            'path': '/ai/runtime/health',
            'headers': [],
            'app': app,
        }
    )


def decode_response(response: Response) -> dict[str, object]:
    """解码 ResponseUtil 返回的 JSONResponse。"""
    return json.loads(response.body)


@pytest.mark.asyncio
async def test_health_reports_healthy_with_latency_and_timestamp() -> None:
    """LiteLLM 可达且 Key 已配置时返回健康状态。"""
    client = FakeRuntimeClient()

    response = await get_ai_runtime_health(build_request(client))
    body = decode_response(response)
    data = body['data']

    assert data['status'] == 'healthy'
    assert data['configured'] is True
    assert data['latencyMs'] >= 0
    assert data['checkedAt']
    assert data['message'] == 'LiteLLM Runtime 运行正常'


@pytest.mark.asyncio
async def test_health_reports_misconfigured_without_network_call() -> None:
    """缺少 Runtime Key 时返回 misconfigured，且不访问网络。"""
    client = FakeRuntimeClient(configured=False)

    response = await get_ai_runtime_health(build_request(client))
    data = decode_response(response)['data']

    assert data['status'] == 'misconfigured'
    assert data['configured'] is False
    assert data['message'] == 'LiteLLM Runtime 尚未完成配置'
    assert client.liveliness_call_count == 0


@pytest.mark.asyncio
async def test_health_reports_misconfigured_when_hook_state_is_missing() -> None:
    """AI Hook 尚未创建客户端时返回脱敏配置状态。"""
    response = await get_ai_runtime_health(build_request(None))
    data = decode_response(response)['data']

    assert data['status'] == 'misconfigured'
    assert data['configured'] is False


@pytest.mark.asyncio
async def test_health_reports_unavailable_with_sanitized_message() -> None:
    """网络故障只返回稳定说明，不透出底层连接信息。"""
    client = FakeRuntimeClient(
        liveliness_error=LiteLLMUnavailableError('private-host.example.com connection refused'),
    )

    response = await get_ai_runtime_health(build_request(client))
    data = decode_response(response)['data']

    assert data['status'] == 'unavailable'
    assert data['configured'] is True
    assert data['message'] == 'LiteLLM Runtime 当前不可用'
    assert 'private-host' not in json.dumps(data)


@pytest.mark.asyncio
async def test_models_normalizes_litellm_model_list() -> None:
    """模型接口只返回 Platform 需要的稳定字段。"""
    client = FakeRuntimeClient(
        models_response={
            'object': 'list',
            'data': [
                {
                    'id': 'chat-default',
                    'object': 'model',
                    'owned_by': 'openai',
                    'internal_metadata': 'must-not-leak',
                }
            ],
        }
    )

    response = await get_ai_runtime_models(build_request(client))
    data = decode_response(response)['data']

    assert data == [{'id': 'chat-default', 'object': 'model', 'ownedBy': 'openai'}]
    assert 'internal_metadata' not in json.dumps(data)


@pytest.mark.asyncio
async def test_models_accepts_empty_model_list() -> None:
    """尚未配置 Deployment 时空模型列表仍是成功响应。"""
    response = await get_ai_runtime_models(build_request(FakeRuntimeClient()))

    assert decode_response(response)['data'] == []


@pytest.mark.asyncio
async def test_models_maps_internal_auth_error_without_leaking_detail() -> None:
    """LiteLLM 拒绝 Runtime Key 时返回内部网关错误，而非客户 401。"""
    client = FakeRuntimeClient(
        models_error=LiteLLMAuthenticationError('rejected runtime-secret private detail'),
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_ai_runtime_models(build_request(client))

    assert exc_info.value.status_code == BAD_GATEWAY_STATUS
    assert exc_info.value.detail == {
        'code': 'LITELLM_AUTHENTICATION_FAILED',
        'message': 'LiteLLM Runtime 内部认证失败',
    }
    assert 'runtime-secret' not in json.dumps(exc_info.value.detail)


@pytest.mark.asyncio
async def test_models_reports_standard_misconfigured_error_when_hook_state_is_missing() -> None:
    """Runtime 客户端未初始化时不公开 Hook 内部实现细节。"""
    with pytest.raises(HTTPException) as exc_info:
        await get_ai_runtime_models(build_request(None))

    assert exc_info.value.status_code == SERVICE_UNAVAILABLE_STATUS
    assert exc_info.value.detail == {
        'code': 'LITELLM_MISCONFIGURED',
        'message': 'LiteLLM Runtime 尚未完成配置',
    }


def test_runtime_routes_require_login_and_runtime_query_permission() -> None:
    """两个诊断接口都要求登录并具备 Runtime 查询权限。"""
    assert ai_runtime_controller.prefix == '/ai/runtime'
    routes = [route for route in ai_runtime_controller.routes if isinstance(route, APIRoute)]
    assert {route.path for route in routes} == {'/ai/runtime/health', '/ai/runtime/models'}

    for route in routes:
        dependencies = [dependency.dependency for dependency in route.dependencies]
        assert any(type(dependency).__name__ == 'PreAuth' for dependency in dependencies)
        permission_dependencies = [
            dependency for dependency in dependencies if type(dependency).__name__ == 'CheckUserInterfaceAuth'
        ]
        assert len(permission_dependencies) == 1
        assert permission_dependencies[0].perm == 'ai:runtime:query'
