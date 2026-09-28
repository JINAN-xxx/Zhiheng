from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import fakeredis.aioredis
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
from plugins.ai.entity.do.ai_developer_api_do import (
    AiApiKey,
    AiApiKeyModelGrant,
    AiApiQuotaPolicy,
    AiApiUsageLedger,
    AiApplication,
    AiModelRateCard,
)
from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
from plugins.ai.entity.do.ai_resource_binding_do import AiResourceBinding
from plugins.ai.hooks import CLIENT_STATE_NAME
from plugins.ai.infrastructure.developer_key_codec import DeveloperKeyCodec, IssuedDeveloperKey
from plugins.ai.infrastructure.litellm_client import RuntimeChatResult, RuntimeRerankResult
from plugins.ai.infrastructure.litellm_errors import LiteLLMUnavailableError

NOW = datetime(2026, 8, 18, 12, 0)
APPLICATION_ID = 'app_00000000000040008000000000000001'
OTHER_APPLICATION_ID = 'app_00000000000040008000000000000002'
KEY_ID = 'key_00000000000040008000000000000001'
OTHER_KEY_ID = 'key_00000000000040008000000000000002'
MODEL_ID = 'mdl_00000000000040008000000000000001'
OTHER_MODEL_ID = 'mdl_00000000000040008000000000000002'
EMBEDDING_MODEL_ID = 'mdl_00000000000040008000000000000003'
RERANK_MODEL_ID = 'mdl_00000000000040008000000000000004'
BINDING_ID = 'bnd_00000000000040008000000000000001'
EMBEDDING_BINDING_ID = 'bnd_00000000000040008000000000000002'
RERANK_BINDING_ID = 'bnd_00000000000040008000000000000003'
GRANT_ID = 'grt_00000000000040008000000000000001'
OTHER_GRANT_ID = 'grt_00000000000040008000000000000002'
EMBEDDING_GRANT_ID = 'grt_00000000000040008000000000000003'
RERANK_GRANT_ID = 'grt_00000000000040008000000000000004'
RATE_CARD_ID = 'rate_00000000000040008000000000000001'
EMBEDDING_RATE_CARD_ID = 'rate_00000000000040008000000000000002'
RERANK_RATE_CARD_ID = 'rate_00000000000040008000000000000003'
MODEL_CODE = 'deepseek-chat'
OTHER_MODEL_CODE = 'other-private-chat'
EMBEDDING_MODEL_CODE = 'company-embedding-v1'
RERANK_MODEL_CODE = 'company-rerank-v1'
ENGINE_MODEL = 'runtime-deepseek-chat'
EMBEDDING_ENGINE_MODEL = 'runtime-embedding-v1'
RERANK_ENGINE_MODEL = 'runtime-rerank-v1'
PEPPER = 'p' * 32
HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
HTTP_NOT_FOUND = 404
HTTP_SERVICE_UNAVAILABLE = 503
HTTP_RATE_LIMITED = 429
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
        self.embedding_payloads: list[Mapping[str, object]] = []
        self.rerank_payloads: list[Mapping[str, object]] = []
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

    async def create_embedding(
        self,
        payload: Mapping[str, object],
        *,
        max_json_response_bytes: int | None = None,
    ) -> Mapping[str, object]:
        del max_json_response_bytes
        self.record_transaction_state('embedding')
        self.embedding_payloads.append(payload)
        if self.error is not None:
            raise self.error
        inputs = payload.get('input')
        assert isinstance(inputs, list)
        return {
            'object': 'list',
            'data': [
                {'object': 'embedding', 'index': index, 'embedding': [0.0123 + index, -0.0456]}
                for index in range(len(inputs))
            ],
            'model': 'PRIVATE-runtime-embedding',
            'usage': {'prompt_tokens': 8, 'total_tokens': 8},
        }

    async def create_rerank(
        self,
        payload: Mapping[str, object],
        *,
        max_json_response_bytes: int | None = None,
    ) -> RuntimeRerankResult:
        del max_json_response_bytes
        self.record_transaction_state('rerank')
        self.rerank_payloads.append(payload)
        if self.error is not None:
            raise self.error
        documents = payload.get('documents')
        assert isinstance(documents, list)
        return RuntimeRerankResult(
            payload={
                'results': [
                    {'index': 1, 'relevance_score': 0.9921},
                    {'index': 0, 'relevance_score': 0.1100},
                ],
                'meta': {
                    'billed_units': {'search_units': 1},
                    'tokens': {'input_tokens': 22},
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


def _model(model_id: str, code: str, model_type: str) -> AiModelCatalog:
    return AiModelCatalog(
        model_id=model_id,
        model_code=code,
        display_name=code,
        model_type=model_type,
        capabilities={'streaming': True} if model_type == 'chat' else {},
        context_window=8_192 if model_type == 'chat' else None,
        max_output_tokens=1_024 if model_type == 'chat' else None,
        default_parameters={'temperature': 0.1} if model_type == 'chat' else {},
        lifecycle_status='active',
        published_at=NOW,
        create_by='tester',
        create_time=NOW,
        update_by='tester',
        update_time=NOW,
    )


def _binding(binding_id: str, model_id: str, engine_model: str) -> AiResourceBinding:
    return AiResourceBinding(
        binding_id=binding_id,
        resource_type='model',
        resource_id=model_id,
        engine_type='litellm',
        engine_resource_id=engine_model,
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


def _rate_card(rate_card_id: str, model_id: str, *, billing_mode: str) -> AiModelRateCard:
    return AiModelRateCard(
        rate_card_id=rate_card_id,
        platform_model_id=model_id,
        version=1,
        billing_mode=billing_mode,
        input_price_per_token=Decimal('0.000001') if billing_mode == 'token' else Decimal('0'),
        output_price_per_token=Decimal('0.000002') if billing_mode == 'token' else Decimal('0'),
        cache_price_per_token=None,
        search_price_per_unit=Decimal('0.001') if billing_mode == 'search_unit' else None,
        currency='USD',
        effective_from=NOW - timedelta(days=1),
        effective_to=None,
        status='active',
        created_by='tester',
        created_at=NOW,
        updated_by='tester',
        updated_at=NOW,
    )


def _quota(api_key_id: str) -> AiApiQuotaPolicy:
    return AiApiQuotaPolicy(
        quota_id=f'qta_0000000000004000800000000000000{1 if api_key_id == KEY_ID else 2}',
        api_key_id=api_key_id,
        rpm_limit=None,
        tpm_limit=None,
        concurrency_limit=None,
        monthly_budget=None,
        warning_ratio=Decimal('0.8000'),
        hard_limit_ratio=Decimal('1.0000'),
        currency='USD',
        version=1,
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
            AiApiQuotaPolicy.__table__,
            AiModelRateCard.__table__,
            AiApiUsageLedger.__table__,
        ):
            await connection.run_sync(table.create)

    issued = _codec(KEY_ID).issue(KEY_ID)
    other_issued = _codec(OTHER_KEY_ID).issue(OTHER_KEY_ID)
    async with session_maker() as session:
        session.add_all(
            (
                _application(APPLICATION_ID, 'billing-prod'),
                _application(OTHER_APPLICATION_ID, 'other-prod'),
                _model(MODEL_ID, MODEL_CODE, 'chat'),
                _model(OTHER_MODEL_ID, OTHER_MODEL_CODE, 'chat'),
                _model(EMBEDDING_MODEL_ID, EMBEDDING_MODEL_CODE, 'embedding'),
                _model(RERANK_MODEL_ID, RERANK_MODEL_CODE, 'rerank'),
            )
        )
        await session.commit()
        session.add_all(
            (
                _key(APPLICATION_ID, issued),
                _key(OTHER_APPLICATION_ID, other_issued),
                _binding(BINDING_ID, MODEL_ID, ENGINE_MODEL),
                _binding(EMBEDDING_BINDING_ID, EMBEDDING_MODEL_ID, EMBEDDING_ENGINE_MODEL),
                _binding(RERANK_BINDING_ID, RERANK_MODEL_ID, RERANK_ENGINE_MODEL),
            )
        )
        await session.commit()
        session.add_all(
            (
                _grant(GRANT_ID, KEY_ID, MODEL_ID),
                _grant(OTHER_GRANT_ID, OTHER_KEY_ID, OTHER_MODEL_ID),
                _grant(EMBEDDING_GRANT_ID, KEY_ID, EMBEDDING_MODEL_ID),
                _grant(RERANK_GRANT_ID, KEY_ID, RERANK_MODEL_ID),
            )
        )
        await session.commit()
        session.add_all(
            (
                _rate_card(RATE_CARD_ID, MODEL_ID, billing_mode='token'),
                _rate_card(EMBEDDING_RATE_CARD_ID, EMBEDDING_MODEL_ID, billing_mode='token'),
                _rate_card(RERANK_RATE_CARD_ID, RERANK_MODEL_ID, billing_mode='search_unit'),
                _quota(KEY_ID),
                _quota(OTHER_KEY_ID),
            )
        )
        await session.commit()

    runtime = TransactionInspectingRuntimeClient()
    redis = fakeredis.aioredis.FakeRedis()
    app = FastAPI()
    setattr(app.state, CLIENT_STATE_NAME, runtime)
    setattr(app.state, 'redis', redis)
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
    await redis.aclose()
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


def _latest_ledger(environment: PublicApiEnvironment) -> AiApiUsageLedger | None:
    """读取最近一条 Usage Ledger（用于断言结算生命周期）。"""
    return None


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

    assert [model.id for model in models.data] == [EMBEDDING_MODEL_CODE, RERANK_MODEL_CODE, MODEL_CODE]
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

    async with environment.session_maker() as session:
        from sqlalchemy import select

        ledgers = (await session.execute(select(AiApiUsageLedger))).scalars().all()
    assert len(ledgers) == 1
    assert ledgers[0].status == 'completed'
    assert ledgers[0].operation_type == 'chat'
    assert ledgers[0].input_tokens == 4
    assert ledgers[0].output_tokens == 3
    assert ledgers[0].cost_estimated is False
    assert ledgers[0].model_code == MODEL_CODE


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

    async with environment.session_maker() as session:
        from sqlalchemy import select

        ledgers = (await session.execute(select(AiApiUsageLedger))).scalars().all()
    assert len(ledgers) == 1
    assert ledgers[0].status == 'completed'
    assert ledgers[0].operation_type == 'chat'
    assert ledgers[0].input_tokens == 4
    assert ledgers[0].output_tokens == 2


@pytest.mark.asyncio
async def test_openai_sdk_creates_embedding_without_internal_leaks(
    public_api_environment: PublicApiEnvironment,
) -> None:
    environment = public_api_environment

    async for client in _openai_client(environment):
        response = await client.embeddings.create(
            model=EMBEDDING_MODEL_CODE,
            input=['first document', 'second document'],
            encoding_format='float',
            user='external-user-42',
        )

    assert response.model == EMBEDDING_MODEL_CODE
    assert response.object == 'list'
    assert [item.index for item in response.data] == [0, 1]
    assert len(response.data[0].embedding) == 2
    assert response.usage is not None and response.usage.prompt_tokens == 8
    serialized = response.model_dump_json()
    assert EMBEDDING_MODEL_ID not in serialized
    assert EMBEDDING_ENGINE_MODEL not in serialized
    assert 'PRIVATE' not in serialized
    assert environment.runtime.embedding_payloads == [
        {
            'model': EMBEDDING_ENGINE_MODEL,
            'input': ['first document', 'second document'],
            'user': 'external-user-42',
        }
    ]

    async with environment.session_maker() as session:
        from sqlalchemy import select

        ledgers = (await session.execute(select(AiApiUsageLedger))).scalars().all()
    assert len(ledgers) == 1
    assert ledgers[0].status == 'completed'
    assert ledgers[0].operation_type == 'embedding'
    assert ledgers[0].input_tokens == 8
    assert ledgers[0].cost_estimated is False
    assert ledgers[0].model_code == EMBEDDING_MODEL_CODE


@pytest.mark.asyncio
async def test_rerank_returns_stable_bounded_results_and_search_unit_ledger(
    public_api_environment: PublicApiEnvironment,
) -> None:
    environment = public_api_environment

    async for client in _http_client(environment):
        response = await client.post(
            '/v1/rerank',
            headers=_authorization(environment),
            json={
                'model': RERANK_MODEL_CODE,
                'query': 'Which document describes the refund policy?',
                'documents': [
                    'Shipping usually takes three days.',
                    {'text': 'Refunds are accepted within thirty days.'},
                ],
                'top_n': 2,
                'return_documents': True,
            },
        )

    assert response.status_code == HTTP_OK
    payload = response.json()
    assert payload['id'].startswith('rrk_') and len(payload['id']) == 36
    assert [item['index'] for item in payload['results']] == [1, 0]
    assert payload['results'][0]['document'] == {'text': 'Refunds are accepted within thirty days.'}
    assert payload['meta'] == {'billed_units': {'search_units': 1}, 'tokens': {'input_tokens': 22}}
    assert 'PRIVATE' not in response.text
    assert RERANK_ENGINE_MODEL not in response.text
    assert environment.runtime.rerank_payloads == [
        {
            'model': RERANK_ENGINE_MODEL,
            'query': 'Which document describes the refund policy?',
            'documents': [
                {'text': 'Shipping usually takes three days.'},
                {'text': 'Refunds are accepted within thirty days.'},
            ],
            'top_n': 2,
            'return_documents': True,
        }
    ]

    async with environment.session_maker() as session:
        from sqlalchemy import select

        ledgers = (await session.execute(select(AiApiUsageLedger))).scalars().all()
    assert len(ledgers) == 1
    assert ledgers[0].status == 'completed'
    assert ledgers[0].operation_type == 'rerank'
    assert ledgers[0].search_units == 1
    assert ledgers[0].input_tokens == 22
    assert ledgers[0].billable_cost == Decimal('0.001')
    assert ledgers[0].cost_estimated is False


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
        assert no_grant.status_code == HTTP_OK
        assert [item['id'] for item in no_grant.json()['data']] == [EMBEDDING_MODEL_CODE, RERANK_MODEL_CODE]
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
        assert disabled_model.status_code == HTTP_OK
        assert [item['id'] for item in disabled_model.json()['data']] == [EMBEDDING_MODEL_CODE, RERANK_MODEL_CODE]
        assert disabled_model_chat.status_code == HTTP_NOT_FOUND

    assert environment.runtime.chat_payloads == []
    assert environment.runtime.stream_payloads == []


@pytest.mark.asyncio
async def test_model_type_mismatch_returns_same_model_not_found_for_all_endpoints(
    public_api_environment: PublicApiEnvironment,
) -> None:
    environment = public_api_environment
    async for client in _http_client(environment):
        embedding_on_chat = await client.post(
            '/v1/embeddings',
            headers=_authorization(environment),
            json={'model': MODEL_CODE, 'input': ['hello']},
        )
        chat_on_embedding = await client.post(
            '/v1/chat/completions',
            headers=_authorization(environment),
            json={'model': EMBEDDING_MODEL_CODE, 'messages': [{'role': 'user', 'content': 'Hello'}]},
        )
        rerank_on_chat = await client.post(
            '/v1/rerank',
            headers=_authorization(environment),
            json={
                'model': MODEL_CODE,
                'query': 'q',
                'documents': ['d1', 'd2'],
            },
        )

    assert embedding_on_chat.status_code == HTTP_NOT_FOUND
    assert chat_on_embedding.status_code == HTTP_NOT_FOUND
    assert rerank_on_chat.status_code == HTTP_NOT_FOUND
    for response in (embedding_on_chat, chat_on_embedding, rerank_on_chat):
        assert response.json()['error']['code'] == 'model_not_found'
    assert environment.runtime.chat_payloads == []
    assert environment.runtime.embedding_payloads == []
    assert environment.runtime.rerank_payloads == []


@pytest.mark.asyncio
async def test_rate_card_billing_mode_mismatch_fails_closed_before_runtime(
    public_api_environment: PublicApiEnvironment,
) -> None:
    environment = public_api_environment
    async with environment.session_maker() as session:
        await session.execute(
            update(AiModelRateCard)
            .where(AiModelRateCard.rate_card_id == RERANK_RATE_CARD_ID)
            .values(billing_mode='token', search_price_per_unit=None)
        )
        await session.commit()

    async for client in _http_client(environment):
        response = await client.post(
            '/v1/rerank',
            headers=_authorization(environment),
            json={'model': RERANK_MODEL_CODE, 'query': 'q', 'documents': ['d1']},
        )

    assert response.status_code == HTTP_NOT_FOUND
    assert response.json()['error']['code'] == 'model_not_found'
    assert environment.runtime.rerank_payloads == []


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

    async with environment.session_maker() as session:
        from sqlalchemy import select

        ledgers = (await session.execute(select(AiApiUsageLedger))).scalars().all()
    assert len(ledgers) == 1
    assert ledgers[0].status == 'failed'
    assert ledgers[0].cost_estimated is True
