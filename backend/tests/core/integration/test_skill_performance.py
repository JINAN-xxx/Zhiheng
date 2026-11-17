from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from uuid import UUID

import httpx
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from sqlalchemy import event, text
from sqlalchemy.engine import Connection

from tests.core.integration.test_skills import install, prepare
from tests.core.unit.test_skill_package import markdown
from zhiheng.modules.skills.orm import SkillDraft, SkillRevision


async def test_confirmed_payload_is_released_with_exact_receipt_and_revision_retained(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    draft = await prepare(admin, 'receipt-payload')
    path = f'/api/skills/import/{draft["draft_id"]}/confirm'
    first = await admin.post(path, json={})
    assert first.status_code == 201, first.text
    async with app.state.container.database.sessions() as session:
        stored = await session.get(SkillDraft, UUID(draft['draft_id']))
        revision = await session.get(SkillRevision, UUID(first.json()['revision_id']))
        assert stored.package_bytes == b''
        assert stored.package_sha == revision.package_sha
        assert stored.confirmed_info == first.json()
        assert revision.package_bytes == markdown('receipt-payload')
    assert (await admin.post(path, json={})).json() == first.json()
    # Semantically unchanged uploads retain the first immutable revision and receipt.
    repeated = await prepare(admin, 'receipt-payload')
    assert repeated['action'] == 'unchanged'
    second = await admin.post(f'/api/skills/import/{repeated["draft_id"]}/confirm', json={})
    assert second.json() == first.json()
    async with app.state.container.database.sessions() as session:
        stored = await session.get(SkillDraft, UUID(repeated['draft_id']))
        assert stored.package_bytes == b'' and stored.confirmed_info == first.json()


async def test_resource_metadata_pages_have_constant_query_count_and_no_binary_payload(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    skills = [await install(admin, 'perf-' + suffix) for suffix in ['one', 'two', 'three']]
    owner = UUID((await admin.get('/api/auth/me')).json()['id'])
    container = app.state.container
    statements = []

    def capture(
        connection: object, cursor: object, statement: str, parameters: object, context: object, executemany: bool
    ) -> None:
        statements.append(statement)

    event.listen(container.database.engine.sync_engine, 'before_cursor_execute', capture)
    try:
        async with container.database.sessions.begin() as session:
            await session.execute(text('SET TRANSACTION READ ONLY'))
            statements.clear()
            all_rows = await container.skills.delegable_metadata(session, owner)
            assert len(all_rows) >= 3
            assert len(statements) == 1 and 'package_bytes' not in statements[0]
            statements.clear()
            rows = await container.skills.resource_items(
                session, owner, [UUID(row['id']) for row in skills], after=None, limit=2
            )
            assert len(rows) == 2 and len(statements) == 1 and 'package_bytes' not in statements[0]
            assert {row.id for row in rows} <= {UUID(row['id']) for row in skills}
            statements.clear()
            next_page = await container.skills.resource_items(
                session, owner, [UUID(row['id']) for row in skills], after=(rows[-1].created_at, rows[-1].id), limit=2
            )
            assert len(next_page) == 1 and len(statements) == 1
            assert {row.id for row in rows}.isdisjoint(row.id for row in next_page)
    finally:
        event.remove(container.database.engine.sync_engine, 'before_cursor_execute', capture)


async def test_receipt_migration_only_releases_completed_upload_copy_and_is_idempotent(
    admin: httpx.AsyncClient, app: FastAPI
) -> None:
    completed = await prepare(admin, 'migration-completed')
    response = await admin.post(f'/api/skills/import/{completed["draft_id"]}/confirm', json={})
    assert response.status_code == 201, response.text
    pending = await prepare(admin, 'migration-pending')
    engine = app.state.container.database.engine
    async with engine.begin() as connection:
        # Recreate the legacy duplicate upload retained by an already completed receipt.
        await connection.execute(
            text('UPDATE skill_drafts SET package_bytes=:payload WHERE id=:id'),
            {'payload': markdown('migration-completed'), 'id': UUID(completed['draft_id'])},
        )
        before = await connection.scalar(
            text("SELECT jsonb_agg(to_jsonb(d)-'package_bytes' ORDER BY id) FROM skill_drafts d")
        )
        revisions = await connection.scalar(text('SELECT jsonb_agg(to_jsonb(r) ORDER BY id) FROM skill_revisions r'))

        def upgrade(sync: Connection) -> None:
            path = Path(__file__).parents[3] / 'migrations/versions/0038_skill_receipt_payload.py'
            spec = spec_from_file_location('receipt_payload_migration', path)
            assert spec is not None and spec.loader is not None
            migration = module_from_spec(spec)
            spec.loader.exec_module(migration)
            with Operations.context(MigrationContext.configure(sync)):
                migration.upgrade()

        await connection.run_sync(upgrade)
        await connection.run_sync(upgrade)
        assert (
            await connection.scalar(
                text("SELECT jsonb_agg(to_jsonb(d)-'package_bytes' ORDER BY id) FROM skill_drafts d")
            )
            == before
        )
        assert (
            await connection.scalar(text('SELECT jsonb_agg(to_jsonb(r) ORDER BY id) FROM skill_revisions r'))
            == revisions
        )
        assert (
            await connection.scalar(
                text('SELECT octet_length(package_bytes) FROM skill_drafts WHERE id=:id'),
                {'id': UUID(completed['draft_id'])},
            )
            == 0
        )
        assert await connection.scalar(
            text('SELECT package_bytes FROM skill_drafts WHERE id=:id'), {'id': UUID(pending['draft_id'])}
        ) == markdown('migration-pending')
    assert (await admin.post(f'/api/skills/import/{completed["draft_id"]}/confirm', json={})).json() == response.json()
