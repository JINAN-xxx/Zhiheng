import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import datetime
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from plugins.ai.dao.ai_resource_binding_dao import AiResourceBindingDao
from plugins.ai.domain.model_runtime import (
    ModelChatRequest,
    ModelChatResponse,
    ModelMessage,
    ModelStreamEvent,
    ModelUsage,
)
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
from plugins.ai.entity.do.ai_resource_binding_do import AiResourceBinding
from plugins.ai.service.model_service import (
    ModelService,
    ModelServiceChatResult,
    ModelServiceStreamResult,
    ResolvedModel,
)

MODEL_ID = 'mdl_123e4567e89b42d3a456426614174000'
BINDING_ID = 'bnd_987e4567e89b42d3a456426614174111'
ENGINE_MODEL = 'runtime-company-chat'
NOW = datetime(2026, 8, 13, 14, 30)
EXPECTED_INPUT_TOKENS = 11
EXPECTED_OUTPUT_TOKENS = 7
TWO_CLOSE_ATTEMPTS = 2


@pytest_asyncio.fixture
async def real_database() -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]]]:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')

    @event.listens_for(engine.sync_engine, 'connect')
    def enable_foreign_keys(connection: object, _record: object) -> None:
        cursor = connection.cursor()  # type: ignore[attr-defined]
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()

    session_maker = async_sessionmaker(bind=engine, expire_on_commit=True)
    async with engine.begin() as connection:
        await connection.run_sync(AiModelCatalog.__table__.create)
        await connection.run_sync(AiResourceBinding.__table__.create)
    try:
        yield engine, session_maker
    finally:
        await engine.dispose()


def _model(
    *,
    lifecycle_status: str = 'active',
    default_parameters: dict[str, object] | None = None,
) -> AiModelCatalog:
    return AiModelCatalog(
        model_id=MODEL_ID,
        model_code='company-chat',
        display_name='企业对话模型',
        model_type='chat',
        capabilities={'streaming': True},
        context_window=128_000,
        max_output_tokens=8_192,
        default_parameters=default_parameters
        if default_parameters is not None
        else {'temperature': 0.2, 'max_tokens': 2_048},
        lifecycle_status=lifecycle_status,
        published_at=NOW if lifecycle_status != 'draft' else None,
        create_by='admin',
        create_time=NOW,
        update_by='admin',
        update_time=NOW,
    )


def _binding(*, binding_status: str = 'active') -> AiResourceBinding:
    return AiResourceBinding(
        binding_id=BINDING_ID,
        resource_type='model',
        resource_id=MODEL_ID,
        engine_type='litellm',
        engine_resource_id=ENGINE_MODEL,
        binding_status=binding_status,
        engine_metadata={},
        version=1,
        create_by='admin',
        create_time=NOW,
        update_by='admin',
        update_time=NOW,
    )


class FakeSession:
    def __init__(self, *, active_transaction: bool = False) -> None:
        self._active_transaction = active_transaction
        self.rollback_count = 0
        self.rollback_error: BaseException | None = None

    def in_transaction(self) -> bool:
        return self._active_transaction

    def begin_read(self) -> None:
        self._active_transaction = True

    async def rollback(self) -> None:
        self.rollback_count += 1
        self._active_transaction = False
        if self.rollback_error is not None:
            raise self.rollback_error


class FakeModelDao:
    model: AiModelCatalog | None = _model()
    calls: list[str] = []

    @classmethod
    def reset(cls, model: AiModelCatalog | None = None) -> None:
        cls.model = _model() if model is None else model
        cls.calls = []

    @classmethod
    async def get_model_by_id(cls, db: FakeSession, model_id: str) -> AiModelCatalog | None:
        db.begin_read()
        cls.calls.append(model_id)
        return cls.model


class FakeBindingDao:
    binding: AiResourceBinding | None = _binding()
    calls: list[str] = []

    @classmethod
    def reset(cls, binding: AiResourceBinding | None = None) -> None:
        cls.binding = _binding() if binding is None else binding
        cls.calls = []

    @classmethod
    async def get_current_binding(cls, db: FakeSession, model_id: str) -> AiResourceBinding | None:
        db.begin_read()
        cls.calls.append(model_id)
        return cls.binding


class FakeEventStream:
    def __init__(
        self,
        events: Sequence[ModelStreamEvent] = (),
        *,
        error: BaseException | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self._events = iter(events)
        self._error = error
        self._close_error = close_error
        self.close_count = 0

    def __aiter__(self) -> 'FakeEventStream':
        return self

    async def __anext__(self) -> ModelStreamEvent:
        if self._error is not None:
            error = self._error
            self._error = None
            raise error
        try:
            return next(self._events)
        except StopIteration:
            raise StopAsyncIteration from None

    async def aclose(self) -> None:
        self.close_count += 1
        if self._close_error is not None:
            error = self._close_error
            self._close_error = None
            raise error


class FakeRuntime:
    def __init__(
        self,
        *,
        response: ModelChatResponse | None = None,
        stream: FakeEventStream | None = None,
        chat_error: BaseException | None = None,
        stream_construct_error: BaseException | None = None,
    ) -> None:
        self.response = response or ModelChatResponse(text='normal response')
        self.stream = stream or FakeEventStream()
        self.chat_error = chat_error
        self.stream_construct_error = stream_construct_error
        self.chat_requests: list[ModelChatRequest] = []
        self.stream_requests: list[ModelChatRequest] = []
        self.close_count = 0

    async def list_model_ids(self) -> set[str]:
        raise AssertionError('Model Service 调用入口不应查询运行时模型列表')

    async def chat(self, request: ModelChatRequest) -> ModelChatResponse:
        self.chat_requests.append(request)
        if self.chat_error is not None:
            raise self.chat_error
        return self.response

    def stream_chat(self, request: ModelChatRequest) -> AsyncIterator[ModelStreamEvent]:
        self.stream_requests.append(request)
        if self.stream_construct_error is not None:
            raise self.stream_construct_error
        return self.stream

    async def aclose(self) -> None:
        self.close_count += 1


class TransactionInspectingRuntime(FakeRuntime):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(stream=FakeEventStream((ModelStreamEvent(event_type='content', delta='only'),)))
        self._session = session
        self.chat_transaction_states: list[bool] = []
        self.stream_transaction_states: list[bool] = []

    async def chat(self, request: ModelChatRequest) -> ModelChatResponse:
        self.chat_transaction_states.append(self._session.in_transaction())
        return await super().chat(request)

    def stream_chat(self, request: ModelChatRequest) -> AsyncIterator[ModelStreamEvent]:
        self.stream_transaction_states.append(self._session.in_transaction())
        return super().stream_chat(request)


class CancellingBindingDao:
    @classmethod
    async def get_current_binding(cls, db: AsyncSession, model_id: str) -> AiResourceBinding | None:
        del db, model_id
        raise asyncio.CancelledError


class FailingBindingDao:
    error = RuntimeError('dao read failed')

    @classmethod
    async def get_current_binding(cls, db: AsyncSession, model_id: str) -> AiResourceBinding | None:
        await AiResourceBindingDao.get_current_binding(db, model_id)
        raise cls.error


@pytest.fixture(autouse=True)
def reset_daos() -> None:
    FakeModelDao.reset()
    FakeBindingDao.reset()


def _service(runtime: FakeRuntime | None = None) -> tuple[ModelService, FakeRuntime]:
    fake_runtime = runtime or FakeRuntime()
    return (
        ModelService(
            FakeSession(),
            fake_runtime,
            model_dao=FakeModelDao,
            binding_dao=FakeBindingDao,
        ),
        fake_runtime,
    )


def _messages() -> tuple[ModelMessage, ...]:
    return (ModelMessage(role='user', content='hello'),)


@pytest.mark.asyncio
async def test_explicit_non_thinking_parameter_is_validated_and_defaults_remain_unchanged() -> None:
    service, runtime = _service()
    await service.chat(MODEL_ID, _messages(), {'thinking_mode': 'disabled'})
    assert runtime.chat_requests[-1].parameters['thinking_mode'] == 'disabled'
    await service.chat(MODEL_ID, _messages())
    assert 'thinking_mode' not in runtime.chat_requests[-1].parameters
    for invalid in ({'type': 'disabled'}, True, 'enabled', 'arbitrary', None):
        with pytest.raises(ValueError):
            await service.chat(MODEL_ID, _messages(), {'thinking_mode': invalid})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'parameters', [{'reasoning_effort': 'low'}, {'thinking_mode': 'disabled', 'reasoning_effort': 'low'}]
)
async def test_explicit_low_reasoning_effort_only_affects_current_chat(parameters: dict[str, object]) -> None:
    service, runtime = _service()

    await service.chat(MODEL_ID, _messages(), parameters)
    assert dict(runtime.chat_requests[-1].parameters) == {'temperature': 0.2, 'max_tokens': 2_048, **parameters}

    await service.chat(MODEL_ID, _messages())
    assert dict(runtime.chat_requests[-1].parameters) == {'temperature': 0.2, 'max_tokens': 2_048}


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', ['medium', 'high', 'minimal', '', None, True, 1, {'effort': 'low'}, ['low']])
async def test_service_rejects_invalid_reasoning_effort_before_runtime(invalid: object) -> None:
    service, runtime = _service()

    with pytest.raises(ValueError, match=r'^模型参数无效$'):
        await service.chat(MODEL_ID, _messages(), {'reasoning_effort': invalid})

    assert runtime.chat_requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize('extra_body', [{}, {'reasoning_effort': 'low'}, {'thinking': {'type': 'disabled'}}])
async def test_service_rejects_caller_extra_body_before_runtime(extra_body: dict[str, object]) -> None:
    service, runtime = _service()

    with pytest.raises(ValueError, match=r'^不支持的模型参数$'):
        await service.chat(MODEL_ID, _messages(), {'reasoning_effort': 'low', 'extra_body': extra_body})

    assert runtime.chat_requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize('defaults', [{'reasoning_effort': 'low'}, {'thinking_mode': 'disabled'}])
async def test_internal_chat_extensions_cannot_be_catalog_defaults(defaults: dict[str, object]) -> None:
    FakeModelDao.model = _model(default_parameters=defaults)
    service, runtime = _service()

    with pytest.raises(ValueError, match=r'^不支持的模型参数$'):
        await service.chat(MODEL_ID, _messages())

    assert runtime.chat_requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize('model_type', ['chat', 'embedding', 'rerank'])
async def test_legacy_catalog_defaults_ignore_known_null_fields_from_other_model_types(model_type: str) -> None:
    FakeModelDao.model = _model(
        default_parameters={
            'temperature': None,
            'top_p': None,
            'max_tokens': None,
            'presence_penalty': None,
            'frequency_penalty': None,
            'dimensions': None,
            'top_n': None,
            'return_documents': None,
        }
    )
    FakeModelDao.model.model_type = model_type
    service, _ = _service()
    resolved = await service.resolve_model(MODEL_ID)
    assert dict(resolved.default_parameters) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize('defaults', [{'dimensions': 1024}, {'unexpected_field': None}])
async def test_legacy_default_compatibility_still_rejects_invalid_fields(defaults: dict[str, object]) -> None:
    FakeModelDao.model = _model(default_parameters=defaults)
    service, runtime = _service()
    with pytest.raises(ValueError, match='不支持的模型参数'):
        await service.resolve_model(MODEL_ID)
    assert runtime.chat_requests == []


class FalseyParameters(dict[str, object]):
    def __bool__(self) -> bool:
        return False


class CoordinatedAsyncGenerator:
    def __init__(self) -> None:
        self.read_started = asyncio.Event()
        self.release_read = asyncio.Event()
        self.finalized = asyncio.Event()

    async def events(self) -> AsyncIterator[ModelStreamEvent]:
        try:
            self.read_started.set()
            await self.release_read.wait()
            yield ModelStreamEvent(event_type='content', delta='first')
            await asyncio.Future()
        finally:
            self.finalized.set()


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid_model_id', ('company-chat', ENGINE_MODEL, 'mdl_not-a-uuid4'))
async def test_resolve_rejects_alias_or_invalid_platform_id_before_database_and_runtime(
    invalid_model_id: str,
) -> None:
    service, runtime = _service()

    with pytest.raises(ValueError, match='资源 ID'):
        await service.resolve_model(invalid_model_id)

    assert FakeModelDao.calls == []
    assert FakeBindingDao.calls == []
    assert runtime.chat_requests == []
    assert runtime.stream_requests == []


def test_constructor_rejects_preexisting_transaction_without_rollback() -> None:
    session = FakeSession(active_transaction=True)

    with pytest.raises(RuntimeError, match='独占的只读数据库会话'):
        ModelService(
            session,  # type: ignore[arg-type]
            FakeRuntime(),
            model_dao=FakeModelDao,
            binding_dao=FakeBindingDao,
        )

    assert session.in_transaction() is True
    assert session.rollback_count == 0


@pytest.mark.asyncio
async def test_resolve_rejects_transaction_started_after_construction_without_rollback() -> None:
    session = FakeSession()
    service = ModelService(
        session,  # type: ignore[arg-type]
        FakeRuntime(),
        model_dao=FakeModelDao,
        binding_dao=FakeBindingDao,
    )
    session.begin_read()

    with pytest.raises(RuntimeError, match='独占的只读数据库会话'):
        await service.resolve_model(MODEL_ID)

    assert FakeModelDao.calls == []
    assert session.in_transaction() is True
    assert session.rollback_count == 0


@pytest.mark.asyncio
async def test_resolve_returns_not_found_for_missing_platform_model() -> None:
    FakeModelDao.model = None
    service, runtime = _service()

    with pytest.raises(ModelServiceError) as raised:
        await service.resolve_model(MODEL_ID)

    assert raised.value.code == 'MODEL_NOT_FOUND'
    assert FakeBindingDao.calls == []
    assert runtime.chat_requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize('lifecycle_status', ('draft', 'disabled'))
async def test_resolve_rejects_non_active_platform_model(lifecycle_status: str) -> None:
    FakeModelDao.model = _model(lifecycle_status=lifecycle_status)
    service, runtime = _service()

    with pytest.raises(ModelServiceError) as raised:
        await service.resolve_model(MODEL_ID)

    assert raised.value.code == 'MODEL_DISABLED'
    assert FakeBindingDao.calls == []
    assert runtime.chat_requests == []


@pytest.mark.asyncio
async def test_resolve_rejects_missing_binding() -> None:
    FakeBindingDao.binding = None
    service, runtime = _service()

    with pytest.raises(ModelServiceError) as raised:
        await service.resolve_model(MODEL_ID)

    assert raised.value.code == 'MODEL_BINDING_MISSING'
    assert runtime.chat_requests == []


@pytest.mark.asyncio
async def test_resolve_rejects_disabled_binding() -> None:
    FakeBindingDao.binding = _binding(binding_status='disabled')
    service, runtime = _service()

    with pytest.raises(ModelServiceError) as raised:
        await service.resolve_model(MODEL_ID)

    assert raised.value.code == 'MODEL_BINDING_MISSING'
    assert runtime.chat_requests == []


@pytest.mark.asyncio
async def test_resolve_returns_immutable_engine_neutral_snapshot() -> None:
    defaults = {'temperature': 0.2, 'max_tokens': 2_048}
    FakeModelDao.model = _model(default_parameters=defaults)
    service, _ = _service()

    result = await service.resolve_model(MODEL_ID)
    defaults['temperature'] = 1.8
    FakeModelDao.model.model_code = 'changed-after-resolution'
    FakeBindingDao.binding.engine_resource_id = 'changed-after-resolution'  # type: ignore[union-attr]

    assert type(result) is ResolvedModel
    assert result.platform_model_id == MODEL_ID
    assert result.binding_id == BINDING_ID
    assert result.engine_type == 'litellm'
    assert result.engine_model_snapshot == ENGINE_MODEL
    assert result.model_code == 'company-chat'
    assert dict(result.default_parameters) == {'temperature': 0.2, 'max_tokens': 2_048}
    with pytest.raises(TypeError):
        result.default_parameters['temperature'] = 1.0  # type: ignore[index]
    with pytest.raises((AttributeError, TypeError)):
        result.engine_model_snapshot = 'mutated'  # type: ignore[misc]


@pytest.mark.asyncio
async def test_resolve_active_model_with_real_daos(
    real_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, session_maker = real_database
    async with session_maker() as seed_session:
        seed_session.add_all((_model(), _binding()))
        await seed_session.commit()

    runtime = FakeRuntime()
    async with session_maker() as session:
        result = await ModelService(session, runtime).resolve_model(MODEL_ID)

        assert session.in_transaction() is False

    assert result == ResolvedModel(
        platform_model_id=MODEL_ID,
        binding_id=BINDING_ID,
        engine_type='litellm',
        engine_model_snapshot=ENGINE_MODEL,
        model_type='chat',
        default_parameters={'temperature': 0.2, 'max_tokens': 2_048},
        model_code='company-chat',
    )
    assert runtime.chat_requests == []
    assert runtime.stream_requests == []


@pytest.mark.asyncio
async def test_resolve_missing_model_closes_real_read_transaction(
    real_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, session_maker = real_database
    async with session_maker() as session:
        service = ModelService(session, FakeRuntime())

        with pytest.raises(ModelServiceError) as raised:
            await service.resolve_model(MODEL_ID)

        assert raised.value.code == 'MODEL_NOT_FOUND'
        assert session.in_transaction() is False


@pytest.mark.asyncio
async def test_resolve_cancellation_closes_real_read_transaction_without_mapping_error(
    real_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, session_maker = real_database
    async with session_maker() as seed_session:
        seed_session.add(_model())
        await seed_session.commit()

    async with session_maker() as session:
        service = ModelService(session, FakeRuntime(), binding_dao=CancellingBindingDao)  # type: ignore[arg-type]

        with pytest.raises(asyncio.CancelledError):
            await service.resolve_model(MODEL_ID)

        assert session.in_transaction() is False


@pytest.mark.asyncio
async def test_resolve_preserves_dao_error_and_closes_real_read_transaction(
    real_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, session_maker = real_database
    async with session_maker() as seed_session:
        seed_session.add_all((_model(), _binding()))
        await seed_session.commit()

    async with session_maker() as session:
        service = ModelService(session, FakeRuntime(), binding_dao=FailingBindingDao)  # type: ignore[arg-type]

        with pytest.raises(RuntimeError) as raised:
            await service.resolve_model(MODEL_ID)

        assert raised.value is FailingBindingDao.error
        assert session.in_transaction() is False


@pytest.mark.asyncio
async def test_resolve_preserves_original_error_when_rollback_is_cancelled() -> None:
    session = FakeSession()
    session.rollback_error = asyncio.CancelledError()
    FakeModelDao.model = None
    service = ModelService(
        session,  # type: ignore[arg-type]
        FakeRuntime(),
        model_dao=FakeModelDao,
        binding_dao=FakeBindingDao,
    )

    with pytest.raises(ModelServiceError) as raised:
        await service.resolve_model(MODEL_ID)

    assert raised.value.code == 'MODEL_NOT_FOUND'
    assert session.rollback_count == 1


@pytest.mark.asyncio
async def test_resolve_does_not_attach_sensitive_rollback_failure_to_domain_error() -> None:
    session = FakeSession()
    session.rollback_error = RuntimeError('Authorization: Bearer rollback-secret')
    FakeModelDao.model = None
    service = ModelService(
        session,  # type: ignore[arg-type]
        FakeRuntime(),
        model_dao=FakeModelDao,
        binding_dao=FakeBindingDao,
    )

    with pytest.raises(ModelServiceError) as raised:
        await service.resolve_model(MODEL_ID)

    serialized = repr((raised.value.__cause__, raised.value.__context__, str(raised.value)))
    assert raised.value.code == 'MODEL_NOT_FOUND'
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert 'rollback-secret' not in serialized


@pytest.mark.asyncio
async def test_resolve_reports_rollback_failure_after_successful_snapshot() -> None:
    rollback_error = RuntimeError('rollback failed')
    session = FakeSession()
    session.rollback_error = rollback_error
    service = ModelService(
        session,  # type: ignore[arg-type]
        FakeRuntime(),
        model_dao=FakeModelDao,
        binding_dao=FakeBindingDao,
    )

    with pytest.raises(RuntimeError) as raised:
        await service.resolve_model(MODEL_ID)

    assert raised.value is rollback_error
    assert session.rollback_count == 1


@pytest.mark.asyncio
async def test_chat_releases_real_read_transaction_before_runtime_call(
    real_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, session_maker = real_database
    async with session_maker() as seed_session:
        seed_session.add_all((_model(), _binding()))
        await seed_session.commit()

    async with session_maker() as session:
        runtime = TransactionInspectingRuntime(session)
        result = await ModelService(session, runtime).chat(MODEL_ID, _messages())

        assert result.response.text == 'normal response'
        assert runtime.chat_transaction_states == [False]
        assert session.in_transaction() is False


@pytest.mark.asyncio
async def test_stream_releases_real_read_transaction_before_return_and_consumption(
    real_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, session_maker = real_database
    async with session_maker() as seed_session:
        seed_session.add_all((_model(), _binding()))
        await seed_session.commit()

    async with session_maker() as session:
        runtime = TransactionInspectingRuntime(session)
        result = await ModelService(session, runtime).stream_chat(MODEL_ID, _messages())

        assert runtime.stream_transaction_states == [False]
        assert session.in_transaction() is False
        assert [event async for event in result] == [ModelStreamEvent(event_type='content', delta='only')]
        assert session.in_transaction() is False


@pytest.mark.asyncio
async def test_chat_merges_defaults_then_allowed_caller_overrides_and_returns_snapshot() -> None:
    usage = ModelUsage(
        input_tokens=EXPECTED_INPUT_TOKENS,
        output_tokens=EXPECTED_OUTPUT_TOKENS,
        raw_cost=Decimal('0.0012'),
    )
    response = ModelChatResponse(text='hello', reasoning='thought', usage=usage, request_id='req-safe')
    runtime = FakeRuntime(response=response)
    service, _ = _service(runtime)
    caller_parameters = {'temperature': 0.7, 'top_p': 0.9}

    result = await service.chat(MODEL_ID, _messages(), caller_parameters)

    assert type(result) is ModelServiceChatResult
    assert result.platform_model_id == MODEL_ID
    assert result.binding_id == BINDING_ID
    assert result.engine_type == 'litellm'
    assert result.engine_model_snapshot == ENGINE_MODEL
    assert result.response is response
    assert caller_parameters == {'temperature': 0.7, 'top_p': 0.9}
    assert len(runtime.chat_requests) == 1
    request = runtime.chat_requests[0]
    assert request.engine_model == ENGINE_MODEL
    assert request.messages == _messages()
    assert dict(request.parameters) == {'temperature': 0.7, 'max_tokens': 2_048, 'top_p': 0.9}
    with pytest.raises(TypeError):
        request.parameters['temperature'] = 0.1  # type: ignore[index]


@pytest.mark.asyncio
async def test_chat_passes_public_stop_and_user_parameters_to_runtime() -> None:
    service, runtime = _service()

    await service.chat(MODEL_ID, _messages(), {'stop': ['END', 'DONE'], 'user': 'caller-42'})

    assert dict(runtime.chat_requests[0].parameters) == {
        'temperature': 0.2,
        'max_tokens': 2_048,
        'stop': ('END', 'DONE'),
        'user': 'caller-42',
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'parameters',
    (
        {'stop': []},
        {'stop': ['1', '2', '3', '4', '5']},
        {'stop': ['safe', 'bad\nvalue']},
        {'user': ''},
        {'user': 'bad\x00caller'},
    ),
)
async def test_chat_rejects_invalid_public_stop_or_user_before_runtime(parameters: dict[str, object]) -> None:
    service, runtime = _service()

    with pytest.raises(ValueError, match='模型参数无效'):
        await service.chat(MODEL_ID, _messages(), parameters)

    assert runtime.chat_requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'forbidden_key',
    ('model', 'api_key', 'apiKey', 'authorization', 'base_url', 'baseUrl', 'provider_private'),
)
async def test_chat_rejects_forbidden_or_unknown_caller_parameter_before_runtime(forbidden_key: str) -> None:
    service, runtime = _service()

    with pytest.raises(ValueError, match='不支持的模型参数') as raised:
        await service.chat(MODEL_ID, _messages(), {forbidden_key: 'credential-secret'})

    assert 'credential-secret' not in str(raised.value)
    assert runtime.chat_requests == []


@pytest.mark.asyncio
async def test_chat_rejects_non_string_parameter_key_without_echoing_value() -> None:
    service, runtime = _service()

    with pytest.raises(ValueError, match='不支持的模型参数') as raised:
        await service.chat(MODEL_ID, _messages(), {1: 'credential-secret'})  # type: ignore[dict-item]

    assert 'credential-secret' not in str(raised.value)
    assert runtime.chat_requests == []


@pytest.mark.asyncio
async def test_chat_rejects_unsafe_catalog_default_before_runtime_without_exposing_value() -> None:
    FakeModelDao.model = _model(default_parameters={'api_key': 'catalog-secret'})
    service, runtime = _service()

    with pytest.raises(ValueError, match='不支持的模型参数') as raised:
        await service.chat(MODEL_ID, _messages())

    assert 'api_key' not in str(raised.value)
    assert 'catalog-secret' not in str(raised.value)
    assert runtime.chat_requests == []


@pytest.mark.asyncio
async def test_chat_treats_caller_none_as_no_override_and_omits_empty_defaults() -> None:
    FakeModelDao.model = _model(default_parameters={'temperature': 0.2, 'top_p': None})
    service, runtime = _service()

    await service.chat(MODEL_ID, _messages(), {'temperature': None, 'max_tokens': None})

    assert dict(runtime.chat_requests[0].parameters) == {'temperature': 0.2}


@pytest.mark.asyncio
async def test_chat_does_not_drop_valid_falsey_parameter_mapping() -> None:
    service, runtime = _service()

    await service.chat(MODEL_ID, _messages(), FalseyParameters(top_p=0.9))

    assert dict(runtime.chat_requests[0].parameters) == {
        'temperature': 0.2,
        'max_tokens': 2_048,
        'top_p': 0.9,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'invalid_parameters',
    (
        {'temperature': True},
        {'temperature': 2.1},
        {'top_p': -0.1},
        {'max_tokens': 0},
        {'presence_penalty': -2.1},
        {'frequency_penalty': 2.1},
    ),
)
async def test_chat_reuses_catalog_parameter_constraints_before_runtime(
    invalid_parameters: dict[str, object],
) -> None:
    service, runtime = _service()

    with pytest.raises(ValueError, match='模型参数无效'):
        await service.chat(MODEL_ID, _messages(), invalid_parameters)

    assert runtime.chat_requests == []


@pytest.mark.asyncio
async def test_chat_preserves_runtime_domain_error_identity() -> None:
    runtime_error = ModelServiceError.runtime_timeout()
    service, _ = _service(FakeRuntime(chat_error=runtime_error))

    with pytest.raises(ModelServiceError) as raised:
        await service.chat(MODEL_ID, _messages())

    assert raised.value is runtime_error


@pytest.mark.asyncio
async def test_chat_preserves_cancellation() -> None:
    service, _ = _service(FakeRuntime(chat_error=asyncio.CancelledError()))

    with pytest.raises(asyncio.CancelledError):
        await service.chat(MODEL_ID, _messages())


@pytest.mark.asyncio
async def test_stream_chat_returns_snapshot_and_delegates_engine_neutral_events() -> None:
    source_stream = FakeEventStream(
        (
            ModelStreamEvent(event_type='content', delta='first'),
            ModelStreamEvent(event_type='done'),
        )
    )
    runtime = FakeRuntime(stream=source_stream)
    service, _ = _service(runtime)

    result = await service.stream_chat(
        MODEL_ID,
        _messages(),
        {'presence_penalty': 0.3, 'frequency_penalty': -0.2},
    )
    events = [event async for event in result]

    assert type(result) is ModelServiceStreamResult
    assert result.platform_model_id == MODEL_ID
    assert result.binding_id == BINDING_ID
    assert result.engine_type == 'litellm'
    assert result.engine_model_snapshot == ENGINE_MODEL
    assert events == [
        ModelStreamEvent(event_type='content', delta='first'),
        ModelStreamEvent(event_type='done'),
    ]
    assert len(runtime.stream_requests) == 1
    assert dict(runtime.stream_requests[0].parameters) == {
        'temperature': 0.2,
        'max_tokens': 2_048,
        'presence_penalty': 0.3,
        'frequency_penalty': -0.2,
    }


@pytest.mark.asyncio
async def test_stream_result_early_aclose_closes_only_current_stream() -> None:
    source_stream = FakeEventStream(
        (
            ModelStreamEvent(event_type='content', delta='first'),
            ModelStreamEvent(event_type='content', delta='second'),
        ),
    )
    runtime = FakeRuntime(stream=source_stream)
    service, _ = _service(runtime)
    result = await service.stream_chat(MODEL_ID, _messages())

    assert await anext(result) == ModelStreamEvent(event_type='content', delta='first')
    await result.aclose()

    assert source_stream.close_count == 1
    assert runtime.close_count == 0

    await result.aclose()
    assert source_stream.close_count == 1


@pytest.mark.asyncio
async def test_stream_concurrent_aclose_waits_for_pending_read_then_finalizes_generator() -> None:
    coordinated = CoordinatedAsyncGenerator()
    runtime = FakeRuntime()
    runtime.stream = coordinated.events()
    service, _ = _service(runtime)
    result = await service.stream_chat(MODEL_ID, _messages())

    pending_read = asyncio.create_task(anext(result))
    await coordinated.read_started.wait()
    pending_close = asyncio.create_task(result.aclose())
    await asyncio.sleep(0)

    assert pending_close.done() is False
    assert coordinated.finalized.is_set() is False

    coordinated.release_read.set()
    assert await pending_read == ModelStreamEvent(event_type='content', delta='first')
    await pending_close

    assert coordinated.finalized.is_set() is True
    with pytest.raises(StopAsyncIteration):
        await anext(result)
    await result.aclose()


@pytest.mark.asyncio
async def test_stream_close_failure_can_be_retried() -> None:
    close_error = ModelServiceError.runtime_timeout()
    source_stream = FakeEventStream(close_error=close_error)
    service, _ = _service(FakeRuntime(stream=source_stream))
    result = await service.stream_chat(MODEL_ID, _messages())

    with pytest.raises(ModelServiceError) as raised:
        await result.aclose()

    assert raised.value is close_error
    await result.aclose()
    assert source_stream.close_count == TWO_CLOSE_ATTEMPTS
    with pytest.raises(StopAsyncIteration):
        await anext(result)


@pytest.mark.asyncio
async def test_stream_result_normal_completion_closes_current_stream_once() -> None:
    source_stream = FakeEventStream((ModelStreamEvent(event_type='content', delta='only'),))
    runtime = FakeRuntime(stream=source_stream)
    service, _ = _service(runtime)
    result = await service.stream_chat(MODEL_ID, _messages())

    assert [event async for event in result] == [ModelStreamEvent(event_type='content', delta='only')]

    assert source_stream.close_count == 1
    assert runtime.close_count == 0


@pytest.mark.asyncio
async def test_stream_result_reports_close_error_after_natural_completion() -> None:
    close_error = ModelServiceError.runtime_timeout()
    source_stream = FakeEventStream(close_error=close_error)
    service, _ = _service(FakeRuntime(stream=source_stream))
    result = await service.stream_chat(MODEL_ID, _messages())

    with pytest.raises(ModelServiceError) as raised:
        await anext(result)

    assert raised.value is close_error


@pytest.mark.asyncio
async def test_stream_active_error_is_not_replaced_by_close_cancellation() -> None:
    runtime_error = ModelServiceError.runtime_unavailable()
    source_stream = FakeEventStream(
        error=runtime_error,
        close_error=asyncio.CancelledError(),
    )
    service, _ = _service(FakeRuntime(stream=source_stream))
    result = await service.stream_chat(MODEL_ID, _messages())

    with pytest.raises(ModelServiceError) as raised:
        await anext(result)

    assert raised.value is runtime_error
    assert source_stream.close_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'construct_error',
    (ModelServiceError.runtime_unavailable(), asyncio.CancelledError()),
)
async def test_stream_chat_preserves_synchronous_construction_error(
    construct_error: BaseException,
) -> None:
    runtime = FakeRuntime(stream_construct_error=construct_error)
    service, _ = _service(runtime)

    with pytest.raises(type(construct_error)) as raised:
        await service.stream_chat(MODEL_ID, _messages())

    assert raised.value is construct_error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'stream_error',
    (ModelServiceError.runtime_unavailable(), asyncio.CancelledError()),
)
async def test_stream_result_preserves_runtime_errors_and_cancellation(stream_error: BaseException) -> None:
    source_stream = FakeEventStream(error=stream_error)
    service, _ = _service(FakeRuntime(stream=source_stream))
    result = await service.stream_chat(MODEL_ID, _messages())

    with pytest.raises(type(stream_error)) as raised:
        await anext(result)

    assert raised.value is stream_error


@pytest.mark.asyncio
async def test_stream_rejects_unknown_parameter_before_runtime_stream_creation() -> None:
    service, runtime = _service()

    with pytest.raises(ValueError, match='不支持的模型参数'):
        await service.stream_chat(MODEL_ID, _messages(), {'model': ENGINE_MODEL})

    assert runtime.stream_requests == []


def test_service_result_types_are_closed_immutable_snapshots() -> None:
    assert ResolvedModel.__dataclass_params__.frozen is True
    assert ModelServiceChatResult.__dataclass_params__.frozen is True
    assert ModelServiceStreamResult.__dataclass_params__.frozen is True
    assert not issubclass(ResolvedModel, Mapping)
