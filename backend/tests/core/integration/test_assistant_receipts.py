from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from tests.core.integration.test_assistant_langchain import assistant
from tests.core.permission_support import account, grants

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI


async def test_assistant_receipt_reports_acceptance_without_input_and_enforces_owner(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str
) -> None:
    agent = await assistant(admin, model_id)
    conversations = f'/api/agents/{agent["id"]}/conversations'
    request_id, create_id = str(uuid4()), str(uuid4())
    receipt = f'{conversations}/submissions/{request_id}'
    params = {'create_id': create_id}
    assert (await admin.get(receipt, params=params)).json() == {'conversation_id': None, 'accepted': False}

    created = await admin.post(conversations, json={'client_request_id': create_id})
    assert created.status_code == 201, created.text
    cid = created.json()['id']
    assert (await admin.get(receipt, params=params)).json() == {'conversation_id': cid, 'accepted': False}
    send = await admin.post(
        f'{conversations}/{cid}/runs',
        json={'client_request_id': request_id, 'message': 'original confidential message'},
    )
    assert send.status_code == 202, send.text
    for target in (params, {**params, 'conversation_id': cid}):
        result = await admin.get(receipt, params=target)
        assert result.status_code == 200, result.text
        assert result.headers['cache-control'] == 'no-store'
        assert result.json() == {'conversation_id': cid, 'accepted': True}

    other = await admin.post(conversations, json={'client_request_id': str(uuid4())})
    assert other.status_code == 201, other.text
    other_id = other.json()['id']
    assert (await admin.get(receipt, params={**params, 'conversation_id': other_id})).json() == {
        'conversation_id': other_id,
        'accepted': False,
    }
    assert (await admin.get(receipt, params={**params, 'conversation_id': cid, 'plaza': 'true'})).status_code == 404
    async with account(admin, app, *grants('platform:agents')) as (other_user, _, _):
        assert (await other_user.get(receipt, params={**params, 'conversation_id': cid})).status_code == 404
        assert (await other_user.get(receipt, params=params)).json() == {'conversation_id': None, 'accepted': False}
