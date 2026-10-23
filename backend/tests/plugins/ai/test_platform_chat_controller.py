import inspect
import json
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from starlette.status import HTTP_422_UNPROCESSABLE_CONTENT

from common.annotation import log_annotation
from common.context import RequestContext
from common.enums import BusinessType
from config.get_db import get_db
from module_admin.service.login_service import LoginService
from plugins.ai.controller import ai_chat_controller as controller_module

EXPECTED_ROUTER_DEPENDENCY_COUNT = 2
TEST_USER_ID = 7


def _routes() -> set[tuple[str, str]]:
    return {(method, route.path) for route in controller_module.ai_chat_controller.routes for method in route.methods}


def test_platform_chat_routes_are_fixed_and_authenticated() -> None:
    routes = _routes()
    expected = {
        ('POST', '/ai/chat/send'),
        ('GET', '/ai/chat/config'),
        ('PUT', '/ai/chat/config'),
        ('GET', '/ai/chat/session/list'),
        ('GET', '/ai/chat/session/{session_id}'),
        ('PATCH', '/ai/chat/session/{session_id}'),
        ('PATCH', '/ai/chat/session/{session_id}/status'),
        ('DELETE', '/ai/chat/session/{session_id}'),
        ('POST', '/ai/chat/cancel'),
    }
    assert expected <= routes
    assert len(controller_module.ai_chat_controller.dependencies) == EXPECTED_ROUTER_DEPENDENCY_COUNT
    assert 'PreAuth' in repr(controller_module.ai_chat_controller.dependencies[0].dependency)
    assert 'UserInterfaceAuth' in repr(controller_module.ai_chat_controller.dependencies[1].dependency)


@pytest.mark.asyncio
async def test_status_route_passes_owned_session_and_validated_status_and_keeps_update_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = {'sessionId': 'ses_00000000000040008000000000000001', 'status': 'archived'}

    class FakeService:
        async def set_session_status(self, user_id: int, session_id: str, status: str) -> dict[str, str]:
            assert user_id == TEST_USER_ID
            assert session_id == result['sessionId']
            assert status == 'archived'
            return result

    monkeypatch.setattr(controller_module, '_platform_chat_service', lambda request, db: FakeService())
    endpoint = controller_module.set_chat_session_status
    request = Request(
        {
            'type': 'http',
            'method': 'PATCH',
            'path': f'/ai/chat/session/{result["sessionId"]}/status',
            'path_params': {'session_id': result['sessionId']},
            'headers': [(b'content-type', b'application/json')],
            'query_string': b'',
            'client': ('127.0.0.1', 12345),
            'app': FastAPI(),
        }
    )
    request._body = b'{"status":"archived"}'
    current_user = controller_module.CurrentUserModel.model_validate(
        {'permissions': [], 'roles': [], 'user': {'userId': TEST_USER_ID, 'userName': 'tester'}}
    )
    operation_logs = []

    async def capture_log(_request: Request, operation_log: object, _source: str) -> None:
        operation_logs.append(operation_log)

    monkeypatch.setattr(log_annotation.LogQueueService, 'enqueue_operation_log', capture_log)
    user_token = RequestContext.set_current_user(current_user)
    try:
        response = await endpoint(
            request,
            result['sessionId'],
            controller_module.PlatformSessionStatusRequestModel(status='archived'),
            object(),
            current_user,
        )
    finally:
        RequestContext.reset_current_user(user_token)
    assert json.loads(response.body)['data'] == result
    assert len(operation_logs) == 1
    assert operation_logs[0].business_type == BusinessType.UPDATE.value
    assert operation_logs[0].oper_name == 'tester'
    assert 'archived' in operation_logs[0].oper_param


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['active', 'archived'])
async def test_session_list_route_passes_status_and_pagination(monkeypatch: pytest.MonkeyPatch, status: str) -> None:
    class FakeService:
        async def list_sessions(self, user_id: int, **kwargs: object) -> SimpleNamespace:
            assert user_id == TEST_USER_ID
            assert kwargs == {'page_num': 2, 'page_size': 1, 'status': status}
            return SimpleNamespace(rows=[], page_num=2, page_size=1, total=1, has_next=False)

    monkeypatch.setattr(controller_module, '_platform_chat_service', lambda request, db: FakeService())
    current_user = controller_module.CurrentUserModel.model_validate(
        {'permissions': [], 'roles': [], 'user': {'userId': TEST_USER_ID, 'userName': 'tester'}}
    )
    query = controller_module.PlatformSessionPageQueryModel(pageNum=2, pageSize=1, status=status)
    request = Request({'type': 'http', 'method': 'GET', 'path': '/ai/chat/session/list', 'headers': []})

    response = await controller_module.get_chat_session_list(query, request, object(), current_user)

    payload = json.loads(response.body)
    assert payload['pageNum'] == query.page_num
    assert payload['total'] == 1


@pytest.mark.asyncio
async def test_session_status_http_contract_rejects_invalid_body_and_query_status() -> None:
    app = FastAPI()
    app.include_router(controller_module.ai_chat_controller)

    async def allow_dependency() -> None:
        return None

    async def database_dependency() -> object:
        return object()

    def current_user() -> controller_module.CurrentUserModel:
        return controller_module.CurrentUserModel.model_validate(
            {'permissions': [], 'roles': [], 'user': {'userId': TEST_USER_ID, 'userName': 'tester'}}
        )

    for route in controller_module.ai_chat_controller.routes:
        for dependency in route.dependencies:
            app.dependency_overrides[dependency.dependency] = allow_dependency
    app.dependency_overrides[get_db] = database_dependency
    app.dependency_overrides[LoginService.get_current_user] = current_user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        for payload in (
            {'status': 'deleted'},
            {'status': None},
            {'status': True},
            {},
            {'status': 'active', 'extra': 1},
        ):
            response = await client.patch('/ai/chat/session/ses_00000000000040008000000000000001/status', json=payload)
            assert response.status_code == HTTP_422_UNPROCESSABLE_CONTENT
        response = await client.get('/ai/chat/session/list?status=deleted')
        assert response.status_code == HTTP_422_UNPROCESSABLE_CONTENT


def test_controller_no_longer_depends_on_agno_or_legacy_model_scope() -> None:
    source = Path(inspect.getfile(controller_module)).read_text(encoding='utf-8')
    for forbidden in ('DataScopeDependency', 'AiModels', 'AiChatService', 'AiModelService', 'agno', 'acancel_run'):
        assert forbidden not in source
    assert 'request.app.state' not in source


def test_sse_event_serializer_has_platform_headers_and_never_serializes_prompt() -> None:
    event = controller_module._serialize_platform_event(
        controller_module.PlatformChatEvent('content', {'sessionId': 'ses_1', 'delta': 'hello'})
    )
    assert event == 'event: content\ndata: {"delta":"hello","sessionId":"ses_1"}\n\n'
    assert 'text/event-stream' in controller_module.PLATFORM_SSE_MEDIA_TYPE
    assert controller_module.PLATFORM_SSE_HEADERS == {
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
    }
    assert 'secret prompt' not in json.dumps(event)


def test_controller_exposes_platform_service_factory() -> None:
    assert callable(controller_module._platform_chat_service)
    assert inspect.iscoroutinefunction(controller_module.send_chat_message)
    assert inspect.iscoroutinefunction(controller_module.cancel_chat_run)


@pytest.mark.asyncio
async def test_send_builds_sse_response_without_provider_payload() -> None:
    class FakeStream:
        def __aiter__(self) -> AsyncIterator[Any]:
            return self

        async def __anext__(self) -> controller_module.PlatformChatEvent:
            if getattr(self, 'done', False):
                raise StopAsyncIteration
            self.done = True
            return controller_module.PlatformChatEvent('done', {'status': 'completed'})

        async def aclose(self) -> None:
            self.closed = True

    class FakeService:
        async def send(self, user_id: int, request: Any) -> FakeStream:
            assert user_id == TEST_USER_ID
            assert request.message == 'hello'
            return FakeStream()

    request = Request({'type': 'http', 'method': 'POST', 'path': '/ai/chat/send', 'headers': [], 'app': object()})
    current_user = controller_module.CurrentUserModel.model_validate(
        {'permissions': [], 'roles': [], 'user': {'userId': TEST_USER_ID, 'userName': 'tester'}}
    )
    chat_request = controller_module.PlatformChatRequestModel.model_validate(
        {'modelId': 'mdl_423e4567e89b42d3a456426614174444', 'clientMessageId': 'msg-1', 'message': 'hello'}
    )
    original = controller_module._platform_chat_service
    controller_module._platform_chat_service = lambda request, db: FakeService()
    try:
        response = await controller_module.send_chat_message(request, chat_request, object(), current_user)
        assert response.media_type == 'text/event-stream'
        assert response.headers['cache-control'] == 'no-cache'
        assert response.headers['x-accel-buffering'] == 'no'
    finally:
        controller_module._platform_chat_service = original
