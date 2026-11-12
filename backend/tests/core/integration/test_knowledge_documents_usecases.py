import asyncio
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.util.concurrency import await_only

from tests.core.integration.test_knowledge import embedding_model
from tests.core.provider import ProviderServer
from zhiheng.modules.files.orm import FileRecord
from zhiheng.modules.knowledge.orm import KnowledgeUpload
from zhiheng.shared.errors import AppError


async def test_staged_upload_keeps_committed_object_when_commit_acknowledgement_is_lost(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    model = await embedding_model(admin, provider)
    response = await admin.post('/api/knowledge/bases', json={'name': 'Documents', 'embed_model_id': model})
    assert response.status_code == 201, response.text
    base_id = response.json()['id']

    def mark_registration(session: Session, _context: object) -> None:
        if any(isinstance(row, KnowledgeUpload) for row in session.new):
            session.info['knowledge_commit_ack_lost'] = True

    def lose_ack(session: Session) -> None:
        if session.info.pop('knowledge_commit_ack_lost', False):
            raise AppError('commit_ack_lost', 'Simulated lost acknowledgement', 503)

    event.listen(Session, 'after_flush', mark_registration)
    event.listen(Session, 'after_commit', lose_ack)
    try:
        result = await admin.post(
            f'/api/knowledge/bases/{base_id}/uploads',
            params={'filename': 'committed.txt'},
            content=b'committed knowledge',
            headers={'Content-Type': 'application/octet-stream'},
        )
    finally:
        event.remove(Session, 'after_flush', mark_registration)
        event.remove(Session, 'after_commit', lose_ack)
    assert result.status_code == 503, result.text
    container = app.state.container
    async with container.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(KnowledgeUpload)) == 1
        file = await session.scalar(select(FileRecord).where(FileRecord.original_filename == 'committed.txt'))
        assert file is not None
        key = file.storage_key
    assert await container.files.storage.read(key) == b'committed knowledge'
    assert container.database.engine.pool.checkedout() == 0


async def test_staged_upload_discards_its_object_after_registration_rollback(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = await embedding_model(admin, provider)
    base = (await admin.post('/api/knowledge/bases', json={'name': 'Rollback', 'embed_model_id': model})).json()
    container = app.state.container

    async def reject(*_args: object, **_kwargs: object) -> UUID:
        raise AppError('registration_failed', 'Simulated registration failure', 503)

    monkeypatch.setattr(container.knowledge, 'register_upload', reject)
    result = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/uploads',
        params={'filename': 'rolled-back.txt'},
        content=b'rolled back',
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert result.status_code == 503, result.text
    async with container.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(FileRecord)) == 0
        assert await session.scalar(select(func.count()).select_from(KnowledgeUpload)) == 0
    assert not await container.files.storage.entries(limit=10)


async def test_document_preview_keeps_committed_parse_artifact_after_lost_acknowledgement(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    model = await embedding_model(admin, provider)
    base = (await admin.post('/api/knowledge/bases', json={'name': 'Preview', 'embed_model_id': model})).json()
    source = (
        await admin.post(
            '/api/files?filename=preview.txt',
            content=b'preview knowledge',
            headers={'Content-Type': 'application/octet-stream'},
        )
    ).json()
    result = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/documents',
        json={'file_ids': [source['id']], 'client_request_id': str(uuid4())},
    )
    assert result.status_code == 202, result.text
    document = (await admin.get(f'/api/knowledge/bases/{base["id"]}/documents')).json()['items'][0]

    def mark_artifact(session: Session, _context: object) -> None:
        if any(isinstance(row, FileRecord) and row.source_file_id is not None for row in session.new):
            session.info['artifact_commit_ack_lost'] = True

    def lose_ack(session: Session) -> None:
        if session.info.pop('artifact_commit_ack_lost', False):
            raise AppError('commit_ack_lost', 'Simulated artifact acknowledgement loss', 503)

    event.listen(Session, 'after_flush', mark_artifact)
    event.listen(Session, 'after_commit', lose_ack)
    path = f'/api/knowledge/bases/{base["id"]}/documents/{document["id"]}/preview'
    try:
        failed = await admin.get(path)
    finally:
        event.remove(Session, 'after_flush', mark_artifact)
        event.remove(Session, 'after_commit', lose_ack)
    assert failed.status_code == 503, failed.text
    container = app.state.container
    async with container.database.sessions() as session:
        assert document['file_id'] != source['id']
        artifact = await session.scalar(
            select(FileRecord).where(FileRecord.source_file_id == UUID(document['file_id']))
        )
        assert artifact is not None
        key = artifact.storage_key
    assert await container.files.storage.read(key)
    recovered = await admin.get(path)
    assert recovered.status_code == 200 and recovered.json()['text'] == 'preview knowledge', recovered.text
    assert container.database.engine.pool.checkedout() == 0


async def test_cancelled_upload_defers_object_cleanup_until_pending_commit_is_known(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    model = await embedding_model(admin, provider)
    base = (await admin.post('/api/knowledge/bases', json={'name': 'Cancellation', 'embed_model_id': model})).json()
    entered, release, committed = asyncio.Event(), asyncio.Event(), asyncio.Event()

    def mark_upload(session: Session, _context: object) -> None:
        if any(isinstance(row, KnowledgeUpload) for row in session.new):
            session.info['pause_knowledge_commit'] = True

    def pause_commit(session: Session) -> None:
        if session.info.get('pause_knowledge_commit'):
            entered.set()
            await_only(release.wait())

    def completed_commit(session: Session) -> None:
        if session.info.pop('pause_knowledge_commit', False):
            committed.set()

    event.listen(Session, 'after_flush', mark_upload)
    event.listen(Session, 'before_commit', pause_commit)
    event.listen(Session, 'after_commit', completed_commit)
    task = asyncio.create_task(
        admin.post(
            f'/api/knowledge/bases/{base["id"]}/uploads',
            params={'filename': 'cancelled-commit.txt'},
            content=b'pending commit bytes',
            headers={'Content-Type': 'application/octet-stream'},
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        release.set()
        await asyncio.wait_for(committed.wait(), 3)
        container = app.state.container
        async with container.database.sessions() as session:
            row = await session.scalar(select(FileRecord).where(FileRecord.original_filename == 'cancelled-commit.txt'))
            assert row is not None
            key = row.storage_key
        assert await container.files.storage.read(key) == b'pending commit bytes'
    finally:
        release.set()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        event.remove(Session, 'after_flush', mark_upload)
        event.remove(Session, 'before_commit', pause_commit)
        event.remove(Session, 'after_commit', completed_commit)
