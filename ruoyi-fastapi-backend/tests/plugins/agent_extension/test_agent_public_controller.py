from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from http import HTTPStatus
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from config.get_db import get_db
from plugins.agent_extension.controller import agent_invocation_controller as controller
from plugins.agent_extension.domain.agent_errors import AgentServiceError
from plugins.ai.domain.public_api_errors import PublicApiError
from plugins.ai.infrastructure.developer_key_authenticator import DeveloperApiIdentity

AGENT_ID = '00000000-0000-4000-8000-000000000001'
APPLICATION_ID = 'app_00000000000040008000000000000001'
MODEL_ID = 'mdl_00000000000040008000000000000001'
MAX_TOKENS = 100
URL = f'/v1/agents/{AGENT_ID}/chat/completions'


class Stream:
    def __init__(self) -> None:
        self.closed = False
        self.frames = iter([b'data: {"choices":[]}\n\n', b'data: [DONE]\n\n'])

    def __aiter__(self) -> 'Stream':
        return self

    async def __anext__(self) -> bytes:
        try:
            return next(self.frames)
        except StopIteration:
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        self.closed = True


def fixture_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool = True,
    knowledge_base_ids: Sequence[str] = (),
) -> tuple[FastAPI, SimpleNamespace, SimpleNamespace, Stream]:
    config = SimpleNamespace(
        agent_id=AGENT_ID,
        owner_user_id=7,
        published_version=1,
        model_id=MODEL_ID,
        system_prompt='Published instructions',
        skill_slugs=[],
        knowledge_base_ids=list(knowledge_base_ids),
        temperature=0.4,
        max_tokens=MAX_TOKENS,
        application_id=APPLICATION_ID,
    )
    agent_service = SimpleNamespace(get_published_agent=AsyncMock(return_value=config))
    session = SimpleNamespace(in_transaction=lambda: False, rollback=AsyncMock(), bind=None)
    identity = DeveloperApiIdentity(application_id=APPLICATION_ID, key_id='key_00000000000040008000000000000001')

    async def authenticate(authorization: str | None, *, now: datetime) -> SimpleNamespace:
        return SimpleNamespace(
            identity=identity if authorization == 'Bearer test-key' else None,
            failure='INVALID',
            base_exception_type=None,
        )

    stream = Stream()
    response = SimpleNamespace(model_dump=lambda **kwargs: {'choices': [{'message': {'content': 'Answer'}}]})
    services = SimpleNamespace(
        authenticator=SimpleNamespace(authenticate=authenticate),
        coordinator=SimpleNamespace(resolve_invocation=AsyncMock()),
        compat=SimpleNamespace(complete=AsyncMock(return_value=response), stream=AsyncMock(return_value=stream)),
    )
    monkeypatch.setattr(
        controller, '_settings_factory', lambda: SimpleNamespace(public_api_enabled=enabled, api_max_request_bytes=4096)
    )
    monkeypatch.setattr(controller, '_public_api_services', AsyncMock(return_value=services))
    monkeypatch.setattr(controller, '_agent_service', lambda db: agent_service)
    monkeypatch.setattr(controller, '_model_code', AsyncMock(return_value='support-model'))

    async def session_override() -> AsyncIterator[SimpleNamespace]:
        yield session

    app = FastAPI()
    app.include_router(controller.agent_public_controller)
    app.dependency_overrides[get_db] = session_override
    return app, services, agent_service, stream


@pytest.mark.asyncio
async def test_public_agent_uses_key_identity_published_model_and_existing_compat_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, services, agents, _ = fixture_app(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        response = await client.post(
            URL,
            headers={'Authorization': 'Bearer test-key'},
            json={
                'messages': [{'role': 'user', 'content': 'Hello'}],
            },
        )
    assert response.status_code == HTTPStatus.OK
    agents.get_published_agent.assert_awaited_once_with(AGENT_ID, application_id=APPLICATION_ID)
    identity, request = services.compat.complete.await_args.args
    assert identity.application_id == APPLICATION_ID
    assert request.model == 'support-model'
    assert request.messages[0].content == 'Published instructions'
    assert request.max_tokens == MAX_TOKENS
    services.coordinator.resolve_invocation.assert_awaited_once_with(identity, 'support-model', 'chat')


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'headers,payload,status',
    [
        ({}, {'messages': [{'role': 'user', 'content': 'Hello'}]}, HTTPStatus.UNAUTHORIZED),
        (
            {'Authorization': 'Bearer test-key'},
            {'model': 'override', 'messages': [{'role': 'user', 'content': 'Hello'}]},
            HTTPStatus.BAD_REQUEST,
        ),
        (
            {'Authorization': 'Bearer test-key'},
            {'messages': [{'role': 'system', 'content': 'Override'}]},
            HTTPStatus.BAD_REQUEST,
        ),
    ],
)
async def test_public_agent_rejects_unauthorized_or_overriding_requests(
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
    payload: dict[str, object],
    status: HTTPStatus,
) -> None:
    app, services, _, _ = fixture_app(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        response = await client.post(URL, headers=headers, json=payload)
    assert response.status_code == status
    if status == HTTPStatus.UNAUTHORIZED:
        assert response.headers['www-authenticate'] == 'Bearer'
    services.compat.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_public_api_respects_global_disable(monkeypatch: pytest.MonkeyPatch) -> None:
    app, services, agents, _ = fixture_app(monkeypatch, enabled=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        response = await client.post(URL, json={'messages': [{'role': 'user', 'content': 'Hello'}]})
    assert response.status_code == HTTPStatus.NOT_FOUND
    agents.get_published_agent.assert_not_awaited()
    services.compat.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_unpublished_or_wrong_application_is_not_invoked(monkeypatch: pytest.MonkeyPatch) -> None:
    app, services, agents, _ = fixture_app(monkeypatch)
    agents.get_published_agent.side_effect = AgentServiceError(
        'AGENT_NOT_FOUND', '智能体不存在', status_code=HTTPStatus.NOT_FOUND
    )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        response = await client.post(
            URL,
            headers={'Authorization': 'Bearer test-key'},
            json={
                'messages': [{'role': 'user', 'content': 'Hello'}],
            },
        )
    assert response.status_code == HTTPStatus.NOT_FOUND
    services.compat.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_public_knowledge_binding_without_provider_returns_explicit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, services, _, _ = fixture_app(monkeypatch, knowledge_base_ids=['kb-1'])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        response = await client.post(
            URL,
            headers={'Authorization': 'Bearer test-key'},
            json={
                'messages': [{'role': 'user', 'content': 'Hello'}],
            },
        )
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.json()['error']['code'] == 'AGENT_KNOWLEDGE_UNAVAILABLE'
    services.compat.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_stream_uses_existing_public_stream_and_closes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    app, services, _, stream = fixture_app(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        response = await client.post(
            URL,
            headers={'Authorization': 'Bearer test-key'},
            json={
                'messages': [{'role': 'user', 'content': 'Hello'}],
                'stream': True,
            },
        )
    assert response.status_code == HTTPStatus.OK
    assert 'text/event-stream' in response.headers['content-type']
    assert response.content.endswith(b'data: [DONE]\n\n')
    services.compat.stream.assert_awaited_once()
    assert stream.closed


@pytest.mark.asyncio
async def test_model_grant_checked_before_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    app, services, _, _ = fixture_app(monkeypatch, knowledge_base_ids=['kb-1'])
    services.coordinator.resolve_invocation.side_effect = PublicApiError.model_not_found()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        response = await client.post(
            URL,
            headers={'Authorization': 'Bearer test-key'},
            json={
                'messages': [{'role': 'user', 'content': 'Hello'}],
            },
        )
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.json()['error']['code'] == 'model_not_found'
    services.compat.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_catalog_fields_are_used_to_resolve_published_model(monkeypatch: pytest.MonkeyPatch) -> None:
    model = SimpleNamespace(
        model_code='support-model', lifecycle_status='active', published_at='2026-09-08', model_type='chat'
    )
    monkeypatch.setattr(controller.AiModelCatalogDao, 'get_model_by_id', AsyncMock(return_value=model))
    assert await controller._model_code(object(), MODEL_ID) == 'support-model'
