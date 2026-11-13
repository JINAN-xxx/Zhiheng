"""Exact graph test role ownership and optional immutable command evidence."""

import json
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import httpx
from fastapi import FastAPI
from sqlalchemy.engine import make_url

from tests.core.integration.test_knowledge import embedding_model
from tests.core.integration.test_knowledge_failures import finish_job, source
from tests.core.provider import ProviderServer
from tests.core.resources import resource
from zhiheng.bootstrap.settings import Settings
from zhiheng.modules.knowledge.graph.contracts import GraphConfig, GraphFrozen
from zhiheng.modules.knowledge.orm import KnowledgeBase
from zhiheng.modules.models.public import ModelBinding


def evidence(name: str, value: object) -> None:
    directory = os.getenv('ZHIHENG_TEST_EVIDENCE_DIR')
    if directory:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        (path / (name + '.json')).write_text(json.dumps(value, indent=2, default=str) + '\n')


@asynccontextmanager
async def graph_role(settings: Settings) -> AsyncIterator[tuple[asyncpg.Connection, str, str]]:
    url = make_url(settings.database_url.get_secret_value().replace('postgresql+asyncpg:', 'postgresql:'))
    assert url.database.startswith('core_test_')
    pg = await asyncpg.connect(url.render_as_string(hide_password=False))
    role, password = 'graph_owner_' + uuid4().hex, secrets.token_urlsafe(24)
    resource('intent', 'postgres_role', role, database=url.database)
    await pg.execute(f'CREATE ROLE "{role}" LOGIN PASSWORD \'{password}\'')
    resource('created', 'postgres_role', role, database=url.database)
    try:
        await pg.execute(f'REVOKE CREATE,TEMPORARY ON DATABASE "{url.database}" FROM PUBLIC')
        await pg.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
        await pg.execute(f'GRANT USAGE ON SCHEMA knowledge_graph_sdk TO "{role}"')
        await pg.execute(f'GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA knowledge_graph_sdk TO "{role}"')
        yield pg, url.set(username=role, password=password).render_as_string(hide_password=False), role
    finally:
        await pg.execute(f'DROP OWNED BY "{role}"')
        await pg.execute(f'DROP ROLE "{role}"')
        resource('deleted', 'postgres_role', role, database=url.database)
        await pg.close()


def empty_frozen() -> GraphFrozen:
    binding = ModelBinding(model_id=uuid4(), connection_id=uuid4(), upstream_model='local', max_output_tokens=2048)
    return GraphFrozen(
        base_id=uuid4(),
        base_owner_id=UUID(int=0),
        config=GraphConfig(version=1, llm_model_id=binding.model_id),
        llm_binding=binding.model_dump(mode='json'),
        embedding_binding=binding.model_copy(update={'model_type': 'embedding'}).model_dump(mode='json'),
        index_revision=uuid4(),
        dimension=2,
        base_version=1,
        base_acl_version=1,
        sources=[],
        chunks=[],
        content_fingerprint='a' * 64,
        fingerprint='a' * 64,
    )


async def graph_base(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, *, legacy: bool = False
) -> dict[str, Any]:
    embed = await embedding_model(admin, provider)
    response = await admin.post('/api/knowledge/bases', json={'name': 'Ownership', 'embed_model_id': embed})
    assert response.status_code == 201, response.text
    base = response.json()
    if legacy:
        async with app.state.container.database.sessions.begin() as session:
            row = await session.get(KnowledgeBase, UUID(base['id']))
            assert row is not None
            row.document_policy = 'legacy'
    file = await source(admin, 'ownership.txt', '甲机构和乙公司合作研究。'.encode())
    response = await admin.post(
        f'/api/knowledge/bases/{base["id"]}/documents',
        json={'file_ids': [file['id']], 'client_request_id': str(uuid4())},
    )
    assert response.status_code == 202, response.text
    assert (await finish_job(admin, app, response.json()))['status'] == 'completed'
    response = await admin.post(
        '/api/models/connections',
        json={
            'name': 'Ownership graph',
            'base_url': f'http://127.0.0.1:{provider.server_port}/v1',
            'api_key': 'local',
        },
    )
    assert response.status_code == 201, response.text
    response = await admin.post(
        '/api/models',
        json={
            'name': 'Ownership graph',
            'connection_id': response.json()['id'],
            'upstream_model': 'graph',
            'max_output_tokens': 4096,
        },
    )
    assert response.status_code == 201, response.text
    chat = response.json()['id']
    for model in (embed, chat):
        response = await admin.post(
            f'/api/models/{model}/rate-cards',
            json={
                'billing_mode': 'token',
                'input_price': '0.000001',
                'output_price': '0.000002',
            },
        )
        assert response.status_code == 201, response.text
    response = await admin.patch(
        f'/api/knowledge/bases/{base["id"]}/graph/config',
        json={
            'expected_version': 0,
            'llm_model_id': chat,
            'max_output_tokens': 2048,
            'concurrency': 1,
        },
    )
    assert response.status_code == 200, response.text
    return base
