from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from sqlalchemy import select

from tests.core.integration.test_assistant_langchain import assistant, execute, send
from tests.core.permission_support import account, grants
from tests.core.provider import ProviderServer
from zhiheng.modules.agents.orm import AgentRunAudit
from zhiheng.runtime.orm import Run


async def test_plaza_preserves_published_version_and_caller_boundaries(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    agent = await assistant(admin, model_id, name='Published name', system_prompt='PUBLISHED PROMPT')
    base = f'/api/agents/{agent["id"]}'
    detail = f'/api/agents/plaza/{agent["id"]}'
    assert not (await admin.get('/api/agents/plaza')).json()['items']
    assert (await admin.get(detail)).status_code == 404
    published = await admin.put(f'{base}/plaza', json={'expected_version': agent['version']})
    assert published.status_code == 200, published.text
    changed = await admin.patch(
        base,
        json={
            'name': 'Unpublished draft',
            'model_id': model_id,
            'system_prompt': 'PRIVATE DRAFT',
            'max_tokens': 32,
            'expected_version': published.json()['version'],
        },
    )
    assert changed.status_code == 200, changed.text
    assert (await admin.put(f'{base}/plaza', json={'expected_version': agent['version']})).status_code == 409

    permissions = grants('platform:agents', 'use', 'publish', 'update')
    async with account(admin, app, *permissions) as (member, _, user):
        directory = await member.get('/api/agents/plaza', params={'search': 'Published'})
        assert directory.status_code == 200, directory.text
        public = directory.json()['items']
        assert public == [
            {
                'id': agent['id'],
                'name': 'Published name',
                'description': '',
                'welcome_message': '',
                'plaza_version': published.json()['plaza_version'],
                'can_start': True,
            }
        ]
        assert (await member.get(detail)).json() == public[0]
        assert (await member.get(base)).status_code == 404
        assert (await member.get('/api/agents')).json()['items'] == []
        assert (
            await member.put(f'{base}/plaza', json={'expected_version': changed.json()['version']})
        ).status_code == 403
        body = {'client_request_id': str(uuid4())}
        assert (await member.post(f'{base}/conversations', json=body)).status_code == 404
        response = await member.post(f'{base}/conversations?plaza=true', json=body)
        assert response.status_code == 201, response.text
        cid = response.json()['id']
        assert response.json()['name'] == 'Published name'
        assert (await member.get(f'{base}/conversations')).json() == []
        assert (await admin.get(f'{base}/conversations/{cid}')).status_code == 404

        runs = f'{base}/conversations/{cid}/runs'
        run = await member.post(runs, json={'client_request_id': str(uuid4()), 'message': 'Hi'})
        assert run.status_code == 202, run.text
        await execute(app, run.json()['id'])
        assert provider.requests[-1]['messages'][0]['content'] == 'PUBLISHED PROMPT'
        async with app.state.container.database.sessions() as session:
            stored = await session.scalar(select(Run).where(Run.id == UUID(run.json()['id'])))
            assert stored is not None and str(stored.owner_id) == str(stored.billing_owner_id) == user['id']

        republished = await admin.put(f'{base}/plaza', json={'expected_version': changed.json()['version']})
        assert republished.status_code == 200, republished.text
        assert (await member.get(detail)).json()['name'] == 'Unpublished draft'
        removed = await admin.request(
            'DELETE', f'{base}/plaza', json={'expected_version': republished.json()['plaza_version']}
        )
        assert removed.status_code == 204 and not removed.content
        assert (await member.get(detail)).status_code == 404
        assert (await member.get('/api/agents/plaza/history')).json()[0]['id'] == cid
        assert (await member.get(f'{base}/conversations/{cid}')).status_code == 200
        assert (
            await member.post(runs, json={'client_request_id': str(uuid4()), 'message': 'Again'})
        ).status_code == 404
        logs = (
            await admin.get('/api/admin/logs', params={'kind': 'operation', 'search': '广场助手'})
        ).json()['items']
        assert [row['action'] for row in logs if row['resource_id'] == agent['id']] == [
            'agent.plaza.withdraw', 'agent.plaza.publish', 'agent.plaza.publish',
        ]


async def test_legacy_enable_permission_does_not_grant_plaza_mutation(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    async with account(admin, app, *grants('platform:agents', 'create', 'publish')) as (member, _, _):
        agent = await assistant(member, model_id)
        base = f'/api/agents/{agent["id"]}'
        version = {'expected_version': agent['version']}
        assert (await member.put(f'{base}/plaza', json=version)).status_code == 403
        assert (await member.request('DELETE', f'{base}/plaza', json=version)).status_code == 403
        disabled = await member.post(f'{base}/disable', json=version)
        assert disabled.status_code == 200 and disabled.json()['status'] == 'disabled'


async def test_delegated_plaza_permission_owns_publication_and_admin_can_only_take_it_down(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    admin_agent = await assistant(admin, model_id, name='Admin assistant')
    admin_base = f'/api/agents/{admin_agent["id"]}'
    async with account(admin, app, *grants('platform:agents', 'create', 'update', 'publish', 'plaza_publish')) as (
        member,
        _,
        _,
    ):
        agent = await assistant(member, model_id, name='Delegated assistant')
        base = f'/api/agents/{agent["id"]}'
        detail = f'/api/agents/plaza/{agent["id"]}'
        assert (await member.put(f'{admin_base}/plaza', json={'expected_version': admin_agent['version']})).status_code == 404
        published = await member.put(f'{base}/plaza', json={'expected_version': agent['version']})
        assert published.status_code == 200, published.text
        old_version = published.json()['plaza_version']
        async with account(admin, app, *grants('platform:agents', 'plaza_publish')) as (other, _, _):
            assert (await other.request('DELETE', f'{base}/plaza', json={'expected_version': old_version})).status_code == 404
            assert (await other.put(f'{base}/plaza', json={'expected_version': old_version})).status_code == 404
        assert (await admin.get(base)).status_code == 404
        assert (await admin.put(f'{base}/plaza', json={'expected_version': published.json()['version']})).status_code == 404

        new = await member.put(f'{base}/plaza', json={'expected_version': published.json()['version']})
        assert new.status_code == 200, new.text
        revision = new.json()['plaza_version']
        assert (await admin.get(detail)).json()['plaza_version'] == revision
        stale = await admin.request('DELETE', f'{base}/plaza', json={'expected_version': old_version})
        assert stale.status_code == 409
        assert stale.json()['error']['code'] == 'assistant_plaza_version_conflict'

        disabled = await member.post(f'{base}/disable', json={'expected_version': new.json()['version']})
        assert disabled.status_code == 200 and disabled.json()['plaza_version'] == revision
        assert (await admin.get(detail)).status_code == 404
        resumed = await member.post(f'{base}/enable', json={'expected_version': disabled.json()['version']})
        assert resumed.status_code == 200 and (await admin.get(detail)).json()['plaza_version'] == revision

        removed = await admin.request('DELETE', f'{base}/plaza', json={'expected_version': revision})
        assert removed.status_code == 204 and not removed.content
        current = (await member.get(base)).json()
        assert current['status'] == 'enabled' and current['plaza_version'] is None
        assert current['system_prompt'] == agent['system_prompt']
        republished = await member.put(f'{base}/plaza', json={'expected_version': current['version']})
        assert republished.status_code == 200, republished.text
        removed = await member.request(
            'DELETE', f'{base}/plaza', json={'expected_version': republished.json()['plaza_version']}
        )
        assert removed.status_code == 204 and not removed.content


async def test_plaza_display_changes_do_not_invalidate_queued_turns(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    agent = await assistant(admin, model_id, name='Original')
    base = f'/api/agents/{agent["id"]}'

    async def publish(version: int) -> dict:
        response = await admin.put(f'{base}/plaza', json={'expected_version': version})
        assert response.status_code == 200, response.text
        return response.json()

    async def republish(version: int, *, prompt: str) -> dict:
        response = await admin.patch(
            base,
            json={
                'name': 'Updated',
                'description': 'New description',
                'model_id': model_id,
                'system_prompt': prompt,
                'max_tokens': 32,
                'expected_version': version,
            },
        )
        assert response.status_code == 200, response.text
        return await publish(response.json()['version'])

    first_version = await publish(agent['version'])
    conversation = await admin.post(f'{base}/conversations?plaza=true', json={'client_request_id': str(uuid4())})
    assert conversation.status_code == 201, conversation.text
    cid = conversation.json()['id']
    first = await send(admin, agent['id'], cid, 'First')
    display_update = await republish(first_version['version'], prompt='ORIGINAL SYSTEM')
    assert (await admin.get(f'{base}/conversations/{cid}')).json()['conversation']['name'] == 'Original'
    await execute(app, first['id'])
    assert provider.requests[-1]['messages'][0]['content'] == 'ORIGINAL SYSTEM'
    async with app.state.container.database.sessions() as session:
        audit = await session.get(AgentRunAudit, UUID(first['id']))
        assert audit is not None and audit.execution is not None
        assert audit.execution['plaza_version'] == first_version['plaza_version']

    second = await send(admin, agent['id'], cid, 'Second')
    await republish(display_update['version'], prompt='CHANGED SYSTEM')
    requests = len(provider.requests)
    await execute(app, second['id'])
    assert len(provider.requests) == requests
    result = (await admin.get(f'{base}/conversations/{cid}')).json()['turns'][-1]['run']
    assert result['error']['code'] == 'assistant_configuration_changed'
