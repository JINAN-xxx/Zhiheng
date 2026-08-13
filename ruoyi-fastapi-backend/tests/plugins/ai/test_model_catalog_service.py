import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from common.vo import PageModel
from exceptions.exception import ServiceException
from plugins.ai.dao.ai_model_catalog_dao import AiModelCatalogDao
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
from plugins.ai.entity.do.ai_resource_binding_do import AiResourceBinding
from plugins.ai.entity.vo.ai_model_catalog_vo import (
    ModelCatalogCreateModel,
    ModelCatalogPageQueryModel,
    ModelCatalogUpdateModel,
    PlatformModelDetailModel,
    PlatformModelModel,
    ResourceBindingUpsertModel,
)
from plugins.ai.service.model_catalog_service import ModelCatalogService, NoModelReferenceChecker

NOW = datetime(2026, 8, 13, 9, 30)
LATER = datetime(2026, 8, 13, 10, 45)
TWO_CALLS = 2
THREE_ROLLBACKS = 3
FOUR_ROLLBACKS = 4
UPDATED_BINDING_VERSION = 2


class CommitFailingAsyncSession(AsyncSession):
    fail_next_commit = False

    async def commit(self) -> None:
        if self.fail_next_commit:
            self.fail_next_commit = False
            raise RuntimeError('commit failed')
        await super().commit()


@pytest_asyncio.fixture
async def real_database() -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker[CommitFailingAsyncSession]]]:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')

    @event.listens_for(engine.sync_engine, 'connect')
    def enable_foreign_keys(connection: Any, _record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()

    session_maker = async_sessionmaker(
        bind=engine,
        class_=CommitFailingAsyncSession,
        expire_on_commit=True,
    )
    async with engine.begin() as connection:
        await connection.run_sync(AiModelCatalog.__table__.create)
        await connection.run_sync(AiResourceBinding.__table__.create)
    try:
        yield engine, session_maker
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def concurrent_service_database(
    tmp_path: Path,
) -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]]]:
    database_path = tmp_path / 'model-service-race.db'
    engine = create_async_engine(f'sqlite+aiosqlite:///{database_path}', connect_args={'timeout': 5})

    @event.listens_for(engine.sync_engine, 'connect')
    def configure_sqlite(connection: Any, _record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.execute('PRAGMA busy_timeout=5000')
        cursor.close()

    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(AiModelCatalog.__table__.create)
        await connection.run_sync(AiResourceBinding.__table__.create)
    try:
        yield engine, session_maker
    finally:
        await engine.dispose()


class FakeSession:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events if events is not None else []
        self.commit_count = 0
        self.rollback_count = 0
        self.commit_error: Exception | None = None
        self.rollback_error: BaseException | None = None

    async def commit(self) -> None:
        self.events.append('commit')
        self.commit_count += 1
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self) -> None:
        self.events.append('rollback')
        self.rollback_count += 1
        if self.rollback_error is not None:
            raise self.rollback_error


class FakeRuntime:
    def __init__(self, model_ids: set[str] | None = None, error: Exception | None = None) -> None:
        self.model_ids = model_ids or set()
        self.error = error
        self.list_calls = 0

    async def list_model_ids(self) -> set[str]:
        self.list_calls += 1
        if self.error is not None:
            raise self.error
        return set(self.model_ids)

    async def aclose(self) -> None:
        return None


class BlockingRuntime(FakeRuntime):
    def __init__(self, model_ids: set[str]) -> None:
        super().__init__(model_ids)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def list_model_ids(self) -> set[str]:
        self.list_calls += 1
        self.entered.set()
        await self.release.wait()
        return set(self.model_ids)


class FakeReferenceChecker:
    def __init__(self, has_references: bool = False, events: list[str] | None = None) -> None:
        self.has_references = has_references
        self.events = events if events is not None else []
        self.calls: list[str] = []

    async def has_model_references(self, model_id: str) -> bool:
        self.events.append('check_references')
        self.calls.append(model_id)
        return self.has_references


class FakeModelDao:
    models: dict[str, AiModelCatalog] = {}
    events: list[str] = []
    create_error: Exception | None = None
    update_error: Exception | None = None

    @classmethod
    def reset(cls, events: list[str] | None = None) -> None:
        cls.models = {}
        cls.events = events if events is not None else []
        cls.create_error = None
        cls.update_error = None

    @classmethod
    async def get_model_by_id(cls, _db: FakeSession, model_id: str) -> AiModelCatalog | None:
        cls.events.append('get_model_by_id')
        return cls.models.get(model_id)

    @classmethod
    async def get_model_for_update(cls, _db: FakeSession, model_id: str) -> AiModelCatalog | None:
        cls.events.append('get_model_for_update')
        return cls.models.get(model_id)

    @classmethod
    async def get_model_by_code(cls, _db: FakeSession, model_code: str) -> AiModelCatalog | None:
        cls.events.append('get_model_by_code')
        return next((model for model in cls.models.values() if model.model_code == model_code), None)

    @classmethod
    async def list_models(
        cls,
        _db: FakeSession,
        query: ModelCatalogPageQueryModel,
        is_page: bool = False,
    ) -> PageModel | list[dict[str, Any]]:
        cls.events.append('list_models')
        rows = [
            PlatformModelModel.model_validate(model).model_dump(by_alias=True, mode='json')
            for model in cls.models.values()
        ]
        if is_page:
            return PageModel(
                rows=rows,
                pageNum=query.page_num,
                pageSize=query.page_size,
                total=len(rows),
                hasNext=False,
            )
        return rows

    @classmethod
    async def create_model(cls, _db: FakeSession, model: AiModelCatalog) -> AiModelCatalog:
        cls.events.append('create_model')
        if cls.create_error is not None:
            raise cls.create_error
        cls.models[model.model_id] = model
        return model

    @classmethod
    async def update_model(cls, _db: FakeSession, model_id: str, values: dict[str, Any]) -> int:
        cls.events.append('update_model')
        if cls.update_error is not None:
            raise cls.update_error
        model = cls.models.get(model_id)
        if model is None:
            return 0
        for key, value in values.items():
            setattr(model, key, value)
        return 1

    @classmethod
    async def update_unpublished_model(cls, db: FakeSession, model_id: str, values: dict[str, Any]) -> int:
        model = cls.models.get(model_id)
        if model is None or model.published_at is not None:
            cls.events.append('update_unpublished_model')
            return 0
        return await cls.update_model(db, model_id, values)

    @classmethod
    async def publish_model_if_state(
        cls,
        db: FakeSession,
        model_id: str,
        *,
        expected_lifecycle_status: str,
        expected_published_at: datetime | None,
        values: dict[str, Any],
    ) -> int:
        model = cls.models.get(model_id)
        if (
            model is None
            or model.lifecycle_status != expected_lifecycle_status
            or model.published_at != expected_published_at
        ):
            cls.events.append('publish_model_if_state')
            return 0
        return await cls.update_model(db, model_id, values)

    @classmethod
    async def disable_model_if_active(cls, db: FakeSession, model_id: str, values: dict[str, Any]) -> int:
        model = cls.models.get(model_id)
        if model is None or model.lifecycle_status != 'active':
            cls.events.append('disable_model_if_active')
            return 0
        return await cls.update_model(db, model_id, values)

    @classmethod
    async def delete_model(cls, _db: FakeSession, model_id: str) -> int:
        cls.events.append('delete_model')
        return int(cls.models.pop(model_id, None) is not None)

    @classmethod
    async def delete_unpublished_draft(cls, db: FakeSession, model_id: str) -> int:
        model = cls.models.get(model_id)
        if model is None or model.lifecycle_status != 'draft' or model.published_at is not None:
            cls.events.append('delete_unpublished_draft')
            return 0
        return await cls.delete_model(db, model_id)


class FakeBindingDao:
    bindings: dict[str, AiResourceBinding] = {}
    events: list[str] = []
    update_rowcount = 1

    @classmethod
    def reset(cls, events: list[str] | None = None) -> None:
        cls.bindings = {}
        cls.events = events if events is not None else []
        cls.update_rowcount = 1

    @classmethod
    async def get_current_binding(cls, _db: FakeSession, model_id: str) -> AiResourceBinding | None:
        cls.events.append('get_current_binding')
        return next((binding for binding in cls.bindings.values() if binding.resource_id == model_id), None)

    @classmethod
    async def get_current_binding_for_update(cls, _db: FakeSession, model_id: str) -> AiResourceBinding | None:
        cls.events.append('get_current_binding_for_update')
        return next((binding for binding in cls.bindings.values() if binding.resource_id == model_id), None)

    @classmethod
    async def create_binding(cls, _db: FakeSession, binding: AiResourceBinding) -> AiResourceBinding:
        cls.events.append('create_binding')
        cls.bindings[binding.binding_id] = binding
        return binding

    @classmethod
    async def update_binding_with_version(
        cls,
        _db: FakeSession,
        binding_id: str,
        *,
        expected_version: int,
        values: dict[str, Any],
    ) -> int:
        cls.events.append('update_binding_with_version')
        if cls.update_rowcount == 0:
            return 0
        binding = cls.bindings[binding_id]
        if binding.version != expected_version:
            return 0
        for key, value in values.items():
            setattr(binding, key, value)
        binding.version = expected_version + 1
        return 1

    @classmethod
    async def delete_binding_by_resource(cls, _db: FakeSession, model_id: str) -> int:
        cls.events.append('delete_binding')
        binding_id = next(
            (key for key, binding in cls.bindings.items() if binding.resource_id == model_id),
            None,
        )
        if binding_id is None:
            return 0
        del cls.bindings[binding_id]
        return 1


class NonSynchronizingModelDao(FakeModelDao):
    @classmethod
    async def update_model(cls, _db: FakeSession, model_id: str, values: dict[str, Any]) -> int:
        cls.events.append('update_model')
        return int(model_id in cls.models)

    update_unpublished_model = update_model
    publish_model_if_state = FakeModelDao.publish_model_if_state
    disable_model_if_active = FakeModelDao.disable_model_if_active


class NonSynchronizingBindingDao(FakeBindingDao):
    @classmethod
    async def update_binding_with_version(
        cls,
        _db: FakeSession,
        binding_id: str,
        *,
        expected_version: int,
        values: dict[str, Any],
    ) -> int:
        del expected_version, values
        cls.events.append('update_binding_with_version')
        return int(binding_id in cls.bindings)


class LosingStateRaceModelDao(FakeModelDao):
    @classmethod
    async def update_unpublished_model(cls, _db: FakeSession, model_id: str, values: dict[str, Any]) -> int:
        del model_id, values
        cls.events.append('update_unpublished_model')
        return 0

    @classmethod
    async def publish_model_if_state(
        cls,
        _db: FakeSession,
        model_id: str,
        *,
        expected_lifecycle_status: str,
        expected_published_at: datetime | None,
        values: dict[str, Any],
    ) -> int:
        del model_id, expected_lifecycle_status, expected_published_at, values
        cls.events.append('publish_model_if_state')
        return 0

    @classmethod
    async def disable_model_if_active(cls, _db: FakeSession, model_id: str, values: dict[str, Any]) -> int:
        del model_id, values
        cls.events.append('disable_model_if_active')
        return 0

    @classmethod
    async def delete_unpublished_draft(cls, _db: FakeSession, model_id: str) -> int:
        del model_id
        cls.events.append('delete_unpublished_draft')
        return 0


@pytest.fixture(autouse=True)
def reset_fake_daos() -> None:
    FakeModelDao.reset()
    FakeBindingDao.reset()


def _service(
    *,
    session: FakeSession | None = None,
    runtime: FakeRuntime | None = None,
    reference_checker: FakeReferenceChecker | None = None,
) -> tuple[ModelCatalogService, FakeSession, FakeRuntime]:
    fake_session = session or FakeSession()
    fake_runtime = runtime or FakeRuntime()
    service = ModelCatalogService(
        fake_session,
        fake_runtime,
        reference_checker or FakeReferenceChecker(),
        model_dao=FakeModelDao,
        binding_dao=FakeBindingDao,
    )
    return service, fake_session, fake_runtime


def _create_payload(model_code: str = 'company-chat') -> ModelCatalogCreateModel:
    return ModelCatalogCreateModel.model_validate(
        {
            'modelCode': model_code,
            'displayName': '企业对话模型',
            'modelType': 'chat',
            'capabilities': {'streaming': True, 'tools': True},
            'contextWindow': 128000,
            'maxOutputTokens': 8192,
            'defaultParameters': {'temperature': 0.2, 'maxTokens': 2048},
        }
    )


async def _create_model(
    service: ModelCatalogService,
    *,
    model_code: str = 'company-chat',
    now: datetime = NOW,
) -> PlatformModelModel:
    return await service.create_model(_create_payload(model_code), username='admin', now=now)


async def _create_binding(
    service: ModelCatalogService,
    model_id: str,
    *,
    engine_resource_id: str = 'runtime-company-chat',
    expected_version: int | None = None,
    binding_status: str = 'active',
    now: datetime = NOW,
) -> Any:
    payload: dict[str, Any] = {
        'engineResourceId': engine_resource_id,
        'bindingStatus': binding_status,
        'engineMetadata': {},
    }
    if expected_version is not None:
        payload['expectedVersion'] = expected_version
    return await service.upsert_binding(
        model_id,
        ResourceBindingUpsertModel.model_validate(payload),
        username='admin',
        now=now,
    )


@pytest.mark.asyncio
async def test_create_generates_server_uuid4_draft_and_commits_plain_json_values() -> None:
    service, session, _ = _service()

    created = await _create_model(service)
    stored = FakeModelDao.models[created.model_id]

    assert created.model_id.startswith('mdl_')
    assert created.lifecycle_status == 'draft'
    assert stored.lifecycle_status == 'draft'
    assert stored.published_at is None
    assert type(stored.capabilities) is dict
    assert type(stored.default_parameters) is dict
    assert stored.create_by == stored.update_by == 'admin'
    assert stored.create_time == stored.update_time == NOW
    assert session.commit_count == 1
    assert session.rollback_count == 0


@pytest.mark.asyncio
async def test_duplicate_model_code_is_fixed_conflict_without_database_text() -> None:
    service, session, _ = _service()
    await _create_model(service)

    with pytest.raises(ServiceException) as exc_info:
        await _create_model(service)

    assert exc_info.value.message == '模型编码已存在'
    assert 'company-chat' not in exc_info.value.message
    assert session.commit_count == 1
    assert session.rollback_count == 1


@pytest.mark.asyncio
async def test_create_integrity_race_rolls_back_and_returns_fixed_conflict() -> None:
    service, session, _ = _service()
    FakeModelDao.create_error = IntegrityError('secret sql', {'api_key': 'secret'}, Exception('raw db text'))

    with pytest.raises(ServiceException) as exc_info:
        await _create_model(service)

    assert exc_info.value.message == '模型编码已存在'
    assert 'secret' not in exc_info.value.message
    assert session.commit_count == 0
    assert session.rollback_count == 1


@pytest.mark.asyncio
async def test_commit_integrity_race_rolls_back_and_returns_fixed_conflict() -> None:
    session = FakeSession()
    session.commit_error = IntegrityError('secret sql', {'password': 'secret'}, Exception('raw db text'))
    service, _, _ = _service(session=session)

    with pytest.raises(ServiceException) as exc_info:
        await _create_model(service)

    assert exc_info.value.message == '模型编码已存在'
    assert session.commit_count == 1
    assert session.rollback_count == 1


@pytest.mark.asyncio
async def test_failed_write_and_failed_commit_rollback_once_and_preserve_original_error() -> None:
    service, session, _ = _service()
    operation_error = RuntimeError('operation failed')
    FakeModelDao.create_error = operation_error

    with pytest.raises(RuntimeError) as operation_exc:
        await _create_model(service)

    assert operation_exc.value is operation_error
    assert session.rollback_count == 1

    FakeModelDao.create_error = None
    session.commit_error = RuntimeError('commit failed')
    with pytest.raises(RuntimeError) as commit_exc:
        await _create_model(service, model_code='second-model')

    assert commit_exc.value is session.commit_error
    assert session.rollback_count == TWO_CALLS


@pytest.mark.asyncio
async def test_rollback_failure_preserves_original_error_but_does_not_swallow_cancellation() -> None:
    service, session, _ = _service()
    operation_error = RuntimeError('operation failed')
    FakeModelDao.create_error = operation_error
    session.rollback_error = RuntimeError('rollback failed')

    with pytest.raises(RuntimeError) as exc_info:
        await _create_model(service)

    assert exc_info.value is operation_error

    session.rollback_error = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await _create_model(service, model_code='another-model')


@pytest.mark.asyncio
async def test_get_and_list_return_whitelisted_response_models_and_camel_case_page() -> None:
    service, _, _ = _service()
    created = await _create_model(service)
    binding = await _create_binding(service, created.model_id)
    FakeModelDao.models[created.model_id].api_key = 'secret'
    FakeBindingDao.bindings[binding.binding_id].engine_type = 'litellm'

    detail = await service.get_model(created.model_id)
    page = await service.list_models(ModelCatalogPageQueryModel.model_validate({}), is_page=True)

    assert isinstance(detail, PlatformModelDetailModel)
    assert detail.binding is not None
    assert detail.binding.binding_id == binding.binding_id
    assert {'apiKey', 'engineType'}.isdisjoint(detail.model_dump(by_alias=True))
    assert isinstance(page, PageModel)
    assert page.rows[0]['modelId'] == created.model_id
    assert 'apiKey' not in page.rows[0]


@pytest.mark.asyncio
async def test_get_missing_model_uses_stable_domain_error() -> None:
    service, _, _ = _service()

    with pytest.raises(ModelServiceError) as exc_info:
        await service.get_model('mdl_00000000000040008000000000000001')

    assert exc_info.value.code == 'MODEL_NOT_FOUND'


@pytest.mark.asyncio
async def test_never_published_draft_can_change_code_and_explicit_null_limits_are_written() -> None:
    service, session, _ = _service()
    created = await _create_model(service)

    updated = await service.update_model(
        created.model_id,
        ModelCatalogUpdateModel.model_validate(
            {'modelCode': 'company-chat-v2', 'contextWindow': None, 'maxOutputTokens': None}
        ),
        username='editor',
        now=LATER,
    )

    stored = FakeModelDao.models[created.model_id]
    assert updated.model_code == 'company-chat-v2'
    assert stored.context_window is None
    assert stored.max_output_tokens is None
    assert stored.update_by == 'editor'
    assert stored.update_time == LATER
    assert session.commit_count == TWO_CALLS


@pytest.mark.asyncio
async def test_published_at_permanently_prevents_code_change_even_when_disabled() -> None:
    service, session, _ = _service(runtime=FakeRuntime({'runtime-company-chat'}))
    created = await _create_model(service)
    await _create_binding(service, created.model_id)
    await service.publish_model(created.model_id, username='publisher', now=LATER)
    await service.disable_model(created.model_id, username='operator', now=LATER)

    with pytest.raises(ServiceException) as exc_info:
        await service.update_model(
            created.model_id,
            ModelCatalogUpdateModel.model_validate({'modelCode': 'renamed'}),
            username='editor',
            now=LATER,
        )

    assert exc_info.value.message == '已发布模型不允许修改模型编码'
    assert session.rollback_count == TWO_CALLS


@pytest.mark.asyncio
async def test_empty_patch_does_not_issue_update_but_keeps_transaction_semantics_clear() -> None:
    service, session, _ = _service()
    created = await _create_model(service)
    FakeModelDao.events.clear()

    result = await service.update_model(
        created.model_id,
        ModelCatalogUpdateModel.model_validate({}),
        username='editor',
        now=LATER,
    )

    assert result.model_id == created.model_id
    assert 'update_model' not in FakeModelDao.events
    assert session.commit_count == 1


@pytest.mark.asyncio
async def test_binding_create_has_platform_identifiers_fixed_engine_and_version_one() -> None:
    service, session, _ = _service()
    model = await _create_model(service)

    created = await _create_binding(service, model.model_id)
    stored = FakeBindingDao.bindings[created.binding_id]

    assert created.binding_id.startswith('bnd_')
    assert stored.resource_type == 'model'
    assert stored.engine_type == 'litellm'
    assert stored.resource_id == model.model_id
    assert stored.version == 1
    assert type(stored.engine_metadata) is dict
    assert session.commit_count == TWO_CALLS


@pytest.mark.asyncio
async def test_binding_create_rejects_expected_version_and_update_requires_correct_version() -> None:
    service, session, _ = _service()
    model = await _create_model(service)

    with pytest.raises(ServiceException) as create_exc:
        await _create_binding(service, model.model_id, expected_version=1)
    assert create_exc.value.message == '新建绑定不允许指定版本号'

    binding = await _create_binding(service, model.model_id)
    with pytest.raises(ServiceException) as missing_exc:
        await _create_binding(service, model.model_id, engine_resource_id='new-alias')
    assert missing_exc.value.message == '更新绑定必须提供当前版本号'

    FakeBindingDao.update_rowcount = 0
    with pytest.raises(ServiceException) as conflict_exc:
        await _create_binding(
            service,
            model.model_id,
            engine_resource_id='new-alias',
            expected_version=binding.version,
        )
    assert conflict_exc.value.message == '绑定版本已变化，请刷新后重试'
    assert session.rollback_count == THREE_ROLLBACKS


@pytest.mark.asyncio
async def test_binding_update_uses_one_cas_and_returns_incremented_version() -> None:
    service, _, _ = _service()
    model = await _create_model(service)
    binding = await _create_binding(service, model.model_id)

    updated = await _create_binding(
        service,
        model.model_id,
        engine_resource_id='runtime-company-chat-v2',
        expected_version=binding.version,
        now=LATER,
    )

    assert updated.engine_resource_id == 'runtime-company-chat-v2'
    assert updated.version == UPDATED_BINDING_VERSION
    assert FakeBindingDao.events.count('update_binding_with_version') == 1


@pytest.mark.asyncio
async def test_update_responses_do_not_depend_on_dao_identity_map_synchronization() -> None:
    session = FakeSession()
    runtime = FakeRuntime({'runtime-company-chat'})
    service = ModelCatalogService(
        session,
        runtime,
        FakeReferenceChecker(),
        model_dao=NonSynchronizingModelDao,
        binding_dao=NonSynchronizingBindingDao,
    )
    model = await _create_model(service)
    binding = await _create_binding(service, model.model_id)

    updated_model = await service.update_model(
        model.model_id,
        ModelCatalogUpdateModel.model_validate({'displayName': '新名称'}),
        username='editor',
        now=LATER,
    )
    updated_binding = await _create_binding(
        service,
        model.model_id,
        engine_resource_id='runtime-company-chat-v2',
        expected_version=binding.version,
        now=LATER,
    )
    published = await service.publish_model(model.model_id, username='publisher', now=LATER)

    FakeModelDao.models[model.model_id].lifecycle_status = 'active'
    disabled = await service.disable_model(model.model_id, username='operator', now=LATER)

    assert updated_model.display_name == '新名称'
    assert updated_model.update_by == 'editor'
    assert updated_binding.engine_resource_id == 'runtime-company-chat-v2'
    assert updated_binding.version == UPDATED_BINDING_VERSION
    assert published.lifecycle_status == 'active'
    assert published.published_at == LATER
    assert disabled.lifecycle_status == 'disabled'


@pytest.mark.asyncio
async def test_publish_requires_active_binding_and_runtime_visible_alias() -> None:
    service, session, runtime = _service(runtime=FakeRuntime({'visible-alias'}))
    model = await _create_model(service)

    with pytest.raises(ModelServiceError) as missing_exc:
        await service.publish_model(model.model_id, username='publisher', now=LATER)
    assert missing_exc.value.code == 'MODEL_BINDING_MISSING'

    await _create_binding(service, model.model_id, engine_resource_id='visible-alias', binding_status='disabled')
    with pytest.raises(ModelServiceError) as disabled_exc:
        await service.publish_model(model.model_id, username='publisher', now=LATER)
    assert disabled_exc.value.code == 'MODEL_BINDING_MISSING'

    binding = next(iter(FakeBindingDao.bindings.values()))
    binding.binding_status = 'active'
    binding.engine_resource_id = 'missing-alias'
    with pytest.raises(ServiceException) as invisible_exc:
        await service.publish_model(model.model_id, username='publisher', now=LATER)
    assert invisible_exc.value.message == '运行时未发现绑定的模型别名'
    assert runtime.list_calls == 1
    assert FakeModelDao.models[model.model_id].lifecycle_status == 'draft'
    assert session.rollback_count == FOUR_ROLLBACKS


@pytest.mark.asyncio
async def test_publish_validates_runtime_before_local_write_and_rolls_back_write_failure() -> None:
    events: list[str] = []
    FakeModelDao.reset(events)
    FakeBindingDao.reset(events)
    service, session, _ = _service(session=FakeSession(events), runtime=FakeRuntime({'runtime-company-chat'}))
    model = await _create_model(service)
    await _create_binding(service, model.model_id)
    events.clear()
    FakeModelDao.update_error = RuntimeError('write failed')

    with pytest.raises(RuntimeError, match='write failed'):
        await service.publish_model(model.model_id, username='publisher', now=LATER)

    assert events == [
        'get_model_by_id',
        'get_current_binding',
        'rollback',
        'get_model_for_update',
        'get_current_binding_for_update',
        'update_model',
        'rollback',
    ]
    assert FakeModelDao.models[model.model_id].lifecycle_status == 'draft'
    assert session.rollback_count == TWO_CALLS


@pytest.mark.asyncio
async def test_publish_sets_first_timestamp_and_republish_keeps_it_but_revalidates_runtime() -> None:
    runtime = FakeRuntime({'runtime-company-chat'})
    service, _, _ = _service(runtime=runtime)
    model = await _create_model(service)
    await _create_binding(service, model.model_id)

    active = await service.publish_model(model.model_id, username='publisher', now=NOW)
    await service.disable_model(model.model_id, username='operator', now=LATER)
    republished = await service.publish_model(model.model_id, username='publisher-2', now=LATER)

    assert active.lifecycle_status == 'active'
    assert republished.lifecycle_status == 'active'
    assert republished.published_at == NOW
    assert runtime.list_calls == TWO_CALLS


@pytest.mark.asyncio
async def test_publish_rejects_binding_changed_while_runtime_snapshot_is_validated(
    concurrent_service_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, session_maker = concurrent_service_database
    runtime = BlockingRuntime({'runtime-company-chat'})
    async with session_maker() as seed_session:
        seed_service = ModelCatalogService(seed_session, FakeRuntime())
        model = await _create_model(seed_service)
        binding = await _create_binding(seed_service, model.model_id)

    async with session_maker() as publish_session, session_maker() as binding_session:
        publish_service = ModelCatalogService(publish_session, runtime)
        publish_task = asyncio.create_task(
            publish_service.publish_model(model.model_id, username='publisher', now=LATER)
        )
        await runtime.entered.wait()

        binding_service = ModelCatalogService(binding_session, FakeRuntime())
        switched = await _create_binding(
            binding_service,
            model.model_id,
            engine_resource_id='runtime-company-chat-v2',
            expected_version=binding.version,
            now=LATER,
        )
        runtime.release.set()

        with pytest.raises(ServiceException) as exc_info:
            await publish_task

    assert switched.version == UPDATED_BINDING_VERSION
    assert exc_info.value.message == '运行时绑定已变化，请重新发布'
    async with session_maker() as verification_session:
        detail = await ModelCatalogService(verification_session, FakeRuntime()).get_model(model.model_id)
    assert detail.lifecycle_status == 'draft'
    assert detail.published_at is None
    assert detail.binding is not None
    assert detail.binding.engine_resource_id == 'runtime-company-chat-v2'


@pytest.mark.asyncio
async def test_upsert_binding_rejects_active_model_until_disabled() -> None:
    service, session, _ = _service(runtime=FakeRuntime({'runtime-company-chat'}))
    model = await _create_model(service)
    binding = await _create_binding(service, model.model_id)
    await service.publish_model(model.model_id, username='publisher', now=NOW)

    with pytest.raises(ServiceException) as exc_info:
        await _create_binding(
            service,
            model.model_id,
            engine_resource_id='runtime-company-chat-v2',
            expected_version=binding.version,
            now=LATER,
        )

    assert exc_info.value.message == '已发布模型请先停用再修改运行时绑定'
    assert session.rollback_count == TWO_CALLS


@pytest.mark.asyncio
async def test_publish_preserves_runtime_domain_error_without_local_state_change() -> None:
    runtime_error = ModelServiceError.runtime_unavailable()
    service, session, _ = _service(runtime=FakeRuntime(error=runtime_error))
    model = await _create_model(service)
    await _create_binding(service, model.model_id)

    with pytest.raises(ModelServiceError) as exc_info:
        await service.publish_model(model.model_id, username='publisher', now=LATER)

    assert exc_info.value is runtime_error
    assert FakeModelDao.models[model.model_id].lifecycle_status == 'draft'
    assert session.events[-2:] == ['rollback', 'rollback']
    assert session.rollback_count == TWO_CALLS


@pytest.mark.asyncio
async def test_real_database_publish_commit_failure_rolls_back_all_local_state(
    real_database: tuple[AsyncEngine, async_sessionmaker[CommitFailingAsyncSession]],
) -> None:
    _, session_maker = real_database
    async with session_maker() as session:
        runtime = FakeRuntime({'runtime-company-chat'})
        service = ModelCatalogService(session, runtime)
        model = await _create_model(service)
        await _create_binding(service, model.model_id)
        session.fail_next_commit = True

        with pytest.raises(RuntimeError, match='commit failed'):
            await service.publish_model(model.model_id, username='publisher', now=LATER)

    async with session_maker() as verification_session:
        stored = await AiModelCatalogDao.get_model_by_id(verification_session, model.model_id)

    assert stored is not None
    assert stored.lifecycle_status == 'draft'
    assert stored.published_at is None


@pytest.mark.asyncio
async def test_real_database_full_lifecycle_returns_committed_snapshots_with_expiring_session(
    real_database: tuple[AsyncEngine, async_sessionmaker[CommitFailingAsyncSession]],
) -> None:
    _, session_maker = real_database
    async with session_maker() as session:
        service = ModelCatalogService(session, FakeRuntime({'runtime-company-chat-v2'}))
        model = await _create_model(service)
        updated = await service.update_model(
            model.model_id,
            ModelCatalogUpdateModel.model_validate({'displayName': '提交后的名称'}),
            username='editor',
            now=LATER,
        )
        binding = await _create_binding(service, model.model_id)
        switched = await _create_binding(
            service,
            model.model_id,
            engine_resource_id='runtime-company-chat-v2',
            expected_version=binding.version,
            now=LATER,
        )
        published = await service.publish_model(model.model_id, username='publisher', now=LATER)
        disabled = await service.disable_model(model.model_id, username='operator', now=LATER)

        assert updated.display_name == '提交后的名称'
        assert switched.engine_resource_id == 'runtime-company-chat-v2'
        assert switched.version == UPDATED_BINDING_VERSION
        assert published.lifecycle_status == 'active'
        assert published.published_at == LATER
        assert disabled.lifecycle_status == 'disabled'
        assert disabled.published_at == LATER

    async with session_maker() as verification_session:
        detail = await ModelCatalogService(
            verification_session,
            FakeRuntime(),
        ).get_model(model.model_id)

    assert detail.display_name == '提交后的名称'
    assert detail.lifecycle_status == 'disabled'
    assert detail.published_at == LATER
    assert detail.binding is not None
    assert detail.binding.engine_resource_id == 'runtime-company-chat-v2'
    assert detail.binding.version == UPDATED_BINDING_VERSION


@pytest.mark.asyncio
async def test_disable_only_allows_active_model() -> None:
    service, _, _ = _service()
    model = await _create_model(service)

    with pytest.raises(ServiceException) as exc_info:
        await service.disable_model(model.model_id, username='operator', now=LATER)

    assert exc_info.value.message == '仅已发布模型可以停用'


@pytest.mark.asyncio
async def test_delete_only_never_published_unreferenced_draft_and_orders_writes() -> None:
    events: list[str] = []
    FakeModelDao.reset(events)
    FakeBindingDao.reset(events)
    checker = FakeReferenceChecker(events=events)
    service, session, _ = _service(session=FakeSession(events), reference_checker=checker)
    model = await _create_model(service)
    await _create_binding(service, model.model_id)
    events.clear()

    await service.delete_draft(model.model_id)

    assert checker.calls == [model.model_id]
    assert events == ['get_model_for_update', 'check_references', 'delete_binding', 'delete_model', 'commit']
    assert session.rollback_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('lifecycle_status', 'published_at', 'expected_message'),
    (
        ('active', NOW, '仅从未发布的草稿可以删除'),
        ('disabled', NOW, '仅从未发布的草稿可以删除'),
        ('draft', NOW, '仅从未发布的草稿可以删除'),
    ),
)
async def test_delete_rejects_published_or_non_draft_models(
    lifecycle_status: str,
    published_at: datetime,
    expected_message: str,
) -> None:
    service, _, _ = _service()
    model = await _create_model(service)
    stored = FakeModelDao.models[model.model_id]
    stored.lifecycle_status = lifecycle_status
    stored.published_at = published_at

    with pytest.raises(ServiceException) as exc_info:
        await service.delete_draft(model.model_id)

    assert exc_info.value.message == expected_message


@pytest.mark.asyncio
async def test_delete_rejects_chat_references_before_any_write() -> None:
    events: list[str] = []
    FakeModelDao.reset(events)
    FakeBindingDao.reset(events)
    checker = FakeReferenceChecker(has_references=True, events=events)
    service, session, _ = _service(session=FakeSession(events), reference_checker=checker)
    model = await _create_model(service)
    events.clear()

    with pytest.raises(ServiceException) as exc_info:
        await service.delete_draft(model.model_id)

    assert exc_info.value.message == '模型已被对话引用，不能删除'
    assert events == ['get_model_for_update', 'check_references', 'rollback']
    assert session.commit_count == 1
    assert session.rollback_count == 1


@pytest.mark.asyncio
async def test_default_reference_checker_is_explicit_and_does_not_query_database() -> None:
    checker = NoModelReferenceChecker()

    assert await checker.has_model_references('mdl_00000000000040008000000000000001') is False


@pytest.mark.asyncio
@pytest.mark.parametrize('operation_name', ('rename', 'publish', 'disable', 'delete'))
async def test_conditional_lifecycle_write_race_returns_fixed_conflict_and_rolls_back(
    operation_name: str,
) -> None:
    session = FakeSession()
    runtime = FakeRuntime({'runtime-company-chat'})
    service = ModelCatalogService(
        session,
        runtime,
        FakeReferenceChecker(),
        model_dao=LosingStateRaceModelDao,
        binding_dao=FakeBindingDao,
    )
    model = await _create_model(service)
    if operation_name in {'publish', 'disable'}:
        await _create_binding(service, model.model_id)
    if operation_name == 'disable':
        FakeModelDao.models[model.model_id].lifecycle_status = 'active'
        FakeModelDao.models[model.model_id].published_at = NOW
    rollback_count_before = session.rollback_count

    with pytest.raises(ServiceException) as exc_info:
        if operation_name == 'rename':
            await service.update_model(
                model.model_id,
                ModelCatalogUpdateModel.model_validate({'modelCode': 'renamed'}),
                username='editor',
                now=LATER,
            )
        elif operation_name == 'publish':
            await service.publish_model(model.model_id, username='publisher', now=LATER)
        elif operation_name == 'disable':
            await service.disable_model(model.model_id, username='operator', now=LATER)
        else:
            await service.delete_draft(model.model_id)

    assert exc_info.value.message == '模型状态已变化，请刷新后重试'
    expected_rollbacks = TWO_CALLS if operation_name == 'publish' else 1
    assert session.rollback_count == rollback_count_before + expected_rollbacks
