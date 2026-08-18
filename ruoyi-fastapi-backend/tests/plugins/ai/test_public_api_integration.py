from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from openai import AsyncOpenAI
from pydantic import SecretStr
from sqlalchemy import event, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from config.get_db import get_db
from plugins.ai.config import LiteLLMSettings
from plugins.ai.controller import openai_compat_controller as controller
from plugins.ai.entity.do.ai_developer_api_do import AiApiKey, AiApiKeyModelGrant, AiApplication
from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
from plugins.ai.entity.do.ai_resource_binding_do import AiResourceBinding
from plugins.ai.hooks import CLIENT_STATE_NAME
from plugins.ai.infrastructure.developer_key_codec import DeveloperKeyCodec, IssuedDeveloperKey
from plugins.ai.infrastructure.litellm_client import RuntimeChatResult
from plugins.ai.infrastructure.litellm_errors import LiteLLMUnavailableError

NOW = datetime(2026, 8, 18, 12, 0)
APPLICATION_ID = 'app_00000000000040008000000000000001'
OTHER_APPLICATION_ID = 'app_00000000000040008000000000000002'
KEY_ID = 'key_00000000000040008000000000000001'
OTHER_KEY_ID = 'key_00000000000040008000000000000002'
MODEL_ID = 'mdl_00000000000040008000000000000001'
OTHER_MODEL_ID = 'mdl_00000000000040008000000000000002'
BINDING_ID = 'bnd_00000000000040008000000000000001'
GRANT_ID = 'grt_00000000000040008000000000000001'
OTHER_GRANT_ID = 'grt_00000000000040008000000000000002'
MODEL_CODE = 'deepseek-chat'
OTHER_MODEL_CODE = 'other-private-chat'
ENGINE_MODEL = 'runtime-deepseek-chat'
PEPPER = 'p' * 32
HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
HTTP_NOT_FOUND = 404
HTTP_SERVICE_UNAVAILABLE = 503
EXPECTED_TOTAL_TOKENS = 7


class RuntimeByteStream:
    def __init__(self, owner: 'TransactionInspectingRuntimeClient', chunks: tuple[bytes, ...]) -> None:
        self._owner = owner
        self._chunks = iter(chunks)
        self.close_count = 0

    def __aiter__(self) -> 'RuntimeByteStream':
        return self

    async def __anext__(self) -> bytes:
        self._owner.record_transaction_state('stream-read')
        try:
            return next(self._chunks)
        except StopIteration:
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        self.close_count += 1


class TransactionInspectingRuntimeClient:
    def __init__(self) -> None:
        self.settings = SimpleNamespace(configured=True)
        self.sessions: list[AsyncSession] = []
        self.transaction_observations: list[tuple[str, tuple[bool, ...]]] = []
        self.chat_payloads: list[Mapping[str, object]] = []
        self.stream_payloads: list[Mapping[str, object]] = []
        self.streams: list[RuntimeByteStream] = []
        self.error: Exception | None = None
        self.close_count = 0

    def record_transaction_state(self, operation: str) -> None:
        states = tuple(session.in_transaction() for session in self.sessions)
        self.transaction_observations.append((operation, states))
        assert not any(states)

    async def list_models(self) -> Mapping[str, object]:
        self.record_transaction_state('list-models')
        return {'data': [{'id': ENGINE_MODEL}]}

    async def create_chat_completion(self, payload: Mapping[str, object]) -> RuntimeChatResult:
        self.record_transaction_state('chat')
        self.chat_payloads.append(payload)
        if self.error is not None:
            raise self.error
        return RuntimeChatResult(
            payload={
                'id': 'PRIVATE-runtime-request-id',
                'choices': [
                    {
                        'message': {
                            'content': 'Hello from runtime',
                            'reasoning_content': 'PRIVATE reasoning',
                        }
                    }
                ],
                'usage': {
                    'prompt_tokens': 4,
                    'completion_tokens': 3,
                    'completion_tokens_details': {'reasoning_tokens': 1},
                },
            }
        )

    def stream_chat_completion(self, payload: Mapping[str, object]) -> AsyncIterator[bytes]:
        self.record_transaction_state('stream-create')
        self.stream_payloads.append(payload)
        if self.error is not None:
            raise self.error
        stream = RuntimeByteStream(
            self,
            (
                b'data: {"id":"PRIVATE-runtime-request-id","choices":[{"delta":{"role":"assistant"}}]}\n\n',
                b'data: {"choices":[{"delta":{"reasoning_content":"PRIVATE reasoning"}}]}\n\n',
                b'data: {"choices":[{"delta":{"content":"Hello "}}]}\n\n',
                b'data: {"choices":[{"delta":{"content":"stream"}}],"usage":{"prompt_tokens":4,"completion_tokens":2}}\n\n',
                b'data: [DONE]\n\n',
            ),
        )
        self.streams.append(stream)
        return stream

    async def aclose(self) -> None:
        self.close_count += 1


@dataclass(frozen=True)
class PublicApiEnvironment:
    engine: AsyncEngine
    session_maker: async_sessionmaker[AsyncSession]
    app: FastAPI
    runtime: TransactionInspectingRuntimeClient
    issued_key: IssuedDeveloperKey
    settings: LiteLLMSettings


def _codec(key_id: str) -> DeveloperKeyCodec:
    fill = 1 if key_id == KEY_ID else 2
    return DeveloperKeyCodec(
        current_version='v1',
        peppers={'v1': SecretStr(PEPPER)},
        token_bytes=lambda size: bytes([fill]) * size,
    )


def _application(application_id: str, code: str) -> AiApplication:
    return AiApplication(
        application_id=application_id,
        application_code=code,
        application_name=code,
        owner_user_id=7,
        status='active',
        version=1,
        created_by='tester',
        created_at=NOW,
        updated_by='tester',
        updated_at=NOW,
    )


def _key(application_id: str, issued: IssuedDeveloperKey) -> AiApiKey:
    return AiApiKey(
        api_key_id=issued.key_id,
        application_id=application_id,
        key_name='integration-key',
        key_prefix=issued.prefix,
        secret_digest=issued.digest,
        digest_version=issued.digest_version,
        fingerprint=issued.fingerprint,
        status='active',
        created_by='tester',
        created_at=NOW,
        updated_by='tester',
        updated_at=NOW,
    )


def _model(model_id: str, code: str) -> AiModelCatalog:
    return AiModelCatalog(
        model_id=model_id,
        model_code=code,
        display_name=code,
        model_type='chat',
        capabilities={'streaming': True},
        context_window=8_192,
        max_output_tokens=1_024,
        default_parameters={'temperature': 0.1},
        lifecycle_status='active',
        published_at=NOW,
        create_by='tester',
        create_time=NOW,
        update_by='tester',
        update_time=NOW,
    )


def _binding() -> AiResourceBinding:
    return AiResourceBinding(
        binding_id=BINDING_ID,
        resource_type='model',
        resource_id=MODEL_ID,
        engine_type='litellm',
        engine_resource_id=ENGINE_MODEL,
        binding_status='active',
        engine_metadata={},
        version=1,
        create_by='tester',
        create_time=NOW,
        update_by='tester',
        update_time=NOW,
    )


def _grant(grant_id: str, key_id: str, model_id: str) -> AiApiKeyModelGrant:
    return AiApiKeyModelGrant(
        grant_id=grant_id,
        api_key_id=key_id,
        platform_model_id=model_id,
        status='active',
        created_by='tester',
        created_at=NOW,
        updated_by='tester',
        updated_at=NOW,
    )


@pytest_asyncio.fixture
async def public_api_environment(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[PublicApiEnvironment]:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')

    @event.listens_for(engine.sync_engine, 'connect')
    def configure_sqlite(connection: Any, _record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()

    session_maker = async_sessionmaker(engine, expire_on_commit=True)
    async with engine.begin() as connection:
        for table in (
            AiModelCatalog.__table__,
            AiResourceBinding.__table__,
            AiApplication.__table__,
            AiApiKey.__table__,
            AiApiKeyModelGrant.__table__,
        ):
            await connection.run_sync(table.create)

    issued = _codec(KEY_ID).issue(KEY_ID)
    other_issued = _codec(OTHER_KEY_ID).issue(OTHER_KEY_ID)
    async with session_maker() as session:
        session.add_all(
            (
                _application(APPLICATION_ID, 'billing-prod'),
                _application(OTHER_APPLICATION_ID, 'other-prod'),
                _model(MODEL_ID, MODEL_CODE),
                _model(OTHER_MODEL_ID, OTHER_MODEL_CODE),
            )
        )
        await session.commit()
        session.add_all((_key(APPLICATION_ID, issued), _key(OTHER_APPLICATION_ID, other_issued), _binding()))
        await session.commit()
        session.add_all(
            (
                _grant(GRANT_ID, KEY_ID, MODEL_ID),
                _grant(OTHER_GRANT_ID, OTHER_KEY_ID, OTHER_MODEL_ID),
            )
        )
        await session.commit()

    runtime = TransactionInspectingRuntimeClient()
    app = FastAPI()
    setattr(app.state, CLIENT_STATE_NAME, runtime)
    app.include_router(controller.openai_compat_controller)

    async def test_db() -> AsyncIterator[AsyncSession]:
        async with session_maker() as session:
            runtime.sessions.append(session)
            try:
                yield session
            finally:
                runtime.sessions.remove(session)

    app.dependency_overrides[get_db] = test_db
    settings = LiteLLMSettings.model_construct(
        public_api_enabled=True,
        api_max_request_bytes=1_048_576,
        api_key_pepper_current_version='v1',
        api_key_pepper_v1=SecretStr(PEPPER),
    )
    monkeypatch.setattr(controller, '_settings_factory', lambda: settings)
    yield PublicApiEnvironment(engine, session_maker, app, runtime, issued, settings)
    await engine.dispose()


def _authorization(environment: PublicApiEnvironment) -> dict[str, str]:
    return {'Authorization': f'Bearer {environment.issued_key.complete_key}'}


async def _http_client(environment: PublicApiEnvironment) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=environment.app)
    async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
        yield client


async def _openai_client(environment: PublicApiEnvironment) -> AsyncIterator[AsyncOpenAI]:
    transport = httpx.ASGITransport(app=environment.app)
    async with httpx.AsyncClient(transport=transport, base_url='http://test') as http_client:
        client = AsyncOpenAI(
            api_key=environment.issued_key.complete_key,
            base_url='http://test/v1',
            http_client=http_client,
        )
        try:
            yield client
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_openai_sdk_lists_only_granted_model_and_completes_without_internal_leaks(
    public_api_environment: PublicApiEnvironment,
) -> None:
    environment = public_api_environment

    async for client in _openai_client(environment):
        models = await client.models.list()
        completion = await client.chat.completions.create(
            model=MODEL_CODE,
            messages=[{'role': 'user', 'content': 'Hello'}],
            temperature=0.2,
            max_tokens=64,
            stop=['END'],
            user='caller-42',
        )

    assert [model.id for model in models.data] == [MODEL_CODE]
    assert completion.model == MODEL_CODE
    assert completion.choices[0].message.content == 'Hello from runtime'
    assert completion.usage is not None and completion.usage.total_tokens == EXPECTED_TOTAL_TOKENS
    serialized = completion.model_dump_json()
    assert MODEL_ID not in serialized
    assert BINDING_ID not in serialized
    assert ENGINE_MODEL not in serialized
    assert 'PRIVATE' not in serialized
    assert len(environment.runtime.chat_payloads) == 1
    payload = environment.runtime.chat_payloads[0]
    assert payload == {
        'model': ENGINE_MODEL,
        'messages': [{'role': 'user', 'content': 'Hello'}],
        'temperature': 0.2,
        'max_tokens': 64,
        'stop': ['END'],
        'user': 'caller-42',
        'stream': False,
    }
    assert environment.issued_key.complete_key not in repr(payload)
    assert environment.runtime.transaction_observations
    assert all(states == (False,) for _, states in environment.runtime.transaction_observations)


@pytest.mark.asyncio
async def test_openai_sdk_consumes_true_sse_stream_with_stable_public_chunks(
    public_api_environment: PublicApiEnvironment,
) -> None:
    environment = public_api_environment

    async for client in _openai_client(environment):
        stream = await client.chat.completions.create(
            model=MODEL_CODE,
            messages=[{'role': 'user', 'content': 'Hello'}],
            stream=True,
        )
        chunks = [chunk async for chunk in stream]

    assert chunks[0].choices[0].delta.role == 'assistant'
    assert ''.join(chunk.choices[0].delta.content or '' for chunk in chunks) == 'Hello stream'
    assert chunks[-1].choices[0].finish_reason == 'stop'
    assert len({chunk.id for chunk in chunks}) == 1
    assert len({chunk.created for chunk in chunks}) == 1
    assert {chunk.model for chunk in chunks} == {MODEL_CODE}
    serialized = ''.join(chunk.model_dump_json() for chunk in chunks)
    assert 'PRIVATE' not in serialized
    assert MODEL_ID not in serialized
    assert ENGINE_MODEL not in serialized
    assert len(environment.runtime.stream_payloads) == 1
    assert environment.runtime.stream_payloads[0] == {
        'model': ENGINE_MODEL,
        'messages': [{'role': 'user', 'content': 'Hello'}],
        'temperature': 0.1,
        'stream': True,
        'stream_options': {'include_usage': True},
    }
    assert len(environment.runtime.streams) == 1
    assert environment.runtime.streams[0].close_count == 1
    assert environment.runtime.close_count == 0
    assert {operation for operation, _ in environment.runtime.transaction_observations} >= {
        'stream-create',
        'stream-read',
    }
    assert all(states == (False,) for _, states in environment.runtime.transaction_observations)


@pytest.mark.asyncio
async def test_revocation_application_grant_and_model_changes_take_effect_on_next_request(
    public_api_environment: PublicApiEnvironment,
) -> None:
    environment = public_api_environment
    async for client in _http_client(environment):
        assert (await client.get('/v1/models', headers=_authorization(environment))).status_code == HTTP_OK

        async with environment.session_maker() as session:
            await session.execute(update(AiApiKey).where(AiApiKey.api_key_id == KEY_ID).values(status='revoked'))
            await session.commit()
        revoked = await client.get('/v1/models', headers=_authorization(environment))
        assert revoked.status_code == HTTP_UNAUTHORIZED

        async with environment.session_maker() as session:
            await session.execute(update(AiApiKey).where(AiApiKey.api_key_id == KEY_ID).values(status='active'))
            await session.execute(
                update(AiApplication).where(AiApplication.application_id == APPLICATION_ID).values(status='disabled')
            )
            await session.commit()
        disabled_application = await client.get('/v1/models', headers=_authorization(environment))
        assert disabled_application.status_code == HTTP_UNAUTHORIZED

        async with environment.session_maker() as session:
            await session.execute(
                update(AiApplication).where(AiApplication.application_id == APPLICATION_ID).values(status='active')
            )
            await session.execute(
                update(AiApiKeyModelGrant).where(AiApiKeyModelGrant.grant_id == GRANT_ID).values(status='disabled')
            )
            await session.commit()
        no_grant = await client.get('/v1/models', headers=_authorization(environment))
        denied_chat = await client.post(
            '/v1/chat/completions',
            headers=_authorization(environment),
            json={'model': MODEL_CODE, 'messages': [{'role': 'user', 'content': 'Hello'}]},
        )
        assert no_grant.status_code == HTTP_OK and no_grant.json()['data'] == []
        assert denied_chat.status_code == HTTP_NOT_FOUND

        async with environment.session_maker() as session:
            await session.execute(
                update(AiApiKeyModelGrant).where(AiApiKeyModelGrant.grant_id == GRANT_ID).values(status='active')
            )
            await session.execute(
                update(AiModelCatalog).where(AiModelCatalog.model_id == MODEL_ID).values(lifecycle_status='disabled')
            )
            await session.commit()
        disabled_model = await client.get('/v1/models', headers=_authorization(environment))
        disabled_model_chat = await client.post(
            '/v1/chat/completions',
            headers=_authorization(environment),
            json={'model': MODEL_CODE, 'messages': [{'role': 'user', 'content': 'Hello'}]},
        )
        assert disabled_model.status_code == HTTP_OK and disabled_model.json()['data'] == []
        assert disabled_model_chat.status_code == HTTP_NOT_FOUND

    assert environment.runtime.chat_payloads == []
    assert environment.runtime.stream_payloads == []


@pytest.mark.asyncio
async def test_runtime_failure_returns_fixed_error_without_key_or_upstream_detail(
    public_api_environment: PublicApiEnvironment,
) -> None:
    environment = public_api_environment
    environment.runtime.error = LiteLLMUnavailableError(
        f'PRIVATE upstream https://runtime.internal Authorization: Bearer {environment.issued_key.complete_key}'
    )

    async for client in _http_client(environment):
        response = await client.post(
            '/v1/chat/completions',
            headers=_authorization(environment),
            json={'model': MODEL_CODE, 'messages': [{'role': 'user', 'content': 'Hello'}]},
        )

    assert response.status_code == HTTP_SERVICE_UNAVAILABLE
    assert response.json()['error']['code'] == 'service_unavailable'
    assert environment.issued_key.complete_key not in response.text
    assert 'PRIVATE' not in response.text
    assert 'runtime.internal' not in response.text
