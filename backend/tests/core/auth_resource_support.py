"""Existing-resource fixtures and side-effect assertions for authorization review."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from sqlalchemy import select

from tests.core.integration.test_resource_files import auth, post
from tests.core.integration.test_resource_key_scopes import application, issue
from tests.core.integration.test_resource_knowledge import finish, setup
from tests.core.integration.test_resource_skills import preview
from tests.core.provider import ProviderServer
from tests.core.unit.test_skill_package import markdown
from zhiheng.modules.billing.orm import (
    CreditAccount,
    CreditHold,
    CreditKeyMonth,
    CreditLedger,
    Invocation,
    UsageAccount,
)
from zhiheng.modules.developer.orm import APIRequest, ResourceGrant
from zhiheng.modules.files.orm import FileRecord
from zhiheng.modules.knowledge.orm import (
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeFolder,
    KnowledgeJob,
    RetrievalIntent,
)
from zhiheng.modules.skills.orm import Skill, SkillDraft, SkillRevision
from zhiheng.runtime.orm import Run

RESOURCE_SCOPES = [
    'files:read',
    'files:write',
    'knowledge:read',
    'knowledge:write',
    'knowledge:query',
    'skills:read',
    'skills:write',
]


@dataclass
class ResourceWorld:
    application: dict[str, Any]
    key: dict[str, Any]
    base: dict[str, Any]
    folder: dict[str, Any]
    document: dict[str, Any]
    file: dict[str, Any]
    trash: dict[str, Any]
    skill: dict[str, Any]
    draft: dict[str, Any]
    operation: dict[str, Any]

    def values(self) -> dict[str, str]:
        return {
            'base_id': self.base['id'],
            'folder_id': self.folder['id'],
            'document_id': self.document['id'],
            'file_id': self.file['id'],
            'trash_id': self.trash['id'],
            'skill_id': self.skill['id'],
            'revision_id': self.skill['current_revision_id'],
            'draft_id': self.draft['draft_id'],
            'operation_id': self.operation['id'],
        }


async def resource_world(
    admin: httpx.AsyncClient,
    app: FastAPI,
    provider: ProviderServer,
    *,
    existing_model_id: str | None = None,
    legacy: bool = False,
) -> ResourceWorld:
    if existing_model_id is None:
        owner_app, _key, base = await setup(admin, provider, app)
    else:
        # A personal-resource user need not have global model-configuration permission.
        owner_app = await application(admin)
        create_key = await issue(admin, owner_app['id'], scopes=RESOURCE_SCOPES, model_ids=[existing_model_id])
        created = await admin.post(
            '/api/v1/knowledge-bases',
            headers=auth(create_key),
            json={'name': 'Another personal base', 'embed_model_id': existing_model_id},
        )
        assert created.status_code == 201, created.text
        base = created.json()
    if legacy:
        async with app.state.container.database.sessions.begin() as session:
            row = await session.get(KnowledgeBase, UUID(base['id']))
            row.document_policy = 'legacy'
    from zhiheng.modules.knowledge.orm import KnowledgeDefaults

    async with app.state.container.database.sessions() as session:
        defaults = await session.get(KnowledgeDefaults, 1)
        assert defaults is not None and defaults.default_rerank_model_id is not None
        ranker = str(defaults.default_rerank_model_id)
    key = await issue(admin, owner_app['id'], scopes=RESOURCE_SCOPES, model_ids=[base['embed_model_id'], ranker])
    source = await post(admin, key)
    assert source.status_code == 201, source.text
    folder = await admin.post(
        f'/api/v1/knowledge-bases/{base["id"]}/folders', headers=auth(key), json={'name': 'Matrix folder'}
    )
    assert folder.status_code == 201, folder.text
    imported = await admin.post(
        f'/api/v1/knowledge-bases/{base["id"]}/documents',
        headers=auth(key),
        json={'file_ids': [source.json()['id']], 'folder_id': folder.json()['id']},
    )
    assert imported.status_code == 202, imported.text
    operation = await finish(admin, app, key, imported.json())
    assert operation['status'] == 'succeeded', operation
    current_base = await admin.get(f'/api/v1/knowledge-bases/{base["id"]}', headers=auth(key))
    assert current_base.status_code == 200, current_base.text
    base = current_base.json()
    documents = await admin.get(f'/api/v1/knowledge-bases/{base["id"]}/documents', headers=auth(key))
    assert documents.status_code == 200 and len(documents.json()['items']) == 1, documents.text
    suffix = owner_app['id'][:8]
    prepared = await preview(admin, key, markdown('matrix-installed-' + suffix, 'Private installed instruction'))
    assert prepared.status_code == 201, prepared.text
    installed = await admin.post(f'/api/v1/skill-imports/{prepared.json()["draft_id"]}/confirm', headers=auth(key))
    assert installed.status_code == 201, installed.text
    draft = await preview(admin, key, markdown('matrix-pending-' + suffix, 'Private pending instruction'))
    assert draft.status_code == 201, draft.text
    trash = await post(admin, key)
    assert trash.status_code == 201, trash.text
    removed = await admin.delete(
        f'/api/v1/files/{trash.json()["id"]}', headers={**auth(key), 'If-Match': trash.headers['etag']}
    )
    assert removed.status_code == 200, removed.text
    return ResourceWorld(
        owner_app,
        key,
        base,
        folder.json(),
        documents.json()['items'][0],
        source.json(),
        removed.json(),
        installed.json(),
        draft.json(),
        operation,
    )


async def domain_facts(app: FastAPI) -> tuple[tuple[Any, ...], ...]:
    """Resource RPM/audit may change on rejection; domain work and billing may not."""
    columns = (
        (FileRecord.id, FileRecord.owner_id, FileRecord.status, FileRecord.version),
        (KnowledgeBase.id, KnowledgeBase.owner_id, KnowledgeBase.version, KnowledgeBase.active_job_id),
        (KnowledgeFolder.id, KnowledgeFolder.base_id, KnowledgeFolder.name, KnowledgeFolder.version),
        (KnowledgeDocument.id, KnowledgeDocument.base_id, KnowledgeDocument.status, KnowledgeDocument.version),
        (Skill.id, Skill.owner_id, Skill.status, Skill.version, Skill.revision_id),
        (SkillDraft.id, SkillDraft.owner_id, SkillDraft.confirmed_skill_id, SkillDraft.confirmed_revision_id),
        (SkillRevision.id, SkillRevision.skill_id),
        (KnowledgeJob.id, KnowledgeJob.status),
        (RetrievalIntent.id, RetrievalIntent.status, RetrievalIntent.main_run_id),
        (APIRequest.id, APIRequest.result_kind, APIRequest.result_id),
        (Run.id, Run.status),
        (
            Invocation.id,
            Invocation.status,
            Invocation.reserved_tokens,
            Invocation.charged_tokens,
            Invocation.reserved_cost,
            Invocation.billable_cost,
        ),
        (UsageAccount.user_id, UsageAccount.limit_tokens, UsageAccount.used_tokens, UsageAccount.reserved_tokens),
        (
            CreditAccount.user_id,
            CreditAccount.version,
            CreditAccount.allocated_points,
            CreditAccount.reclaimed_points,
            CreditAccount.used_points,
            CreditAccount.reserved_points,
        ),
        (CreditKeyMonth.key_id, CreditKeyMonth.month, CreditKeyMonth.used_points, CreditKeyMonth.reserved_points),
        (
            CreditHold.run_id,
            CreditHold.status,
            CreditHold.version,
            CreditHold.reserved_points,
            CreditHold.charged_points,
        ),
        (
            CreditLedger.id,
            CreditLedger.amount,
            CreditLedger.available_delta,
            CreditLedger.reserved_delta,
            CreditLedger.available_after,
            CreditLedger.reserved_after,
        ),
    )
    async with app.state.container.database.sessions() as session:
        facts = [tuple((await session.execute(select(*values).order_by(values[0]))).all()) for values in columns]
        return tuple(facts)


@asynccontextmanager
async def without_grants(app: FastAPI, application_id: str) -> AsyncIterator[None]:
    """Remove only this test App's actions, preserving the real target resources."""
    database = app.state.container.database
    async with database.sessions.begin() as session:
        rows = list(
            await session.scalars(select(ResourceGrant).where(ResourceGrant.application_id == UUID(application_id)))
        )
        previous = [(row.kind, row.resource_id, list(row.actions)) for row in rows]
        for row in rows:
            row.actions = []
    try:
        yield
    finally:
        async with database.sessions.begin() as session:
            for kind, resource_id, actions in previous:
                row = await session.get(ResourceGrant, (UUID(application_id), kind, resource_id))
                assert row is not None
                row.actions = actions


async def other_key(client: httpx.AsyncClient, model_ids: list[str]) -> dict[str, Any]:
    owner_app = await application(client)
    return await issue(client, owner_app['id'], scopes=RESOURCE_SCOPES, model_ids=model_ids)


def request_headers(key: dict[str, Any], *, etag: str | None = None) -> dict[str, str]:
    headers = auth(key, uuid4())
    if etag is not None:
        headers['If-Match'] = etag
    return headers
