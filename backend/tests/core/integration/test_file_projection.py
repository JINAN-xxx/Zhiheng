from datetime import timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event, select

from zhiheng.modules.files.orm import FileGrant, FileRecord, FileReference
from zhiheng.modules.files.public import FileBinding, FileQuery, resource_page
from zhiheng.modules.identity.orm import IdentityUser
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow


def file_record(owner_id: UUID, index: int) -> FileRecord:
    return FileRecord(
        storage_key=uuid4().hex,
        original_filename=f'{index}.txt',
        display_name=f'{index}.txt',
        size=1,
        mime='text/plain',
        sha256='a' * 64,
        owner_id=owner_id,
        uploader_id=owner_id,
        storage_status='normal',
    )


async def test_console_and_resource_pages_batch_file_projection(admin: httpx.AsyncClient, app: FastAPI) -> None:
    owner_id = UUID((await admin.get('/api/auth/me')).json()['id'])
    container = app.state.container
    owner = Principal(owner_id, 'admin', True)
    files = [file_record(owner_id, index) for index in range(100)]
    selected = files[-1]
    expiry = utcnow() + timedelta(days=2)
    async with container.database.sessions.begin() as session:
        member_id = await session.scalar(select(IdentityUser.id).where(IdentityUser.username == 'member'))
        assert member_id is not None
        session.add_all(files)
        await session.flush()
        session.add_all(
            [
                FileGrant(
                    file_id=selected.id, user_id=owner_id, effect='deny', expires_at=utcnow() - timedelta(days=1)
                ),
                FileGrant(file_id=selected.id, user_id=member_id, effect='allow', expires_at=expiry),
                FileReference(file_id=selected.id, resource_type='test', resource_id=uuid4()),
                FileReference(file_id=selected.id, resource_type='test', resource_id=uuid4()),
            ]
        )
    bindings = tuple(FileBinding(row.id, owner_id, '0') for row in files)
    statements: list[str] = []

    def capture(
        connection: object, cursor: object, statement: str, parameters: object, context: object, executemany: bool
    ) -> None:
        statements.append(statement)

    engine = container.database.engine.sync_engine
    event.listen(engine, 'before_cursor_execute', capture)
    try:
        async with container.database.sessions() as session:
            for size in (1, 100):
                statements.clear()
                page = await container.files.queries.list_files(session, owner, FileQuery(page_size=size))
                assert len(page.items) == size and page.total == 100
                assert len(statements) == 4
                statements.clear()
                delegated = await resource_page(
                    container.files, session, owner, bindings, purpose=None, after=None, limit=size
                )
                assert len(delegated) == size
                assert len(statements) == 3
            assert {row.id: row for row in page.items} == {row.id: row for row in delegated}
            summary = next(row for row in page.items if row.id == selected.id)
            assert summary.reference_count == 2 and summary.acl_entry_count == 2
            assert summary.acl_nearest_expire_at == expiry
            empty_acl = next(row for row in page.items if row.id != selected.id)
            assert empty_acl.reference_count == empty_acl.acl_entry_count == 0
            assert empty_acl.acl_nearest_expire_at is None
            member = Principal(member_id, 'member', False)
            visible = await container.files.queries.list_files(session, member, FileQuery(owner_id=owner_id))
            assert visible.total == 1 and visible.items == [summary]
    finally:
        event.remove(engine, 'before_cursor_execute', capture)


@pytest.mark.parametrize('change', ['grant', 'file'])
async def test_file_recheck_refreshes_retained_orm_records(admin: httpx.AsyncClient, app: FastAPI, change: str) -> None:
    owner_id = UUID((await admin.get('/api/auth/me')).json()['id'])
    container = app.state.container
    stored = file_record(owner_id, 0)
    async with container.database.sessions.begin() as session:
        member_id = await session.scalar(select(IdentityUser.id).where(IdentityUser.username == 'member'))
        assert member_id is not None
        session.add(stored)
        await session.flush()
        session.add(FileGrant(file_id=stored.id, user_id=member_id, effect='allow'))
    member = Principal(member_id, 'member', False)
    async with container.database.sessions() as cached:
        stale_file = await cached.get(FileRecord, stored.id)
        stale_grant = await cached.get(FileGrant, (stored.id, member.id))
        assert stale_file is not None and stale_grant is not None
        await container.files.ensure_readable(cached, member, stored.id)
        async with container.database.sessions.begin() as writer:
            if change == 'grant':
                grant = await writer.get(FileGrant, (stored.id, member.id))
                assert grant is not None
                grant.effect = 'deny'
            else:
                file = await writer.get(FileRecord, stored.id)
                assert file is not None
                file.status = 'deleted'
        with pytest.raises(AppError) as rejected:
            await container.files.ensure_readable(cached, member, stored.id)
        assert rejected.value.status_code == 404
        if change == 'grant':
            assert stale_grant.effect == 'deny'
        else:
            assert stale_file.status == 'deleted'
