import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from sqlalchemy import select

from zhiheng.cli.legacy_files import LegacyFileSource
from zhiheng.modules.files.orm import FileRecord
from zhiheng.modules.files.public import FileStorageMigration
from zhiheng.shared.errors import AppError


async def register_legacy(app: FastAPI, directory: Path, content: bytes = b'original') -> tuple[str, object]:
    container = app.state.container
    key = uuid4().hex
    (directory / key).write_bytes(content)
    async with container.database.sessions.begin() as session:
        users = await container.identity.directory(session, search='admin')
        principal = await container.identity.principal_for_user(session, users[0].id)
        info = await container.files.create(session, principal, filename='legacy.txt', content=content, storage_key=key)
    return key, info.id


async def test_one_time_copy_preserves_metadata_and_is_resumable(app: FastAPI, tmp_path: Path) -> None:
    container = app.state.container
    key, file_id = await register_legacy(app, tmp_path)
    migration = FileStorageMigration(container.database.sessions)
    source, target = LegacyFileSource(tmp_path), container.files.storage
    result = await migration.run(source, target)
    assert (result.scanned, result.copied, result.existing, result.total_bytes) == (1, 1, 0, 8)
    assert await target.read(key) == b'original'
    assert (tmp_path / key).read_bytes() == b'original'
    async with container.database.sessions() as session:
        row = await session.get(FileRecord, file_id)
        assert row.storage_key == key and row.version == row.acl_version == 1
        assert row.sha256 == hashlib.sha256(b'original').hexdigest()
    repeated = await migration.run(source, target)
    assert repeated.copied == 0 and repeated.existing == 1
    verified = await migration.run(source, target, verify_only=True)
    assert verified.scanned == 1 and verified.copied == 0


@pytest.mark.parametrize('failure', ['source-corrupt', 'target-conflict', 'source-missing'])
async def test_failed_copy_preserves_metadata_and_never_overwrites_existing_objects(
    app: FastAPI, tmp_path: Path, failure: str
) -> None:
    container = app.state.container
    key, file_id = await register_legacy(app, tmp_path)
    if failure == 'source-corrupt':
        (tmp_path / key).write_bytes(b'changed!')
    elif failure == 'source-missing':
        (tmp_path / key).unlink()
    else:
        await container.files.storage.import_object(key, b'conflict')
    with pytest.raises(AppError):
        await FileStorageMigration(container.database.sessions).run(LegacyFileSource(tmp_path), container.files.storage)
    async with container.database.sessions() as session:
        row = await session.scalar(select(FileRecord).where(FileRecord.id == file_id))
        assert row.version == row.acl_version == 1 and row.status == 'active'
    if failure == 'target-conflict':
        assert await container.files.storage.read(key) == b'conflict'
