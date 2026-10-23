import asyncio
import base64
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from plugins.ai.domain.developer_api_errors import DeveloperApiError
from plugins.ai.entity.do.ai_developer_api_do import (
    AiApiKey,
    AiApiKeyModelGrant,
    AiApiQuotaPolicy,
    AiApplication,
)
from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
from plugins.ai.entity.vo.ai_developer_api_vo import (
    DeveloperKeyCreatedModel,
    DeveloperKeyCreateModel,
    DeveloperKeyRotateModel,
)
from plugins.ai.infrastructure.developer_key_codec import DeveloperKeyCodec
from plugins.ai.service.developer_key_service import DeveloperApiActor, DeveloperKeyService

NOW = datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc)
DB_NOW = NOW.replace(tzinfo=None)
APPLICATION_ID = 'app_00000000000040008000000000000001'
SECOND_APPLICATION_ID = 'app_00000000000040008000000000000002'
KEY_ID = 'key_00000000000040008000000000000001'
SECOND_KEY_ID = 'key_00000000000040008000000000000002'
MODEL_ID = 'mdl_00000000000040008000000000000001'
SECOND_MODEL_ID = 'mdl_00000000000040008000000000000002'
OWNER = DeveloperApiActor(user_id=7, username='alice', is_admin=False)
OTHER = DeveloperApiActor(user_id=8, username='bob', is_admin=False)
ADMIN = DeveloperApiActor(user_id=99, username='admin', is_admin=True)
EXPECTED_CREATED_RECORDS = 3
EXPECTED_KEY_COUNT_AFTER_ROTATE = 2
GRACE_SECONDS = 3600
TOKEN_BYTES = b'x' * 32
TOKEN_TEXT = base64.urlsafe_b64encode(TOKEN_BYTES).rstrip(b'=').decode('ascii')


class FakeSession:
    def __init__(self) -> None:
        self.commit_count = 0
        self.rollback_count = 0
        self.commit_error: BaseException | None = None
        self.rollback_error: BaseException | None = None

    async def commit(self) -> None:
        self.commit_count += 1
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self) -> None:
        self.rollback_count += 1
        if self.rollback_error is not None:
            raise self.rollback_error


class FakeDeveloperApiDao:
    def __init__(self) -> None:
        self.applications: dict[str, AiApplication] = {}
        self.keys: dict[str, AiApiKey] = {}
        self.grants: dict[str, list[AiApiKeyModelGrant]] = {}
        self.quotas: dict[str, AiApiQuotaPolicy] = {}
        self.active_model_ids: set[str] = set()
        self.events: list[str] = []
        self.create_key_error: BaseException | None = None
        self.transition_override: int | None = None

    async def get_application_for_update(self, _db: FakeSession, application_id: str) -> AiApplication | None:
        self.events.append('get_application_for_update')
        return self.applications.get(application_id)

    async def get_application_by_id(self, _db: FakeSession, application_id: str) -> AiApplication | None:
        self.events.append('get_application_by_id')
        return self.applications.get(application_id)

    async def find_active_model_ids(self, _db: FakeSession, model_ids: set[str]) -> set[str]:
        self.events.append('find_active_model_ids')
        return model_ids & self.active_model_ids

    async def create_key(self, _db: FakeSession, api_key: AiApiKey) -> AiApiKey:
        self.events.append('create_key')
        if self.create_key_error is not None:
            raise self.create_key_error
        self.keys[api_key.api_key_id] = api_key
        return api_key

    async def get_key_by_id(
        self,
        _db: FakeSession,
        application_id: str,
        api_key_id: str,
    ) -> AiApiKey | None:
        self.events.append('get_key_by_id')
        key = self.keys.get(api_key_id)
        return key if key is not None and key.application_id == application_id else None

    async def get_key_for_update(
        self,
        _db: FakeSession,
        application_id: str,
        api_key_id: str,
    ) -> AiApiKey | None:
        self.events.append('get_key_for_update')
        return await self.get_key_by_id(_db, application_id, api_key_id)

    async def list_keys_by_application(self, _db: FakeSession, application_id: str) -> list[AiApiKey]:
        self.events.append('list_keys_by_application')
        return [item for item in self.keys.values() if item.application_id == application_id]

    async def transition_key_status(
        self,
        _db: FakeSession,
        application_id: str,
        api_key_id: str,
        *,
        expected_statuses: set[str],
        values: dict[str, Any],
    ) -> int:
        self.events.append('transition_key_status')
        if self.transition_override is not None:
            return self.transition_override
        key = await self.get_key_by_id(_db, application_id, api_key_id)
        if key is None or key.status not in expected_statuses:
            return 0
        for field, value in values.items():
            setattr(key, field, value)
        return 1

    async def replace_grants(
        self,
        _db: FakeSession,
        api_key_id: str,
        grants: list[AiApiKeyModelGrant],
    ) -> int:
        self.events.append('replace_grants')
        self.grants[api_key_id] = list(grants)
        return len(grants)

    async def list_grants(self, _db: FakeSession, api_key_id: str) -> list[AiApiKeyModelGrant]:
        self.events.append('list_grants')
        return list(self.grants.get(api_key_id, []))

    async def create_quota(self, _db: FakeSession, quota: AiApiQuotaPolicy) -> AiApiQuotaPolicy:
        self.events.append('create_quota')
        self.quotas[quota.api_key_id] = quota
        return quota

    async def get_quota_by_key(self, _db: FakeSession, api_key_id: str) -> AiApiQuotaPolicy | None:
        self.events.append('get_quota_by_key')
        return self.quotas.get(api_key_id)


class BlockingTransitionDao(FakeDeveloperApiDao):
    def __init__(self) -> None:
        super().__init__()
        self._transition_lock = asyncio.Lock()
        self._entered_count = 0
        self._both_entered = asyncio.Event()

    async def transition_key_status(
        self,
        db: FakeSession,
        application_id: str,
        api_key_id: str,
        *,
        expected_statuses: set[str],
        values: dict[str, Any],
    ) -> int:
        self._entered_count += 1
        if self._entered_count == EXPECTED_KEY_COUNT_AFTER_ROTATE:
            self._both_entered.set()
        await self._both_entered.wait()
        async with self._transition_lock:
            return await super().transition_key_status(
                db,
                application_id,
                api_key_id,
                expected_statuses=expected_statuses,
                values=values,
            )


class CommitFailingAsyncSession(AsyncSession):
    fail_next_commit = False

    async def commit(self) -> None:
        if self.fail_next_commit:
            self.fail_next_commit = False
            raise RuntimeError('PRIVATE-COMMIT-ERROR')
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
        for table in (
            AiApplication.__table__,
            AiApiKey.__table__,
            AiApiKeyModelGrant.__table__,
            AiApiQuotaPolicy.__table__,
        ):
            await connection.run_sync(table.create)
    try:
        yield engine, session_maker
    finally:
        await engine.dispose()


def _codec(*, configured: bool = True) -> DeveloperKeyCodec:
    pepper = 'p' * 32 if configured else 'short'
    return DeveloperKeyCodec(
        current_version='v1',
        peppers={'v1': SecretStr(pepper)},
        token_bytes=lambda _size: TOKEN_BYTES,
    )


def _application(
    application_id: str = APPLICATION_ID,
    *,
    owner_user_id: int = OWNER.user_id,
    status: str = 'active',
) -> AiApplication:
    return AiApplication(
        application_id=application_id,
        application_code=f'app-{application_id[-2:]}',
        application_name=f'Application {application_id[-2:]}',
        owner_user_id=owner_user_id,
        status=status,
        version=1,
        disabled_at=DB_NOW if status == 'disabled' else None,
        created_by='alice',
        created_at=DB_NOW,
        updated_by='alice',
        updated_at=DB_NOW,
    )


def _key(
    key_id: str = KEY_ID,
    *,
    application_id: str = APPLICATION_ID,
    status: str = 'active',
) -> AiApiKey:
    return AiApiKey(
        api_key_id=key_id,
        application_id=application_id,
        key_name='production',
        key_prefix=f'sk-zh_{key_id}',
        secret_digest='a' * 64,
        digest_version='v1',
        fingerprint='test',
        status=status,
        created_by='alice',
        created_at=DB_NOW,
        updated_by='alice',
        updated_at=DB_NOW,
    )


def _grant(key_id: str = KEY_ID, model_id: str = MODEL_ID) -> AiApiKeyModelGrant:
    return AiApiKeyModelGrant(
        grant_id='grt_00000000000040008000000000000001',
        api_key_id=key_id,
        platform_model_id=model_id,
        status='active',
        created_by='alice',
        created_at=DB_NOW,
        updated_by='alice',
        updated_at=DB_NOW,
    )


def _quota(key_id: str = KEY_ID) -> AiApiQuotaPolicy:
    return AiApiQuotaPolicy(
        quota_id='qta_00000000000040008000000000000001',
        api_key_id=key_id,
        rpm_limit=60,
        tpm_limit=10000,
        concurrency_limit=5,
        monthly_budget=Decimal('100.00'),
        warning_ratio=Decimal('0.8000'),
        hard_limit_ratio=Decimal('1.0000'),
        currency='USD',
        version=1,
        created_by='alice',
        created_at=DB_NOW,
        updated_by='alice',
        updated_at=DB_NOW,
    )


def _payload() -> DeveloperKeyCreateModel:
    return DeveloperKeyCreateModel.model_validate(
        {
            'keyName': 'production',
            'modelIds': [MODEL_ID],
            'quota': {
                'rpmLimit': 60,
                'tpmLimit': 10000,
                'concurrencyLimit': 5,
                'monthlyBudget': '100.00',
                'warningRatio': '0.8000',
                'hardLimitRatio': '1.0000',
                'currency': 'USD',
            },
        }
    )


def _service(
    *,
    configured: bool = True,
) -> tuple[DeveloperKeyService, FakeSession, FakeDeveloperApiDao]:
    session = FakeSession()
    dao = FakeDeveloperApiDao()
    service = DeveloperKeyService(session, _codec(configured=configured), developer_api_dao=dao)  # type: ignore[arg-type]
    return service, session, dao


def _seed_active(dao: FakeDeveloperApiDao) -> None:
    dao.applications[APPLICATION_ID] = _application()
    dao.active_model_ids.add(MODEL_ID)


def _assert_secret_absent_from_production_traceback(error: BaseException, secret: str) -> None:
    assert secret not in str(error)
    assert secret not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None
    traceback = error.__traceback__
    while traceback is not None:
        filename = traceback.tb_frame.f_code.co_filename
        if '/plugins/ai/' in filename and '/tests/' not in filename:
            assert secret not in repr(traceback.tb_frame.f_locals)
        traceback = traceback.tb_next


@pytest.mark.asyncio
async def test_unconfigured_codec_fails_before_any_database_access() -> None:
    service, session, dao = _service(configured=False)

    with pytest.raises(DeveloperApiError) as create_error:
        await service.create_key(APPLICATION_ID, _payload(), actor=OWNER, now=NOW)
    with pytest.raises(DeveloperApiError) as rotate_error:
        await service.rotate_key(
            APPLICATION_ID,
            KEY_ID,
            DeveloperKeyRotateModel(gracePeriodSeconds=GRACE_SECONDS),
            actor=OWNER,
            now=NOW,
        )

    assert create_error.value.code == rotate_error.value.code == 'DEVELOPER_API_MISCONFIGURED'
    assert dao.events == []
    assert session.commit_count == session.rollback_count == 0


@pytest.mark.asyncio
async def test_create_key_atomically_writes_digest_grants_quota_and_returns_secret_once() -> None:
    service, session, dao = _service()
    _seed_active(dao)

    created = await service.create_key(APPLICATION_ID, _payload(), actor=OWNER, now=NOW)

    assert created.api_key.startswith(f'sk-zh_{created.key_id}_')
    assert created.api_key not in repr(created)
    assert created.masked_key.endswith(created.fingerprint)
    assert created.grants[0].model_id == MODEL_ID
    assert created.quota.monthly_budget == Decimal('100.00')
    stored = dao.keys[created.key_id]
    assert stored.secret_digest != created.api_key
    assert created.api_key not in repr(stored)
    assert dao.events[-EXPECTED_CREATED_RECORDS:] == ['create_key', 'replace_grants', 'create_quota']
    assert session.commit_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('application', 'models', 'actor', 'expected_code'),
    (
        (None, {MODEL_ID}, OWNER, 'APPLICATION_NOT_FOUND'),
        (_application(owner_user_id=OTHER.user_id), {MODEL_ID}, OWNER, 'APPLICATION_NOT_FOUND'),
        (_application(status='disabled'), {MODEL_ID}, OWNER, 'APPLICATION_DISABLED'),
        (_application(), set(), OWNER, 'MODEL_GRANT_INVALID'),
    ),
)
async def test_create_rejects_invalid_application_or_model_grants(
    application: AiApplication | None,
    models: set[str],
    actor: DeveloperApiActor,
    expected_code: str,
) -> None:
    service, session, dao = _service()
    if application is not None:
        dao.applications[APPLICATION_ID] = application
    dao.active_model_ids = models

    with pytest.raises(DeveloperApiError) as captured:
        await service.create_key(APPLICATION_ID, _payload(), actor=actor, now=NOW)

    assert captured.value.code == expected_code
    assert session.rollback_count == 1
    assert not dao.keys


@pytest.mark.asyncio
async def test_list_is_owner_scoped_and_never_returns_digest_or_complete_key() -> None:
    service, _, dao = _service()
    _seed_active(dao)
    dao.keys[KEY_ID] = _key()
    dao.grants[KEY_ID] = [_grant()]
    dao.quotas[KEY_ID] = _quota()

    summaries = await service.list_keys(APPLICATION_ID, actor=OWNER)

    assert len(summaries) == 1
    serialized = summaries[0].model_dump(by_alias=True, mode='json')
    assert serialized['maskedKey'].endswith('test')
    assert 'apiKey' not in serialized
    assert 'secretDigest' not in repr(summaries)
    with pytest.raises(DeveloperApiError) as denied:
        await service.list_keys(APPLICATION_ID, actor=OTHER)
    assert denied.value.code == 'APPLICATION_NOT_FOUND'


@pytest.mark.asyncio
async def test_rotate_copies_policy_and_grants_then_only_one_cas_wins() -> None:
    service, session, dao = _service()
    _seed_active(dao)
    dao.keys[KEY_ID] = _key()
    dao.grants[KEY_ID] = [_grant()]
    dao.quotas[KEY_ID] = _quota()

    created = await service.rotate_key(
        APPLICATION_ID,
        KEY_ID,
        DeveloperKeyRotateModel(gracePeriodSeconds=GRACE_SECONDS),
        actor=OWNER,
        now=NOW,
    )

    assert created.key_id != KEY_ID
    assert dao.keys[KEY_ID].status == 'retiring'
    assert dao.keys[KEY_ID].retire_at == DB_NOW + timedelta(seconds=GRACE_SECONDS)
    assert dao.grants[created.key_id][0].platform_model_id == MODEL_ID
    assert dao.quotas[created.key_id].rpm_limit == dao.quotas[KEY_ID].rpm_limit
    assert len(dao.keys) == EXPECTED_KEY_COUNT_AFTER_ROTATE
    assert session.commit_count == 1

    with pytest.raises(DeveloperApiError) as stale:
        await service.rotate_key(
            APPLICATION_ID,
            KEY_ID,
            DeveloperKeyRotateModel(gracePeriodSeconds=GRACE_SECONDS),
            actor=OWNER,
            now=NOW,
        )
    assert stale.value.code == 'API_KEY_INACTIVE'
    assert len(dao.keys) == EXPECTED_KEY_COUNT_AFTER_ROTATE


@pytest.mark.asyncio
async def test_zero_grace_rotation_revokes_old_key_immediately() -> None:
    service, _, dao = _service()
    _seed_active(dao)
    dao.keys[KEY_ID] = _key()
    dao.grants[KEY_ID] = [_grant()]
    dao.quotas[KEY_ID] = _quota()

    await service.rotate_key(
        APPLICATION_ID,
        KEY_ID,
        DeveloperKeyRotateModel(gracePeriodSeconds=0),
        actor=ADMIN,
        now=NOW,
    )

    assert dao.keys[KEY_ID].status == 'revoked'
    assert dao.keys[KEY_ID].revoked_at == DB_NOW
    assert dao.keys[KEY_ID].retire_at is None


@pytest.mark.asyncio
async def test_two_concurrent_rotations_only_allow_one_old_key_cas_winner() -> None:
    dao = BlockingTransitionDao()
    _seed_active(dao)
    dao.keys[KEY_ID] = _key()
    dao.grants[KEY_ID] = [_grant()]
    dao.quotas[KEY_ID] = _quota()
    sessions = (FakeSession(), FakeSession())
    services = (
        DeveloperKeyService(sessions[0], _codec(), developer_api_dao=dao),  # type: ignore[arg-type]
        DeveloperKeyService(sessions[1], _codec(), developer_api_dao=dao),  # type: ignore[arg-type]
    )

    results = await asyncio.gather(
        *(
            service.rotate_key(
                APPLICATION_ID,
                KEY_ID,
                DeveloperKeyRotateModel(gracePeriodSeconds=GRACE_SECONDS),
                actor=OWNER,
                now=NOW,
            )
            for service in services
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, DeveloperKeyCreatedModel) for result in results) == 1
    failures = [result for result in results if isinstance(result, DeveloperApiError)]
    assert len(failures) == 1 and failures[0].code == 'API_KEY_INACTIVE'
    assert len(dao.keys) == EXPECTED_KEY_COUNT_AFTER_ROTATE
    assert sum(session.commit_count for session in sessions) == 1
    assert sum(session.rollback_count for session in sessions) == 1


@pytest.mark.asyncio
async def test_revoke_is_idempotent_and_scoped_to_application() -> None:
    service, session, dao = _service()
    dao.applications[APPLICATION_ID] = _application()
    dao.applications[SECOND_APPLICATION_ID] = _application(SECOND_APPLICATION_ID, owner_user_id=OTHER.user_id)
    dao.keys[KEY_ID] = _key()
    dao.grants[KEY_ID] = [_grant()]
    dao.quotas[KEY_ID] = _quota()

    revoked = await service.revoke_key(APPLICATION_ID, KEY_ID, actor=OWNER, now=NOW)
    repeated = await service.revoke_key(APPLICATION_ID, KEY_ID, actor=OWNER, now=NOW)

    assert revoked.status == repeated.status == 'revoked'
    assert session.commit_count == 1
    assert session.rollback_count == 1
    with pytest.raises(DeveloperApiError) as foreign:
        await service.revoke_key(SECOND_APPLICATION_ID, KEY_ID, actor=OTHER, now=NOW)
    assert foreign.value.code == 'API_KEY_NOT_FOUND'


@pytest.mark.asyncio
async def test_cancellation_is_preserved_when_rollback_raises_ordinary_error() -> None:
    service, session, dao = _service()
    _seed_active(dao)
    dao.create_key_error = asyncio.CancelledError()
    session.rollback_error = RuntimeError('rollback failed')

    with pytest.raises(asyncio.CancelledError):
        await service.create_key(APPLICATION_ID, _payload(), actor=OWNER, now=NOW)
    assert session.rollback_count == 1


@pytest.mark.asyncio
async def test_rollback_base_exception_is_rethrown_from_clean_boundary_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr('plugins.ai.service.developer_key_service.new_resource_id', lambda _prefix: KEY_ID)
    service, session, dao = _service()
    _seed_active(dao)
    dao.create_key_error = RuntimeError('write failed')
    session.rollback_error = KeyboardInterrupt()
    complete_key = f'sk-zh_{KEY_ID}_{TOKEN_TEXT}'

    with pytest.raises(KeyboardInterrupt) as captured:
        await service.create_key(APPLICATION_ID, _payload(), actor=OWNER, now=NOW)

    _assert_secret_absent_from_production_traceback(captured.value, complete_key)


@pytest.mark.asyncio
async def test_real_commit_failure_rolls_back_and_traceback_does_not_retain_complete_key(
    real_database: tuple[AsyncEngine, async_sessionmaker[CommitFailingAsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, session_maker = real_database
    monkeypatch.setattr(
        'plugins.ai.service.developer_key_service.new_resource_id',
        lambda prefix: {
            'key': KEY_ID,
            'grt': 'grt_00000000000040008000000000000001',
            'qta': 'qta_00000000000040008000000000000001',
        }[prefix],
    )
    async with session_maker() as seed:
        seed.add_all(
            [
                _application(),
                AiModelCatalog(
                    model_id=MODEL_ID,
                    model_code='deepseek-chat',
                    display_name='DeepSeek Chat',
                    model_type='chat',
                    capabilities={},
                    default_parameters={},
                    lifecycle_status='active',
                    published_at=DB_NOW,
                    create_by='alice',
                    create_time=DB_NOW,
                    update_by='alice',
                    update_time=DB_NOW,
                ),
            ]
        )
        await seed.commit()

    complete_key = f'sk-zh_{KEY_ID}_{TOKEN_TEXT}'
    async with session_maker() as session:
        session.fail_next_commit = True
        with pytest.raises(RuntimeError) as captured:
            await DeveloperKeyService(session, _codec()).create_key(
                APPLICATION_ID,
                _payload(),
                actor=OWNER,
                now=NOW,
            )
    _assert_secret_absent_from_production_traceback(captured.value, complete_key)

    async with session_maker() as verify_session:
        assert (await verify_session.execute(select(AiApiKey))).scalars().all() == []
        assert (await verify_session.execute(select(AiApiKeyModelGrant))).scalars().all() == []
        assert (await verify_session.execute(select(AiApiQuotaPolicy))).scalars().all() == []


@pytest.mark.asyncio
async def test_real_rotate_commit_failure_restores_old_key_and_removes_new_resources(
    real_database: tuple[AsyncEngine, async_sessionmaker[CommitFailingAsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, session_maker = real_database
    monkeypatch.setattr(
        'plugins.ai.service.developer_key_service.new_resource_id',
        lambda prefix: {
            'key': SECOND_KEY_ID,
            'grt': 'grt_00000000000040008000000000000002',
            'qta': 'qta_00000000000040008000000000000002',
        }[prefix],
    )
    async with session_maker() as seed:
        seed.add_all(
            [
                _application(),
                AiModelCatalog(
                    model_id=MODEL_ID,
                    model_code='deepseek-chat',
                    display_name='DeepSeek Chat',
                    model_type='chat',
                    capabilities={},
                    default_parameters={},
                    lifecycle_status='active',
                    published_at=DB_NOW,
                    create_by='alice',
                    create_time=DB_NOW,
                    update_by='alice',
                    update_time=DB_NOW,
                ),
            ]
        )
        await seed.commit()
        seed.add(_key())
        await seed.commit()
        seed.add_all([_grant(), _quota()])
        await seed.commit()

    complete_key = f'sk-zh_{SECOND_KEY_ID}_{TOKEN_TEXT}'
    async with session_maker() as session:
        session.fail_next_commit = True
        with pytest.raises(RuntimeError) as captured:
            await DeveloperKeyService(session, _codec()).rotate_key(
                APPLICATION_ID,
                KEY_ID,
                DeveloperKeyRotateModel(gracePeriodSeconds=GRACE_SECONDS),
                actor=OWNER,
                now=NOW,
            )
    _assert_secret_absent_from_production_traceback(captured.value, complete_key)

    async with session_maker() as verify_session:
        keys = (await verify_session.execute(select(AiApiKey))).scalars().all()
        grants = (await verify_session.execute(select(AiApiKeyModelGrant))).scalars().all()
        quotas = (await verify_session.execute(select(AiApiQuotaPolicy))).scalars().all()
    assert [(key.api_key_id, key.status) for key in keys] == [(KEY_ID, 'active')]
    assert [grant.api_key_id for grant in grants] == [KEY_ID]
    assert [quota.api_key_id for quota in quotas] == [KEY_ID]
