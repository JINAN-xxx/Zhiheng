from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from sqlalchemy import select

from tests.core.integration.test_assistant_langchain import assistant
from tests.core.integration.test_knowledge_retrieval_v2 import ready
from tests.core.permission_support import account, grants
from tests.core.provider import ProviderServer
from zhiheng.modules.agents.orm import AgentRunAudit
from zhiheng.runtime.orm import Run
from zhiheng.worker.execution import execute_run


async def test_department_assistant_delegates_retrieval_without_lending_library_access(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    knowledge, _, _, vectors = await ready(admin, app, provider)
    kb = knowledge['id']
    dept = await admin.post('/api/admin/departments', json={'name': '制度试点'})
    assert dept.status_code == 201, dept.text
    department = dept.json()['id']
    agent = await assistant(admin, model_id, knowledge_base_ids=[kb])
    base = f'/api/agents/{agent["id"]}'
    publish = {'expected_version': agent['version'], 'department_id': department}
    without_consent = await admin.put(f'{base}/plaza', json=publish)
    assert without_consent.json()['error']['code'] == 'assistant_plaza_knowledge_consent'
    released = await admin.put(f'{base}/plaza', json={**publish, 'allow_knowledge': True})
    assert released.status_code == 200, released.text
    permissions = grants('platform:agents', 'use')
    async with account(admin, app, *permissions) as (member, _, user):
        assert not (await member.get('/api/agents/plaza')).json()['items']
        request = {'client_request_id': str(uuid4())}
        assert (await member.post(f'{base}/conversations?plaza=true', json=request)).status_code == 404
        update = await admin.patch(
            f'/api/admin/users/{user["id"]}',
            json={'department_id': department, 'expected_version': user['version']},
        )
        assert update.status_code == 200, update.text
        assert (await member.get(f'/api/knowledge/bases/{kb}')).status_code in {403, 404}
        choices = (await member.get('/api/agents/plaza')).json()['items']
        assert choices[0]['id'] == agent['id'] and choices[0]['can_start']
        assert 'knowledge_base_ids' not in choices[0]
        chat = await member.post(f'{base}/conversations?plaza=true', json=request)
        assert chat.status_code == 201, chat.text
        path = f'{base}/conversations/{chat.json()["id"]}'
        intent = await member.post(
            f'{path}/runs', json={'client_request_id': str(uuid4()), 'message': '项目编号是什么'}
        )
        assert intent.status_code == 202, intent.text
        preparation = intent.json()['id']
        container = app.state.container
        for _ in range(12):
            await container.knowledge_preparation.tick(UUID(preparation))
            async with container.database.sessions() as session:
                queued = list(await session.scalars(select(Run).where(Run.status == 'pending')))
            for run in queued:
                await execute_run(container, run.id)
            if any(run.resource_type == 'agent_chat' for run in queued):
                break
        assert vectors.searches
        result = await member.get(path)
        assert result.status_code == 200, result.text
        turn = result.json()['turns'][0]
        assert turn['run']['status'] == 'completed', turn
        assert turn['sources'] == []
        async with container.database.sessions() as session:
            audit = await session.get(AgentRunAudit, UUID(turn['run']['id']))
            run = await session.get(Run, UUID(turn['run']['id']))
            assert audit and audit.knowledge and run and str(run.billing_owner_id) == user['id']
        changed = await admin.patch(
            base, json={
                'name': agent['name'], 'model_id': model_id, 'max_tokens': 32,
                'expected_version': released.json()['version']
            }
        )
        assert changed.status_code == 200, changed.text
        replacement = await admin.put(
            f'{base}/plaza', json={'expected_version': changed.json()['version'], 'department_id': department}
        )
        assert replacement.status_code == 200, replacement.text
        revoked = await member.post(
            f'{path}/runs', json={'client_request_id': str(uuid4()), 'message': '再讲一次'}
        )
        assert revoked.status_code == 409, revoked.text
        assert revoked.json()['error']['code'] == 'assistant_history_source_unavailable'
