import json
from collections.abc import Callable, Generator
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException, Response
from fastapi.routing import APIRoute
from pydantic import SecretStr
from starlette.requests import Request

from common.annotation import log_annotation
from common.annotation.log_annotation import Log
from common.aspect.data_scope import GetDataScope
from common.context import RequestContext
from common.vo import PageModel
from module_admin.entity.vo.user_vo import CurrentUserModel, UserInfoModel
from plugins.ai.controller import ai_application_controller as controller_module
from plugins.ai.controller.ai_application_controller import (
    ai_application_controller,
    create_application,
    create_developer_key,
    delete_application,
    disable_application,
    enable_application,
    get_application,
    list_applications,
    list_developer_keys,
    revoke_developer_key,
    rotate_developer_key,
    update_application,
)
from plugins.ai.domain.developer_api_errors import DeveloperApiError
from plugins.ai.entity.vo.ai_developer_api_vo import (
    ApplicationCreateModel,
    ApplicationModel,
    ApplicationPageQueryModel,
    ApplicationUpdateModel,
    DeveloperKeyCreatedModel,
    DeveloperKeyCreateModel,
    DeveloperKeyRotateModel,
    DeveloperKeySummaryModel,
)
from plugins.ai.infrastructure import developer_api_provider as provider_module

NOW = datetime(2026, 8, 18, 16, 0)
APPLICATION_ID = 'app_00000000000040008000000000000001'
KEY_ID = 'key_00000000000040008000000000000001'
MODEL_ID = 'mdl_00000000000040008000000000000001'
SECRET_KEY = f'sk-zh_{KEY_ID}_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA'
EXPECTED_ROUTE_COUNT = 12
EXPECTED_WRITE_COUNT = 8
OWNER_USER_ID = 7
NOT_FOUND_STATUS = 404


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


def current_user(*, admin: bool = False) -> CurrentUserModel:
    user_id = 1 if admin else OWNER_USER_ID
    return CurrentUserModel(
        permissions=['*:*:*'],
        roles=[],
        user=UserInfoModel(userId=user_id, userName='admin' if admin else 'alice', dept=None, role=[]),
    )


def decode(response: Response) -> dict[str, Any]:
    return json.loads(response.body)


def application_payload() -> ApplicationCreateModel:
    return ApplicationCreateModel(applicationCode='billing-prod', applicationName='Billing')


def key_payload() -> DeveloperKeyCreateModel:
    return DeveloperKeyCreateModel.model_validate(
        {
            'keyName': 'production',
            'modelIds': [MODEL_ID],
            'quota': {
                'rpmLimit': 60,
                'monthlyBudget': '100.00',
                'warningRatio': '0.8000',
                'hardLimitRatio': '1.0000',
            },
        }
    )


def application_data() -> dict[str, Any]:
    return {
        'applicationId': APPLICATION_ID,
        'applicationCode': 'billing-prod',
        'applicationName': 'Billing',
        'ownerUserId': 7,
        'status': 'active',
        'version': 1,
        'disabledAt': None,
        'deletedAt': None,
        'createdBy': 'alice',
        'createdAt': NOW,
        'updatedBy': 'alice',
        'updatedAt': NOW,
    }


def key_data(*, include_secret: bool) -> dict[str, Any]:
    data = {
        'keyId': KEY_ID,
        'applicationId': APPLICATION_ID,
        'keyName': 'production',
        'maskedKey': 'sk-zh_key_000...AAAA',
        'fingerprint': 'AAAA',
        'status': 'active',
        'expiresAt': None,
        'retireAt': None,
        'lastUsedAt': None,
        'createdAt': NOW,
        'grants': [{'grantId': 'grt_00000000000040008000000000000001', 'modelId': MODEL_ID, 'status': 'active'}],
        'quota': {
            'quotaId': 'qta_00000000000040008000000000000001',
            'rpmLimit': 60,
            'tpmLimit': None,
            'concurrencyLimit': None,
            'monthlyBudget': Decimal('100.00'),
            'warningRatio': Decimal('0.8000'),
            'hardLimitRatio': Decimal('1.0000'),
            'currency': 'USD',
            'version': 1,
        },
    }
    if include_secret:
        data['apiKey'] = SECRET_KEY
    return data


class FakeApplicationService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.error: DeveloperApiError | None = None

    async def _result(self, method: str, *args: object, **kwargs: object) -> object:
        self.calls.append((method, args, kwargs))
        if self.error is not None:
            raise self.error
        if method == 'list_applications':
            return PageModel(rows=[application_data()], pageNum=1, pageSize=10, total=1, hasNext=False)
        return ApplicationModel.model_validate(application_data())

    async def list_applications(self, *args: object, **kwargs: object) -> object:
        return await self._result('list_applications', *args, **kwargs)

    async def get_application(self, *args: object, **kwargs: object) -> object:
        return await self._result('get_application', *args, **kwargs)

    async def create_application(self, *args: object, **kwargs: object) -> object:
        return await self._result('create_application', *args, **kwargs)

    async def update_application(self, *args: object, **kwargs: object) -> object:
        return await self._result('update_application', *args, **kwargs)

    async def disable_application(self, *args: object, **kwargs: object) -> object:
        return await self._result('disable_application', *args, **kwargs)

    async def enable_application(self, *args: object, **kwargs: object) -> object:
        return await self._result('enable_application', *args, **kwargs)

    async def delete_application(self, *args: object, **kwargs: object) -> object:
        return await self._result('delete_application', *args, **kwargs)


class FakeKeyService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.error: DeveloperApiError | None = None

    async def _result(self, method: str, *args: object, **kwargs: object) -> object:
        self.calls.append((method, args, kwargs))
        if self.error is not None:
            raise self.error
        if method == 'list_keys':
            return [DeveloperKeySummaryModel.model_validate(key_data(include_secret=False))]
        model = DeveloperKeyCreatedModel if method in {'create_key', 'rotate_key'} else DeveloperKeySummaryModel
        return model.model_validate(key_data(include_secret=method in {'create_key', 'rotate_key'}))

    async def list_keys(self, *args: object, **kwargs: object) -> object:
        return await self._result('list_keys', *args, **kwargs)

    async def create_key(self, *args: object, **kwargs: object) -> object:
        return await self._result('create_key', *args, **kwargs)

    async def rotate_key(self, *args: object, **kwargs: object) -> object:
        return await self._result('rotate_key', *args, **kwargs)

    async def revoke_key(self, *args: object, **kwargs: object) -> object:
        return await self._result('revoke_key', *args, **kwargs)


@pytest.fixture(autouse=True)
def controller_context(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[FakeApplicationService, FakeKeyService, list[object]], None, None]:
    application_service = FakeApplicationService()
    key_service = FakeKeyService()
    operation_logs: list[object] = []
    user_token = RequestContext.set_current_user(current_user())
    patterns_token = RequestContext.set_current_exclude_patterns([])

    async def capture_log(_request: Request, operation_log: object, _source: str) -> None:
        operation_logs.append(operation_log)

    monkeypatch.setattr(log_annotation.LogQueueService, 'enqueue_operation_log', capture_log)
    monkeypatch.setattr(controller_module, 'get_application_service', lambda _db: application_service)
    monkeypatch.setattr(controller_module, 'get_developer_key_service', lambda _db: key_service)
    monkeypatch.setattr(controller_module, '_current_time', lambda: NOW)
    try:
        yield application_service, key_service, operation_logs
    finally:
        RequestContext.reset_current_user(user_token)
        RequestContext.reset_current_exclude_patterns(patterns_token)


def route_map() -> dict[tuple[str, str], APIRoute]:
    return {
        (next(iter(route.methods)), route.path): route
        for route in ai_application_controller.routes
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
    return next(
        (cell.cell_contents for cell in (route.endpoint.__closure__ or ()) if isinstance(cell.cell_contents, Log)),
        None,
    )


def test_routes_use_exact_methods_permissions_login_and_no_data_scope() -> None:
    expected = {
        ('GET', '/ai/applications'): 'ai:application:list',
        ('GET', '/ai/applications/{application_id}'): 'ai:application:query',
        ('POST', '/ai/applications'): 'ai:application:add',
        ('PATCH', '/ai/applications/{application_id}'): 'ai:application:edit',
        ('POST', '/ai/applications/{application_id}/disable'): 'ai:application:disable',
        ('POST', '/ai/applications/{application_id}/enable'): 'ai:application:enable',
        ('DELETE', '/ai/applications/{application_id}'): 'ai:application:delete',
        ('GET', '/ai/applications/{application_id}/keys'): 'ai:application:key:list',
        ('POST', '/ai/applications/{application_id}/keys'): 'ai:application:key:add',
        ('POST', '/ai/applications/{application_id}/keys/{key_id}/rotate'): 'ai:application:key:rotate',
        ('POST', '/ai/applications/{application_id}/keys/{key_id}/revoke'): 'ai:application:key:revoke',
        ('GET', '/ai/applications/{application_id}/usage'): 'ai:application:query',
    }
    routes = route_map()
    assert ai_application_controller.prefix == '/ai/applications'
    assert len(routes) == EXPECTED_ROUTE_COUNT
    assert set(routes) == set(expected)
    for key, permission in expected.items():
        calls = dependency_calls(routes[key])
        assert any(type(call).__name__ == 'PreAuth' for call in calls)
        assert [call.perm for call in calls if type(call).__name__ == 'CheckUserInterfaceAuth'] == [permission]
        assert not any(isinstance(call, GetDataScope) for call in calls)


def test_write_routes_use_path_only_request_and_secret_safe_response_allowlists() -> None:
    routes = route_map()
    write_routes = {key: log_decorator(route) for key, route in routes.items() if key[0] in {'POST', 'PATCH', 'DELETE'}}
    assert len(write_routes) == EXPECTED_WRITE_COUNT
    for key, decorator in write_routes.items():
        assert decorator is not None
        assert decorator.response_log_mode == 'include'
        assert 'data.api_key' not in decorator.response_include_fields
        if key == ('POST', '/ai/applications'):
            assert decorator.request_log_mode == 'none'
        else:
            assert decorator.request_log_mode == 'include'
            assert set(decorator.request_include_fields) <= {
                'path_params.application_id',
                'path_params.key_id',
            }


def test_controller_boundary_and_provider_constructs_codec_without_clients_or_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Path(controller_module.__file__).read_text(encoding='utf-8')
    assert 'request.app.state' not in source
    assert 'LiteLLM' not in source
    assert 'Redis' not in source
    assert 'DeveloperKeyCodec' not in source

    settings = SimpleNamespace(
        api_key_pepper_current_version='v1',
        api_key_pepper_v1=SecretStr('p' * 32),
    )
    monkeypatch.setattr(provider_module, 'LiteLLMSettings', lambda: settings)
    application_service = provider_module.get_application_service(object())
    key_service = provider_module.get_developer_key_service(object())
    assert type(application_service).__name__ == 'ApplicationService'
    assert type(key_service).__name__ == 'DeveloperKeyService'
    assert key_service._codec.issue(KEY_ID).complete_key.startswith(f'sk-zh_{KEY_ID}_')


@pytest.mark.asyncio
async def test_controller_delegates_all_operations_with_actor_and_time(
    controller_context: tuple[FakeApplicationService, FakeKeyService, list[object]],
) -> None:
    app_service, key_service, _ = controller_context
    db = object()
    owner = current_user()
    query = ApplicationPageQueryModel(pageNum=1, pageSize=10)
    update_payload = ApplicationUpdateModel(applicationName='Billing V2', expectedVersion=1)
    rotate_payload = DeveloperKeyRotateModel(gracePeriodSeconds=3600)

    await list_applications(build_request('/ai/applications'), query, db, owner)
    await get_application(build_request(f'/ai/applications/{APPLICATION_ID}'), APPLICATION_ID, db, owner)
    await create_application(build_request('/ai/applications', 'POST'), application_payload(), db, owner)
    await update_application(
        build_request(
            f'/ai/applications/{APPLICATION_ID}',
            'PATCH',
            path_params={'application_id': APPLICATION_ID},
        ),
        APPLICATION_ID,
        update_payload,
        db,
        owner,
    )
    await disable_application(
        build_request(
            f'/ai/applications/{APPLICATION_ID}/disable',
            'POST',
            path_params={'application_id': APPLICATION_ID},
        ),
        APPLICATION_ID,
        db,
        owner,
    )
    await enable_application(
        build_request(
            f'/ai/applications/{APPLICATION_ID}/enable',
            'POST',
            path_params={'application_id': APPLICATION_ID},
        ),
        APPLICATION_ID,
        db,
        owner,
    )
    await delete_application(
        build_request(
            f'/ai/applications/{APPLICATION_ID}',
            'DELETE',
            path_params={'application_id': APPLICATION_ID},
        ),
        APPLICATION_ID,
        db,
        owner,
    )
    await list_developer_keys(build_request(f'/ai/applications/{APPLICATION_ID}/keys'), APPLICATION_ID, db, owner)
    await create_developer_key(
        build_request(
            f'/ai/applications/{APPLICATION_ID}/keys',
            'POST',
            path_params={'application_id': APPLICATION_ID},
        ),
        APPLICATION_ID,
        key_payload(),
        db,
        owner,
    )
    await rotate_developer_key(
        build_request(
            f'/ai/applications/{APPLICATION_ID}/keys/{KEY_ID}/rotate',
            'POST',
            path_params={'application_id': APPLICATION_ID, 'key_id': KEY_ID},
        ),
        APPLICATION_ID,
        KEY_ID,
        rotate_payload,
        db,
        owner,
    )
    await revoke_developer_key(
        build_request(
            f'/ai/applications/{APPLICATION_ID}/keys/{KEY_ID}/revoke',
            'POST',
            path_params={'application_id': APPLICATION_ID, 'key_id': KEY_ID},
        ),
        APPLICATION_ID,
        KEY_ID,
        db,
        owner,
    )

    assert [call[0] for call in app_service.calls] == [
        'list_applications',
        'get_application',
        'create_application',
        'update_application',
        'disable_application',
        'enable_application',
        'delete_application',
    ]
    assert [call[0] for call in key_service.calls] == ['list_keys', 'create_key', 'rotate_key', 'revoke_key']
    for method, _args, kwargs in key_service.calls:
        assert kwargs['actor'].user_id == OWNER_USER_ID, method
        assert kwargs['actor'].is_admin is False, method
    assert key_service.calls[1][2]['now'] == NOW
    assert app_service.calls[2][2] == {'owner_user_id': OWNER_USER_ID, 'username': 'alice', 'now': NOW}


@pytest.mark.asyncio
async def test_domain_error_maps_to_http_status_without_internal_chain(
    controller_context: tuple[FakeApplicationService, FakeKeyService, list[object]],
) -> None:
    app_service, _, _ = controller_context
    app_service.error = DeveloperApiError.application_not_found()

    with pytest.raises(HTTPException) as captured:
        await get_application(
            build_request(f'/ai/applications/{APPLICATION_ID}'),
            APPLICATION_ID,
            object(),
            current_user(),
        )

    assert captured.value.status_code == NOT_FOUND_STATUS
    assert captured.value.detail == {'code': 'APPLICATION_NOT_FOUND', 'message': '应用不存在'}
    assert captured.value.__cause__ is None
    assert captured.value.__suppress_context__ is True


@pytest.mark.asyncio
async def test_key_secret_is_returned_once_but_never_written_to_operation_log(
    controller_context: tuple[FakeApplicationService, FakeKeyService, list[object]],
) -> None:
    _, _, operation_logs = controller_context
    dynamic_body_secret = 'request-secret-must-not-be-logged'
    request = build_request(
        f'/ai/applications/{APPLICATION_ID}/keys',
        'POST',
        body=json.dumps({'keyName': dynamic_body_secret, 'modelIds': [MODEL_ID]}).encode(),
        path_params={'application_id': APPLICATION_ID},
    )

    response = await create_developer_key(request, APPLICATION_ID, key_payload(), object(), current_user())

    assert decode(response)['data']['apiKey'] == SECRET_KEY
    assert len(operation_logs) == 1
    operation_log = operation_logs[0]
    assert json.loads(operation_log.oper_param) == {
        'mode': 'include',
        'selected': {'path_params.application_id': APPLICATION_ID},
    }
    response_audit = json.loads(operation_log.json_result)
    assert response_audit['selected']['data.key_id'] == KEY_ID
    assert response_audit['selected']['data.fingerprint'] == 'AAAA'
    assert SECRET_KEY not in operation_log.oper_param
    assert SECRET_KEY not in operation_log.json_result
    assert dynamic_body_secret not in operation_log.oper_param


@pytest.mark.asyncio
async def test_write_domain_error_is_audited_with_only_stable_fields(
    controller_context: tuple[FakeApplicationService, FakeKeyService, list[object]],
) -> None:
    _, key_service, operation_logs = controller_context
    key_service.error = DeveloperApiError.api_key_not_found()

    with pytest.raises(HTTPException) as captured:
        await revoke_developer_key(
            build_request(
                f'/ai/applications/{APPLICATION_ID}/keys/{KEY_ID}/revoke',
                'POST',
                path_params={'application_id': APPLICATION_ID, 'key_id': KEY_ID},
            ),
            APPLICATION_ID,
            KEY_ID,
            object(),
            current_user(),
        )

    assert captured.value.status_code == NOT_FOUND_STATUS
    assert captured.value.detail == {'code': 'API_KEY_NOT_FOUND', 'message': 'API Key 不存在'}
    assert len(operation_logs) == 1
    operation_log = operation_logs[0]
    assert json.loads(operation_log.oper_param)['selected'] == {
        'path_params.application_id': APPLICATION_ID,
        'path_params.key_id': KEY_ID,
    }
    assert json.loads(operation_log.json_result)['selected'] == {
        'code': 'API_KEY_NOT_FOUND',
        'msg': 'API Key 不存在',
    }
