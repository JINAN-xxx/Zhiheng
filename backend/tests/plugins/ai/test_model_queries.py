import ast
from collections.abc import AsyncIterator
from dataclasses import FrozenInstanceError, asdict
from datetime import datetime
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
from plugins.ai.entity.do.ai_resource_binding_do import AiResourceBinding
from plugins.ai.public.model_queries import (
    get_published_chat_model,
    get_published_chat_model_for_update,
    list_active_bound_models,
    lock_model_reference,
)


@pytest_asyncio.fixture
async def catalog_db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    maker = async_sessionmaker(engine, expire_on_commit=True, autoflush=False)
    async with engine.begin() as connection:
        await connection.run_sync(AiModelCatalog.__table__.create)
        await connection.run_sync(AiResourceBinding.__table__.create)
    try:
        yield maker
    finally:
        await engine.dispose()


def model_record(
    model_id: str,
    *,
    model_type: str = 'chat',
    status: str = 'active',
    published: bool = True,
    name: str = 'Example model',
    max_tokens: int | None = 8192,
) -> AiModelCatalog:
    return AiModelCatalog(
        model_id=model_id,
        model_code=f'code-{model_id}',
        display_name=name,
        model_type=model_type,
        lifecycle_status=status,
        published_at=datetime(2026, 9, 1) if published else None,
        max_output_tokens=max_tokens,
    )


def binding_record(model_id: str, status: str = 'active') -> AiResourceBinding:
    return AiResourceBinding(
        binding_id=f'binding-{model_id}',
        resource_type='model',
        resource_id=model_id,
        engine_type='litellm',
        engine_resource_id=f'engine-{model_id}',
        binding_status=status,
        engine_metadata={'private_configuration': 'must-not-cross-the-query-boundary'},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('model_type', 'status', 'published', 'available'),
    [
        ('chat', 'active', True, True),
        ('chat', 'active', False, False),
        ('chat', 'draft', False, False),
        ('chat', 'disabled', True, False),
        ('embedding', 'active', True, False),
        ('rerank', 'active', True, False),
    ],
)
async def test_chat_query_preserves_publication_and_type_rules(
    catalog_db: async_sessionmaker[AsyncSession], model_type: str, status: str, published: bool, available: bool
) -> None:
    async with catalog_db() as db:
        db.add(model_record('candidate', model_type=model_type, status=status, published=published))
        await db.commit()
        result = await get_published_chat_model(db, 'candidate')
        assert (result is not None) is available
        if result is not None:
            assert asdict(result) == {
                'model_id': 'candidate',
                'model_code': 'code-candidate',
                'max_output_tokens': 8192,
            }
        assert await get_published_chat_model(db, 'missing') is None


@pytest.mark.asyncio
async def test_chat_snapshot_is_immutable_and_survives_session_close(
    catalog_db: async_sessionmaker[AsyncSession],
) -> None:
    async with catalog_db() as db:
        db.add(model_record('unbounded', max_tokens=None))
        await db.commit()
        snapshot = await get_published_chat_model(db, 'unbounded')
        await db.rollback()
    assert snapshot is not None
    assert snapshot.model_code == 'code-unbounded'
    assert snapshot.max_output_tokens is None
    with pytest.raises(FrozenInstanceError):
        snapshot.model_code = 'replacement'


@pytest.mark.asyncio
async def test_bound_options_keep_existing_knowledge_filter_and_safe_projection(
    catalog_db: async_sessionmaker[AsyncSession],
) -> None:
    async with catalog_db() as db:
        db.add_all(
            [
                model_record('embedding-b', model_type='embedding', name='B'),
                # Knowledge options historically require active model/binding, without a publication timestamp check.
                model_record('embedding-a', model_type='embedding', name='A', published=False),
                model_record('disabled', model_type='embedding', status='disabled'),
                model_record('draft', model_type='embedding', status='draft', published=False),
                model_record('binding-disabled', model_type='embedding'),
                model_record('unbound', model_type='embedding'),
                model_record('chat'),
                model_record('rerank', model_type='rerank'),
            ]
        )
        await db.flush()
        db.add_all(
            [binding_record(key) for key in ['embedding-a', 'embedding-b', 'disabled', 'draft', 'chat', 'rerank']]
        )
        db.add(binding_record('binding-disabled', status='disabled'))
        await db.commit()
        embeddings = await list_active_bound_models(db, model_type='embedding')
        chats = await list_active_bound_models(db, model_type='chat')
    assert [asdict(item) for item in embeddings] == [
        {'model_id': 'embedding-a', 'display_name': 'A', 'model_code': 'code-embedding-a'},
        {'model_id': 'embedding-b', 'display_name': 'B', 'model_code': 'code-embedding-b'},
    ]
    assert [item.model_id for item in chats] == ['chat']
    with pytest.raises(FrozenInstanceError):
        embeddings[0].display_name = 'replacement'


@pytest.mark.asyncio
@pytest.mark.parametrize('query_kind', ['chat', 'bound'])
@pytest.mark.parametrize('finish', ['commit', 'rollback'])
async def test_queries_leave_pending_writes_and_transaction_ownership_to_caller(
    catalog_db: async_sessionmaker[AsyncSession], query_kind: str, finish: str
) -> None:
    async with catalog_db() as db:
        db.add(model_record('existing'))
        await db.commit()
        db.add(model_record('pending'))
        await db.flush()
        if query_kind == 'chat':
            assert await get_published_chat_model(db, 'existing') is not None
        else:
            assert await list_active_bound_models(db, model_type='chat') == []
        assert db.in_transaction()
        if finish == 'commit':
            await db.commit()
        else:
            await db.rollback()
    async with catalog_db() as db:
        assert (await get_published_chat_model(db, 'pending') is not None) is (finish == 'commit')


def test_consumers_do_not_import_model_persistence_implementation() -> None:
    root = Path(__file__).resolve().parents[3]
    files = list((root / 'plugins/agent_extension').rglob('*.py'))
    files.append(root / 'plugins/ai/controller/ai_knowledge_controller.py')
    forbidden = (
        'plugins.ai.dao.ai_model_catalog_dao',
        'plugins.ai.entity.do.ai_model_catalog_do',
        'plugins.ai.entity.do.ai_resource_binding_do',
    )
    violations = []
    for path in files:
        for node in ast.walk(ast.parse(path.read_text())):
            imports = []
            if isinstance(node, ast.Import):
                imports = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports = [node.module, *(f'{node.module}.{item.name}' for item in node.names)]
            if any(name == prefix or name.startswith(prefix + '.') for name in imports for prefix in forbidden):
                violations.append(f'{path.relative_to(root)}:{node.lineno}')
    assert not violations, '模型持久化实现只能由公开查询入口封装：' + ', '.join(violations)


@pytest.mark.asyncio
async def test_reference_lock_preserves_frozen_disabled_models_and_rejects_missing(
    catalog_db: async_sessionmaker[AsyncSession],
) -> None:
    async with catalog_db() as db:
        db.add(model_record('active'))
        db.add(model_record('disabled', status='disabled'))
        await db.commit()
        assert await lock_model_reference(db, 'disabled')
        assert not await lock_model_reference(db, 'missing')
        assert await get_published_chat_model_for_update(db, 'active') is not None
        assert await get_published_chat_model_for_update(db, 'disabled') is None
        assert db.in_transaction()
        await db.rollback()
