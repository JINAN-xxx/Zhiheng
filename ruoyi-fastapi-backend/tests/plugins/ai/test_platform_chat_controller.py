import inspect
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import Request

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
        ('DELETE', '/ai/chat/session/{session_id}'),
        ('POST', '/ai/chat/cancel'),
    }
    assert expected <= routes
    assert len(controller_module.ai_chat_controller.dependencies) == EXPECTED_ROUTER_DEPENDENCY_COUNT
    assert 'PreAuth' in repr(controller_module.ai_chat_controller.dependencies[0].dependency)
    assert 'UserInterfaceAuth' in repr(controller_module.ai_chat_controller.dependencies[1].dependency)


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
