from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from tests.core.integration.test_agents import config
from tests.core.integration.test_work import published, task
from tests.core.provider import ProviderServer
from zhiheng.modules.work.orm import WorkMember, WorkMessage
from zhiheng.worker.execution import execute_run


def conversation() -> list[dict[str, str]]:
    return [{'role': role, 'content': f'{index}:{role}'} for index in range(22) for role in ('user', 'assistant')] + [
        {'role': 'user', 'content': 'current question'}
    ]


@pytest.mark.parametrize('rounds', [0, 1, 20])
async def test_work_history_rounds_uses_frozen_member_keeps_goal_attribution_and_full_history(
    admin: httpx.AsyncClient, model_id: str, app: FastAPI, provider: ProviderServer, rounds: int
) -> None:
    agent = await published(admin, model_id, system_prompt='FROZEN SYSTEM')
    work = await task(admin, agent, goal='PERSISTENT TASK GOAL')
    edit = await admin.patch(
        f'/api/agents/{agent["id"]}',
        json=config(model_id, application_id=agent['application_id'], expected_version=2),
    )
    assert edit.status_code == 200, edit.text
    assert (await admin.post(f'/api/agents/{agent["id"]}/enable', json={'expected_version': 3})).status_code == 200
    async with app.state.container.database.sessions.begin() as session:
        member = await session.scalar(select(WorkMember).where(WorkMember.task_id == UUID(work['id'])))
        member.config = {**member.config, 'history_rounds': rounds}
        for index, message in enumerate(conversation()[:-1], 2):
            session.add(
                WorkMessage(
                    task_id=UUID(work['id']),
                    client_request_id=uuid4(),
                    sequence=index,
                    role=message['role'],
                    content=message['content'],
                    status='completed',
                    agent_id=UUID(agent['id']),
                    agent_name_snapshot='Frozen author',
                    agent_revision=agent['version'],
                )
            )
    endpoint = f'/api/work/tasks/{work["id"]}'
    admitted = await admin.post(
        endpoint + '/runs',
        json={
            'agent_id': agent['id'],
            'message': 'current question',
            'client_request_id': str(uuid4()),
            'expected_version': work['version'],
        },
    )
    assert admitted.status_code == 202, admitted.text
    await execute_run(app.state.container, UUID(admitted.json()['id']))
    messages = provider.requests[-1]['messages']
    assert messages[0] == {'role': 'system', 'content': 'FROZEN SYSTEM'}
    assert 'PERSISTENT TASK GOAL' in messages[1]['content']
    history = [message for message in messages if message['role'] != 'system']
    assert len(history) == rounds * 2 + 1
    assert history[-1] == {'role': 'user', 'content': 'current question'}
    if rounds:
        assert history[0]['content'] == f'{22 - rounds}:user'
        assert history[-2]['content'] == '[Agent: Frozen author]\n21:assistant'
    assert len((await admin.get(endpoint)).json()['messages']) == 47
