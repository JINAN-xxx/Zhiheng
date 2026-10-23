from collections.abc import AsyncIterator
from dataclasses import fields
from datetime import datetime
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import event, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from plugins.ai.dao.ai_developer_api_dao import AiDeveloperApiDao, DeveloperKeyAuthRecord, PublicModelRecord
from plugins.ai.entity.do.ai_developer_api_do import AiApiKey, AiApiKeyModelGrant, AiApplication
from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog

NOW = datetime(2026, 8, 18, 12, 0)
APPLICATION_ID = 'app_00000000000040008000000000000001'
KEY_ID = 'key_00000000000040008000000000000001'
SECOND_KEY_ID = 'key_00000000000040008000000000000002'
MODEL_ID = 'mdl_00000000000040008000000000000001'
SECOND_MODEL_ID = 'mdl_00000000000040008000000000000002'
DIGEST = 'a' * 64


@pytest_asyncio.fixture
async def database() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')

    @event.listens_for(engine.sync_engine, 'connect')
    def configure_sqlite(connection: Any, _record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()

    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(AiModelCatalog.__table__.create)
        await connection.run_sync(AiApplication.__table__.create)
        await connection.run_sync(AiApiKey.__table__.create)
        await connection.run_sync(AiApiKeyModelGrant.__table__.create)
    async with session_maker() as session:
        session.add(_application())
        session.add_all((_model(), _model(SECOND_MODEL_ID, code='other-chat')))
        await session.commit()
        session.add_all((_key(), _key(SECOND_KEY_ID)))
        await session.commit()
        session.add_all(
            (
                _grant('grt_00000000000040008000000000000001', KEY_ID, MODEL_ID),
                _grant('grt_00000000000040008000000000000002', SECOND_KEY_ID, SECOND_MODEL_ID),
            )
        )
        await session.commit()
        yield session
        await session.rollback()
    await engine.dispose()


def _application() -> AiApplication:
    return AiApplication(
        application_id=APPLICATION_ID,
        application_code='billing-prod',
        application_name='Billing',
        owner_user_id=7,
        status='active',
        version=1,
        created_by='alice',
        created_at=NOW,
        updated_by='alice',
        updated_at=NOW,
    )


def _key(key_id: str = KEY_ID) -> AiApiKey:
    return AiApiKey(
        api_key_id=key_id,
        application_id=APPLICATION_ID,
        key_name=key_id,
        key_prefix=f'sk-zh_{key_id}',
        secret_digest=DIGEST,
        digest_version='v1',
        fingerprint='ABCD',
        status='active',
        created_by='alice',
        created_at=NOW,
        updated_by='alice',
        updated_at=NOW,
    )


def _model(model_id: str = MODEL_ID, *, code: str = 'deepseek-chat') -> AiModelCatalog:
    return AiModelCatalog(
        model_id=model_id,
        model_code=code,
        display_name=code,
        model_type='chat',
        capabilities={},
        default_parameters={},
        lifecycle_status='active',
        published_at=NOW,
        create_by='alice',
        create_time=NOW,
        update_by='alice',
        update_time=NOW,
    )


def _grant(grant_id: str, key_id: str, model_id: str) -> AiApiKeyModelGrant:
    return AiApiKeyModelGrant(
        grant_id=grant_id,
        api_key_id=key_id,
        platform_model_id=model_id,
        status='active',
        created_by='alice',
        created_at=NOW,
        updated_by='alice',
        updated_at=NOW,
    )


@pytest.mark.asyncio
async def test_auth_lookup_returns_frozen_snapshot_without_orm_or_digest_repr(database: AsyncSession) -> None:
    record = await AiDeveloperApiDao.get_key_auth_record(database, f'sk-zh_{KEY_ID}')

    assert isinstance(record, DeveloperKeyAuthRecord)
    assert record.key_id == KEY_ID
    assert record.application_id == APPLICATION_ID
    assert record.secret_digest == DIGEST
    assert record.key_status == 'active'
    assert record.application_status == 'active'
    assert DIGEST not in repr(record)
    with pytest.raises(AttributeError):
        record.key_status = 'revoked'


@pytest.mark.asyncio
async def test_public_models_are_key_scoped_and_contain_only_public_snapshot(database: AsyncSession) -> None:
    first = await AiDeveloperApiDao.list_public_models_for_key(database, KEY_ID)
    second = await AiDeveloperApiDao.list_public_models_for_key(database, SECOND_KEY_ID)

    assert first == [
        PublicModelRecord(
            platform_model_id=MODEL_ID,
            model_code='deepseek-chat',
            published_at=NOW,
            model_type='chat',
        )
    ]
    assert second == [
        PublicModelRecord(
            platform_model_id=SECOND_MODEL_ID,
            model_code='other-chat',
            published_at=NOW,
            model_type='chat',
        )
    ]
    field_names = {item.name for item in fields(first[0])}
    assert 'binding' not in field_names
    assert 'provider' not in field_names


@pytest.mark.asyncio
async def test_public_model_resolution_cannot_cross_key_grants(database: AsyncSession) -> None:
    allowed = await AiDeveloperApiDao.get_public_model_for_key(database, KEY_ID, 'deepseek-chat')
    denied = await AiDeveloperApiDao.get_public_model_for_key(database, KEY_ID, 'other-chat')

    assert allowed is not None and allowed.platform_model_id == MODEL_ID
    assert denied is None


@pytest.mark.asyncio
async def test_public_queries_observe_fresh_key_application_grant_and_model_state(database: AsyncSession) -> None:
    await database.execute(update(AiApiKey).where(AiApiKey.api_key_id == KEY_ID).values(status='revoked'))
    await database.execute(
        update(AiApiKeyModelGrant).where(AiApiKeyModelGrant.api_key_id == KEY_ID).values(status='disabled')
    )
    await database.execute(
        update(AiModelCatalog).where(AiModelCatalog.model_id == MODEL_ID).values(lifecycle_status='disabled')
    )
    await database.execute(
        update(AiApplication).where(AiApplication.application_id == APPLICATION_ID).values(status='disabled')
    )
    await database.commit()

    auth = await AiDeveloperApiDao.get_key_auth_record(database, f'sk-zh_{KEY_ID}')
    models = await AiDeveloperApiDao.list_public_models_for_key(database, KEY_ID)

    assert auth is not None and auth.key_status == 'revoked' and auth.application_status == 'disabled'
    assert models == []
