from uuid import uuid4

import httpx
from fastapi import FastAPI

from tests.core.integration.test_assistant_langchain import assistant
from tests.core.permission_support import account, grants


async def test_plaza_history_paginates_owned_conversations_after_unpublishing(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    agent = await assistant(admin, model_id)
    base = f'/api/agents/{agent["id"]}'
    published = await admin.put(f'{base}/plaza', json={'expected_version': agent['version']})
    assert published.status_code == 200, published.text

    endpoints = f'{base}/conversations'
    history = '/api/agents/plaza/history'

    async def create(client: httpx.AsyncClient, *, plaza: bool) -> str:
        result = await client.post(endpoints, params={'plaza': plaza}, json={'client_request_id': str(uuid4())})
        assert result.status_code == 201, result.text
        return str(result.json()['id'])

    admin_private = await create(admin, plaza=False)
    admin_plaza = await create(admin, plaza=True)
    async with account(admin, app, *grants('platform:agents', 'use')) as (member, _, _):
        mine = [await create(member, plaza=True) for _ in range(14)]
        removed = await admin.request(
            'DELETE', f'{base}/plaza', json={'expected_version': published.json()['plaza_version']}
        )
        assert removed.status_code == 204 and not removed.content
        assert not (await member.get('/api/agents/plaza')).json()['items']

        first = await member.get(history)
        assert first.status_code == 200, first.text
        first_ids = [item['id'] for item in first.json()]
        assert first_ids == list(reversed(mine[2:]))
        second = await member.get(history, params={'before': first_ids[-1]})
        assert second.status_code == 200, second.text
        assert [item['id'] for item in second.json()] == list(reversed(mine[:2]))
        assert (await member.get(history, params={'before': mine[0]})).json() == []
        assert (await member.get(f'{endpoints}/{mine[0]}')).status_code == 200
        assert (await member.get(history, params={'before': admin_plaza})).status_code == 404
        assert (await admin.get(history, params={'before': admin_private})).status_code == 404
        assert [item['id'] for item in (await admin.get(history)).json()] == [admin_plaza]
