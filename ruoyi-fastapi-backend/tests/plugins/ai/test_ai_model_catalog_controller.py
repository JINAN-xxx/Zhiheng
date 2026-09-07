import json
from collections.abc import Callable, Generator
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Response
from fastapi.routing import APIRoute
from starlette.requests import Request
from starlette.status import HTTP_200_OK, HTTP_404_NOT_FOUND, HTTP_503_SERVICE_UNAVAILABLE

from common.annotation import log_annotation
from common.annotation.log_annotation import Log
from common.aspect.data_scope import GetDataScope
from common.context import RequestContext
from config.get_db import get_db
from module_admin.entity.vo.user_vo import CurrentUserModel, UserInfoModel
from module_admin.service.login_service import LoginService
from plugins.ai.controller import ai_model_catalog_controller as controller_module
from plugins.ai.controller.ai_model_catalog_controller import (
    ai_model_catalog_controller,
    create_platform_model,
    delete_platform_model,
    disable_platform_model,
    get_platform_model,
    list_platform_models,
    publish_platform_model,
    update_platform_model,
    upsert_platform_model_binding,
)
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.entity.vo.ai_model_catalog_vo import (
    ModelCatalogCreateModel,
    ModelCatalogPageQueryModel,
    ModelCatalogUpdateModel,
    ResourceBindingUpsertModel,
)
from plugins.ai.infrastructure.model_runtime_provider import MissingModelRuntimeEngine

MODEL_ID = 'mdl_123e4567e89b42d3a456426614174000'
BINDING_ID = 'bnd_987e4567e89b42d3a456426614174111'
NOW = datetime(2026, 8, 13, 16, 30)
EXPECTED_WRITE_CALLS = 6
EXPECTED_ROUTE_COUNT = 8


def build_request(
    path: str,
    method: str = 'GET',
    *,
    body: bytes = b'',
    path_params: dict[str, str] | None = None,
    app: FastAPI | None = None,
) -> Request:
    """构造控制器和日志装饰器使用的真实 Request。"""
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
            'app': app or FastAPI(),
        },
        receive=receive,
    )


def decode_response(response: Response) -> dict[str, Any]:
    return json.loads(response.body)


def current_user() -> CurrentUserModel:
    return CurrentUserModel(
        permissions=['*:*:*'],
        roles=[],
        user=UserInfoModel(userName='catalog-admin', dept=None, role=[]),
    )


@pytest.fixture(autouse=True)
def controller_context(monkeypatch: pytest.MonkeyPatch) -> Generator[list[object], None, None]:
    """提供日志上下文，并捕获真实 Log 装饰器生成的操作日志。"""
    operation_logs: list[object] = []
    user_token = RequestContext.set_current_user(current_user())
    patterns_token = RequestContext.set_current_exclude_patterns([])

    async def capture_operation_log(_request: Request, operation_log: object, _source: str) -> None:
        operation_logs.append(operation_log)

    monkeypatch.setattr(log_annotation.LogQueueService, 'enqueue_operation_log', capture_operation_log)
    monkeypatch.setattr(controller_module, '_current_time', lambda: NOW)
    FakeCatalogService.reset()
    monkeypatch.setattr(controller_module, 'ModelCatalogService', FakeCatalogService)
    try:
        yield operation_logs
    finally:
        RequestContext.reset_current_user(user_token)
        RequestContext.reset_current_exclude_patterns(patterns_token)


class FakeCatalogService:
    instances: list['FakeCatalogService'] = []
    error: ModelServiceError | None = None

    def __init__(self, db: object, runtime: object) -> None:
        self.db = db
        self.runtime = runtime
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        type(self).instances.append(self)

    @classmethod
    def reset(cls) -> None:
        cls.instances = []
        cls.error = None

    async def _result(self, method: str, *args: object, **kwargs: object) -> object:
        self.calls.append((method, args, kwargs))
        if self.error is not None:
            raise self.error
        if method == 'list_models':
            return SimpleNamespace(
                model_dump=lambda **_kwargs: {
                    'rows': [],
                    'pageNum': 1,
                    'pageSize': 10,
                    'total': 0,
                    'hasNext': False,
                }
            )
        if method == 'delete_model':
            return None
        return {'modelId': MODEL_ID, 'bindingId': BINDING_ID}

    async def list_models(self, *args: object, **kwargs: object) -> object:
        return await self._result('list_models', *args, **kwargs)

    async def get_model(self, *args: object, **kwargs: object) -> object:
        return await self._result('get_model', *args, **kwargs)

    async def create_model(self, *args: object, **kwargs: object) -> object:
        return await self._result('create_model', *args, **kwargs)

    async def update_model(self, *args: object, **kwargs: object) -> object:
        return await self._result('update_model', *args, **kwargs)

    async def upsert_binding(self, *args: object, **kwargs: object) -> object:
        return await self._result('upsert_binding', *args, **kwargs)

    async def publish_model(self, *args: object, **kwargs: object) -> object:
        return await self._result('publish_model', *args, **kwargs)

    async def disable_model(self, *args: object, **kwargs: object) -> object:
        return await self._result('disable_model', *args, **kwargs)

    async def delete_model(self, *args: object, **kwargs: object) -> object:
        return await self._result('delete_model', *args, **kwargs)


def route_map() -> dict[tuple[str, str], APIRoute]:
    return {
        (next(iter(route.methods)), route.path): route
        for route in ai_model_catalog_controller.routes
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


def test_routes_use_exact_methods_permissions_login_and_no_data_scope() -> None:
    assert ai_model_catalog_controller.prefix == '/ai/models'
    expected_permissions = {
        ('GET', '/ai/models'): 'ai:model:list',
        ('GET', '/ai/models/{model_id}'): 'ai:model:query',
        ('POST', '/ai/models'): 'ai:model:add',
        ('PATCH', '/ai/models/{model_id}'): 'ai:model:edit',
        ('PUT', '/ai/models/{model_id}/binding'): 'ai:model:edit',
        ('POST', '/ai/models/{model_id}/publish'): 'ai:model:publish',
        ('POST', '/ai/models/{model_id}/disable'): 'ai:model:edit',
        ('DELETE', '/ai/models/{model_id}'): 'ai:model:remove',
        ('GET', '/ai/models/{model_id}/rate-cards'): 'ai:model:query',
        ('POST', '/ai/models/{model_id}/rate-cards'): 'ai:model:edit',
        ('POST', '/ai/models/{model_id}/smoke-test'): 'ai:model:test',
    }

    routes = route_map()
    assert set(routes) == set(expected_permissions)
    for route_key, expected_permission in expected_permissions.items():
        calls = dependency_calls(routes[route_key])
        assert any(type(call).__name__ == 'PreAuth' for call in calls)
        permissions = [call.perm for call in calls if type(call).__name__ == 'CheckUserInterfaceAuth']
        assert permissions == [expected_permission]
        assert not any(isinstance(call, GetDataScope) for call in calls)


def test_controller_does_not_import_runtime_client_state_or_litellm_adapter() -> None:
    """Controller 只能依赖引擎 Provider，不能读取 Hook state 或构造 Adapter。"""
    source = Path(controller_module.__file__).read_text(encoding='utf-8')

    assert 'CLIENT_STATE_NAME' not in source
    assert 'LiteLLMRuntimeAdapter' not in source
    assert 'request.app.state' not in source


def test_smoke_test_uses_an_independent_model_service_session_factory() -> None:
    source = Path(controller_module.__file__).read_text(encoding='utf-8')

    assert 'from config.database import AsyncSessionLocal' in source
    assert 'ModelService(query_db, runtime, session_factory=AsyncSessionLocal)' in source


def test_write_routes_use_strict_audit_payload_allowlists() -> None:
    routes = route_map()
    write_keys = {
        ('POST', '/ai/models'),
        ('PATCH', '/ai/models/{model_id}'),
        ('PUT', '/ai/models/{model_id}/binding'),
        ('POST', '/ai/models/{model_id}/publish'),
        ('POST', '/ai/models/{model_id}/disable'),
        ('DELETE', '/ai/models/{model_id}'),
    }
    assert len(write_keys) == EXPECTED_WRITE_CALLS

    for route_key in write_keys:
        decorator = log_decorator(routes[route_key])
        assert decorator is not None
        assert decorator.response_log_mode == 'include'
        assert set(decorator.response_include_fields) <= {'code', 'msg', 'data.model_id', 'data.binding_id'}
        if route_key == ('POST', '/ai/models'):
            assert decorator.request_log_mode == 'none'
            assert decorator.request_include_fields == ()
        else:
            assert decorator.request_log_mode == 'include'
            assert decorator.request_include_fields == ('path_params.model_id',)


@pytest.mark.asyncio
async def test_controller_delegates_all_catalog_operations_with_username_and_time() -> None:
    database = object()
    user = current_user()
    query = ModelCatalogPageQueryModel(pageNum='1', pageSize='10')
    create = ModelCatalogCreateModel(modelCode='company-chat', displayName='企业模型', modelType='chat')
    update = ModelCatalogUpdateModel(displayName='企业模型二代')
    binding = ResourceBindingUpsertModel(engineResourceId='runtime-company-chat')

    await list_platform_models(build_request('/ai/models'), query, database)
    await get_platform_model(build_request(f'/ai/models/{MODEL_ID}'), MODEL_ID, database)
    await create_platform_model(build_request('/ai/models', 'POST'), create, database, user)
    await update_platform_model(
        build_request(f'/ai/models/{MODEL_ID}', 'PATCH', path_params={'model_id': MODEL_ID}),
        MODEL_ID,
        update,
        database,
        user,
    )
    await upsert_platform_model_binding(
        build_request(
            f'/ai/models/{MODEL_ID}/binding',
            'PUT',
            path_params={'model_id': MODEL_ID},
        ),
        MODEL_ID,
        binding,
        database,
        user,
    )
    await publish_platform_model(
        build_request(f'/ai/models/{MODEL_ID}/publish', 'POST', path_params={'model_id': MODEL_ID}),
        MODEL_ID,
        database,
        user,
    )
    await disable_platform_model(
        build_request(f'/ai/models/{MODEL_ID}/disable', 'POST', path_params={'model_id': MODEL_ID}),
        MODEL_ID,
        database,
        user,
    )
    await delete_platform_model(
        build_request(f'/ai/models/{MODEL_ID}', 'DELETE', path_params={'model_id': MODEL_ID}),
        MODEL_ID,
        database,
        user,
    )

    assert len(FakeCatalogService.instances) == EXPECTED_ROUTE_COUNT
    calls = [instance.calls[0] for instance in FakeCatalogService.instances]
    assert calls[0] == ('list_models', (query,), {'is_page': True})
    assert calls[1] == ('get_model', (MODEL_ID,), {})
    assert calls[2] == ('create_model', (create,), {'username': 'catalog-admin', 'now': NOW})
    assert calls[3] == ('update_model', (MODEL_ID, update), {'username': 'catalog-admin', 'now': NOW})
    assert calls[4] == ('upsert_binding', (MODEL_ID, binding), {'username': 'catalog-admin', 'now': NOW})
    assert calls[5] == ('publish_model', (MODEL_ID,), {'username': 'catalog-admin', 'now': NOW})
    assert calls[6] == ('disable_model', (MODEL_ID,), {'username': 'catalog-admin', 'now': NOW})
    assert calls[7] == ('delete_model', (MODEL_ID,), {})
    assert all(instance.db is database for instance in FakeCatalogService.instances)


@pytest.mark.asyncio
async def test_missing_runtime_does_not_block_catalog_reads() -> None:
    response = await list_platform_models(
        build_request('/ai/models'),
        ModelCatalogPageQueryModel(),
        object(),
    )

    assert response.status_code == HTTP_200_OK
    assert decode_response(response)['rows'] == []
    assert isinstance(FakeCatalogService.instances[0].runtime, MissingModelRuntimeEngine)


@pytest.mark.asyncio
async def test_controller_resolves_runtime_through_infrastructure_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = object()

    def fake_get_model_runtime_engine(_request: Request) -> object:
        return runtime

    monkeypatch.setattr(
        controller_module,
        'get_model_runtime_engine',
        fake_get_model_runtime_engine,
        raising=False,
    )

    await list_platform_models(build_request('/ai/models'), ModelCatalogPageQueryModel(), object())

    assert FakeCatalogService.instances[0].runtime is runtime


@pytest.mark.asyncio
async def test_missing_runtime_publish_maps_to_stable_misconfigured_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RuntimeCallingCatalogService(FakeCatalogService):
        async def publish_model(self, *args: object, **kwargs: object) -> object:
            del args, kwargs
            return await self.runtime.list_model_ids()

    monkeypatch.setattr(controller_module, 'ModelCatalogService', RuntimeCallingCatalogService)

    with pytest.raises(HTTPException) as exc_info:
        await publish_platform_model(
            build_request(f'/ai/models/{MODEL_ID}/publish', 'POST', path_params={'model_id': MODEL_ID}),
            MODEL_ID,
            object(),
            current_user(),
        )

    assert exc_info.value.status_code == HTTP_503_SERVICE_UNAVAILABLE
    assert exc_info.value.detail == {
        'code': 'MODEL_RUNTIME_MISCONFIGURED',
        'message': '模型运行时尚未完成配置',
    }
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


@pytest.mark.asyncio
async def test_model_service_error_maps_to_http_exception_without_internal_chain() -> None:
    FakeCatalogService.error = ModelServiceError.not_found()

    with pytest.raises(HTTPException) as exc_info:
        await get_platform_model(build_request(f'/ai/models/{MODEL_ID}'), MODEL_ID, object())

    assert exc_info.value.status_code == HTTP_404_NOT_FOUND
    assert exc_info.value.detail == {'code': 'MODEL_NOT_FOUND', 'message': '平台模型不存在'}
    assert exc_info.value.__cause__ is None
    assert exc_info.value.__suppress_context__ is True


@pytest.mark.asyncio
async def test_model_service_error_from_write_route_is_audited_and_preserved(
    controller_context: list[object],
) -> None:
    secret = 'dynamic-provider-secret-must-not-be-audited'
    FakeCatalogService.error = ModelServiceError.not_found()
    request = build_request(
        f'/ai/models/{MODEL_ID}',
        'PATCH',
        body=json.dumps(
            {
                'displayName': '企业模型二代',
                'defaultParameters': {'providerPrivate': secret},
            }
        ).encode(),
        path_params={'model_id': MODEL_ID},
    )

    with pytest.raises(HTTPException) as exc_info:
        await update_platform_model(
            request,
            MODEL_ID,
            ModelCatalogUpdateModel(displayName='企业模型二代'),
            object(),
            current_user(),
        )

    assert exc_info.value.status_code == HTTP_404_NOT_FOUND
    assert exc_info.value.detail == {'code': 'MODEL_NOT_FOUND', 'message': '平台模型不存在'}
    assert len(controller_context) == 1
    operation_log = controller_context[0]
    assert json.loads(operation_log.oper_param) == {
        'mode': 'include',
        'selected': {'path_params.model_id': MODEL_ID},
    }
    assert json.loads(operation_log.json_result) == {
        'mode': 'include',
        'selected': {'code': 'MODEL_NOT_FOUND', 'msg': '平台模型不存在'},
    }
    assert operation_log.status == 1
    assert operation_log.error_msg == '平台模型不存在'
    assert secret not in operation_log.oper_param
    assert secret not in operation_log.json_result


@pytest.mark.asyncio
async def test_audit_queue_failure_does_not_replace_write_route_http_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeCatalogService.error = ModelServiceError.not_found()

    async def fail_operation_log(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError('audit queue unavailable')

    monkeypatch.setattr(log_annotation.LogQueueService, 'enqueue_operation_log', fail_operation_log)

    with pytest.raises(HTTPException) as exc_info:
        await update_platform_model(
            build_request(f'/ai/models/{MODEL_ID}', 'PATCH', path_params={'model_id': MODEL_ID}),
            MODEL_ID,
            ModelCatalogUpdateModel(displayName='企业模型二代'),
            object(),
            current_user(),
        )

    assert exc_info.value.status_code == HTTP_404_NOT_FOUND
    assert exc_info.value.detail == {'code': 'MODEL_NOT_FOUND', 'message': '平台模型不存在'}


@pytest.mark.asyncio
async def test_model_service_error_reaches_asgi_with_expected_status_and_public_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeCatalogService.error = ModelServiceError.not_found()
    app = FastAPI()
    app.include_router(ai_model_catalog_controller)

    async def allow_dependency() -> None:
        return None

    async def database_dependency() -> object:
        return object()

    for route in ai_model_catalog_controller.routes:
        if not isinstance(route, APIRoute):
            continue
        for dependency in route.dependencies:
            app.dependency_overrides[dependency.dependency] = allow_dependency
    app.dependency_overrides[get_db] = database_dependency
    app.dependency_overrides[LoginService.get_current_user] = current_user

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
        response = await client.get(f'/ai/models/{MODEL_ID}')

    assert response.status_code == HTTP_404_NOT_FOUND
    assert response.json() == {
        'detail': {'code': 'MODEL_NOT_FOUND', 'message': '平台模型不存在'},
    }


@pytest.mark.asyncio
async def test_actual_log_excludes_dynamic_json_and_records_only_path_model_id(
    controller_context: list[object],
) -> None:
    secret = 'provider-private-value-must-not-be-logged'
    body = json.dumps(
        {
            'displayName': '企业模型二代',
            'defaultParameters': {'temperature': 1.1, 'providerPrivate': secret},
        }
    ).encode()

    response = await update_platform_model(
        build_request(
            f'/ai/models/{MODEL_ID}',
            'PATCH',
            body=body,
            path_params={'model_id': MODEL_ID},
        ),
        MODEL_ID,
        ModelCatalogUpdateModel(displayName='企业模型二代'),
        object(),
        current_user(),
    )

    assert response.status_code == HTTP_200_OK
    assert len(controller_context) == 1
    operation_log = controller_context[0]
    assert json.loads(operation_log.oper_param) == {
        'mode': 'include',
        'selected': {'path_params.model_id': MODEL_ID},
    }
    assert secret not in operation_log.oper_param
    assert 'defaultParameters' not in operation_log.oper_param
    response_log = json.loads(operation_log.json_result)
    assert response_log['mode'] == 'include'
    assert response_log['selected']['data.model_id'] == MODEL_ID
    assert secret not in operation_log.json_result
