"""Real PostgreSQL checkpoints retain both bounded reconciliation cursors."""

import asyncio
import hashlib
import os
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import insert, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from zhiheng.bootstrap.migrations import migrate, migration_directory
from zhiheng.bootstrap.settings import Settings
from zhiheng.infrastructure.database import SCHEMA_REVISION
from zhiheng.modules.files.jobs import BATCH_SIZE
from zhiheng.modules.files.orm import FileJob, FileReconcileIssue, FileRecord
from zhiheng.shared.time import utcnow


async def test_reconcile_checkpoints_both_sources_without_repeating_or_skipping(app: FastAPI) -> None:
    container = app.state.container
    async with container.database.sessions() as session:
        users = await container.identity.directory(session, search='admin')
        principal = await container.identity.principal_for_user(session, users[0].id)

    count = 2 * BATCH_SIZE + 3
    content = b'checkpoint regression object'
    file_ids = [UUID(int=index + 1) for index in range(count)]
    async with container.database.sessions.begin() as session:
        session.add_all(
            FileRecord(
                id=file_id,
                storage_key=file_id.hex,
                original_filename=f'missing-{index}.txt',
                display_name=f'missing-{index}.txt',
                size=len(content),
                mime='text/plain',
                sha256=hashlib.sha256(content).hexdigest(),
                owner_id=principal.id,
                uploader_id=principal.id,
            )
            for index, file_id in enumerate(file_ids)
        )
    # Distinct physical orphans make object traversal observable in persisted issue facts.
    object_keys = sorted([await container.files.storage.put(content) for _ in range(count)])

    def age_objects(root: Path) -> None:
        timestamp = (utcnow() - timedelta(hours=2)).timestamp()
        for key in object_keys:
            os.utime(root / key, (timestamp, timestamp))

    await asyncio.to_thread(age_objects, container.settings.files_root)
    seen_files: list[UUID] = []
    seen_objects: list[str] = []
    for batch, start in enumerate(range(0, count, BATCH_SIZE)):
        job = await container.file_jobs.run(principal, 'reconcile')
        expected_files = file_ids[start : start + BATCH_SIZE]
        expected_objects = object_keys[start : start + BATCH_SIZE]
        assert job.status == 'succeeded' and job.errors == []
        assert job.scanned == job.changed == len(expected_files) + len(expected_objects)
        async with container.database.sessions() as session:
            persisted = await session.get(FileJob, job.id)
            assert persisted is not None
            if batch < 2:
                assert persisted.cursor == f'{expected_files[-1]}:{expected_objects[-1]}'
                assert len(persisted.cursor) == 69
            else:
                assert persisted.cursor == ':'
            issues = list(await session.scalars(select(FileReconcileIssue).where(FileReconcileIssue.job_id == job.id)))
            scanned_files = sorted(issue.file_id for issue in issues if issue.kind == 'missing')
            scanned_objects = sorted(issue.storage_key for issue in issues if issue.kind == 'orphan')
            assert scanned_files == expected_files
            assert scanned_objects == expected_objects
            seen_files.extend(scanned_files)
            seen_objects.extend(scanned_objects)
    assert seen_files == file_ids and len(set(seen_files)) == count
    assert seen_objects == object_keys and len(set(seen_objects)) == count


async def test_schema17_upgrade_preserves_successful_cursor_and_accepts_both_source_bounds(settings: Settings) -> None:
    config = Config()
    config.set_main_option('script_location', str(migration_directory()))

    def schema17(connection: Connection) -> None:
        config.attributes['connection'] = connection
        command.upgrade(config, '0017_chat_context_memory')

    engine = create_async_engine(settings.database_url.get_secret_value())
    previous_id = uuid4()
    previous_cursor = f'{uuid4()}:'
    try:
        async with engine.begin() as connection:
            name = await connection.scalar(text('SELECT current_database()'))
            assert isinstance(name, str) and name.startswith('core_test_')
            await connection.run_sync(schema17)
            assert (
                await connection.scalar(text('SELECT version_num FROM alembic_version')) == '0017_chat_context_memory'
            )
            width_query = text(
                'SELECT character_maximum_length FROM information_schema.columns '
                "WHERE table_schema='public' AND table_name='file_jobs' AND column_name='cursor'"
            )
            assert await connection.scalar(width_query) == 64
            await connection.execute(
                insert(FileJob).values(
                    id=previous_id,
                    kind='reconcile',
                    status='succeeded',
                    finished_at=utcnow(),
                    scanned=100,
                    changed=0,
                    cursor=previous_cursor,
                )
            )
            before = list(await connection.scalars(text('SELECT to_jsonb(t)::text FROM file_jobs t ORDER BY id')))
        await migrate(settings)
        async with engine.begin() as connection:
            assert await connection.scalar(text('SELECT version_num FROM alembic_version')) == SCHEMA_REVISION
            assert await connection.scalar(width_query) == 128
            assert (
                list(await connection.scalars(text('SELECT to_jsonb(t)::text FROM file_jobs t ORDER BY id'))) == before
            )
            assert (
                await connection.scalar(text('SELECT cursor FROM file_jobs WHERE id=:id'), {'id': previous_id})
                == previous_cursor
            )
            for key_width in (32, 64):
                cursor = f'{uuid4()}:{"f" * key_width}'
                identifier = uuid4()
                await connection.execute(insert(FileJob).values(id=identifier, kind='reconcile', cursor=cursor))
                assert (
                    await connection.scalar(text('SELECT cursor FROM file_jobs WHERE id=:id'), {'id': identifier})
                    == cursor
                )
            assert FileJob.__table__.c.cursor.type.length == 128
    finally:
        await engine.dispose()
