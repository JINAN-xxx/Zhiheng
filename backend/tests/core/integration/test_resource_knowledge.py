"""Resource KB admissions retain native jobs, execution guards and operation projections."""

import asyncio
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select

from tests.core.integration.test_knowledge import VectorFixture, embedding_model
from tests.core.integration.test_resource_files import auth, envelope, post
from tests.core.integration.test_resource_key_scopes import application, issue
from tests.core.knowledge_support import knowledge_models
from tests.core.provider import ProviderServer
from zhiheng.modules.billing.orm import Invocation
from zhiheng.modules.knowledge.orm import KnowledgeJob
from zhiheng.runtime.orm import Run
from zhiheng.worker.execution import execute_run

SCOPES = ['files:read', 'files:write', 'knowledge:read', 'knowledge:write', 'knowledge:query']


async def setup(
    admin: httpx.AsyncClient, provider: ProviderServer, app: FastAPI, *, supplied_key: bool = True
) -> tuple[dict, dict, dict]:
    model = await embedding_model(admin, provider)
    rate = await admin.post(
        f'/api/models/{model}/rate-cards', json={'billing_mode': 'token', 'input_price': '1', 'output_price': '0'}
    )
    assert rate.status_code == 201, rate.text
    owner_app = await application(admin)
    key = await issue(admin, owner_app['id'], scopes=SCOPES, model_ids=await knowledge_models(admin, model))
    c = app.state.container
    c.knowledge_jobs.vectors = c.knowledge_preparation.vectors = VectorFixture()
    created = await admin.post(
        '/api/v1/knowledge-bases',
        headers=auth(key, uuid4() if supplied_key else None),
        json={'name': 'Personal', 'embed_model_id': model},
    )
    assert created.status_code == 201, created.text
    return owner_app, key, created.json()


async def finish(admin: httpx.AsyncClient, app: FastAPI, key: dict, operation: dict) -> dict:
    c = app.state.container
    for _ in range(40):
        native = UUID(operation['native_id'])
        if operation['kind'] == 'knowledge_job':
            await c.knowledge_jobs.tick(native)
        else:
            await c.knowledge_preparation.tick(native)
        async with c.database.sessions() as session:
            pending = list(await session.scalars(select(Run.id).where(Run.status == 'pending')))
        for run_id in pending:
            await execute_run(c, run_id)
        response = await admin.get('/api/v1/operations/' + operation['id'], headers=auth(key))
        assert response.status_code == 200, response.text
        operation = response.json()
        if operation['status'] in {'succeeded', 'partially_succeeded', 'failed', 'cancelled', 'interrupted'}:
            return operation
    raise AssertionError(str(operation))


async def test_resource_knowledge_native_index_query_operations_and_replay(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    owner_app, key, base = await setup(admin, provider, app)
    endpoint = '/api/v1/knowledge-bases/' + base['id']
    file = await post(admin, key)
    external = uuid4()
    body = {'file_ids': [file.json()['id']]}
    responses = await asyncio.gather(
        *(admin.post(endpoint + '/documents', headers=auth(key, external), json=body) for _ in range(3))
    )
    assert {r.status_code for r in responses} == {202}, [r.text for r in responses]
    operation = responses[0].json()
    assert all(r.json()['id'] == operation['id'] for r in responses)
    assert responses[0].headers['location'] == '/api/v1/operations/' + operation['id']
    indexed = await finish(admin, app, key, operation)
    assert indexed['status'] == 'succeeded', str(indexed)
    assert len(provider.requests) == 1
    documents = await admin.get(endpoint + '/documents', headers=auth(key))
    assert len(documents.json()['items']) == 1 and documents.json()['has_more'] is False
    query_key = uuid4()
    queries = await asyncio.gather(
        *(admin.post(endpoint + '/query', headers=auth(key, query_key), json={'query': 'four'}) for _ in range(3))
    )
    assert {query.status_code for query in queries} == {202}, [query.text for query in queries]
    assert len({query.json()['id'] for query in queries}) == 1
    envelope(await admin.post(endpoint + '/query', headers=auth(key, query_key), json={'query': 'changed'}), 409)
    result = await finish(admin, app, key, queries[0].json())
    assert result['status'] == 'succeeded' and result['result']['hits'][0]['content'] == 'four', result
    assert len(provider.requests) == 3
    assert provider.requests[-1]['model'] == 'rerank'
    second = await issue(admin, owner_app['id'], scopes=SCOPES, model_ids=[base['embed_model_id']])
    await admin.patch('/api/models/' + base['embed_model_id'], json={'enabled': False})
    replay = await admin.post(endpoint + '/documents', headers=auth(second, external), json=body)
    assert replay.status_code == 202 and replay.json()['id'] == operation['id'], replay.text
    assert (await admin.get('/api/v1/operations/' + operation['id'], headers=auth(second))).status_code == 200
    async with app.state.container.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(KnowledgeJob)) == 1
        invocations = list(await session.scalars(select(Invocation)))
        assert len(invocations) == 3 and all(row.key_id == UUID(key['id']) for row in invocations)
    other_app = await application(admin)
    other = await issue(admin, other_app['id'], scopes=SCOPES)
    envelope(await admin.get(endpoint, headers=auth(other)), 404)
    envelope(await admin.get('/api/v1/operations/' + operation['id'], headers=auth(other)), 404)
    assert (await admin.get('/api/v1/operations', headers=auth(other))).json()['items'] == []


async def test_resource_knowledge_metadata_key_and_admission_model_allowlist(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    owner_app, key, base = await setup(admin, provider, app)
    metadata = await issue(admin, owner_app['id'], scopes=SCOPES)
    endpoint = '/api/v1/knowledge-bases/' + base['id']
    assert (await admin.get(endpoint, headers=auth(metadata))).status_code == 200
    file = await post(admin, metadata)
    envelope(
        await admin.post(
            endpoint + '/documents', headers=auth(metadata, uuid4()), json={'file_ids': [file.json()['id']]}
        ),
        404,
    )
    envelope(await admin.patch(endpoint, headers=auth(key), json={'name': 'Changed'}), 428)
    envelope(
        await admin.patch(
            endpoint, headers={**auth(key), 'If-Match': '"knowledge-base-999"'}, json={'name': 'Changed'}
        ),
        412,
    )
    changed = await admin.patch(
        endpoint, headers={**auth(key), 'If-Match': '"knowledge-base-1"'}, json={'name': 'Changed'}
    )
    assert changed.status_code == 200 and changed.json()['version'] == 2, changed.text
    folder = await admin.post(endpoint + '/folders', headers=auth(key, uuid4()), json={'name': 'Folder'})
    assert folder.status_code == 201, folder.text
    assert (await admin.get(endpoint + '/folders', headers=auth(key))).json()['items'][0]['id'] == folder.json()['id']
    async with app.state.container.database.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(KnowledgeJob)) == 0


async def test_resource_native_queued_revocation_cancel_and_deleted_base_history(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    _owner_app, key, base = await setup(admin, provider, app)
    endpoint = '/api/v1/knowledge-bases/' + base['id']
    file = await post(admin, key)
    queued = await admin.post(
        endpoint + '/documents', headers=auth(key, uuid4()), json={'file_ids': [file.json()['id']]}
    )
    assert queued.status_code == 202, queued.text
    cancelled = await admin.post('/api/v1/operations/' + queued.json()['id'] + '/cancel', headers=auth(key))
    assert cancelled.status_code == 202 and cancelled.json()['status'] == 'cancel_requested', cancelled.text
    terminal = await finish(admin, app, key, cancelled.json())
    assert terminal['status'] == 'cancelled' and provider.requests == []
    current = await admin.get(endpoint, headers=auth(key))
    deleted = await admin.delete(
        endpoint + '?confirm_references=true', headers={**auth(key, uuid4()), 'If-Match': current.headers['etag']}
    )
    assert deleted.status_code == 202, deleted.text
    complete = await finish(admin, app, key, deleted.json())
    assert complete['status'] == 'succeeded', complete
    assert (await admin.get('/api/v1/operations/' + complete['id'], headers=auth(key))).status_code == 200
    assert (await admin.get(endpoint, headers=auth(key))).status_code == 404


@pytest.mark.parametrize('supplied_key', [True, False])
async def test_resource_complete_folder_rechunk_rebuild_document_delete_routes(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer, supplied_key: bool
) -> None:
    _owner_app, key, base = await setup(admin, provider, app, supplied_key=supplied_key)
    endpoint = '/api/v1/knowledge-bases/' + base['id']
    folder = await admin.post(
        endpoint + '/folders', headers=auth(key, uuid4() if supplied_key else None), json={'name': 'Root'}
    )
    assert folder.status_code == 201, folder.text
    folder_url = endpoint + '/folders/' + folder.json()['id']
    changed = await admin.patch(
        folder_url, headers={**auth(key), 'If-Match': folder.headers['etag']}, json={'name': 'Renamed'}
    )
    assert changed.status_code == 200, changed.text
    child = await admin.post(
        endpoint + '/folders',
        headers=auth(key, uuid4() if supplied_key else None),
        json={'name': 'Child', 'parent_id': folder.json()['id']},
    )
    file = await post(admin, key)
    bad_duplicate = await admin.post(
        endpoint + '/documents',
        headers=auth(key, uuid4() if supplied_key else None),
        json={'file_ids': [file.json()['id'], file.json()['id']]},
    )
    envelope(bad_duplicate, 422)
    imported = await admin.post(
        endpoint + '/documents',
        headers=auth(key, uuid4() if supplied_key else None),
        json={'file_ids': [file.json()['id']], 'folder_id': child.json()['id']},
    )
    assert imported.status_code == 202, imported.text
    assert (await finish(admin, app, key, imported.json()))['status'] == 'succeeded'
    query = await admin.post(
        endpoint + '/query', headers=auth(key, uuid4() if supplied_key else None), json={'query': 'four'}
    )
    assert query.status_code == 202, query.text
    assert (await finish(admin, app, key, query.json()))['status'] == 'succeeded'
    document = (await admin.get(endpoint + '/documents', headers=auth(key))).json()['items'][0]
    rechunked = await admin.post(
        endpoint + '/documents/' + document['id'] + '/rechunk',
        headers={
            **auth(key, uuid4() if supplied_key else None),
            'If-Match': f'"knowledge-document-{document["version"]}"',
        },
        json={'chunk_size': 200, 'chunk_overlap': 20},
    )
    assert rechunked.status_code == 202, rechunked.text
    assert (await finish(admin, app, key, rechunked.json()))['status'] == 'succeeded'
    current = await admin.get(endpoint, headers=auth(key))
    rebuilt = await admin.post(
        endpoint + '/rebuild',
        headers={**auth(key, uuid4() if supplied_key else None), 'If-Match': current.headers['etag']},
    )
    assert rebuilt.status_code == 202, rebuilt.text
    assert (await finish(admin, app, key, rebuilt.json()))['status'] == 'succeeded'
    removed = await admin.delete(
        folder_url, headers={**auth(key, uuid4() if supplied_key else None), 'If-Match': changed.headers['etag']}
    )
    assert removed.status_code == 202, removed.text
    assert (await finish(admin, app, key, removed.json()))['status'] == 'succeeded'
    assert (await admin.get(endpoint + '/folders', headers=auth(key))).json()['items'] == []
    assert (await admin.get(endpoint + '/documents', headers=auth(key))).json()['items'] == []
    another = await admin.post(
        endpoint + '/documents',
        headers=auth(key, uuid4() if supplied_key else None),
        json={'file_ids': [file.json()['id']]},
    )
    assert another.status_code == 202, another.text
    assert (await finish(admin, app, key, another.json()))['status'] == 'succeeded'
    document = (await admin.get(endpoint + '/documents', headers=auth(key))).json()['items'][0]
    removed = await admin.delete(
        endpoint + '/documents/' + document['id'],
        headers={
            **auth(key, uuid4() if supplied_key else None),
            'If-Match': f'"knowledge-document-{document["version"]}"',
        },
    )
    assert removed.status_code == 202, removed.text
    assert (await finish(admin, app, key, removed.json()))['status'] == 'succeeded'

    current = await admin.get(endpoint, headers=auth(key))
    deleted = await admin.delete(
        endpoint + '?confirm_references=true',
        headers={**auth(key, uuid4() if supplied_key else None), 'If-Match': current.headers['etag']},
    )
    assert deleted.status_code == 202, deleted.text
    assert (await finish(admin, app, key, deleted.json()))['status'] == 'succeeded'


async def test_resource_validation_uses_native_text_constraints(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    _owner_app, key, base = await setup(admin, provider, app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://test'
    ) as client:
        for name in (' ', '\x00'):
            response = await client.post(
                '/api/v1/knowledge-bases',
                headers=auth(key, uuid4()),
                json={'name': name, 'embed_model_id': base['embed_model_id']},
            )
            envelope(response, 422)
        endpoint = '/api/v1/knowledge-bases/' + base['id']
        envelope(
            await client.patch(endpoint, headers={**auth(key), 'If-Match': '"knowledge-base-1"'}, json={'name': ' '}),
            422,
        )
        envelope(await client.post(endpoint + '/query', headers=auth(key, uuid4()), json={'query': ' '}), 422)


async def test_resource_metadata_without_key_keeps_identical_submissions_distinct(
    admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer
) -> None:
    _owner_app, key, base = await setup(admin, provider, app)
    body = {'name': 'Another', 'embed_model_id': base['embed_model_id']}
    responses = [await admin.post('/api/v1/knowledge-bases', headers=auth(key), json=body) for _ in range(2)]
    assert [response.status_code for response in responses] == [201, 201], [r.text for r in responses]
    assert responses[0].json()['id'] != responses[1].json()['id']
