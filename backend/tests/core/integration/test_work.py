import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select

from tests.core.integration.test_agents import config
from zhiheng.runtime.orm import Run
from zhiheng.worker.execution import execute_run


async def published(admin: httpx.AsyncClient, model_id: str, **kwargs) -> dict:
    created = await admin.post(
        '/api/developer/applications', json={'code': 'work-' + uuid4().hex, 'name': 'Work publication'}
    )
    assert created.status_code == 201, created.text
    application = created.json()['id']
    agent = (await admin.post('/api/agents', json=config(model_id, application_id=application, **kwargs))).json()
    response = await admin.post(f'/api/agents/{agent["id"]}/publish', json={'expected_version': 1})
    assert response.status_code == 200, response.text
    return response.json()


async def task(admin: httpx.AsyncClient, agent: dict, **kwargs) -> dict:
    body = {
        'goal': '共同目标',
        'agent_ids': [agent['id']],
        'active_agent_id': agent['id'],
        'client_request_id': str(uuid4()),
    }
    body.update(kwargs)
    response = await admin.post('/api/work/tasks', json=body)
    assert response.status_code == 201, response.text
    return response.json()


def turn(t: dict, agent: dict, **kwargs) -> dict:
    return dict(
        agent_id=agent['id'],
        message=t['goal'],
        client_request_id=t['create_client_request_id'],
        expected_version=t['version'],
        **kwargs,
    )


@pytest.mark.asyncio
async def test_create_is_persistent_idempotent_and_strict(admin: httpx.AsyncClient, model_id: str) -> None:
    a = await published(admin, model_id)
    body = {
        'goal': '  共同目标  ',
        'agent_ids': [a['id']],
        'active_agent_id': a['id'],
        'client_request_id': str(uuid4()),
    }
    r = await admin.post('/api/work/tasks', json=body)
    assert r.status_code == 201, r.text
    t = r.json()
    assert t['goal'] == '共同目标' and t['messages'][0]['content'] == '共同目标'
    assert t['current_run_id'] is None and t['version'] == 1
    assert (await admin.post('/api/work/tasks', json=body)).json()['id'] == t['id']
    assert (await admin.post('/api/work/tasks', json={**body, 'goal': 'changed'})).status_code == 409
    for bad in (' ', '\x00', '\ud800', '中' * 22000):
        assert (
            await admin.post(
                '/api/work/tasks',
                content=__import__('json').dumps({**body, 'goal': bad}),
                headers={'Content-Type': 'application/json'},
            )
        ).status_code == 422
    assert (
        await admin.patch('/api/work/tasks/' + t['id'], json={'expected_version': True, 'title': 'x'})
    ).status_code == 422
    assert 'config' not in str(t['members']) and 'system_prompt' not in str(t)


@pytest.mark.asyncio
async def test_work_turn_replay_shared_attribution_and_pending_cancel(
    admin: httpx.AsyncClient, model_id: str, app: object, provider: object
) -> None:
    a = await published(admin, model_id, system_prompt='FROZEN SYSTEM')
    t = await task(admin, a)
    endpoint = f'/api/work/tasks/{t["id"]}'
    r = await admin.post(endpoint + '/runs', json=turn(t, a))
    assert r.status_code == 202, r.text
    run = r.json()
    assert (await admin.post(endpoint + '/runs', json=turn(t, a))).json()['id'] == run['id']
    busy = (await admin.get(endpoint)).json()
    assert busy['current_run_id'] == run['id']
    assert (await admin.patch(endpoint, json={'expected_version': busy['version'], 'title': 'x'})).status_code == 409
    await execute_run(app.state.container, UUID(run['id']))
    done = (await admin.get(endpoint)).json()
    assert done['current_run_id'] is None and done['messages'][-1]['status'] == 'completed'
    assert done['messages'][-1]['content'] == '你好'
    r2 = await admin.post(
        endpoint + '/runs',
        json={
            'agent_id': a['id'],
            'message': '继续',
            'client_request_id': str(uuid4()),
            'expected_version': done['version'],
        },
    )
    assert r2.status_code == 202, r2.text
    async with app.state.container.database.sessions() as session:
        row = await session.get(Run, UUID(r2.json()['id']))
        text = str(row.payload['messages'])
        assert '[Agent: 研究助手; publishedVersion: 1]' in text
        assert '先思考' not in text and 'FROZEN SYSTEM' in text
    cancel = await admin.post(endpoint + '/runs/' + r2.json()['id'] + '/cancel', json={})
    assert cancel.json()['usage']['status'] == 'known'
    after = (await admin.get(endpoint)).json()
    assert after['current_run_id'] is None and after['messages'][-1]['status'] == 'cancelled'
    assert (await admin.post(endpoint + '/runs', json=turn(t, a))).json()['id'] == run['id']


@pytest.mark.asyncio
async def test_members_publication_upgrade_and_state_cas(admin: httpx.AsyncClient, model_id: str) -> None:
    a = await published(admin, model_id)
    t = await task(admin, a)
    url = '/api/work/tasks/' + t['id']
    await admin.patch(
        '/api/agents/' + a['id'],
        json=config(model_id, application_id=a['application_id'], name='新版', expected_version=2),
    )
    await admin.post('/api/agents/' + a['id'] + '/publish', json={'expected_version': 3})
    old = (await admin.get(url)).json()
    assert old['members'][0]['published_version'] == 1 and old['members'][0]['latest_published_version'] == 2
    assert (
        await admin.patch(url + '/members/' + a['id'], json={'expected_version': 1, 'expected_published_version': 1})
    ).status_code == 409
    upgraded = await admin.patch(
        url + '/members/' + a['id'], json={'expected_version': 1, 'expected_published_version': 2}
    )
    assert upgraded.status_code == 200, upgraded.text
    assert upgraded.json()['members'][0]['agent_name_snapshot'] == '新版'
    assert (await admin.patch(url, json={'expected_version': 1, 'title': 'stale'})).status_code == 409
    done = await admin.patch(url, json={'expected_version': 2, 'status': 'completed'})
    assert done.status_code == 200, done.text
    assert (await admin.patch(url, json={'expected_version': 3, 'goal': 'x'})).status_code == 409
    reopened = await admin.patch(url, json={'expected_version': 3, 'status': 'active'})
    removed = await admin.delete(
        url + '/members/' + a['id'],
        params={'expected_version': reopened.json()['version']},
        headers={'Content-Type': 'application/json'},
    )
    assert removed.status_code == 200 and removed.json()['active_agent_id'] is None
    added = await admin.post(
        url + '/members', json={'expected_version': removed.json()['version'], 'agent_id': a['id']}
    )
    assert added.status_code == 200 and added.json()['members'][-1]['published_version'] == 2


@pytest.mark.asyncio
async def test_concurrent_runs_single_admission_and_unpublish_guard(admin: httpx.AsyncClient, model_id: str) -> None:
    a = await published(admin, model_id)
    t = await task(admin, a)
    url = '/api/work/tasks/' + t['id']
    pair = await asyncio.gather(
        *(admin.post(url + '/runs', json={**turn(t, a), 'client_request_id': str(uuid4())}) for _ in range(2))
    )
    assert sorted(r.status_code for r in pair) == [202, 409]
    admitted = next(r.json() for r in pair if r.status_code == 202)
    await admin.post(url + '/runs/' + admitted['id'] + '/cancel', json={})
    await admin.post('/api/agents/' + a['id'] + '/unpublish', json={'expected_version': 2})
    current = (await admin.get(url)).json()
    result = await admin.post(url + '/runs', json={**turn(current, a), 'client_request_id': str(uuid4())})
    assert result.status_code == 404, result.text


@asynccontextmanager
async def member_client(app: object) -> AsyncIterator[httpx.AsyncClient]:
    from zhiheng.api.dependencies import COOKIE_NAME

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
    result = await client.post('/api/auth/login', json={'username': 'member', 'password': 'platform-test-password'})
    assert result.status_code == 200
    assert COOKIE_NAME in client.cookies
    try:
        yield client
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_owner_hidden404_admin_uses_original_owner_and_work_bound_runs(
    admin: httpx.AsyncClient, model_id: str, app: object
) -> None:
    a = await published(admin, model_id)
    admin_task = await task(admin, a)
    async with member_client(app) as member:
        url = '/api/work/tasks/' + admin_task['id']
        assert (await member.get(url)).status_code == 404
        assert (
            await member.post(
                '/api/work/tasks',
                json={
                    'goal': 'q',
                    'agent_ids': [a['id']],
                    'active_agent_id': a['id'],
                    'client_request_id': str(uuid4()),
                },
            )
        ).status_code == 404
        own = await published(member, model_id)
        own_task = await task(member, own)
        url = '/api/work/tasks/' + own_task['id']
        denied = await admin.post(url + '/members', json={'agent_id': a['id'], 'expected_version': 1})
        assert denied.status_code == 404, denied.text
        directory = (await admin.get('/api/work/agents/available', params={'task_id': own_task['id']})).json()
        assert [entry['id'] for entry in directory] == [own['id']]
        # Admin's own Task may use an agent owned by another user.
        allowed = await admin.post(
            '/api/work/tasks/' + admin_task['id'] + '/members', json={'agent_id': own['id'], 'expected_version': 1}
        )
        assert allowed.status_code == 200, allowed.text
        run = (await admin.post(url + '/runs', json=turn(own_task, own))).json()
        assert (await member.get('/api/runs/' + run['id'])).status_code == 404
        assert (await member.get(url + '/runs/' + run['id'])).status_code == 200
        assert (await member.get(url + '/runs/' + str(uuid4()))).status_code == 404
        other_run = await admin.post(
            '/api/agents/' + a['id'] + '/preview',
            json={'messages': [{'role': 'user', 'content': 'private'}], 'client_request_id': str(uuid4())},
        )
        assert (await member.get(url + '/runs/' + other_run.json()['id'])).status_code == 404
        stopped = await member.post(url + '/runs/' + run['id'] + '/cancel', json={})
        assert stopped.json()['status'] == 'cancelled'
        async with app.state.container.database.sessions() as session:
            row = await session.get(Run, UUID(run['id']))
            assert str(row.owner_id) == admin_task['owner_id']
            assert str(row.billing_owner_id) == own_task['owner_id']


@pytest.mark.asyncio
async def test_creation_member_set_fingerprint_limit_readd_and_summary(admin: httpx.AsyncClient, model_id: str) -> None:
    agents = [await published(admin, model_id, name=f'member {i}') for i in range(11)]
    body = {
        'goal': 'same goal',
        'agent_ids': [a['id'] for a in agents[:10]],
        'active_agent_id': agents[0]['id'],
        'client_request_id': str(uuid4()),
    }
    first = await admin.post('/api/work/tasks', json=body)
    assert first.status_code == 201, first.text
    replay = await admin.post('/api/work/tasks', json={**body, 'agent_ids': list(reversed(body['agent_ids']))})
    assert replay.json()['id'] == first.json()['id']
    url = '/api/work/tasks/' + first.json()['id']
    assert (
        await admin.post(url + '/members', json={'agent_id': agents[-1]['id'], 'expected_version': 1})
    ).status_code == 409
    duplicate = {**body, 'agent_ids': [agents[0]['id'], agents[0]['id']]}
    assert (await admin.post('/api/work/tasks', json=duplicate)).status_code == 422
    listing = (await admin.get('/api/work/tasks', params={'search': 'same', 'status': 'active'})).json()
    assert listing['total'] == 1 and 'messages' not in listing['items'][0] and 'members' not in listing['items'][0]
    removed = await admin.delete(
        url + '/members/' + agents[0]['id'],
        params={'expected_version': 1},
        headers={'Content-Type': 'application/json'},
    )
    readd = await admin.post(
        url + '/members', json={'agent_id': agents[0]['id'], 'expected_version': removed.json()['version']}
    )
    assert readd.status_code == 200
    assert len([m for m in readd.json()['members'] if m['removed_at'] is None]) == 10
    assert len(readd.json()['members']) == 11
    archived = await admin.patch(url, json={'status': 'archived', 'expected_version': readd.json()['version']})
    reopened = await admin.patch(url, json={'status': 'active', 'expected_version': archived.json()['version']})
    assert reopened.status_code == 200, reopened.text
    assert (
        await admin.delete(
            url, params={'expected_version': reopened.json()['version']}, headers={'Content-Type': 'application/json'}
        )
    ).status_code == 204
    assert (await admin.get(url)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['failed', 'silent_cancel', 'interrupted'])
async def test_partial_reasoning_terminal_cancel_recovery_and_fencing(  # noqa: PLR0915 - exercise the complete terminal boundary
    admin: httpx.AsyncClient, model_id: str, app: object, outcome: str
) -> None:
    from collections.abc import AsyncIterator
    from datetime import timedelta

    from zhiheng.application.inference import InferenceExecutor
    from zhiheng.modules.models.public import ModelBinding, ModelDelta, ModelMessage, ProviderError
    from zhiheng.shared.time import utcnow
    from zhiheng.worker.main import recover_expired

    container = app.state.container
    a = await published(admin, model_id)
    t = await task(admin, a)
    url = '/api/work/tasks/' + t['id']
    r = (await admin.post(url + '/runs', json=turn(t, a))).json()
    run_id = UUID(r['id'])
    if outcome == 'interrupted':
        async with container.database.sessions.begin() as session:
            claim = await container.runs.claim(session, run_id, 3)
            await container.runs.start_provider(session, run_id, claim.attempt_id)
            await container.runs.project(session, run_id, claim.attempt_id, 'partial', '思考')
            row = await session.get(Run, run_id)
            row.lease_until = utcnow() - timedelta(seconds=1)
        await recover_expired(container)
        await recover_expired(container)
        async with container.database.sessions.begin() as session:
            assert not await container.runs.project(session, run_id, claim.attempt_id, 'OLD', 'OLD')
    else:
        started = asyncio.Event()
        closed = asyncio.Event()

        class Provider:
            async def stream(self, messages: tuple[ModelMessage, ...]) -> AsyncIterator[ModelDelta]:
                try:
                    yield ModelDelta(text='partial', reasoning='思考')
                    started.set()
                    if outcome == 'silent_cancel':
                        await asyncio.Event().wait()
                    raise ProviderError
                finally:
                    closed.set()

        async def factory(binding: ModelBinding) -> Provider:
            return Provider()

        executor = InferenceExecutor(
            sessions=container.database.sessions,
            runs=container.runs,
            usage=container.usage,
            chat=container.chat,
            provider_factory=factory,
            lease_seconds=3,
            timeout_seconds=2,
        )
        execution = asyncio.create_task(executor.execute(run_id))
        await asyncio.wait_for(started.wait(), 2)
        if outcome == 'silent_cancel':
            before = (await admin.get(url)).json()
            assert before['messages'][-1]['reasoning'] == '思考' and before['messages'][-1]['status'] == 'running'
            cancellation = await admin.post(url + '/runs/' + r['id'] + '/cancel', json={})
            assert cancellation.json()['status'] == 'cancel_requested'
            assert (await admin.get(url)).json()['current_run_id'] == r['id']
        await asyncio.wait_for(execution, 3)
        assert closed.is_set()
    after = (await admin.get(url)).json()
    last = after['messages'][-1]
    assert after['current_run_id'] is None
    assert last['status'] == {'failed': 'failed', 'silent_cancel': 'cancelled', 'interrupted': 'interrupted'}[outcome]
    assert last['content'] == 'partial' and last['reasoning'] == '思考'
    assert last['usage']['status'] == 'unknown'
    await execute_run(container, run_id)  # duplicate delivery must not run again
    follow = await admin.post(
        url + '/runs',
        json={
            'agent_id': a['id'],
            'client_request_id': str(uuid4()),
            'message': 'retry',
            'expected_version': after['version'],
        },
    )
    assert follow.status_code == 202, follow.text
    async with container.database.sessions() as session:
        row = await session.get(Run, UUID(follow.json()['id']))
        prompt = str(row.payload['messages'])
        assert 'partial' in prompt and '思考' not in prompt
    # An old terminal projection cannot release the new occupant or overwrite history.
    async with container.database.sessions.begin() as session:
        await container.work_runs.project(session, await container.runs.projection(session, run_id))
    assert (await admin.get(url)).json()['current_run_id'] == follow.json()['id']


@pytest.mark.asyncio
async def test_frozen_skill_model_parameters_and_internal_application_independence(
    admin: httpx.AsyncClient, model_id: str, app: object, provider: object
) -> None:
    from tests.core.integration.test_agents import switch_current_skill_revision
    from tests.core.integration.test_skills import install

    original = await install(admin, 'work-original', 'FROZEN WORK SKILL')
    future = await install(admin, 'work-future', 'FUTURE WORK SKILL')
    a = await published(admin, model_id, temperature=0.3, skill_slugs=[original['slug']])
    t = await task(admin, a)
    await switch_current_skill_revision(app, original, future)
    await admin.post('/api/developer/applications/' + a['application_id'] + '/disable', json={})
    assert a['id'] in [item['id'] for item in (await admin.get('/api/work/agents/available')).json()]
    r = await admin.post('/api/work/tasks/' + t['id'] + '/runs', json=turn(t, a))
    assert r.status_code == 202, r.text
    await execute_run(app.state.container, UUID(r.json()['id']))
    assert 'FROZEN WORK SKILL' in str(provider.requests[-1]) and 'FUTURE WORK SKILL' not in str(provider.requests[-1])
    assert provider.requests[-1]['temperature'] == 0.3
    assert provider.requests[-1]['max_completion_tokens'] == 32
    async with app.state.container.database.sessions() as session:
        from zhiheng.modules.work.orm import WorkMessage

        audit = await session.scalar(
            select(WorkMessage).where(WorkMessage.run_id == UUID(r.json()['id']), WorkMessage.role == 'assistant')
        )
        assert audit.audit['skills'][0]['revision_id'] == original['revision_id']
    done = (await admin.get('/api/work/tasks/' + t['id'])).json()
    await admin.patch('/api/skills/' + original['id'] + '/status', json={'status': 'disabled', 'expected_version': 2})
    blocked = await admin.post(
        '/api/work/tasks/' + t['id'] + '/runs', json={**turn(done, a), 'client_request_id': str(uuid4())}
    )
    assert blocked.status_code == 422, blocked.text
    assert (
        await admin.delete('/api/models/' + model_id, headers={'Content-Type': 'application/json'})
    ).status_code == 409
    assert (
        await admin.delete('/api/skills/' + original['id'], headers={'Content-Type': 'application/json'})
    ).status_code == 409


@pytest.mark.asyncio
async def test_batch_freeze_locks_all_models_before_shared_connection(
    admin: httpx.AsyncClient, model_id: str, app: object
) -> None:
    from zhiheng.modules.models.orm import ModelDefinition

    container = app.state.container
    first_model = (await admin.get('/api/models')).json()[0]
    second = (
        await admin.post(
            '/api/models',
            json={
                'name': 'second',
                'connection_id': first_model['connection_id'],
                'upstream_model': 'second',
                'max_output_tokens': 64,
            },
        )
    ).json()['id']
    a, b = sorted([model_id, second])
    aa, bb = await published(admin, a), await published(admin, b)
    creation = None
    async with container.database.sessions.begin() as session:
        await session.scalar(select(ModelDefinition).where(ModelDefinition.id == UUID(b)).with_for_update())
        creation = asyncio.create_task(
            admin.post(
                '/api/work/tasks',
                json={
                    'goal': 'lock order',
                    'agent_ids': [aa['id'], bb['id']],
                    'active_agent_id': aa['id'],
                    'client_request_id': str(uuid4()),
                },
            )
        )
        await asyncio.sleep(0.1)
        # A batch waiting for B must not already hold the shared connection.
        await asyncio.wait_for(container.models.resolve(session, UUID(b)), 0.4)
    result = await asyncio.wait_for(creation, 2)
    assert result.status_code == 201, result.text


@pytest.mark.asyncio
async def test_delete_query_version_rejects_float_notation(admin: httpx.AsyncClient, model_id: str) -> None:
    a = await published(admin, model_id)
    t = await task(admin, a)
    result = await admin.delete(
        '/api/work/tasks/' + t['id'], params={'expected_version': '1.0'}, headers={'Content-Type': 'application/json'}
    )
    assert result.status_code == 422, result.text


@pytest.mark.asyncio
async def test_latest_publication_unavailable_cannot_be_bypassed_by_frozen_member(
    admin: httpx.AsyncClient, model_id: str
) -> None:
    a = await published(admin, model_id)
    t = await task(admin, a)
    model = (await admin.get('/api/models')).json()[0]
    future = (
        await admin.post(
            '/api/models',
            json={
                'name': 'future model',
                'connection_id': model['connection_id'],
                'upstream_model': 'future',
                'max_output_tokens': 64,
            },
        )
    ).json()['id']
    updated = await admin.patch(
        '/api/agents/' + a['id'], json=config(future, application_id=a['application_id'], expected_version=2)
    )
    assert updated.status_code == 200
    await admin.post('/api/agents/' + a['id'] + '/publish', json={'expected_version': 3})
    await admin.patch('/api/models/' + future, json={'enabled': False})
    response = await admin.post('/api/work/tasks/' + t['id'] + '/runs', json=turn(t, a))
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_finish_account_wait_does_not_block_busy_admission(
    admin: httpx.AsyncClient, model_id: str, app: object
) -> None:
    from zhiheng.application.inference import InferenceExecutor, InferenceProgress
    from zhiheng.modules.developer.orm import UsageAccount

    container = app.state.container
    a = await published(admin, model_id)
    t = await task(admin, a)
    url = '/api/work/tasks/' + t['id']
    admitted = (await admin.post(url + '/runs', json=turn(t, a))).json()
    run_id = UUID(admitted['id'])
    async with container.database.sessions.begin() as session:
        claim = await container.runs.claim(session, run_id, 3)
    finishing = None
    executor = InferenceExecutor(
        sessions=container.database.sessions,
        runs=container.runs,
        usage=container.usage,
        chat=container.chat,
        provider_factory=container.prepare_provider,
        lease_seconds=3,
        timeout_seconds=2,
    )
    async with container.database.sessions.begin() as session:
        await session.scalar(select(UsageAccount).where(UsageAccount.user_id == UUID(t['owner_id'])).with_for_update())
        finishing = asyncio.create_task(executor._finish(claim, InferenceProgress(), 'failed', None, rejected=True))
        await asyncio.sleep(0.1)
        current = (await admin.get(url)).json()
        response = await asyncio.wait_for(
            admin.post(
                url + '/runs',
                json={
                    'agent_id': a['id'],
                    'message': 'second',
                    'client_request_id': str(uuid4()),
                    'expected_version': current['version'],
                },
            ),
            0.5,
        )
        assert response.status_code == 409, response.text
    await asyncio.wait_for(finishing, 2)
    assert (await admin.get(url)).json()['current_run_id'] is None


@pytest.mark.asyncio
async def test_bounded_context_preserves_last_user_and_full_audit_history(
    admin: httpx.AsyncClient, model_id: str, app: object
) -> None:
    from zhiheng.modules.work.orm import WorkMessage

    a = await published(admin, model_id)
    t = await task(admin, a)
    url = '/api/work/tasks/' + t['id']
    async with app.state.container.database.sessions.begin() as session:
        for i in range(110):
            session.add(
                WorkMessage(
                    task_id=UUID(t['id']),
                    client_request_id=uuid4(),
                    sequence=i + 2,
                    role='assistant' if i % 2 else 'user',
                    content=f'entry {i}:' + '中' * 1000,
                    reasoning='PRIVATE REASONING',
                    status='failed',
                    agent_id=UUID(a['id']),
                    agent_name_snapshot='旧成员',
                    published_version=1,
                )
            )
    old_goal = await admin.post(url + '/runs', json=turn(t, a))
    assert old_goal.status_code == 409
    admitted = await admin.post(
        url + '/runs',
        json={
            'agent_id': a['id'],
            'message': 'LATEST USER',
            'client_request_id': str(uuid4()),
            'expected_version': t['version'],
        },
    )
    assert admitted.status_code == 202, admitted.text
    async with app.state.container.database.sessions() as session:
        row = await session.get(Run, UUID(admitted.json()['id']))
        history = [item for item in row.payload['messages'] if item['role'] != 'system']
        assert len(history) <= 96 and sum(len(item['content'].encode()) for item in history) <= 65536
        assert history[-1]['content'] == 'LATEST USER'
        assert 'entry 0:' not in str(history) and 'PRIVATE REASONING' not in str(history)
    assert len((await admin.get(url)).json()['messages']) == 113


@pytest.mark.asyncio
async def test_identical_key_reserves_once_and_generic_cancel_projects_work(
    admin: httpx.AsyncClient, model_id: str, app: object
) -> None:
    from zhiheng.modules.developer.orm import Invocation

    a = await published(admin, model_id)
    t = await task(admin, a)
    url = '/api/work/tasks/' + t['id']
    pair = await asyncio.gather(*(admin.post(url + '/runs', json=turn(t, a)) for _ in range(2)))
    assert all(item.status_code == 202 for item in pair)
    run_id = pair[0].json()['id']
    assert pair[1].json()['id'] == run_id
    changed = await admin.post(url + '/runs', json={**turn(t, a), 'message': 'other'})
    assert changed.status_code == 409
    async with app.state.container.database.sessions() as session:
        rows = list(await session.scalars(select(Invocation).where(Invocation.run_id == UUID(run_id))))
        assert len(rows) == 1
    cancelled = await admin.post('/api/runs/' + run_id + '/cancel', json={})
    assert cancelled.json()['status'] == 'cancelled'
    current = (await admin.get(url)).json()
    assert current['current_run_id'] is None and current['messages'][-1]['status'] == 'cancelled'
    stream = await admin.get(url + '/runs/' + run_id + '/events')
    assert 'event: snapshot' in stream.text and '"status":"cancelled"' in stream.text


@pytest.mark.asyncio
async def test_initial_goal_key_keeps_original_agent_after_active_selection_changes(
    admin: httpx.AsyncClient, model_id: str, app: object
) -> None:
    a = await published(admin, model_id, name='original selection')
    b = await published(admin, model_id, name='other member')
    t = await task(admin, a, agent_ids=[a['id'], b['id']])
    url = '/api/work/tasks/' + t['id']
    rejected = await admin.post(url + '/runs', json=turn(t, b))
    assert rejected.status_code == 409, rejected.text
    changed = await admin.patch(url, json={'expected_version': t['version'], 'active_agent_id': b['id']})
    assert changed.status_code == 200, changed.text
    current = changed.json()
    rejected = await admin.post(url + '/runs', json=turn(current, b))
    assert rejected.status_code == 409, rejected.text
    assert (await admin.get(url)).json()['current_run_id'] is None
    async with app.state.container.database.sessions() as session:
        rows = list(await session.scalars(select(Run).where(Run.resource_id == UUID(t['id']))))
        assert rows == []
    accepted = await admin.post(url + '/runs', json=turn(current, a))
    assert accepted.status_code == 202, accepted.text


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['page', 'page_size'])
async def test_work_list_query_accepts_only_positive_decimal_integers(admin: httpx.AsyncClient, field: str) -> None:
    for value in ('1.0', '20.0', 'true', 'false', '-1', '+1', '01', ' 1', '1 ', '0', '1e0'):
        response = await admin.get('/api/work/tasks', params={field: value})
        assert response.status_code == 422, (field, value, response.text)
    for value in ('1', '2', '20', '100'):
        response = await admin.get('/api/work/tasks', params={field: value})
        assert response.status_code == 200, (field, value, response.text)
        assert response.json()[field] == int(value)
    assert (await admin.get('/api/work/tasks', params={'page_size': '101'})).status_code == 422
    assert (await admin.get('/api/work/tasks')).json()['page_size'] == 20


@pytest.mark.asyncio
async def test_work_members_keep_individual_skill_count_and_byte_limits(
    admin: httpx.AsyncClient, model_id: str
) -> None:
    from tests.core.integration.test_skills import install

    skills = [await install(admin, f'work-combined-{i}', f'SKILL {i}:' + 'x' * 7000) for i in range(6)]
    a = await published(admin, model_id, skill_slugs=[item['slug'] for item in skills[:3]])
    b = await published(admin, model_id, skill_slugs=[item['slug'] for item in skills[3:]])
    response = await admin.post(
        '/api/work/tasks',
        json={
            'goal': 'different specialists',
            'agent_ids': [b['id'], a['id']],
            'active_agent_id': a['id'],
            'client_request_id': str(uuid4()),
        },
    )
    assert response.status_code == 201, response.text
    assert len(response.json()['members']) == 2
    oversized_config = await admin.patch(
        '/api/agents/' + a['id'],
        json=config(
            model_id,
            application_id=a['application_id'],
            expected_version=2,
            skill_slugs=[item['slug'] for item in skills],
        ),
    )
    assert oversized_config.status_code == 422
    oversized_content = await admin.patch(
        '/api/agents/' + a['id'],
        json=config(
            model_id,
            application_id=a['application_id'],
            expected_version=2,
            skill_slugs=[item['slug'] for item in skills[:5]],
        ),
    )
    assert (
        oversized_content.status_code == 422 and oversized_content.json()['error']['code'] == 'skill_context_too_long'
    )


@pytest.mark.asyncio
async def test_frozen_and_latest_publication_skill_sets_have_independent_limits(
    admin: httpx.AsyncClient, model_id: str, app: object
) -> None:
    from tests.core.integration.test_skills import install

    skills = [await install(admin, f'work-old-current-{i}', f'SKILL {i}:' + 'x' * 7000) for i in range(6)]
    a = await published(admin, model_id, skill_slugs=[item['slug'] for item in skills[:3]])
    t = await task(admin, a)
    edited = await admin.patch(
        '/api/agents/' + a['id'],
        json=config(
            model_id,
            application_id=a['application_id'],
            expected_version=2,
            skill_slugs=[item['slug'] for item in skills[3:]],
        ),
    )
    assert edited.status_code == 200, edited.text
    assert (await admin.post('/api/agents/' + a['id'] + '/publish', json={'expected_version': 3})).status_code == 200
    url = '/api/work/tasks/' + t['id']
    admitted = await admin.post(url + '/runs', json=turn(t, a))
    assert admitted.status_code == 202, admitted.text
    async with app.state.container.database.sessions() as session:
        row = await session.get(Run, UUID(admitted.json()['id']))
        assert 'SKILL 0:' in str(row.payload['messages'])
        assert 'SKILL 3:' not in str(row.payload['messages'])
    cancelled = await admin.post(url + '/runs/' + admitted.json()['id'] + '/cancel', json={})
    assert cancelled.status_code == 200
    disabled = await admin.patch(
        '/api/skills/' + skills[-1]['id'] + '/status', json={'status': 'disabled', 'expected_version': 1}
    )
    assert disabled.status_code == 200, disabled.text
    current = (await admin.get(url)).json()
    rejected = await admin.post(url + '/runs', json={**turn(current, a), 'client_request_id': str(uuid4())})
    assert rejected.status_code == 422, rejected.text


@pytest.mark.asyncio
@pytest.mark.parametrize('delegated', [False, True])
async def test_work_runtime_key_collision_returns_conflict_without_partial_admission(
    admin: httpx.AsyncClient, model_id: str, app: object, provider: object, delegated: bool
) -> None:
    from uuid import uuid5

    from sqlalchemy import func

    from zhiheng.modules.developer.orm import Invocation
    from zhiheng.runtime.orm import Outbox

    async with member_client(app) as member:
        creator = member if delegated else admin
        a = await published(creator, model_id)
        t = await task(creator, a)
    runtime_key = str(uuid5(UUID(t['id']), t['create_client_request_id']))
    generic = await admin.post('/api/models/' + model_id + '/test', json={'client_request_id': runtime_key})
    assert generic.status_code == 202, generic.text
    url = '/api/work/tasks/' + t['id']
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url='http://test',
        cookies=admin.cookies,
    ) as observer:
        response = await observer.post(url + '/runs', json=turn(t, a))
    assert response.status_code == 409, response.text
    assert response.json()['error']['code'] == 'idempotency_conflict'
    current = (await admin.get(url)).json()
    assert current['version'] == 1 and current['current_run_id'] is None and len(current['messages']) == 1
    assert provider.requests == []
    # Conflicts are resolved before model admission, even if the model is disabled later.
    await admin.patch('/api/models/' + model_id, json={'enabled': False})
    assert (await admin.post(url + '/runs', json=turn(t, a))).status_code == 409
    async with app.state.container.database.sessions() as session:
        for row in (Run, Invocation, Outbox):
            assert await session.scalar(select(func.count()).select_from(row)) == 1


@pytest.mark.asyncio
async def test_cross_caller_work_replay_precedes_caller_runtime_key_conflict(
    admin: httpx.AsyncClient, model_id: str, app: object
) -> None:
    from uuid import uuid5

    async with member_client(app) as member:
        a = await published(member, model_id)
        t = await task(member, a)
        url = '/api/work/tasks/' + t['id']
        admitted = await member.post(url + '/runs', json=turn(t, a))
        assert admitted.status_code == 202, admitted.text
        runtime_key = str(uuid5(UUID(t['id']), t['create_client_request_id']))
        generic = await admin.post('/api/models/' + model_id + '/test', json={'client_request_id': runtime_key})
        assert generic.status_code == 202, generic.text
        replay = await admin.post(url + '/runs', json=turn(t, a))
        assert replay.status_code == 202 and replay.json()['id'] == admitted.json()['id'], replay.text
        assert replay.json()['id'] != generic.json()['id']


@pytest.mark.asyncio
async def test_delegated_work_locks_caller_runtime_key_before_model_admission(
    admin: httpx.AsyncClient, model_id: str, app: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uuid import uuid5

    async with member_client(app) as member:
        a = await published(member, model_id)
        t = await task(member, a)
    c = app.state.container
    runtime_key = uuid5(UUID(t['id']), t['create_client_request_id'])
    domain_locked, model_entered = asyncio.Event(), asyncio.Event()
    original_lock, original_models = c.runs.lock_request, c.models.resolve_many

    async def observed_lock(session: object, owner_id: UUID, request_id: UUID) -> None:
        await original_lock(session, owner_id, request_id)
        if owner_id == UUID(t['owner_id']):
            domain_locked.set()

    async def observed_models(session: object, model_ids: list[UUID]) -> object:
        model_entered.set()
        return await original_models(session, model_ids)

    monkeypatch.setattr(c.runs, 'lock_request', observed_lock)
    monkeypatch.setattr(c.models, 'resolve_many', observed_models)
    admission = None
    try:
        async with c.database.sessions.begin() as session:
            principal = await c.identity.authenticate(session, admin.cookies['zhiheng_session'])
            await c.runs.lock_request(session, principal.id, runtime_key)
            admission = asyncio.create_task(admin.post('/api/work/tasks/' + t['id'] + '/runs', json=turn(t, a)))
            await asyncio.wait_for(domain_locked.wait(), 2)
            await asyncio.sleep(0.05)
            assert not model_entered.is_set(), 'Work must wait for caller request lock before taking model locks'
            assert not admission.done()
            generic = await c.admission.admit_test(
                session,
                principal,
                model_id=UUID(model_id),
                client_request_id=runtime_key,
                token_limit=c.settings.token_limit,
            )
        response = await asyncio.wait_for(admission, 2)
        assert response.status_code == 409, response.text
        assert (await admin.get('/api/runs/' + str(generic.id))).status_code == 200
    finally:
        if admission is not None and not admission.done():
            admission.cancel()
            await asyncio.gather(admission, return_exceptions=True)
