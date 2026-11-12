from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from zhiheng.infrastructure.file_storage import LocalFileStorage
from zhiheng.modules.files.orm import FileReconcileIssue
from zhiheng.modules.files.service import FileService
from zhiheng.modules.identity.public import IdentityService
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


@pytest.fixture
def issue_session() -> Iterator[Session]:
    engine = create_engine('sqlite:///:memory:')
    try:
        FileReconcileIssue.__table__.create(engine)
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ('statuses', 'expected'),
    [
        ([], {'open': 0, 'resolved': 0, 'ignored': 0, 'quarantined': 0}),
        (['resolved'], {'open': 0, 'resolved': 1, 'ignored': 0, 'quarantined': 0}),
        (
            ['open', 'open', 'resolved', 'ignored', 'quarantined'],
            {'open': 2, 'resolved': 1, 'ignored': 1, 'quarantined': 1},
        ),
    ],
)
async def test_issue_stats_counts_real_grouped_results(
    issue_session: Session, tmp_path: Path, statuses: list[str], expected: dict[str, int]
) -> None:
    for status in statuses:
        issue_session.add(
            FileReconcileIssue(
                job_id=uuid4(),
                identity_key=uuid4().hex,
                storage_key=uuid4().hex,
                kind='missing',
                status=status,
            )
        )
    issue_session.flush()
    service = FileService(LocalFileStorage(tmp_path), IdentityService())
    admin = Principal(uuid4(), 'admin', True)
    # Keep the actual SQLAlchemy grouped Result; only adapt the SQLite call to async.
    async_session = AsyncMock(spec=AsyncSession)
    async_session.execute.side_effect = issue_session.execute

    assert await service.issue_stats(async_session, admin) == expected


async def test_issue_stats_rejects_non_admin(tmp_path: Path) -> None:
    service = FileService(LocalFileStorage(tmp_path), IdentityService())
    member = Principal(uuid4(), 'member', False)
    session = AsyncMock(spec=AsyncSession)

    with pytest.raises(AppError) as failure:
        await service.issue_stats(session, member)
    assert failure.value.status_code == 403
    session.execute.assert_not_awaited()
