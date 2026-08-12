import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from common.vo import PageModel
from plugins.ai.dao.ai_model_catalog_dao import AiModelCatalogDao
from plugins.ai.dao.ai_resource_binding_dao import AiResourceBindingDao
from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
from plugins.ai.entity.do.ai_resource_binding_do import AiResourceBinding
from plugins.ai.entity.vo.ai_model_catalog_vo import ModelCatalogPageQueryModel

EXPECTED_FILTERED_MODELS = 2
EXPECTED_CONCURRENT_STATEMENTS = 2
UPDATED_BINDING_VERSION = 2


class CommitForbiddenAsyncSession(AsyncSession):
    """DAO tests fail immediately if a DAO takes ownership of the transaction."""

    async def commit(self) -> None:
        raise AssertionError('DAO must not commit transactions')


@pytest_asyncio.fixture
async def database() -> AsyncIterator[tuple[AsyncEngine, CommitForbiddenAsyncSession]]:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    session_maker = async_sessionmaker(
        bind=engine,
        class_=CommitForbiddenAsyncSession,
        expire_on_commit=False,
    )
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: AiModelCatalog.__table__.create(sync_connection, checkfirst=True)
        )
        await connection.run_sync(
            lambda sync_connection: AiResourceBinding.__table__.create(sync_connection, checkfirst=True)
        )
    async with session_maker() as session:
        yield engine, session
        await session.rollback()
    await engine.dispose()


@pytest_asyncio.fixture
async def concurrent_database(
    tmp_path: Path,
) -> AsyncIterator[tuple[AsyncEngine, async_sessionmaker[AsyncSession]]]:
    database_path = tmp_path / 'binding-race.db'
    engine = create_async_engine(f'sqlite+aiosqlite:///{database_path}', connect_args={'timeout': 5})

    @event.listens_for(engine.sync_engine, 'connect')
    def configure_sqlite(connection: Any, _record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.execute('PRAGMA busy_timeout=5000')
        cursor.close()

    session_maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: AiModelCatalog.__table__.create(sync_connection, checkfirst=True)
        )
        await connection.run_sync(
            lambda sync_connection: AiResourceBinding.__table__.create(sync_connection, checkfirst=True)
        )
    try:
        yield engine, session_maker
    finally:
        await engine.dispose()


def _catalog(
    model_id: str,
    model_code: str,
    display_name: str,
    *,
    model_type: str = 'chat',
    lifecycle_status: str = 'draft',
    create_time: datetime | None = None,
) -> AiModelCatalog:
    timestamp = create_time or datetime(2026, 8, 12, 10, 0, 0)
    return AiModelCatalog(
        model_id=model_id,
        model_code=model_code,
        display_name=display_name,
        model_type=model_type,
        capabilities={'vision': False, 'streaming': True},
        context_window=128_000,
        max_output_tokens=8_192,
        default_parameters={'temperature': 0.2, 'max_tokens': 2_048},
        lifecycle_status=lifecycle_status,
        create_by='admin',
        create_time=timestamp,
        update_by='admin',
        update_time=timestamp,
    )


def _binding(
    binding_id: str,
    model_id: str,
    *,
    engine_resource_id: str = 'litellm-gpt-4o',
) -> AiResourceBinding:
    timestamp = datetime(2026, 8, 12, 10, 0, 0)
    return AiResourceBinding(
        binding_id=binding_id,
        resource_type='model',
        resource_id=model_id,
        engine_type='litellm',
        engine_resource_id=engine_resource_id,
        binding_status='active',
        engine_metadata={'deployment': 'primary'},
        version=1,
        create_by='admin',
        create_time=timestamp,
        update_by='admin',
        update_time=timestamp,
    )


@pytest.mark.asyncio
async def test_catalog_create_flush_and_read_by_id_or_code_preserves_json(
    database: tuple[AsyncEngine, CommitForbiddenAsyncSession],
) -> None:
    _, session = database
    model = _catalog('mdl_00000000000040008000000000000001', 'gpt-4o', 'GPT-4o')

    created = await AiModelCatalogDao.create_model(session, model)
    session.expunge_all()
    by_id = await AiModelCatalogDao.get_model_by_id(session, model.model_id)
    by_code = await AiModelCatalogDao.get_model_by_code(session, model.model_code)

    assert created.model_id == model.model_id
    assert by_id is not None
    assert by_id.model_code == 'gpt-4o'
    assert by_id.capabilities == {'vision': False, 'streaming': True}
    assert isinstance(by_id.capabilities, dict)
    assert by_id.default_parameters == {'temperature': 0.2, 'max_tokens': 2_048}
    assert isinstance(by_id.default_parameters, dict)
    assert by_code is not None
    assert by_code.model_id == model.model_id


@pytest.mark.asyncio
async def test_catalog_queries_return_none_when_model_is_missing(
    database: tuple[AsyncEngine, CommitForbiddenAsyncSession],
) -> None:
    _, session = database

    assert await AiModelCatalogDao.get_model_by_id(session, 'mdl_00000000000040008000000000000001') is None
    assert await AiModelCatalogDao.get_model_by_code(session, 'missing') is None


@pytest.mark.asyncio
async def test_catalog_paginated_filtering_returns_project_page_model_with_stable_order(
    database: tuple[AsyncEngine, CommitForbiddenAsyncSession],
) -> None:
    _, session = database
    models = [
        _catalog('mdl_00000000000040008000000000000003', 'chat-zeta', 'Chat Zeta'),
        _catalog('mdl_00000000000040008000000000000001', 'chat-alpha', 'Chat Alpha'),
        _catalog(
            'mdl_00000000000040008000000000000002',
            'embed-alpha',
            'Embed Alpha',
            model_type='embedding',
            lifecycle_status='active',
        ),
    ]
    for model in models:
        await AiModelCatalogDao.create_model(session, model)

    result = await AiModelCatalogDao.list_models(
        session,
        ModelCatalogPageQueryModel(
            modelCode='chat',
            displayName='Chat',
            modelType='chat',
            lifecycleStatus='draft',
            pageNum=1,
            pageSize=1,
        ),
        is_page=True,
    )

    assert isinstance(result, PageModel)
    assert result.page_num == 1
    assert result.page_size == 1
    assert result.total == EXPECTED_FILTERED_MODELS
    assert result.has_next is True
    assert len(result.rows) == 1
    assert result.rows[0]['modelId'] == 'mdl_00000000000040008000000000000001'
    assert result.rows[0]['modelCode'] == 'chat-alpha'
    assert result.rows[0]['capabilities'] == {'vision': False, 'streaming': True}
    assert result.rows[0]['defaultParameters'] == {'temperature': 0.2, 'max_tokens': 2_048}


@pytest.mark.asyncio
async def test_catalog_non_paginated_query_returns_usable_camel_case_rows(
    database: tuple[AsyncEngine, CommitForbiddenAsyncSession],
) -> None:
    _, session = database
    await AiModelCatalogDao.create_model(
        session,
        _catalog(
            'mdl_00000000000040008000000000000001',
            'embed-small',
            'Embed Small',
            model_type='embedding',
            lifecycle_status='active',
        ),
    )
    await AiModelCatalogDao.create_model(
        session,
        _catalog('mdl_00000000000040008000000000000002', 'chat-small', 'Chat Small'),
    )

    result = await AiModelCatalogDao.list_models(
        session,
        ModelCatalogPageQueryModel(modelType='embedding', lifecycleStatus='active'),
        is_page=False,
    )

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]['modelId'] == 'mdl_00000000000040008000000000000001'
    assert result[0]['modelType'] == 'embedding'
    assert isinstance(result[0]['capabilities'], dict)
    assert isinstance(result[0]['defaultParameters'], dict)


@pytest.mark.asyncio
async def test_catalog_update_uses_explicit_values_and_delete_is_only_a_database_action(
    database: tuple[AsyncEngine, CommitForbiddenAsyncSession],
) -> None:
    _, session = database
    model_id = 'mdl_00000000000040008000000000000001'
    await AiModelCatalogDao.create_model(session, _catalog(model_id, 'draft-model', 'Draft Model'))

    updated = await AiModelCatalogDao.update_model(
        session,
        model_id,
        {
            'display_name': 'Updated Draft',
            'capabilities': {'reasoning': True, 'tools': True},
            'default_parameters': {'temperature': 0.7},
            'update_by': 'operator',
        },
    )
    session.expunge_all()
    stored = await AiModelCatalogDao.get_model_by_id(session, model_id)

    assert updated == 1
    assert stored is not None
    assert stored.model_code == 'draft-model'
    assert stored.display_name == 'Updated Draft'
    assert stored.capabilities == {'reasoning': True, 'tools': True}
    assert stored.default_parameters == {'temperature': 0.7}
    assert stored.update_by == 'operator'

    deleted = await AiModelCatalogDao.delete_model(session, model_id)

    assert deleted == 1
    assert await AiModelCatalogDao.get_model_by_id(session, model_id) is None


@pytest.mark.asyncio
async def test_catalog_update_rejects_identity_changes_and_treats_empty_values_as_noop(
    database: tuple[AsyncEngine, CommitForbiddenAsyncSession],
) -> None:
    _, session = database
    model_id = 'mdl_00000000000040008000000000000001'
    await AiModelCatalogDao.create_model(session, _catalog(model_id, 'draft-model', 'Draft Model'))

    assert await AiModelCatalogDao.update_model(session, model_id, {}) == 0
    with pytest.raises(ValueError, match='model_id'):
        await AiModelCatalogDao.update_model(
            session,
            model_id,
            {'model_id': 'mdl_00000000000040008000000000000002'},
        )


@pytest.mark.asyncio
async def test_binding_create_and_current_lookup_use_platform_model_resource_key(
    database: tuple[AsyncEngine, CommitForbiddenAsyncSession],
) -> None:
    _, session = database
    model_id = 'mdl_00000000000040008000000000000001'
    binding_id = 'bnd_00000000000040008000000000000002'
    await AiModelCatalogDao.create_model(session, _catalog(model_id, 'gpt-4o', 'GPT-4o'))

    created = await AiResourceBindingDao.create_binding(session, _binding(binding_id, model_id))
    session.expunge_all()
    by_id = await AiResourceBindingDao.get_binding_by_id(session, binding_id)
    current = await AiResourceBindingDao.get_current_binding(session, model_id)
    not_primary_key_lookup = await AiResourceBindingDao.get_current_binding(session, binding_id)

    assert created.binding_id == binding_id
    assert by_id is not None
    assert by_id.resource_type == 'model'
    assert current is not None
    assert current.binding_id == binding_id
    assert current.resource_id == model_id
    assert current.engine_metadata == {'deployment': 'primary'}
    assert isinstance(current.engine_metadata, dict)
    assert not_primary_key_lookup is None


@pytest.mark.asyncio
async def test_binding_version_update_rejects_identity_changes(
    database: tuple[AsyncEngine, CommitForbiddenAsyncSession],
) -> None:
    _, session = database
    model_id = 'mdl_00000000000040008000000000000001'
    binding_id = 'bnd_00000000000040008000000000000002'
    await AiModelCatalogDao.create_model(session, _catalog(model_id, 'gpt-4o', 'GPT-4o'))
    await AiResourceBindingDao.create_binding(session, _binding(binding_id, model_id))

    with pytest.raises(ValueError, match='binding_id'):
        await AiResourceBindingDao.update_binding_with_version(
            session,
            binding_id,
            expected_version=1,
            values={'binding_id': 'bnd_00000000000040008000000000000003'},
        )


@pytest.mark.asyncio
async def test_binding_optimistic_lock_is_one_conditional_update_and_only_one_concurrent_writer_wins(
    concurrent_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    engine, session_maker = concurrent_database
    model_id = 'mdl_00000000000040008000000000000001'
    binding_id = 'bnd_00000000000040008000000000000002'
    async with session_maker() as seed_session:
        await AiModelCatalogDao.create_model(seed_session, _catalog(model_id, 'gpt-4o', 'GPT-4o'))
        await AiResourceBindingDao.create_binding(seed_session, _binding(binding_id, model_id))
        await seed_session.commit()

    statements: list[str] = []

    def record_statement(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        statements.append(statement.strip().lower())

    event.listen(engine.sync_engine, 'before_cursor_execute', record_statement)

    async def write(engine_resource_id: str, username: str) -> int:
        async with session_maker() as writer_session:
            rowcount = await AiResourceBindingDao.update_binding_with_version(
                writer_session,
                binding_id,
                expected_version=1,
                values={'engine_resource_id': engine_resource_id, 'update_by': username},
            )
            await writer_session.commit()
            return rowcount

    try:
        rowcounts = await asyncio.gather(
            write('litellm-gpt-4.1', 'writer-one'),
            write('litellm-gpt-5', 'writer-two'),
        )
    finally:
        event.remove(engine.sync_engine, 'before_cursor_execute', record_statement)

    assert sorted(rowcounts) == [0, 1]
    assert len(statements) == EXPECTED_CONCURRENT_STATEMENTS
    assert all(statement.startswith('update ai_resource_binding') for statement in statements)
    for statement in statements:
        set_clause, where_clause = statement.split(' where ', maxsplit=1)
        assert 'version=?' in set_clause.replace(' ', '')
        assert 'binding_id = ?' in where_clause
        assert 'version = ?' in where_clause

    async with session_maker() as verification_session:
        stored = await AiResourceBindingDao.get_binding_by_id(verification_session, binding_id)
        assert stored is not None
        assert stored.version == UPDATED_BINDING_VERSION
        assert stored.engine_resource_id in {'litellm-gpt-4.1', 'litellm-gpt-5'}
        expected_writer = 'writer-one' if stored.engine_resource_id == 'litellm-gpt-4.1' else 'writer-two'
        assert stored.update_by == expected_writer
