"""Independent clients and real persisted inputs for the authorization audit matrix."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
from fastapi import FastAPI


@asynccontextmanager
async def identity_client(app: FastAPI, *, admin: bool = False) -> AsyncIterator[httpx.AsyncClient]:
    username = ('audit-admin-' if admin else 'audit-member-') + uuid4().hex
    async with app.state.container.database.sessions.begin() as session:
        await app.state.container.accounts.create_user(
            session, username=username, password='platform-test-password', is_admin=admin
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='http://test', headers={'content-type': 'application/json'}
    ) as client:
        response = await client.post(
            '/api/auth/login', json={'username': username, 'password': 'platform-test-password'}
        )
        assert response.status_code == 200, response.text
        yield client


def ids(value: object) -> set[str]:
    """Extract resource identifiers from a paged result or a bare list."""
    if isinstance(value, dict):
        value = value.get('items', [])
    assert isinstance(value, list)
    return {str(item['id']) for item in value}


async def graph_inputs(owner: httpx.AsyncClient, app: FastAPI, provider: object, model_id: str) -> tuple[dict, dict]:
    """Admit a real frozen graph job without starting the optional graph SDK process."""
    from uuid import UUID

    from tests.core.integration.test_knowledge_failures import ready_base
    from zhiheng.modules.knowledge.public import GraphBuildRequest

    base = await ready_base(owner, app, provider)
    response = await owner.patch(
        f'/api/knowledge/bases/{base["id"]}/graph/config',
        json={'expected_version': 0, 'llm_model_id': model_id, 'max_output_tokens': 1024},
    )
    assert response.status_code == 200, response.text
    container = app.state.container
    user_id = UUID((await owner.get('/api/auth/me')).json()['id'])
    async with container.database.sessions.begin() as session:
        principal = await container.identity.principal_for_user(session, user_id)
        job = await container.graph.admit(
            session,
            principal,
            UUID(base['id']),
            GraphBuildRequest(client_request_id=uuid4(), expected_version=1),
            'build',
        )
    return base, job.model_dump(mode='json')
