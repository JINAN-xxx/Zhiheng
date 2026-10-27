from uuid import uuid4

import httpx
from fastapi import FastAPI

from tests.core.integration.test_knowledge import embedding_model
from tests.core.provider import ProviderServer


async def test_graph_configuration_cas_unavailable_and_capability_rejection(
    admin: httpx.AsyncClient, app: FastAPI, model_id: str, provider: ProviderServer
) -> None:
    embedding = await embedding_model(admin, provider)
    base = (await admin.post('/api/knowledge/bases', json={'name': 'Graph', 'embed_model_id': embedding})).json()
    path = f'/api/knowledge/bases/{base["id"]}/graph'
    before = await admin.get(path + '/config')
    assert before.status_code == 200, before.text
    assert before.json()['config'] is None and before.json()['available'] is False
    body = {'expected_version': 0, 'llm_model_id': model_id}
    configured = await admin.patch(path + '/config', json=body)
    assert configured.status_code == 200, configured.text
    assert configured.json()['config']['version'] == 1
    assert configured.json()['supported_reasoning_strategies'] == ['auto', 'default']
    assert (await admin.patch(path + '/config', json=body)).status_code == 409
    assert (
        await admin.patch(path + '/config', json={**body, 'expected_version': 1, 'reasoning_strategy': 'low'})
    ).status_code == 422
    assert (
        await admin.post(path + '/build', json={'expected_version': 1, 'client_request_id': str(uuid4())})
    ).status_code == 503
    assert provider.requests == []
