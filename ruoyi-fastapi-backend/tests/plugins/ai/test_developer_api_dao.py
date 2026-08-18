from collections.abc import AsyncIterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.dialects import mysql, postgresql
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from common.vo import PageModel
from plugins.ai.dao.ai_developer_api_dao import AiDeveloperApiDao
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
from plugins.ai.entity.vo.ai_developer_api_vo import ApplicationPageQueryModel

NOW = datetime(2026, 8, 18, 10, 0)
APPLICATION_ID = 'app_00000000000040008000000000000001'
SECOND_APPLICATION_ID = 'app_00000000000040008000000000000002'
KEY_ID = 'key_00000000000040008000000000000001'
SECOND_KEY_ID = 'key_00000000000040008000000000000002'
MODEL_ID = 'mdl_00000000000040008000000000000001'
SECOND_MODEL_ID = 'mdl_00000000000040008000000000000002'
BINDING_ID = 'bnd_00000000000040008000000000000001'
RATE_CARD_ID = 'rate_00000000000040008000000000000001'
REQUEST_ID = 'use_00000000000040008000000000000001'
EXPECTED_APPLICATION_COUNT = 2
EXPECTED_UPDATED_VERSION = 2
DEFAULT_RPM_LIMIT = 60


class CommitForbiddenAsyncSession(AsyncSession):
    async def commit(self) -> None:
        raise AssertionError('DAO must not commit transactions')


@pytest_asyncio.fixture
async def database() -> AsyncIterator[tuple[AsyncEngine, CommitForbiddenAsyncSession]]:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')

    @event.listens_for(engine.sync_engine, 'connect')
    def configure_sqlite(connection: Any, _record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()

    session_maker = async_sessionmaker(bind=engine, class_=CommitForbiddenAsyncSession, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(AiModelCatalog.__table__.create)
        await connection.run_sync(AiResourceBinding.__table__.create)
        for table in (
            AiApplication.__table__,
            AiApiKey.__table__,
            AiApiKeyModelGrant.__table__,
            AiApiQuotaPolicy.__table__,
            AiModelRateCard.__table__,
            AiApiUsageLedger.__table__,
        ):
            await connection.run_sync(table.create)
    async with session_maker() as session:
        yield engine, session
        await session.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def concurrent_database(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f'sqlite+aiosqlite:///{tmp_path / "developer-api-dao.db"}',
        connect_args={'timeout': 5},
    )

    @event.listens_for(engine.sync_engine, 'connect')
    def configure_sqlite(connection: Any, _record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.execute('PRAGMA busy_timeout=5000')
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()

    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(AiModelCatalog.__table__.create)
        await connection.run_sync(AiApplication.__table__.create)
        await connection.run_sync(AiApiKey.__table__.create)
    try:
        yield session_maker
    finally:
        await engine.dispose()


def _application(
    application_id: str = APPLICATION_ID,
    *,
    code: str = 'billing-prod',
    owner_user_id: int = 7,
) -> AiApplication:
    return AiApplication(
        application_id=application_id,
        application_code=code,
        application_name=code.replace('-', ' ').title(),
        owner_user_id=owner_user_id,
        status='active',
        version=1,
        created_by='alice',
        created_at=NOW,
        updated_by='alice',
        updated_at=NOW,
    )


def _key(
    key_id: str = KEY_ID,
    *,
    application_id: str = APPLICATION_ID,
    name: str = 'production',
) -> AiApiKey:
    return AiApiKey(
        api_key_id=key_id,
        application_id=application_id,
        key_name=name,
        key_prefix=f'sk-zh_{key_id}',
        secret_digest='a' * 64,
        digest_version='v1',
        fingerprint='9f3c',
        status='active',
        created_by='alice',
        created_at=NOW,
        updated_by='alice',
        updated_at=NOW,
    )


def _model(model_id: str = MODEL_ID, *, status: str = 'active') -> AiModelCatalog:
    return AiModelCatalog(
        model_id=model_id,
        model_code=f'model-{model_id[-2:]}',
        display_name=f'Model {model_id[-2:]}',
        model_type='chat',
        capabilities={},
        default_parameters={},
        lifecycle_status=status,
        published_at=NOW if status == 'active' else None,
        create_by='alice',
        create_time=NOW,
        update_by='alice',
        update_time=NOW,
    )


@pytest.mark.asyncio
async def test_application_create_get_lock_and_owner_paginate_without_commit(
    database: tuple[AsyncEngine, CommitForbiddenAsyncSession],
) -> None:
    _, session = database
    await AiDeveloperApiDao.create_application(session, _application())
    await AiDeveloperApiDao.create_application(
        session,
        _application(SECOND_APPLICATION_ID, code='search-prod', owner_user_id=8),
    )

    by_id = await AiDeveloperApiDao.get_application_by_id(session, APPLICATION_ID)
    by_code = await AiDeveloperApiDao.get_application_by_code(session, 'billing-prod')
    locked = await AiDeveloperApiDao.get_application_for_update(session, APPLICATION_ID)
    page = await AiDeveloperApiDao.list_applications(
        session,
        ApplicationPageQueryModel(pageNum=1, pageSize=10),
        owner_user_id=7,
        is_page=True,
    )

    assert by_id is not None and by_id.application_name == 'Billing Prod'
    assert by_code is not None and by_code.application_id == APPLICATION_ID
    assert locked is not None and locked.application_id == APPLICATION_ID
    assert isinstance(page, PageModel)
    assert page.total == 1
    assert page.rows[0]['applicationId'] == APPLICATION_ID


@pytest.mark.asyncio
async def test_application_admin_page_and_versioned_update_are_atomic(
    database: tuple[AsyncEngine, CommitForbiddenAsyncSession],
) -> None:
    _, session = database
    await AiDeveloperApiDao.create_application(session, _application())
    await AiDeveloperApiDao.create_application(
        session,
        _application(SECOND_APPLICATION_ID, code='search-prod', owner_user_id=8),
    )

    page = await AiDeveloperApiDao.list_applications(
        session,
        ApplicationPageQueryModel(pageNum=1, pageSize=10),
        owner_user_id=None,
        is_page=True,
    )
    first = await AiDeveloperApiDao.update_application_with_version(
        session,
        APPLICATION_ID,
        expected_version=1,
        values={'application_name': 'Billing V2', 'updated_by': 'alice', 'updated_at': NOW},
    )
    stale = await AiDeveloperApiDao.update_application_with_version(
        session,
        APPLICATION_ID,
        expected_version=1,
        values={'application_name': 'Stale Write'},
    )

    assert page.total == EXPECTED_APPLICATION_COUNT
    assert first == 1
    assert stale == 0
    stored = await AiDeveloperApiDao.get_application_by_id(session, APPLICATION_ID)
    assert stored is not None and stored.application_name == 'Billing V2' and stored.version == EXPECTED_UPDATED_VERSION


@pytest.mark.asyncio
async def test_application_disable_is_conditional_and_update_allowlist_rejects_identity_changes(
    database: tuple[AsyncEngine, CommitForbiddenAsyncSession],
) -> None:
    _, session = database
    await AiDeveloperApiDao.create_application(session, _application())

    assert (
        await AiDeveloperApiDao.disable_application_if_active(
            session,
            APPLICATION_ID,
            {'disabled_at': NOW, 'updated_by': 'alice', 'updated_at': NOW},
        )
        == 1
    )
    assert await AiDeveloperApiDao.disable_application_if_active(session, APPLICATION_ID, {'disabled_at': NOW}) == 0
    with pytest.raises(ValueError, match='owner_user_id'):
        await AiDeveloperApiDao.update_application_with_version(
            session,
            APPLICATION_ID,
            expected_version=2,
            values={'owner_user_id': 9},
        )
    with pytest.raises(ValueError, match='status'):
        await AiDeveloperApiDao.update_application_with_version(
            session,
            APPLICATION_ID,
            expected_version=2,
            values={'status': 'active'},
        )


@pytest.mark.asyncio
async def test_key_create_list_lock_and_status_transition_are_scoped_to_application(
    database: tuple[AsyncEngine, CommitForbiddenAsyncSession],
) -> None:
    _, session = database
    await AiDeveloperApiDao.create_application(session, _application())
    await AiDeveloperApiDao.create_key(session, _key())

    by_id = await AiDeveloperApiDao.get_key_by_id(session, APPLICATION_ID, KEY_ID)
    locked = await AiDeveloperApiDao.get_key_for_update(session, APPLICATION_ID, KEY_ID)
    listed = await AiDeveloperApiDao.list_keys_by_application(session, APPLICATION_ID)
    changed = await AiDeveloperApiDao.transition_key_status(
        session,
        APPLICATION_ID,
        KEY_ID,
        expected_statuses={'active'},
        values={'status': 'retiring', 'retire_at': NOW, 'updated_at': NOW},
    )
    stale = await AiDeveloperApiDao.transition_key_status(
        session,
        APPLICATION_ID,
        KEY_ID,
        expected_statuses={'active'},
        values={'status': 'revoked'},
    )

    assert by_id is not None and by_id.secret_digest == 'a' * 64
    assert locked is not None and locked.api_key_id == KEY_ID
    assert [item.api_key_id for item in listed] == [KEY_ID]
    assert (changed, stale) == (1, 0)
    assert await AiDeveloperApiDao.get_key_by_id(session, SECOND_APPLICATION_ID, KEY_ID) is None


@pytest.mark.asyncio
async def test_grants_replace_and_active_model_lookup_use_platform_ids(
    database: tuple[AsyncEngine, CommitForbiddenAsyncSession],
) -> None:
    _, session = database
    session.add_all([_model(), _model(SECOND_MODEL_ID, status='disabled')])
    await AiDeveloperApiDao.create_application(session, _application())
    await AiDeveloperApiDao.create_key(session, _key())
    first = AiApiKeyModelGrant(
        grant_id='grt_00000000000040008000000000000001',
        api_key_id=KEY_ID,
        platform_model_id=MODEL_ID,
        status='active',
        created_by='alice',
        created_at=NOW,
        updated_by='alice',
        updated_at=NOW,
    )

    assert await AiDeveloperApiDao.replace_grants(session, KEY_ID, [first]) == 1
    grants = await AiDeveloperApiDao.list_grants(session, KEY_ID)
    active = await AiDeveloperApiDao.find_active_model_ids(session, {MODEL_ID, SECOND_MODEL_ID})

    assert [grant.platform_model_id for grant in grants] == [MODEL_ID]
    assert active == {MODEL_ID}


@pytest.mark.asyncio
async def test_quota_create_get_and_versioned_update_are_atomic(
    database: tuple[AsyncEngine, CommitForbiddenAsyncSession],
) -> None:
    _, session = database
    await AiDeveloperApiDao.create_application(session, _application())
    await AiDeveloperApiDao.create_key(session, _key())
    quota = AiApiQuotaPolicy(
        quota_id='qta_00000000000040008000000000000001',
        api_key_id=KEY_ID,
        rpm_limit=DEFAULT_RPM_LIMIT,
        monthly_budget=Decimal('100.00'),
        warning_ratio=Decimal('0.8'),
        hard_limit_ratio=Decimal('1.0'),
        currency='USD',
        version=1,
        created_by='alice',
        created_at=NOW,
        updated_by='alice',
        updated_at=NOW,
    )
    await AiDeveloperApiDao.create_quota(session, quota)

    assert (await AiDeveloperApiDao.get_quota_by_key(session, KEY_ID)).rpm_limit == DEFAULT_RPM_LIMIT  # type: ignore[union-attr]
    assert (
        await AiDeveloperApiDao.update_quota_with_version(
            session,
            KEY_ID,
            expected_version=1,
            values={'rpm_limit': 120, 'updated_at': NOW},
        )
        == 1
    )
    assert (
        await AiDeveloperApiDao.update_quota_with_version(
            session,
            KEY_ID,
            expected_version=1,
            values={'rpm_limit': 240},
        )
        == 0
    )


@pytest.mark.asyncio
async def test_rate_card_and_ledger_basics_flush_without_commit(
    database: tuple[AsyncEngine, CommitForbiddenAsyncSession],
) -> None:
    _, session = database
    session.add(_model())
    await AiDeveloperApiDao.create_application(session, _application())
    await AiDeveloperApiDao.create_key(session, _key())
    session.add(
        AiResourceBinding(
            binding_id=BINDING_ID,
            resource_type='model',
            resource_id=MODEL_ID,
            engine_type='litellm',
            engine_resource_id='deepseek-chat',
            binding_status='active',
            engine_metadata={},
            version=1,
            create_by='alice',
            create_time=NOW,
            update_by='alice',
            update_time=NOW,
        )
    )
    rate_card = AiModelRateCard(
        rate_card_id=RATE_CARD_ID,
        platform_model_id=MODEL_ID,
        version=1,
        input_price_per_token=Decimal('0.000001'),
        output_price_per_token=Decimal('0.000002'),
        currency='USD',
        effective_from=NOW,
        status='active',
        created_by='alice',
        created_at=NOW,
        updated_by='alice',
        updated_at=NOW,
    )
    await AiDeveloperApiDao.create_rate_card(session, rate_card)
    ledger = AiApiUsageLedger(
        request_id=REQUEST_ID,
        application_id=APPLICATION_ID,
        api_key_id=KEY_ID,
        platform_model_id=MODEL_ID,
        model_code='model-01',
        binding_id=BINDING_ID,
        engine_type='litellm',
        engine_model_snapshot='deepseek-chat',
        rate_card_id=RATE_CARD_ID,
        rate_card_version=1,
        currency='USD',
        cost_estimated=False,
        status='started',
        started_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    await AiDeveloperApiDao.create_usage_ledger(session, ledger)

    assert await AiDeveloperApiDao.get_active_rate_card(session, MODEL_ID, NOW) is rate_card
    stored = await AiDeveloperApiDao.get_usage_ledger(session, REQUEST_ID)
    assert stored is ledger


class _ScalarResult:
    def scalars(self) -> '_ScalarResult':
        return self

    def first(self) -> None:
        return None


class _RecordingSession:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, statement: Any) -> _ScalarResult:
        self.statements.append(statement)
        return _ScalarResult()


@pytest.mark.asyncio
async def test_application_and_key_lock_statements_compile_for_update() -> None:
    session = _RecordingSession()

    await AiDeveloperApiDao.get_application_for_update(session, APPLICATION_ID)  # type: ignore[arg-type]
    await AiDeveloperApiDao.get_key_for_update(session, APPLICATION_ID, KEY_ID)  # type: ignore[arg-type]

    for statement in session.statements:
        for dialect in (postgresql.dialect(), mysql.dialect()):
            assert 'FOR UPDATE' in str(statement.compile(dialect=dialect)).upper()


@pytest.mark.asyncio
async def test_two_sessions_cannot_both_transition_the_same_active_key(
    concurrent_database: async_sessionmaker[AsyncSession],
) -> None:
    async with concurrent_database() as seed:
        await AiDeveloperApiDao.create_application(seed, _application())
        await AiDeveloperApiDao.create_key(seed, _key())
        await seed.commit()

    async with concurrent_database() as first, concurrent_database() as stale:
        assert (
            await AiDeveloperApiDao.transition_key_status(
                first,
                APPLICATION_ID,
                KEY_ID,
                expected_statuses={'active'},
                values={'status': 'retiring', 'retire_at': NOW},
            )
            == 1
        )
        await first.commit()
        assert (
            await AiDeveloperApiDao.transition_key_status(
                stale,
                APPLICATION_ID,
                KEY_ID,
                expected_statuses={'active'},
                values={'status': 'revoked'},
            )
            == 0
        )
        await stale.commit()
