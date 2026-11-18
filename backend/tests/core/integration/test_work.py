"""Current workspace HTTP contract; legacy execution is seeded only by named test fixtures."""

import json
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import text

from tests.core.legacy_work import legacy_task, legacy_turn, legacy_work_request, published
from tests.core.permission_support import account, grants
from zhiheng.bootstrap.migrations import migrate
from zhiheng.bootstrap.settings import Settings


def create_body(model_id: str, goal: str = '分析资料') -> dict[str, object]:
    return {
        'goal': goal,
        'model_id': model_id,
        'skill_slugs': [],
        'client_request_id': str(uuid4()),
    }


async def test_goal_driven_create_is_persistent_idempotent_and_strict(admin: httpx.AsyncClient, model_id: str) -> None:
    body = create_body(model_id, '  分析资料  ')
    created = await admin.post('/api/work/tasks', json=body)
    assert created.status_code == 201, created.text
    task = created.json()
    assert task['mode'] == 'agent' and task['goal'] == '分析资料'
    assert task['members'] == [] and task['configuration']['model_id'] == model_id
    assert task['messages'][0]['content'] == '分析资料'
    assert task['current_run_id'] is None and task['version'] == 1
    assert (await admin.post('/api/work/tasks', json=body)).json()['id'] == task['id']
    assert (await admin.post('/api/work/tasks', json={**body, 'goal': '不同目标'})).status_code == 409
    for value in ('\x00', '\ud800', '中' * 22000):
        assert (
            await admin.post(
                '/api/work/tasks',
                content=json.dumps({**body, 'goal': value}),
                headers={'Content-Type': 'application/json'},
            )
        ).status_code == 422
    assert (
        await admin.patch('/api/work/tasks/' + task['id'], json={'expected_version': True, 'title': '修改'})
    ).status_code == 422


async def test_upload_first_draft_reads_files_without_code_execution(admin: httpx.AsyncClient, model_id: str) -> None:
    created = await admin.post('/api/work/tasks', json=create_body(model_id, ''))
    assert created.status_code == 201, created.text
    task = created.json()
    assert task['goal'] == '' and task['messages'] == []
    endpoint = '/api/work/tasks/' + task['id']
    uploaded = await admin.post(
        endpoint + '/files',
        params={'filename': '金额.csv'},
        content='部门,金额\n研发,120\n'.encode(),
        headers={'Content-Type': 'application/octet-stream'},
    )
    assert uploaded.status_code == 201, uploaded.text
    current = uploaded.json()
    assert current['version'] == 2 and len(current['inputs']) == 1
    assert set(current['configuration']) == {'model_id', 'skill_slugs'}
    file_id = current['inputs'][0]['file_id']
    download = await admin.get(endpoint + '/inputs/' + file_id + '/download')
    assert download.status_code == 200 and download.content == '部门,金额\n研发,120\n'.encode()


async def test_work_only_role_uses_own_tasks_without_model_management_or_storage_permissions(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    async with account(admin, app, *grants('platform:work', 'create', 'use', 'update', 'delete')) as (member, _, _):
        options = await member.get('/api/work/options')
        assert options.status_code == 200, options.text
        assert model_id in {model['id'] for model in options.json()['models']}
        assert all(set(model) == {'id', 'name', 'max_output_tokens'} for model in options.json()['models'])
        assert (await member.get('/api/models/connections')).status_code == 403
        created = await member.post('/api/work/tasks', json=create_body(model_id))
        assert created.status_code == 201, created.text
        task = created.json()
        assert (await admin.get('/api/work/tasks/' + task['id'])).status_code == 404
        uploaded = await member.post(
            '/api/work/tasks/' + task['id'] + '/files',
            params={'filename': 'notes.txt'},
            content=b'owned notes',
            headers={'Content-Type': 'application/octet-stream'},
        )
        assert uploaded.status_code == 201, uploaded.text
        file_id = uploaded.json()['inputs'][0]['file_id']
        assert (
            await admin.get('/api/work/tasks/' + task['id'] + '/inputs/' + file_id + '/download')
        ).status_code == 404


async def test_new_parent_run_replays_and_pending_cancel_remains_owned(admin: httpx.AsyncClient, model_id: str) -> None:
    body = create_body(model_id)
    task = (await admin.post('/api/work/tasks', json=body)).json()
    endpoint = '/api/work/tasks/' + task['id']
    turn = {
        'message': task['goal'],
        'client_request_id': task['create_client_request_id'],
        'expected_version': task['version'],
    }
    admitted = await admin.post(endpoint + '/runs', json=turn)
    assert admitted.status_code == 202, admitted.text
    run = admitted.json()
    assert run['resource_type'] == 'work_agent'
    assert (await admin.post(endpoint + '/runs', json=turn)).json()['id'] == run['id']
    current = (await admin.get(endpoint)).json()
    assert current['current_run_id'] == run['id'] and len(current['messages']) == 2
    busy = await admin.patch(endpoint, json={'expected_version': current['version'], 'title': '忙碌任务'})
    assert busy.status_code == 409, busy.text
    cancelled = await admin.post(endpoint + '/runs/' + run['id'] + '/cancel', json={})
    assert cancelled.status_code == 200 and cancelled.json()['status'] == 'cancelled'
    current = (await admin.get(endpoint)).json()
    assert current['current_run_id'] is None and current['execution_state'] == 'cancelled'
    assert len([message for message in current['messages'] if message['role'] == 'user']) == 1


async def test_legacy_history_reads_and_accepted_run_cancel_remain_public_but_new_legacy_execution_is_retired(
    admin: httpx.AsyncClient, model_id: str
) -> None:
    agent = await published(admin, model_id)
    task = await legacy_task(admin, agent)
    endpoint = '/api/work/tasks/' + task['id']
    assert (await admin.get(endpoint)).json()['mode'] == 'legacy'
    legacy_body = {
        'goal': '旧式任务',
        'agent_ids': [agent['id']],
        'active_agent_id': agent['id'],
        'client_request_id': str(uuid4()),
    }
    assert (await admin.post('/api/work/tasks', json=legacy_body)).status_code == 422
    assert (await admin.post(endpoint + '/runs', json=legacy_turn(task, agent))).status_code == 422
    member = await admin.post(
        endpoint + '/members', json={'agent_id': agent['id'], 'expected_version': task['version']}
    )
    assert member.status_code == 409, member.text
    accepted = await legacy_work_request(admin, 'POST', endpoint + '/runs', json=legacy_turn(task, agent))
    assert accepted.status_code == 202, accepted.text
    cancelled = await admin.post(endpoint + '/runs/' + accepted.json()['id'] + '/cancel', json={})
    assert cancelled.status_code == 200 and cancelled.json()['status'] == 'cancelled'


@pytest.mark.parametrize('field', ['page', 'page_size'])
async def test_work_query_accepts_positive_decimal_versions_only(admin: httpx.AsyncClient, field: str) -> None:
    for value in ('0', '-1', '1.0', '1e2', 'true', '10000000000'):
        assert (await admin.get('/api/work/tasks', params={field: value})).status_code == 422
    assert (await admin.get('/api/work/tasks', params={'page': '1', 'page_size': '20'})).status_code == 200


@pytest.mark.parametrize('previous_opt_in', [False, True])
async def test_execution_opt_in_migration_preserves_existing_tasks(
    admin: httpx.AsyncClient, app: FastAPI, settings: Settings, model_id: str, previous_opt_in: bool
) -> None:
    task = (await admin.post('/api/work/tasks', json=create_body(model_id, '迁移前已有任务'))).json()
    async with app.state.container.database.sessions.begin() as session:
        await session.execute(
            text(
                "UPDATE work_tasks SET configuration = configuration || jsonb_build_object('allow_code_execution', CAST(:enabled AS boolean)) WHERE id = CAST(:id AS uuid)"
            ),
            {'enabled': previous_opt_in, 'id': task['id']},
        )
        await session.execute(text("UPDATE alembic_version SET version_num = '0042_workspace_merge'"))
    await migrate(settings)
    current = await admin.get('/api/work/tasks/' + task['id'])
    assert current.status_code == 200, current.text
    assert current.json()['goal'] == task['goal']
    assert current.json()['configuration'] == {'model_id': model_id, 'skill_slugs': []}
