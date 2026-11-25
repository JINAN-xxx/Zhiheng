"""Resource pagination keeps its scan position when a candidate loses read access."""

from typing import TYPE_CHECKING
from uuid import UUID

import pytest

from tests.core.integration.test_resource_files import auth, envelope, post
from tests.core.integration.test_resource_requests import setup_access
from zhiheng.api.common.pagination import decode_cursor
from zhiheng.modules.files.orm import FileRecord
from zhiheng.shared.errors import AppError

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession

    from zhiheng.shared.principal import Principal


@pytest.mark.parametrize(
    ('file_count', 'limit', 'revoked_indexes', 'expected_pages', 'first_scan'),
    [
        (5, 2, (1, 3), ((4, 2), (0,)), (4, 3, 2, 1, 0)),
        (10, 1, tuple(range(2, 10)), ((), (1,), (0,)), tuple(range(9, 1, -1))),
        (8, 1, tuple(range(8)), ((),), tuple(range(7, -1, -1))),
    ],
)
async def test_cursor_advances_when_candidates_are_quarantined_after_selection(
    admin: 'httpx.AsyncClient',
    app: 'FastAPI',
    monkeypatch: pytest.MonkeyPatch,
    file_count: int,
    limit: int,
    revoked_indexes: tuple[int, ...],
    expected_pages: tuple[tuple[int, ...], ...],
    first_scan: tuple[int, ...],
) -> None:
    container, application, key, _caller = await setup_access(admin, app)
    ids: list[UUID] = []
    for _ in range(file_count):
        result = await post(admin, key)
        assert result.status_code == 201, result.text
        ids.append(UUID(result.json()['id']))
    quarantine = {ids[index] for index in revoked_indexes}
    inspected: list[UUID] = []
    ensure_readable = container.files.ensure_readable

    async def freshness_check(session: 'AsyncSession', principal: 'Principal', file_id: UUID) -> None:
        inspected.append(file_id)
        if file_id in quarantine:
            quarantine.remove(file_id)
            async with container.database.sessions.begin() as concurrent:
                record = await concurrent.get(FileRecord, file_id)
                assert record is not None
                record.storage_status = 'quarantined'
        await ensure_readable(session, principal, file_id)

    monkeypatch.setattr(container.files, 'ensure_readable', freshness_check)
    endpoint = '/api/v1/files'
    headers = auth(key)
    position: str | None = None
    for index, expected in enumerate(expected_pages):
        params: dict[str, str | int] = {'limit': limit, 'purpose': 'knowledge'}
        if position is not None:
            params['cursor'] = position
        response = await admin.get(endpoint, params=params, headers=headers)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert [UUID(item['id']) for item in payload['items']] == [ids[i] for i in expected]
        position = payload['next_cursor']
        assert payload['has_more'] is (index < len(expected_pages) - 1)
        assert (position is not None) is payload['has_more']
        if index == 0:
            assert inspected == [ids[i] for i in first_scan]
        if index == 0 and position is not None:
            # A skipped file is the continuation anchor, but no plaintext position is exposed.
            with pytest.raises(AppError, match='分页游标无效'):
                decode_cursor(position, f'{application["id"]}:file:purpose=knowledge')
            envelope(
                await admin.get(endpoint, params={**params, 'cursor': position, 'purpose': 'skill'}, headers=headers),
                422,
            )
