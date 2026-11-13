"""Concurrent retries must preserve one migration identity across file IO."""

import asyncio
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from tests.core.integration.test_knowledge import VectorFixture
from tests.core.integration.test_knowledge_failures import finish_job
from tests.core.integration.test_knowledge_materials_v2 import library
from tests.core.provider import ProviderServer
from zhiheng.modules.knowledge.orm import KnowledgeBase, KnowledgeDocument, KnowledgeMigration


async def test_concurrent_migration_confirmation_reuses_one_copy_and_job(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = await library(admin, provider)
    container = app.state.container
    container.knowledge_jobs.vectors = VectorFixture()
    async with container.database.sessions.begin() as session:
        row = await session.get(KnowledgeBase, UUID(base['id']))
        assert row is not None
        row.document_policy = 'legacy'
    upload = await admin.post(
        '/api/files?filename=migration-concurrent.txt',
        content=b'legacy original remains independent of the migration copy',
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert upload.status_code == 201, upload.text
    queued = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/documents',
        json={'file_ids': [upload.json()['id']], 'client_request_id': str(uuid4())},
    )
    assert queued.status_code == 202, queued.text
    assert (await finish_job(admin, app, queued.json()))['status'] == 'completed'
    base = (await admin.get(f'/api/knowledge/bases/{base["id"]}')).json()
    body = {
        'expected_version': base['version'],
        'expected_acl_version': base['acl_version'],
        'confirm_shared_access': True,
        'client_request_id': str(uuid4()),
    }

    entered = asyncio.Event()
    duplicate_read = asyncio.Event()
    release = asyncio.Event()
    original_read = container.files.read_content
    reads = 0

    async def paused_read(ticket: Any) -> bytes:
        nonlocal reads
        reads += 1
        entered.set()
        if reads > 1:
            duplicate_read.set()
        await release.wait()
        return await original_read(ticket)

    monkeypatch.setattr(container.files, 'read_content', paused_read)
    path = f'/api/knowledge/bases/{base["id"]}/migration'
    first = asyncio.create_task(admin.post(path, json=body))
    await asyncio.wait_for(entered.wait(), timeout=5)
    second = asyncio.create_task(admin.post(path, json=body))
    try:
        # Without request serialization both retries reach external IO. With it,
        # the second request waits for the first transaction's durable identity.
        try:
            await asyncio.wait_for(duplicate_read.wait(), timeout=1)
        except TimeoutError:
            pass
    finally:
        release.set()
    responses = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), timeout=10)
    assert all(isinstance(response, httpx.Response) for response in responses), responses
    assert [response.status_code for response in responses] == [202, 202], [response.text for response in responses]
    assert responses[0].json()['id'] == responses[1].json()['id']
    assert reads == 1
    async with container.database.sessions() as session:
        copies = await session.scalar(
            select(func.count())
            .select_from(KnowledgeDocument)
            .where(
                KnowledgeDocument.base_id == UUID(base['id']),
                KnowledgeDocument.migration_source_document_id.is_not(None),
            )
        )
        migrations = await session.scalar(
            select(func.count()).select_from(KnowledgeMigration).where(KnowledgeMigration.base_id == UUID(base['id']))
        )
    assert copies == migrations == 1
