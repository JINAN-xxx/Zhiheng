from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from plugins.ai.infrastructure.knowledge_runtime_provider import create_knowledge_runtime, owner_can_query_knowledge


@asynccontextmanager
async def session_factory() -> AsyncIterator[SimpleNamespace]:
    yield SimpleNamespace(in_transaction=lambda: False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'active,roles,permissions,expected',
    [
        (True, [1], [], True),
        (True, [2], ['ai:knowledge:query'], True),
        (True, [2], ['ai:knowledge:list'], False),
        (True, [2], [], False),
        (False, [1], ['*:*:*'], False),
    ],
)
async def test_owner_permission_is_queried_from_current_user_state(
    active: bool,
    roles: list[int],
    permissions: list[str],
    expected: bool,
) -> None:
    profile = {
        'user_basic_info': SimpleNamespace(user_id=7, status='0', del_flag='0') if active else None,
        'user_role_info': [SimpleNamespace(role_id=role) for role in roles],
        'user_menu_info': [SimpleNamespace(perms=permission) for permission in permissions],
    }
    dao = SimpleNamespace(get_user_by_id=AsyncMock(return_value=profile))
    assert await owner_can_query_knowledge(7, session_factory=session_factory, user_dao=dao) is expected
    dao.get_user_by_id.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_factory_keeps_model_service_sessions_independent(tmp_path: Path) -> None:
    captured = {}
    closed = []

    @asynccontextmanager
    async def sessions() -> AsyncIterator[SimpleNamespace]:
        try:
            yield SimpleNamespace(in_transaction=lambda: False)
        finally:
            closed.append(True)

    class Runtime:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    result = create_knowledge_runtime(FastAPI(), data_dir=tmp_path, runtime_factory=Runtime, session_factory=sessions)
    assert isinstance(result, Runtime)
    assert captured['data_dir'] == tmp_path
    assert closed == []
    async with captured['model_service_factory']() as service:
        assert service is not None
    assert closed == [True]
