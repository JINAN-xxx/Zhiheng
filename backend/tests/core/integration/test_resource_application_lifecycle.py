import asyncio
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.core.integration.test_developer import provision
from tests.core.integration.test_work import published, task
from zhiheng.modules.models.public import ModelBinding


async def test_disabled_application_can_rename_and_active_application_can_delete(
    admin: httpx.AsyncClient, model_id: str
) -> None:
    application_id, key = await provision(admin, model_id)
    disabled = await admin.post(f'/api/developer/applications/{application_id}/disable', json={})
    assert disabled.status_code == 200
    renamed = await admin.patch(
        f'/api/developer/applications/{application_id}',
        json={'name': '停用后的新名称', 'expected_version': disabled.json()['version']},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()['status'] == 'disabled' and renamed.json()['name'] == '停用后的新名称'
    assert (await admin.post(f'/api/developer/applications/{application_id}/enable', json={})).status_code == 200
    deleted = await admin.delete(
        f'/api/developer/applications/{application_id}', headers={'content-type': 'application/json'}
    )
    assert deleted.status_code == 200 and deleted.json()['status'] == 'disabled', deleted.text
    assert (await admin.get(f'/api/developer/applications/{application_id}')).status_code == 404
    assert (await admin.get('/v1/models', headers={'Authorization': f'Bearer {key["api_key"]}'})).status_code == 401


async def test_reenabling_application_revalidates_usable_key_models(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    application_id, key = await provision(admin, model_id)
    assert (await admin.post(f'/api/developer/applications/{application_id}/disable', json={})).status_code == 200
    async with app.state.container.database.sessions.begin() as session:
        await session.execute(
            text('UPDATE models SET deleted_at=now(),enabled=false,published=false WHERE id=:id'), {'id': model_id}
        )
    rejected = await admin.post(f'/api/developer/applications/{application_id}/enable', json={})
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()['error']['code'] == 'application_dependencies_unavailable'
    assert rejected.json()['error']['details']['references'][0]['id'] == key['id']
    assert (await admin.get(f'/api/developer/applications/{application_id}')).json()['status'] == 'disabled'
    revoked = await admin.post(f'/api/developer/applications/{application_id}/keys/{key["id"]}/revoke', json={})
    assert revoked.status_code == 200
    assert (await admin.post(f'/api/developer/applications/{application_id}/enable', json={})).status_code == 200


async def test_published_agent_deletion_lists_current_work_and_preserves_history(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    agent = await published(admin, model_id)
    work = await task(admin, agent)
    rejected = await admin.delete(f'/api/agents/{agent["id"]}', headers={'content-type': 'application/json'})
    assert rejected.status_code == 409, rejected.text
    references = rejected.json()['error']['details']['references']
    assert any(row['kind'] == 'work' and row['id'] == work['id'] for row in references)
    assert (await admin.get(f'/api/agents/{agent["id"]}')).json()['published_version'] == 1
    removed = await admin.delete(
        f'/api/work/tasks/{work["id"]}',
        params={'expected_version': work['version']},
        headers={'content-type': 'application/json'},
    )
    assert removed.status_code == 204
    deleted = await admin.delete(f'/api/agents/{agent["id"]}', headers={'content-type': 'application/json'})
    assert deleted.status_code == 200, deleted.text
    assert (await admin.get(f'/api/agents/{agent["id"]}')).status_code == 404
    async with app.state.container.database.sessions() as session:
        assert (
            await session.scalar(text('SELECT count(*) FROM agent_versions WHERE agent_id=:id'), {'id': agent['id']})
            == 1
        )
        assert (
            await session.scalar(text('SELECT count(*) FROM work_members WHERE task_id=:id'), {'id': work['id']}) == 1
        )


@pytest.mark.parametrize('resource', ['model', 'skill', 'agent'])
async def test_reopening_work_revalidates_frozen_dependencies(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, resource: str
) -> None:
    agent = await published(admin, model_id, skill_slugs=['document-summary'])
    work = await task(admin, agent)
    completed = await admin.patch(
        f'/api/work/tasks/{work["id"]}', json={'expected_version': work['version'], 'status': 'completed'}
    )
    assert completed.status_code == 200, completed.text
    async with app.state.container.database.sessions.begin() as session:
        if resource == 'model':
            await session.execute(
                text('UPDATE models SET deleted_at=now(),enabled=false WHERE id=:id'), {'id': model_id}
            )
        elif resource == 'skill':
            await session.execute(
                text("UPDATE skills SET deleted_at=now(),status='disabled' WHERE slug='document-summary'")
            )
        else:
            await session.execute(
                text('UPDATE agents SET deleted_at=now(),published_version=NULL WHERE id=:id'), {'id': agent['id']}
            )
    rejected = await admin.patch(
        f'/api/work/tasks/{work["id"]}', json={'expected_version': completed.json()['version'], 'status': 'active'}
    )
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()['error']['code'] == 'work_dependencies_unavailable'
    retained = (await admin.get(f'/api/work/tasks/{work["id"]}')).json()
    assert retained['status'] == 'completed' and retained['version'] == completed.json()['version']


async def test_archived_work_can_reopen_when_dependencies_are_valid(admin: httpx.AsyncClient, model_id: str) -> None:
    agent = await published(admin, model_id)
    work = await task(admin, agent)
    archived = await admin.patch(
        f'/api/work/tasks/{work["id"]}', json={'expected_version': work['version'], 'status': 'archived'}
    )
    assert archived.status_code == 200, archived.text
    reopened = await admin.patch(
        f'/api/work/tasks/{work["id"]}', json={'expected_version': archived.json()['version'], 'status': 'active'}
    )
    assert reopened.status_code == 200 and reopened.json()['status'] == 'active', reopened.text


async def test_reopening_work_takes_model_before_task_lock(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent = await published(admin, model_id)
    work = await task(admin, agent)
    completed = await admin.patch(
        f'/api/work/tasks/{work["id"]}', json={'expected_version': work['version'], 'status': 'completed'}
    )
    assert completed.status_code == 200
    container = app.state.container
    entered = asyncio.Event()
    resolve = container.models.resolve_many
    async with container.database.sessions() as holder:

        async def observe(session: AsyncSession, ids: list[UUID]) -> dict[UUID, ModelBinding]:
            if session is not holder:
                entered.set()
            return await resolve(session, ids)

        monkeypatch.setattr(container.models, 'resolve_many', observe)
        await holder.begin()
        await resolve(holder, [UUID(model_id)])
        request = asyncio.create_task(
            admin.patch(
                f'/api/work/tasks/{work["id"]}',
                json={'expected_version': completed.json()['version'], 'status': 'active'},
            )
        )
        task_lock_available = True
        try:
            await asyncio.wait_for(entered.wait(), 5)
            try:
                async with holder.begin_nested():
                    await holder.execute(
                        text('SELECT id FROM work_tasks WHERE id=:id FOR UPDATE NOWAIT'), {'id': work['id']}
                    )
            except DBAPIError:
                task_lock_available = False
        finally:
            await holder.rollback()
        result = await asyncio.wait_for(request, 10)
        assert task_lock_available, 'Reopen locked Task before acquiring Model, reversing turn admission order'
        assert result.status_code == 200, result.text
