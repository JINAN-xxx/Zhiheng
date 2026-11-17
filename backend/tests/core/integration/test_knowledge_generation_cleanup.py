"""Retired index payloads are reclaimed only after namespace GC, in short bounded batches."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from tests.core.integration.test_knowledge import VectorFixture
from tests.core.integration.test_knowledge_failures import finish_job, ready_base, source
from tests.core.integration.test_knowledge_required_rerank import finish_query
from zhiheng.modules.files.orm import FileRecord, FileReference
from zhiheng.modules.identity.orm import IdentityUser
from zhiheng.modules.knowledge.generations import GenerationStore
from zhiheng.modules.knowledge.orm import (
    GenerationPin,
    IndexGeneration,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeJob,
    KnowledgeStep,
    RetrievalIntent,
)
from zhiheng.modules.knowledge.parsing_orm import ParsingJob
from zhiheng.runtime.orm import Run
from zhiheng.shared.time import utcnow

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession

    from tests.core.provider import ProviderServer


@dataclass(frozen=True)
class Material:
    owner_id: UUID
    base_id: UUID
    file_id: UUID
    document_id: UUID


@pytest.fixture
async def material(app: FastAPI, model_id: str) -> Material:
    async with app.state.container.database.sessions.begin() as session:
        owner_id = await session.scalar(select(IdentityUser.id).where(IdentityUser.username == 'admin'))
        assert owner_id is not None
        base = KnowledgeBase(owner_id=owner_id, name='Generation cleanup', embed_model_id=UUID(model_id))
        file = FileRecord(
            storage_key=uuid4().hex,
            original_filename='retained.txt',
            display_name='retained.txt',
            size=16,
            mime='text/plain',
            sha256='a' * 64,
            owner_id=owner_id,
            uploader_id=owner_id,
        )
        session.add_all([base, file])
        await session.flush()
        document = KnowledgeDocument(
            base_id=base.id,
            file_id=file.id,
            file_name=file.original_filename,
            file_size=file.size,
            mime=file.mime,
            source_sha256=file.sha256,
            chunk_size=1000,
            chunk_overlap=200,
        )
        session.add(document)
        await session.flush()
        return Material(owner_id, base.id, file.id, document.id)


async def generation(
    session: AsyncSession, material: Material, status: str, *, chunks: int = 1, dimension: int | None = 2
) -> UUID:
    row = IndexGeneration(
        base_id=material.base_id,
        attempt_id=uuid4(),
        collection_name='cleanup_' + uuid4().hex,
        status=status,
        binding={},
        fingerprint='b' * 64,
        dimension=dimension,
        gc_after=utcnow() - timedelta(seconds=1),
    )
    session.add(row)
    await session.flush()
    content = 'Retained source.'
    session.add_all(
        KnowledgeChunk(
            generation_id=row.id,
            id=uuid4(),
            document_id=material.document_id,
            document_revision=row.id,
            file_id=material.file_id,
            ordinal=ordinal,
            content=content,
            content_hash=hashlib.sha256(content.encode()).hexdigest(),
            model_text=content,
            vector=[0.6, 0.8],
        )
        for ordinal in range(chunks)
    )
    await session.flush()
    return row.id


async def chunk_count(session: AsyncSession, revision: UUID) -> int:
    return (
        await session.scalar(
            select(func.count()).select_from(KnowledgeChunk).where(KnowledgeChunk.generation_id == revision)
        )
    ) or 0


def intent(material: Material) -> RetrievalIntent:
    return RetrievalIntent(
        namespace_id=uuid4(),
        client_request_id=uuid4(),
        actor_id=material.owner_id,
        owner_id=material.owner_id,
        resource_type='knowledge_query',
        resource_id=material.base_id,
        fingerprint='c' * 64,
        request={},
        dependencies={},
        deadline=utcnow() + timedelta(minutes=1),
    )


async def test_cleanup_protects_uncollected_active_and_pinned_generations(app: FastAPI, material: Material) -> None:
    sessions = app.state.container.database.sessions
    async with sessions.begin() as session:
        protected = [
            await generation(session, material, status) for status in ('candidate', 'active', 'retired', 'deleting')
        ]
        active_pointer = await generation(session, material, 'deleted')
        base = await session.get(KnowledgeBase, material.base_id)
        assert base is not None
        base.active_revision = active_pointer
        pinned = await generation(session, material, 'deleted')
        preparation = intent(material)
        session.add(preparation)
        await session.flush()
        session.add(GenerationPin(intent_id=preparation.id, generation_id=pinned))
        eligible = [await generation(session, material, 'deleted', dimension=value) for value in (2, None)]
    assert await app.state.container.knowledge_jobs.cleanup_retired_chunks() == 1
    assert await app.state.container.knowledge_jobs.cleanup_retired_chunks() == 1
    assert await app.state.container.knowledge_jobs.cleanup_retired_chunks() == 0
    async with sessions() as session:
        assert all([await chunk_count(session, revision) == 1 for revision in [*protected, active_pointer, pinned]])
        assert all([await chunk_count(session, revision) == 0 for revision in eligible])


async def test_cleanup_is_bounded_idempotent_and_preserves_native_history(app: FastAPI, material: Material) -> None:
    sessions = app.state.container.database.sessions
    async with sessions.begin() as session:
        revision = await generation(session, material, 'deleted', chunks=1001)
        job = KnowledgeJob(
            id=uuid4(),
            base_id=material.base_id,
            actor_id=material.owner_id,
            client_request_id=uuid4(),
            fingerprint='d' * 64,
            operation='import',
            index_revision=revision,
            status='completed',
            finished_at=utcnow(),
        )
        run = Run(
            owner_id=material.owner_id,
            billing_owner_id=material.owner_id,
            resource_type='knowledge_embedding',
            resource_id=job.id,
            client_request_id=uuid4(),
            fingerprint='e' * 64,
            payload={},
            status='completed',
        )
        session.add_all([job, run])
        await session.flush()
        session.add_all(
            [
                KnowledgeStep(parent_id=job.id, step_key='embedding:retained:0', run_id=run.id, fingerprint='e' * 64),
                ParsingJob(
                    parent_id=job.id,
                    owner_id=material.owner_id,
                    file_id=material.file_id,
                    source_sha256='a' * 64,
                    source_version=1,
                    source_acl_version=1,
                    config={},
                    phase='completed',
                    deadline=utcnow(),
                ),
                intent(material),
                FileReference(
                    file_id=material.file_id,
                    resource_type='knowledge_document',
                    resource_id=material.document_id,
                ),
            ]
        )
        retained = await session.get(IndexGeneration, revision)
        assert retained is not None
        snapshot = (retained.collection_name, retained.binding, retained.dimension, retained.gc_after)
    assert await app.state.container.knowledge_jobs.cleanup_retired_chunks() == 1000
    async with sessions() as session:
        assert await chunk_count(session, revision) == 1
    assert await app.state.container.knowledge_jobs.cleanup_retired_chunks() == 1
    assert await app.state.container.knowledge_jobs.cleanup_retired_chunks() == 0
    async with sessions() as session:
        row = await session.get(IndexGeneration, revision)
        assert row is not None and row.status == 'deleted'
        assert (row.collection_name, row.binding, row.dimension, row.gc_after) == snapshot
        for table in (KnowledgeJob, KnowledgeStep, ParsingJob, RetrievalIntent, Run, FileReference, KnowledgeDocument):
            assert await session.scalar(select(func.count()).select_from(table)) == 1
        assert await session.get(FileRecord, material.file_id) is not None


async def test_concurrent_cleanup_skips_owned_generation_and_finishes_remaining_batch(
    app: FastAPI, material: Material
) -> None:
    sessions = app.state.container.database.sessions
    store = GenerationStore()
    async with sessions.begin() as session:
        first = await generation(session, material, 'deleted', chunks=3)
        second = await generation(session, material, 'deleted')
    async with sessions.begin() as owner:
        assert await store.cleanup_chunks(owner, limit=2) == 2
        # The second transaction must skip the generation held by the first,
        # without waiting for its remaining chunk or replaying its deleted rows.
        async with sessions.begin() as concurrent:
            assert await asyncio.wait_for(store.cleanup_chunks(concurrent), timeout=2) == 1
            assert await chunk_count(concurrent, second) == 0
    async with sessions.begin() as session:
        assert await store.cleanup_chunks(session) == 1
    async with sessions() as session:
        assert await chunk_count(session, first) == await chunk_count(session, second) == 0


async def test_cleanup_skips_a_chunk_owned_by_another_transaction(app: FastAPI, material: Material) -> None:
    sessions = app.state.container.database.sessions
    store = GenerationStore()
    async with sessions.begin() as session:
        revision = await generation(session, material, 'deleted', chunks=3)
    async with sessions.begin() as reader:
        locked = await reader.scalar(
            select(KnowledgeChunk.id)
            .where(KnowledgeChunk.generation_id == revision)
            .order_by(KnowledgeChunk.id)
            .limit(1)
            .with_for_update()
        )
        async with sessions.begin() as cleaner:
            assert await asyncio.wait_for(store.cleanup_chunks(cleaner), timeout=2) == 2
            assert await chunk_count(cleaner, revision) == 1
        assert await reader.get(KnowledgeChunk, (revision, locked)) is not None
    assert await app.state.container.knowledge_jobs.cleanup_retired_chunks() == 1


async def test_failed_remote_gc_preserves_chunks_and_success_keeps_namespace_retry(
    app: FastAPI, material: Material, monkeypatch: pytest.MonkeyPatch
) -> None:
    container = app.state.container
    vectors = VectorFixture()
    container.knowledge_jobs.vectors = vectors
    async with container.database.sessions.begin() as session:
        revision = await generation(session, material, 'retired', chunks=2)

    async def failed_drop(spec: object) -> None:
        raise RuntimeError('local vector failure')

    with monkeypatch.context() as patch:
        patch.setattr(vectors, 'drop', failed_drop)
        with pytest.raises(RuntimeError, match='local vector failure'):
            await container.knowledge_jobs.gc(revision)
    assert await container.knowledge_jobs.cleanup_retired_chunks() == 0
    async with container.database.sessions() as session:
        assert await chunk_count(session, revision) == 2
    await container.knowledge_jobs.gc(revision)
    assert await container.knowledge_jobs.cleanup_retired_chunks() == 2
    async with container.database.sessions.begin() as session:
        row = await session.get(IndexGeneration, revision)
        assert row is not None and row.status == 'deleted' and row.dimension == 2
        assert row.gc_after is not None and row.gc_after > utcnow() + timedelta(minutes=59)
        assert revision not in await container.knowledge_jobs.generations.pending(session)
        row.gc_after = utcnow() - timedelta(seconds=1)
    async with container.database.sessions() as session:
        assert revision in await container.knowledge_jobs.generations.pending(session)
    await container.knowledge_jobs.gc(revision)
    assert await container.knowledge_jobs.cleanup_retired_chunks() == 0


async def test_cleaned_history_preserves_current_retrieval_carry_forward_and_job_replay(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    base = await ready_base(admin, app, provider)
    container = app.state.container
    endpoint = '/api/knowledge/bases/' + base['id']
    old = UUID((await admin.get(endpoint)).json()['active_revision'])
    file = await source(admin, 'second.txt', b'The health endpoint is /health.')
    body = {'file_ids': [file['id']], 'client_request_id': str(uuid4())}
    accepted = await admin.post(endpoint + '/documents', json=body)
    assert accepted.status_code == 202, accepted.text
    job = await finish_job(admin, app, accepted.json())
    assert job['status'] == 'completed', str(job)
    current = UUID(job['index_revision'])
    async with container.database.sessions.begin() as session:
        row = await session.get(IndexGeneration, old)
        assert row is not None
        row.gc_after = utcnow() - timedelta(seconds=1)
    await container.knowledge_jobs.gc(old)
    assert await container.knowledge_jobs.cleanup_retired_chunks() == 1
    async with container.database.sessions() as session:
        assert await chunk_count(session, old) == 0
        assert await chunk_count(session, current) == 2
    replay = await admin.post(endpoint + '/documents', json=body)
    assert replay.status_code == 202 and replay.json()['id'] == job['id'], replay.text
    response = await admin.post(endpoint + '/query', json={'query': 'service port', 'client_request_id': str(uuid4())})
    assert response.status_code == 202, response.text
    result = await finish_query(admin, app, response.json())
    assert result['status'] == 'completed', str(result)
    expected = {'The service listens on port 8080.', 'The health endpoint is /health.'}
    assert result['hits'] and {hit['content'] for hit in result['hits']} <= expected
    async with container.database.sessions() as session:
        row = await session.get(KnowledgeBase, UUID(base['id']))
        assert row is not None
        owner = await container.identity.principal_for_user(session, row.owner_id)
        snapshots = await container.knowledge_preparation.store.snapshot(session, owner, [row.id])
        snapshot = snapshots[0]
        raw_hits = await container.knowledge_jobs.vectors.search(
            snapshot.spec, [0.6, 0.8], 10, [item.document_id for item in snapshot.sources]
        )
        verified = await container.knowledge_preparation.store.provenance(session, snapshot, raw_hits)
        assert {hit.content for hit in verified} == expected
    third = await source(admin, 'third.txt', b'Another source.')
    accepted = await admin.post(
        endpoint + '/documents', json={'file_ids': [third['id']], 'client_request_id': str(uuid4())}
    )
    assert accepted.status_code == 202, accepted.text
    next_job = await finish_job(admin, app, accepted.json())
    assert next_job['status'] == 'completed', str(next_job)
    async with container.database.sessions() as session:
        assert await chunk_count(session, UUID(next_job['index_revision'])) == 3
