import ast
import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from plugins.ai.entity.do.ai_developer_api_do import AiApplication
from plugins.ai.public.application_queries import is_active_owned_application

OWNER_ID = 10
OTHER_OWNER_ID = 11


def application(application_id: str, **changes: object) -> AiApplication:
    return AiApplication(
        application_id=application_id,
        application_code=application_id,
        application_name=application_id,
        **{'owner_user_id': OWNER_ID, 'status': 'active', **changes},
    )


@pytest_asyncio.fixture
async def application_db() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    factory = async_sessionmaker(engine, expire_on_commit=True, autoflush=False)
    async with engine.begin() as conn:
        await conn.run_sync(AiApplication.__table__.create)
    try:
        yield factory
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['active', 'disabled', 'deleted', 'other-owner', 'missing'])
async def test_application_eligibility_preserves_owner_status_and_soft_delete_rules(
    application_db: async_sessionmaker[AsyncSession], mode: str
) -> None:
    changes = {
        'disabled': {'status': 'disabled'},
        'deleted': {'deleted_at': datetime(2026, 9, 29)},
        'other-owner': {'owner_user_id': OTHER_OWNER_ID},
    }.get(mode, {})
    async with application_db() as db:
        db.add(application('candidate', **changes))
        await db.commit()
        result = await is_active_owned_application(db, 'missing' if mode == 'missing' else 'candidate', OWNER_ID)
    assert type(result) is bool
    assert result is (mode == 'active')


@pytest.mark.asyncio
@pytest.mark.parametrize('finish', ['commit', 'rollback'])
@pytest.mark.parametrize('owner_id', [OWNER_ID, OTHER_OWNER_ID])
async def test_query_preserves_callers_pending_writes_and_transaction(
    application_db: async_sessionmaker[AsyncSession], finish: str, owner_id: int
) -> None:
    async with application_db() as db:
        db.add(application('existing'))
        await db.commit()
        db.add(application('pending'))
        await db.flush()
        assert await is_active_owned_application(db, 'existing', owner_id) is (owner_id == OWNER_ID)
        assert db.in_transaction()
        if finish == 'commit':
            await db.commit()
        else:
            await db.rollback()
    async with application_db() as db:
        assert await is_active_owned_application(db, 'pending', OWNER_ID) is (finish == 'commit')


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [RuntimeError('database unavailable'), asyncio.CancelledError()])
async def test_query_preserves_errors_and_cancellation_without_rolling_back(
    application_db: async_sessionmaker[AsyncSession], monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    async with application_db() as db:
        db.add(application('pending'))
        await db.flush()
        with monkeypatch.context() as patch:
            patch.setattr(db, 'execute', AsyncMock(side_effect=error))
            with pytest.raises(type(error)) as caught:
                await is_active_owned_application(db, 'pending', OWNER_ID)
        assert caught.value is error
        assert db.in_transaction()
        await db.rollback()
    async with application_db() as db:
        assert not await is_active_owned_application(db, 'pending', OWNER_ID)


def test_agent_does_not_import_application_persistence_implementation() -> None:
    root = Path(__file__).resolve().parents[3]
    forbidden = ('plugins.ai.dao.ai_developer_api_dao', 'plugins.ai.entity.do.ai_developer_api_do')
    violations = []
    for path in (root / 'plugins/agent_extension').rglob('*.py'):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module, *(f'{node.module}.{item.name}' for item in node.names)]
            if any(name == prefix or name.startswith(prefix + '.') for name in names for prefix in forbidden):
                violations.append(f'{path.relative_to(root)}:{node.lineno}')
    assert not violations, 'Agent must consume the public application query: ' + ', '.join(violations)
