import asyncio
import inspect
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from starlette.requests import Request

from config.get_db import get_db
from plugins.ai.config import LiteLLMSettings
from plugins.ai.controller import openai_compat_controller as controller
from plugins.ai.domain.public_api_errors import PublicApiError
from plugins.ai.entity.vo.ai_openai_compat_vo import (
    OpenAIChatCompletionResponseModel,
    OpenAIModelListResponseModel,
)
from plugins.ai.infrastructure import public_api_provider
from plugins.ai.infrastructure.developer_key_authenticator import DeveloperApiIdentity
from plugins.ai.infrastructure.public_api_provider import PublicApiServices, get_public_api_services
from plugins.ai.public import api_transport
from plugins.ai.service.api_entitlement_service import ApiEntitlementService
from plugins.ai.service.openai_compat_service import OpenAICompatService

APPLICATION_ID = 'app_00000000000040008000000000000001'
KEY_ID = 'key_00000000000040008000000000000001'
MODEL_CODE = 'deepseek-chat'
COMPLETE_KEY = f'sk-zh_{KEY_ID}_' + ('A' * 43)
AUTHORIZATION = f'Bearer {COMPLETE_KEY}'
MAX_BODY_BYTES = 2_048
CREATED = 1786982400
HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_UNAUTHORIZED = 401
HTTP_NOT_FOUND = 404


class FakeSession:
    def __init__(self) -> None:
        self.query_count = 0

    def in_transaction(self) -> bool:
        return False


class FakeAuthenticator:
    def __init__(self) -> None:
        self.calls: list[str | None] = []

    async def authenticate(self, authorization: str | None, *, now: object) -> SimpleNamespace:
        del now
        self.calls.append(authorization)
        if authorization != AUTHORIZATION:
            return SimpleNamespace(identity=None, failure='INVALID', base_exception_type=None)
        return SimpleNamespace(
            identity=DeveloperApiIdentity(application_id=APPLICATION_ID, key_id=KEY_ID),
            failure=None,
            base_exception_type=None,
        )


class FakeEntitlement:
    def __init__(self) -> None:
        self.list_calls: list[DeveloperApiIdentity] = []

    async def list_models(self, identity: DeveloperApiIdentity) -> OpenAIModelListResponseModel:
        self.list_calls.append(identity)
        return OpenAIModelListResponseModel.model_validate(
            {
                'object': 'list',
                'data': [{'id': MODEL_CODE, 'object': 'model', 'created': CREATED, 'owned_by': 'zhiheng'}],
            }
        )


class FakePublicStream:
    def __init__(self, frames: tuple[bytes, ...]) -> None:
        self._frames = iter(frames)
        self.close_count = 0

    def __aiter__(self) -> 'FakePublicStream':
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self._frames)
        except StopIteration:
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        self.close_count += 1


class FakeCompatService:
    def __init__(self) -> None:
        self.complete_calls: list[tuple[DeveloperApiIdentity, object]] = []
        self.stream_calls: list[tuple[DeveloperApiIdentity, object]] = []
        self.error: PublicApiError | None = None
        self.last_stream: FakePublicStream | None = None

    async def complete(self, identity: DeveloperApiIdentity, request: object) -> OpenAIChatCompletionResponseModel:
        self.complete_calls.append((identity, request))
        if self.error is not None:
            raise self.error
        return OpenAIChatCompletionResponseModel.model_validate(
            {
                'id': 'chatcmpl_00000000000040008000000000000001',
                'object': 'chat.completion',
                'created': CREATED,
                'model': MODEL_CODE,
                'choices': [
                    {
                        'index': 0,
                        'message': {'role': 'assistant', 'content': 'Hello'},
                        'finish_reason': 'stop',
                    }
                ],
                'usage': {'prompt_tokens': 2, 'completion_tokens': 1, 'total_tokens': 3},
            }
        )

    async def stream(self, identity: DeveloperApiIdentity, request: object) -> FakePublicStream:
        self.stream_calls.append((identity, request))
        if self.error is not None:
            raise self.error
        self.last_stream = FakePublicStream(
            (
                b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n',
                b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n',
                b'data: [DONE]\n\n',
            )
        )
        return self.last_stream


def _valid_body(*, stream: bool = False) -> dict[str, object]:
    return {
        'model': MODEL_CODE,
        'messages': [{'role': 'user', 'content': 'Hello'}],
        'stream': stream,
    }


def _app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool = True,
    max_body_bytes: int = MAX_BODY_BYTES,
) -> tuple[FastAPI, FakeSession, FakeAuthenticator, FakeEntitlement, FakeCompatService, list[int]]:
    app = FastAPI()
    app.include_router(controller.openai_compat_controller)
    session = FakeSession()
    authenticator = FakeAuthenticator()
    entitlement = FakeEntitlement()
    compat = FakeCompatService()
    provider_calls: list[int] = []

    async def fake_db() -> AsyncIterator[FakeSession]:
        yield session

    async def fake_provider(request: object, db: object, settings: object) -> PublicApiServices:
        del request, db, settings
        provider_calls.append(1)
        return PublicApiServices(
            authenticator=authenticator,
            entitlement=entitlement,
            coordinator=None,  # type: ignore[arg-type]
            compat=compat,
        )

    app.dependency_overrides[get_db] = fake_db
    monkeypatch.setattr(
        controller,
        '_settings_factory',
        lambda: SimpleNamespace(public_api_enabled=enabled, api_max_request_bytes=max_body_bytes),
    )
    monkeypatch.setattr(controller, '_public_api_services', fake_provider)
    return app, session, authenticator, entitlement, compat, provider_calls


async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        yield client


def test_public_router_is_independent_and_controller_has_no_internal_runtime_or_zhiheng_auth() -> None:
    routes = {(method, route.path) for route in controller.openai_compat_controller.routes for method in route.methods}
    assert routes == {
        ('GET', '/v1/models'),
        ('POST', '/v1/chat/completions'),
        ('POST', '/v1/embeddings'),
        ('POST', '/v1/rerank'),
    }
    assert controller.openai_compat_controller.dependencies == []
    source = Path(inspect.getfile(controller)).read_text(encoding='utf-8')
    for forbidden in (
        'PreAuth',
        'ResponseUtil',
        'CLIENT_STATE_NAME',
        'LiteLLMRuntimeAdapter',
        'request.app.state',
        'LITELLM_RUNTIME_KEY',
        'LITELLM_MANAGEMENT_KEY',
    ):
        assert forbidden not in source


@pytest.mark.asyncio
async def test_feature_flag_disabled_returns_real_404_without_constructing_services_or_querying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, session, _, _, _, provider_calls = _app(monkeypatch, enabled=False)

    async for client in _client(app):
        response = await client.get('/v1/models', headers={'Authorization': AUTHORIZATION})

    assert response.status_code == HTTP_NOT_FOUND
    assert response.json() == PublicApiError.feature_disabled().public_dict()
    assert provider_calls == []
    assert session.query_count == 0


@pytest.mark.asyncio
async def test_models_uses_single_bearer_and_returns_unwrapped_openai_list(monkeypatch: pytest.MonkeyPatch) -> None:
    app, _, authenticator, entitlement, _, _ = _app(monkeypatch)

    async for client in _client(app):
        response = await client.get('/v1/models', headers={'Authorization': AUTHORIZATION})

    assert response.status_code == HTTP_OK
    assert response.json() == {
        'object': 'list',
        'data': [{'id': MODEL_CODE, 'object': 'model', 'created': CREATED, 'owned_by': 'zhiheng'}],
    }
    assert authenticator.calls == [AUTHORIZATION]
    assert entitlement.list_calls == [DeveloperApiIdentity(application_id=APPLICATION_ID, key_id=KEY_ID)]


@pytest.mark.asyncio
async def test_repeated_authorization_is_rejected_without_forwarding_any_key(monkeypatch: pytest.MonkeyPatch) -> None:
    app, _, authenticator, entitlement, _, _ = _app(monkeypatch)

    async for client in _client(app):
        response = await client.get(
            '/v1/models',
            headers=[('Authorization', AUTHORIZATION), ('Authorization', 'Bearer attacker-key')],
        )

    assert response.status_code == HTTP_UNAUTHORIZED
    assert response.json()['error']['code'] == 'invalid_api_key'
    assert authenticator.calls == [None]
    assert entitlement.list_calls == []
    assert COMPLETE_KEY not in response.text


@pytest.mark.asyncio
async def test_non_stream_chat_returns_openai_json_without_platform_wrapper(monkeypatch: pytest.MonkeyPatch) -> None:
    app, _, _, _, compat, _ = _app(monkeypatch)

    async for client in _client(app):
        response = await client.post(
            '/v1/chat/completions',
            headers={'Authorization': AUTHORIZATION},
            json=_valid_body(),
        )

    assert response.status_code == HTTP_OK
    assert response.headers['content-type'].startswith('application/json')
    assert response.json()['object'] == 'chat.completion'
    assert response.json()['choices'][0]['message']['content'] == 'Hello'
    assert 'code' not in response.json()
    assert len(compat.complete_calls) == 1
    assert compat.stream_calls == []


@pytest.mark.asyncio
async def test_stream_chat_returns_real_sse_headers_frames_and_closes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    app, _, _, _, compat, _ = _app(monkeypatch)

    async for client in _client(app):
        response = await client.post(
            '/v1/chat/completions',
            headers={'Authorization': AUTHORIZATION},
            json=_valid_body(stream=True),
        )

    assert response.status_code == HTTP_OK
    assert response.headers['content-type'].startswith('text/event-stream')
    assert response.headers['cache-control'] == 'no-cache'
    assert response.headers['x-accel-buffering'] == 'no'
    assert response.content.endswith(b'data: [DONE]\n\n')
    assert len(compat.stream_calls) == 1
    assert compat.complete_calls == []
    assert compat.last_stream is not None
    assert compat.last_stream.close_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('headers', 'content'),
    (
        ({'Content-Type': 'application/json', 'Content-Length': 'invalid'}, b'{}'),
        ({'Content-Type': 'text/plain'}, b'{}'),
        ({'Content-Type': 'application/json'}, b'{"model":"unknown"}'),
    ),
)
async def test_invalid_body_metadata_or_shape_returns_fixed_400_before_model_call(
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
    content: bytes,
) -> None:
    app, _, _, _, compat, _ = _app(monkeypatch)
    request_headers = {'Authorization': AUTHORIZATION, **headers}

    async for client in _client(app):
        response = await client.post('/v1/chat/completions', headers=request_headers, content=content)

    assert response.status_code == HTTP_BAD_REQUEST
    assert response.json()['error']['code'] == 'invalid_request'
    assert compat.complete_calls == []
    assert compat.stream_calls == []


@pytest.mark.asyncio
async def test_chunked_body_over_limit_stops_before_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    app, _, _, _, compat, _ = _app(monkeypatch, max_body_bytes=16)

    async def chunks() -> AsyncIterator[bytes]:
        yield b'{"model":"deep'
        yield b'seek-chat","messages":[]}'

    async for client in _client(app):
        response = await client.post(
            '/v1/chat/completions',
            headers={'Authorization': AUTHORIZATION, 'Content-Type': 'application/json'},
            content=chunks(),
        )

    assert response.status_code == HTTP_BAD_REQUEST
    assert response.json()['error']['code'] == 'invalid_request'
    assert compat.complete_calls == []
    assert compat.stream_calls == []


@pytest.mark.asyncio
async def test_repeated_content_length_is_rejected_before_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    app, _, _, _, compat, _ = _app(monkeypatch)
    body = b'{"model":"deepseek-chat","messages":[{"role":"user","content":"Hello"}]}'

    async for client in _client(app):
        response = await client.post(
            '/v1/chat/completions',
            headers=[
                ('Authorization', AUTHORIZATION),
                ('Content-Type', 'application/json'),
                ('Content-Length', str(len(body))),
                ('Content-Length', str(len(body))),
            ],
            content=body,
        )

    assert response.status_code == HTTP_BAD_REQUEST
    assert response.json()['error']['code'] == 'invalid_request'
    assert compat.complete_calls == []
    assert compat.stream_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('error', 'status_code'),
    (
        (PublicApiError.upstream_error(), 502),
        (PublicApiError.service_unavailable(), 503),
    ),
)
async def test_pre_stream_failure_remains_json_error(
    monkeypatch: pytest.MonkeyPatch,
    error: PublicApiError,
    status_code: int,
) -> None:
    app, _, _, _, compat, _ = _app(monkeypatch)
    compat.error = error

    async for client in _client(app):
        response = await client.post(
            '/v1/chat/completions',
            headers={'Authorization': AUTHORIZATION},
            json=_valid_body(stream=True),
        )

    assert response.status_code == status_code
    assert response.headers['content-type'].startswith('application/json')
    assert response.json() == error.public_dict()


@pytest.mark.asyncio
async def test_stream_cancellation_is_not_replaced_by_close_failure() -> None:
    class CancellingStream:
        def __aiter__(self) -> 'CancellingStream':
            return self

        async def __anext__(self) -> bytes:
            raise asyncio.CancelledError

        async def aclose(self) -> None:
            raise RuntimeError('close failed')

    stream = api_transport.streaming_response(CancellingStream()).body_iterator

    with pytest.raises(asyncio.CancelledError):
        await anext(stream)


@pytest.mark.asyncio
async def test_controller_cancellation_traceback_does_not_retain_authorization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CancellingAuthenticator(FakeAuthenticator):
        async def authenticate(self, authorization: str | None, *, now: object) -> SimpleNamespace:
            del authorization, now
            return SimpleNamespace(identity=None, failure='CANCELLED', base_exception_type=asyncio.CancelledError)

    services = PublicApiServices(
        authenticator=CancellingAuthenticator(),  # type: ignore[arg-type]
        entitlement=FakeEntitlement(),  # type: ignore[arg-type]
        coordinator=None,  # type: ignore[arg-type]
        compat=FakeCompatService(),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        controller,
        '_settings_factory',
        lambda: SimpleNamespace(public_api_enabled=True, api_max_request_bytes=MAX_BODY_BYTES),
    )

    async def lambda_services(request: object, db: object, settings: object) -> PublicApiServices:
        del request, db, settings
        return services

    monkeypatch.setattr(controller, '_public_api_services', lambda_services)
    app = FastAPI()
    request = Request(
        {
            'type': 'http',
            'method': 'GET',
            'path': '/v1/models',
            'headers': [(b'authorization', AUTHORIZATION.encode())],
            'app': app,
        }
    )

    with pytest.raises(asyncio.CancelledError) as raised:
        await controller.list_public_models(request, FakeSession())  # type: ignore[arg-type]

    traceback = raised.value.__traceback__
    while traceback is not None:
        filename = traceback.tb_frame.f_code.co_filename
        if '/plugins/ai/' in filename and '/tests/' not in filename:
            assert COMPLETE_KEY not in repr(traceback.tb_frame.f_locals)
        traceback = traceback.tb_next


@pytest.mark.asyncio
async def test_public_provider_builds_real_auth_entitlement_and_compat_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = LiteLLMSettings.model_construct(
        api_key_pepper_current_version='v1',
        api_key_pepper_v1=SecretStr('p' * 32),
    )
    app = FastAPI()
    request = Request({'type': 'http', 'method': 'GET', 'path': '/v1/models', 'headers': [], 'app': app})

    def fake_session_factory(db: object) -> object:
        del db
        return FakeSession

    monkeypatch.setattr(public_api_provider, '_session_factory_factory', fake_session_factory)
    services = await get_public_api_services(request, FakeSession(), settings)

    assert type(services.entitlement) is ApiEntitlementService
    assert type(services.compat) is OpenAICompatService
    assert services.coordinator is not None
    assert services.authenticator is not None
