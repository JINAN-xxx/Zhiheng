import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from plugins.ai.domain.model_runtime import ModelEmbeddingRequest, ModelEmbeddingResponse, ModelEmbeddingVector
from plugins.ai.knowledge import graph as graph_module
from plugins.ai.knowledge import models as knowledge_models
from plugins.ai.knowledge import stores
from plugins.ai.knowledge.models import (
    ChunkParameters,
    DatabaseCreate,
    GraphBuildRequest,
    KnowledgeError,
    QueryParameters,
)
from plugins.ai.knowledge.runtime import KnowledgeRuntime
from plugins.ai.service.model_service import ModelService

MODEL_ID = 'mdl_423e4567e89b42d3a456426614174444'
TEST_OWNER_ID = 7
EXPECTED_MAX_PASSAGES = 8
EXPECTED_BACKFILL_DOCUMENTS = 2
EXPECTED_RETRY_ATTEMPTS = 2
EXPECTED_MAX_CONTEXT_BYTES = 24000


class Models:
    def __init__(self) -> None:
        self.binding = 'binding-v1'
        self.disabled = False
        self.calls = []
        self.embedding_requests = []

    async def resolve_model(self, model_id: str, expected_type: str | None = None) -> SimpleNamespace:
        if self.disabled:
            raise RuntimeError('private runtime diagnostic')
        self.calls.append((model_id, expected_type))
        return SimpleNamespace(
            platform_model_id=model_id,
            model_type=expected_type,
            binding_id=self.binding,
            engine_model_snapshot='remote-model',
            default_parameters={},
        )

    async def embed(self, model_id: str, inputs: Sequence[str]) -> SimpleNamespace:
        self.embedding_requests.append((model_id, list(inputs)))
        await self.resolve_model(model_id, 'embedding')
        return SimpleNamespace(
            response=SimpleNamespace(
                vectors=[SimpleNamespace(index=i, values=[1.0, 0.0, 0.5]) for i in range(len(inputs))]
            )
        )


class Store:
    def __init__(self) -> None:
        self.rows = {}
        self.fail = False
        self.query_error = False
        self.extra = []

    async def upsert(
        self, db_id: str, chunks: list[dict[str, Any]], vectors: list[list[float]], spec: dict[str, Any]
    ) -> None:
        self.rows.setdefault(db_id, {}).update({chunk['id']: dict(chunk) for chunk in chunks})
        if self.fail:
            raise RuntimeError('private vector credentials')

    async def query(self, db_id: str, vector: list[float], top_k: int, spec: dict[str, Any]) -> list[dict[str, Any]]:
        if self.query_error:
            raise RuntimeError('private vector credentials')
        return [dict(row, score=0.9) for row in self.rows.get(db_id, {}).values()][:top_k] + self.extra

    async def delete_chunks(self, db_id: str, chunk_ids: Sequence[str]) -> None:
        for chunk_id in chunk_ids:
            self.rows.get(db_id, {}).pop(chunk_id, None)

    async def delete_database(self, db_id: str) -> None:
        self.rows.pop(db_id, None)

    async def close(self) -> None:
        pass


RuntimeSetup = tuple[KnowledgeRuntime, Models, Store, set[int]]


def test_new_knowledge_bases_default_to_milvus_and_reject_other_storage_types() -> None:
    payload = DatabaseCreate(database_name='资料', embed_model_id=MODEL_ID)
    assert payload.kb_type == 'milvus'
    for kind in ('chroma', 'lightrag', 'unknown'):
        with pytest.raises(ValidationError):
            DatabaseCreate(database_name='资料', embed_model_id=MODEL_ID, kb_type=kind)


@pytest.fixture
def setup_runtime(tmp_path: Path) -> RuntimeSetup:
    models, store, owners = Models(), Store(), {7}

    @asynccontextmanager
    async def model_factory() -> AsyncIterator[Models]:
        yield models

    async def authorized(owner: int) -> bool:
        return owner in owners

    runtime = KnowledgeRuntime(tmp_path, model_factory, authorized, store_factory=lambda _: store)
    return runtime, models, store, owners


@pytest.mark.asyncio
@pytest.mark.parametrize('milvus_available', [False, True])
@pytest.mark.parametrize('graph_available', [False, True])
async def test_creation_capabilities_offer_only_milvus_with_independent_graph_support(
    setup_runtime: RuntimeSetup, monkeypatch: pytest.MonkeyPatch, milvus_available: bool, graph_available: bool
) -> None:
    runtime, _, _, _ = setup_runtime
    monkeypatch.setattr(
        stores,
        'available_types',
        lambda: {
            'chroma': {'available': True},
            'milvus': {'available': milvus_available},
            'lightrag': {'available': graph_available},
        },
    )
    assert await runtime.types() == {
        'kb_types': {'milvus': {'available': milvus_available}},
        'graph_available': graph_available,
    }


@pytest.mark.asyncio
async def test_default_creation_persists_milvus_storage(setup_runtime: RuntimeSetup) -> None:
    runtime, _, store, _ = setup_runtime
    selected_types = []

    def select_store(kind: str) -> Store:
        selected_types.append(kind)
        return store

    runtime._store_factory = select_store
    database = await runtime.create_database(DatabaseCreate(database_name='资料', embed_model_id=MODEL_ID))
    assert selected_types == ['milvus']
    assert database['kb_type'] == 'milvus'
    assert (await runtime.get_database(database['db_id']))['kb_type'] == 'milvus'


async def create(runtime: KnowledgeRuntime, name: str = '资料') -> dict[str, Any]:
    return await runtime.create_database(DatabaseCreate(database_name=name, description='', embed_model_id=MODEL_ID))


async def upload(
    runtime: KnowledgeRuntime,
    db_id: str,
    content: bytes = b'Employees submit invoices and approval forms.',
    filename: str = 'guide.txt',
) -> dict[str, Any]:
    async def chunks() -> AsyncIterator[bytes]:
        yield content

    return await runtime.upload(db_id, filename, chunks())


async def index(
    runtime: KnowledgeRuntime, db_id: str, content: bytes = b'Employees submit invoices and approval forms.'
) -> str:
    uploaded = await upload(runtime, db_id, content)
    result = await runtime.add_documents(db_id, [uploaded['upload_id']], ChunkParameters())
    assert result['status'] == 'success'
    return result['items'][0]['id']


@pytest.mark.asyncio
async def test_metadata_survives_restart_without_runtime_credentials(setup_runtime: RuntimeSetup) -> None:
    runtime, models, store, _ = setup_runtime
    db = await create(runtime)
    document_id = await index(runtime, db['db_id'])
    reopened = KnowledgeRuntime(
        runtime.data_dir, runtime.model_service_factory, runtime.owner_authorizer, store_factory=lambda _: store
    )
    assert (await reopened.list_databases())['databases'][0]['db_id'] == db['db_id']
    passages = await reopened.retrieve_context(owner_user_id=7, knowledge_base_ids=[db['db_id']], query='invoices')
    assert passages[0].source_id == document_id
    assert passages[0].knowledge_base_id == db['db_id']
    assert passages[0].title == 'guide.txt'
    persisted = (runtime.data_dir / 'metadata.json').read_text()
    for forbidden in ('api_key', 'base_url', 'remote-model', 'private runtime', 'binding-v1'):
        assert forbidden not in persisted
    assert MODEL_ID in persisted
    assert models.calls


@pytest.mark.asyncio
async def test_owner_revocation_and_missing_binding_fail_whole_request(setup_runtime: RuntimeSetup) -> None:
    runtime, _, store, owners = setup_runtime
    db = await create(runtime)
    await index(runtime, db['db_id'])
    owners.clear()
    with pytest.raises(KnowledgeError, match='权限'):
        await runtime.retrieve_context(owner_user_id=7, knowledge_base_ids=[db['db_id']], query='invoice')
    owners.add(7)
    with pytest.raises(KnowledgeError):
        await runtime.retrieve_context(owner_user_id=7, knowledge_base_ids=[db['db_id'], 'kb_missing'], query='invoice')
    assert store.rows


@pytest.mark.asyncio
async def test_upload_is_opaque_bounded_and_cannot_cross_kb_or_folder(setup_runtime: RuntimeSetup) -> None:
    runtime, *_ = setup_runtime
    first, second = await create(runtime), await create(runtime, 'Other')
    for filename in ('../../outside.txt', '/tmp/outside.txt', '..\\outside.txt'):
        with pytest.raises(KnowledgeError):
            await upload(runtime, first['db_id'], filename=filename)
    uploaded = await upload(runtime, first['db_id'])
    assert 'path' not in json.dumps(uploaded)
    with pytest.raises(KnowledgeError):
        await runtime.add_documents(second['db_id'], [uploaded['upload_id']], ChunkParameters())
    with pytest.raises(KnowledgeError):
        await runtime.add_documents(first['db_id'], ['/etc/passwd'], ChunkParameters())
    runtime.max_upload_bytes = 3
    with pytest.raises(KnowledgeError) as caught:
        await upload(runtime, first['db_id'], b'abcd')
    assert caught.value.status_code == HTTPStatus.REQUEST_ENTITY_TOO_LARGE


@pytest.mark.asyncio
async def test_queries_propagate_failure_and_never_recreate_changed_index(setup_runtime: RuntimeSetup) -> None:
    runtime, models, store, _ = setup_runtime
    db = await create(runtime)
    await index(runtime, db['db_id'])
    original = dict(store.rows[db['db_id']])
    models.binding = 'binding-v2'
    with pytest.raises(KnowledgeError) as caught:
        await runtime.retrieve_context(owner_user_id=7, knowledge_base_ids=[db['db_id']], query='invoice')
    assert caught.value.code == 'KNOWLEDGE_REINDEX_REQUIRED'
    assert store.rows[db['db_id']] == original
    models.binding = 'binding-v1'
    store.query_error = True
    with pytest.raises(KnowledgeError) as caught:
        await runtime.retrieve_context(owner_user_id=7, knowledge_base_ids=[db['db_id']], query='invoice')
    assert caught.value.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert 'private' not in json.dumps(caught.value.public_dict())


@pytest.mark.asyncio
async def test_unowned_or_forged_sources_reject_retrieval(setup_runtime: RuntimeSetup) -> None:
    runtime, _, store, _ = setup_runtime
    db = await create(runtime)
    await index(runtime, db['db_id'])
    store.extra = [{'id': 'chunk_foreign', 'doc_id': 'doc_foreign', 'content': 'secret', 'score': 1.0}]
    with pytest.raises(KnowledgeError):
        await runtime.retrieve_context(owner_user_id=7, knowledge_base_ids=[db['db_id']], query='invoice')


@pytest.mark.asyncio
async def test_failed_index_is_explicit_and_not_available_for_context(setup_runtime: RuntimeSetup) -> None:
    runtime, _, store, _ = setup_runtime
    db = await create(runtime)
    uploaded = await upload(runtime, db['db_id'])
    store.fail = True
    result = await runtime.add_documents(db['db_id'], [uploaded['upload_id']], ChunkParameters())
    assert result['status'] == 'failed'
    assert result['items'][0]['status'] == 'failed'
    assert 'private' not in json.dumps(result)
    documents = await runtime.list_documents(db['db_id'])
    assert documents['documents'][0]['status'] == 'failed'


@pytest.mark.asyncio
async def test_folder_and_document_scope_and_recursive_delete(setup_runtime: RuntimeSetup) -> None:
    runtime, *_ = setup_runtime
    first, second = await create(runtime), await create(runtime, 'Other')
    folder = await runtime.create_folder(first['db_id'], 'Folder')
    child = await runtime.create_folder(first['db_id'], 'Child', folder['id'])
    with pytest.raises(KnowledgeError):
        await runtime.rename_folder(second['db_id'], folder['id'], 'Moved')
    doc_id = await index(runtime, first['db_id'])
    with pytest.raises(KnowledgeError):
        await runtime.preview(second['db_id'], doc_id)
    assert 'invoices' in (await runtime.preview(first['db_id'], doc_id))['content']
    await runtime.delete_folder(first['db_id'], folder['id'])
    assert child['id'] not in json.dumps(await runtime.list_folders(first['db_id']))


def test_chunk_parameters_reject_invalid_overlap_and_unknown_model_credentials() -> None:

    with pytest.raises(ValidationError):
        ChunkParameters(chunk_size=100, chunk_overlap=100)
    with pytest.raises(ValidationError):
        DatabaseCreate(database_name='bad', description='', embed_model_id=MODEL_ID, api_key='do-not-persist')


@pytest.mark.asyncio
async def test_multiple_runtime_instances_preserve_each_others_metadata(setup_runtime: RuntimeSetup) -> None:

    runtime, _, store, _ = setup_runtime
    other = KnowledgeRuntime(
        runtime.data_dir, runtime.model_service_factory, runtime.owner_authorizer, store_factory=lambda _: store
    )
    results = await asyncio.gather(*(create(runtime if i % 2 else other, str(i)) for i in range(8)))
    ids = {result['db_id'] for result in results}
    assert {db['db_id'] for db in (await runtime.list_databases())['databases']} == ids


@pytest.mark.asyncio
async def test_index_attempt_is_durable_before_vectors_are_written(setup_runtime: RuntimeSetup) -> None:
    runtime, _, store, _ = setup_runtime
    db = await create(runtime)
    original = store.upsert

    async def checked_upsert(
        db_id: str, chunks: list[dict[str, Any]], vectors: list[list[float]], spec: dict[str, Any]
    ) -> None:
        state = json.loads((runtime.data_dir / 'metadata.json').read_text())
        document = state['documents'][chunks[0]['doc_id']]
        assert document['status'] == 'processing'
        assert {chunk['id'] for chunk in chunks} <= {chunk['id'] for chunk in document['chunks']}
        await original(db_id, chunks, vectors, spec)

    store.upsert = checked_upsert
    await index(runtime, db['db_id'])


@pytest.mark.asyncio
async def test_graph_empty_preview_then_first_index_and_failed_document_cleanup(
    setup_runtime: RuntimeSetup, monkeypatch: pytest.MonkeyPatch
) -> None:

    runtime, models, _, _ = setup_runtime
    monkeypatch.setattr(stores, 'require_graph', lambda: None)
    created = []

    class Graph:
        def __init__(self, directory: Path, database: dict[str, Any], factory: object) -> None:
            self.database = database
            created.append(self)

        async def insert(self, doc_id: str, content: str, title: str) -> None:
            assert self.database['index_spec']

        async def labels(self) -> list[str]:
            return ['Invoice']

        async def delete_document(self, doc_id: str) -> None:
            pass

        async def delete_database(self, documents: Sequence[str]) -> None:
            pass

        async def close(self) -> None:
            pass

    monkeypatch.setattr(graph_module, 'KnowledgeGraph', Graph)
    payload = DatabaseCreate(
        database_name='Graph', embed_model_id=MODEL_ID, llm_model_id=MODEL_ID, enable_knowledge_graph=True
    )
    database = await runtime.create_database(payload)
    assert await runtime.graph_labels(database['db_id']) == {'labels': []}
    assert not created
    await index(runtime, database['db_id'])
    assert (await graph_finished(runtime, database['db_id']))['status'] == 'completed'
    assert await runtime.graph_labels(database['db_id']) == {'labels': ['Invoice']}

    failed_database = await runtime.create_database(payload)
    models.disabled = True
    uploaded = await upload(runtime, failed_database['db_id'])
    failed = await runtime.add_documents(failed_database['db_id'], [uploaded['upload_id']], ChunkParameters())
    assert failed['status'] == 'failed'
    await runtime.delete_document(failed_database['db_id'], failed['items'][0]['id'])
    await runtime.delete_database(failed_database['db_id'])
    assert len(created) == 1


@pytest.mark.asyncio
async def test_retrieval_utf8_budget_and_revoked_models(setup_runtime: RuntimeSetup) -> None:
    runtime, models, _, _ = setup_runtime
    database = await create(runtime)
    uploaded = await upload(runtime, database['db_id'], ('材料证明。' * 6000).encode())
    await runtime.add_documents(database['db_id'], [uploaded['upload_id']], ChunkParameters(chunk_size=4000))
    hits = await runtime.retrieve_context(owner_user_id=7, knowledge_base_ids=[database['db_id']], query='证明')
    assert 1 <= len(hits) <= EXPECTED_MAX_PASSAGES
    assert (
        sum(
            len(value.encode('utf-8'))
            for hit in hits
            for value in (hit.content, hit.source_id, hit.title, hit.knowledge_base_id)
        )
        <= EXPECTED_MAX_CONTEXT_BYTES
    )
    models.disabled = True
    with pytest.raises(KnowledgeError):
        await runtime.retrieve_context(owner_user_id=7, knowledge_base_ids=[database['db_id']], query='证明')


@pytest.mark.asyncio
async def test_real_model_service_accepts_multiline_index_without_altering_passages(tmp_path: Path) -> None:

    recorded = []

    class DB:
        def in_transaction(self) -> bool:
            return False

    class ModelDao:
        @classmethod
        async def get_model_by_id(cls, db: object, model_id: str) -> SimpleNamespace:
            return SimpleNamespace(
                model_id=model_id, lifecycle_status='active', model_type='embedding', default_parameters={}
            )

    class BindingDao:
        @classmethod
        async def get_current_binding(cls, db: object, model_id: str) -> SimpleNamespace:
            return SimpleNamespace(
                binding_status='active', binding_id='binding-1', engine_type='litellm', engine_resource_id='embed-model'
            )

    class Engine:
        async def embed(self, request: ModelEmbeddingRequest) -> ModelEmbeddingResponse:
            recorded.extend(request.inputs)
            return ModelEmbeddingResponse(
                vectors=tuple(ModelEmbeddingVector(index=i, values=(1.0, 0.0, 0.5)) for i in range(len(request.inputs)))
            )

    @asynccontextmanager
    async def factory() -> AsyncIterator[ModelService]:
        yield ModelService(DB(), Engine(), model_dao=ModelDao, binding_dao=BindingDao)

    async def owner_authorizer(user_id: int) -> bool:
        return user_id == TEST_OWNER_ID

    store = Store()
    runtime = KnowledgeRuntime(tmp_path, factory, owner_authorizer, store_factory=lambda _: store)
    database = await create(runtime)
    content = '# 报销说明\n\n发票\t审批单。'
    await index(runtime, database['db_id'], content.encode())
    hits = await runtime.retrieve_context(owner_user_id=7, knowledge_base_ids=[database['db_id']], query='发票')
    assert hits[0].content == content
    assert all('\n' not in text and '\t' not in text for text in recorded)
    assert any('发票 审批单。' in text for text in recorded)


@pytest.mark.asyncio
async def test_changed_engine_inside_same_binding_is_rejected(setup_runtime: RuntimeSetup) -> None:
    runtime, models, _, _ = setup_runtime
    database = await create(runtime)
    await index(runtime, database['db_id'])
    original = models.embed

    async def changed(model_id: str, inputs: Sequence[str]) -> SimpleNamespace:
        result = await original(model_id, inputs)
        result.engine_model_snapshot = 'different-deployment'
        return result

    models.embed = changed
    with pytest.raises(KnowledgeError) as caught:
        await runtime.retrieve_context(owner_user_id=7, knowledge_base_ids=[database['db_id']], query='invoice')
    assert caught.value.code == 'KNOWLEDGE_REINDEX_REQUIRED'


@pytest.mark.asyncio
async def test_malformed_control_character_is_not_sent_to_embedding(setup_runtime: RuntimeSetup) -> None:
    runtime, _, store, _ = setup_runtime
    database = await create(runtime)
    uploaded = await upload(runtime, database['db_id'], b'normal\\x00text'.replace(b'\\x00', bytes([0])))
    result = await runtime.add_documents(database['db_id'], [uploaded['upload_id']], ChunkParameters())
    assert result['status'] == 'failed'
    assert result['items'][0]['error']['code'] == 'KNOWLEDGE_TEXT_INVALID'
    assert not store.rows


@pytest.mark.asyncio
async def test_vector_index_embeddings_do_not_exceed_provider_batch_size(setup_runtime: RuntimeSetup) -> None:
    runtime, models, *_ = setup_runtime
    database = await create(runtime)
    resolved = await models.resolve_model(MODEL_ID, 'embedding')
    vectors = await runtime._embed(database, [f'text-{i}' for i in range(19)], resolved)
    assert len(vectors) == sum([8, 8, 3])
    assert [len(texts) for _, texts in models.embedding_requests] == [8, 8, 3]


class ControlledGraph:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.release.set()
        self.fail_documents: set[str] = set()
        self.inserted: list[str] = []
        self.documents: set[str] = set()
        self.deleted: list[str] = []
        self.deleted_databases: list[list[str]] = []
        self.closed = False

    async def insert(self, doc_id: str, content: str, title: str) -> None:
        self.started.set()
        self.documents.add(doc_id)
        self.inserted.append(doc_id)
        await self.release.wait()
        if doc_id in self.fail_documents:
            raise RuntimeError('private model credential')

    async def delete_document(self, doc_id: str) -> None:
        self.documents.discard(doc_id)
        self.deleted.append(doc_id)

    async def labels(self) -> list[str]:
        return ['Invoice']

    async def delete_database(self, documents: Sequence[str]) -> None:
        self.deleted_databases.append(list(documents))
        self.documents.clear()

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def controlled_graph(monkeypatch: pytest.MonkeyPatch) -> ControlledGraph:
    graph = ControlledGraph()
    monkeypatch.setattr(stores, 'require_graph', lambda: None)
    monkeypatch.setattr(graph_module, 'KnowledgeGraph', lambda *args: graph)
    return graph


async def graph_finished(runtime: KnowledgeRuntime, db_id: str) -> dict[str, Any]:
    async def finished() -> dict[str, Any]:
        while True:
            status = await runtime.graph_build_status(db_id)
            if status['status'] not in {'queued', 'running'}:
                return status
            await asyncio.sleep(0.01)

    return await asyncio.wait_for(finished(), timeout=3)


@pytest.mark.asyncio
async def test_backfill_requires_model_and_publishes_disabled_status(setup_runtime: RuntimeSetup) -> None:
    runtime, *_ = setup_runtime
    database = await create(runtime)
    assert database['graph_build']['status'] == 'disabled'
    assert (await runtime.graph_build_status(database['db_id']))['status'] == 'disabled'
    with pytest.raises(KnowledgeError) as error:
        await runtime.start_graph_build(database['db_id'], GraphBuildRequest())
    assert error.value.code == 'KNOWLEDGE_GRAPH_MODEL_REQUIRED'
    assert not (await runtime.get_database(database['db_id']))['enable_knowledge_graph']
    with pytest.raises(KnowledgeError):
        await runtime.graph_build_status('../../metadata')
    for invalid in ('wrong', '../mdl_secret', '', 4):
        with pytest.raises(ValidationError):
            GraphBuildRequest(llm_model_id=invalid)
    with pytest.raises(ValidationError):
        GraphBuildRequest(llm_model_id=MODEL_ID, api_key='secret')


@pytest.mark.asyncio
async def test_backfill_is_idempotent_durable_and_keeps_vector_reads_available(
    setup_runtime: RuntimeSetup, controlled_graph: ControlledGraph
) -> None:
    runtime, models, store, _ = setup_runtime
    database = await create(runtime)
    db_id = database['db_id']
    document_id = await index(runtime, db_id)
    extra_upload = await upload(runtime, db_id)
    folder = await runtime.create_folder(db_id, 'Folder')
    original_rows = dict(store.rows[db_id])
    original_spec = (await runtime.metadata.read())['databases'][db_id]['index_spec']
    controlled_graph.release.clear()
    started = await runtime.start_graph_build(db_id, GraphBuildRequest(llm_model_id=MODEL_ID))
    assert started['status'] == 'queued'
    await asyncio.wait_for(controlled_graph.started.wait(), 2)
    running = await runtime.graph_build_status(db_id)
    assert running['status'] == 'running'
    assert running['current_document'] == {'id': document_id, 'file_name': 'guide.txt'}
    assert running['total_documents'] == 1
    assert running['processed_documents'] == 0
    assert (await runtime.start_graph_build(db_id, GraphBuildRequest()))['job_id'] == started['job_id']
    persisted = json.loads((runtime.data_dir / 'metadata.json').read_text())
    assert persisted['databases'][db_id]['graph_build']['status'] == 'running'
    assert (await runtime.get_database(db_id))['graph_build']['job_id'] == started['job_id']
    assert (await runtime.list_databases())['databases'][0]['graph_build']['status'] == 'running'
    hits = await asyncio.wait_for(
        runtime.retrieve_context(owner_user_id=7, knowledge_base_ids=[db_id], query='invoice'), 0.5
    )
    assert hits[0].source_id == document_id
    for action in (
        lambda: upload(runtime, db_id),
        lambda: runtime.add_documents(db_id, [extra_upload['upload_id']], ChunkParameters()),
        lambda: runtime.rechunk(db_id, document_id, ChunkParameters()),
        lambda: runtime.delete_document(db_id, document_id),
        lambda: runtime.delete_database(db_id),
        lambda: runtime.delete_folder(db_id, folder['id']),
    ):
        with pytest.raises(KnowledgeError) as error:
            await asyncio.wait_for(action(), 0.5)
        assert error.value.code == 'KNOWLEDGE_GRAPH_BUSY'
    before_graph_query = list(models.calls)
    with pytest.raises(KnowledgeError) as error:
        await runtime.graph_labels(db_id)
    assert error.value.code == 'KNOWLEDGE_GRAPH_INDEX_INCOMPLETE'
    assert models.calls == before_graph_query
    controlled_graph.release.set()
    completed = await graph_finished(runtime, db_id)
    assert completed['status'] == 'completed'
    assert completed['processed_documents'] == completed['total_documents'] == 1
    assert completed['failed_documents'] == 0
    assert completed['current_document'] is None
    assert completed['finished_at'] and completed['started_at']
    assert store.rows[db_id] == original_rows
    state = await runtime.metadata.read()
    assert state['databases'][db_id]['index_spec'] == original_spec
    assert state['documents'][document_id]['status'] == 'done'
    assert state['documents'][document_id]['graph_status'] == 'completed'
    assert await runtime.graph_labels(db_id) == {'labels': ['Invoice']}
    await runtime.close()


@pytest.mark.asyncio
async def test_failed_backfill_rolls_back_and_retry_skips_successful_documents(
    setup_runtime: RuntimeSetup, controlled_graph: ControlledGraph
) -> None:
    runtime, models, store, _ = setup_runtime
    database = await create(runtime)
    db_id = database['db_id']
    first = await index(runtime, db_id)
    second = await index(runtime, db_id, b'Procurement needs approval.')
    original_rows, embedding_requests = dict(store.rows[db_id]), list(models.embedding_requests)
    controlled_graph.fail_documents.add(second)
    await runtime.start_graph_build(db_id, GraphBuildRequest(llm_model_id=MODEL_ID))
    failed = await graph_finished(runtime, db_id)
    assert failed['status'] == 'failed'
    assert failed['processed_documents'] == EXPECTED_BACKFILL_DOCUMENTS
    assert failed['failed_documents'] == 1
    assert failed['error']['code'] == 'KNOWLEDGE_GRAPH_BUILD_FAILED'
    docs = {doc['id']: doc for doc in (await runtime.list_documents(db_id))['documents']}
    assert docs[first]['graph_status'] == 'completed'
    assert docs[second]['graph_status'] == 'failed'
    assert docs[second]['status'] == 'done' and docs[second]['error'] is None
    assert docs[second]['graph_error']['code'] == 'KNOWLEDGE_GRAPH_BUILD_FAILED'
    assert controlled_graph.documents == {first}
    assert 'private' not in json.dumps(failed)
    assert store.rows[db_id] == original_rows
    assert models.embedding_requests == embedding_requests
    with pytest.raises(KnowledgeError) as error:
        await runtime.graph_labels(db_id)
    assert error.value.code == 'KNOWLEDGE_GRAPH_INDEX_INCOMPLETE'
    controlled_graph.fail_documents.clear()
    await runtime.start_graph_build(db_id, GraphBuildRequest())
    retried = await graph_finished(runtime, db_id)
    assert retried['status'] == 'completed'
    assert retried['processed_documents'] == retried['total_documents'] == EXPECTED_BACKFILL_DOCUMENTS
    assert controlled_graph.inserted.count(first) == 1
    assert controlled_graph.inserted.count(second) == EXPECTED_RETRY_ATTEMPTS
    assert controlled_graph.documents == {first, second}
    await runtime.close()


@pytest.mark.asyncio
async def test_graph_build_retains_safe_failure_reason_in_job_and_document(
    setup_runtime: RuntimeSetup,
    controlled_graph: ControlledGraph,
) -> None:
    runtime, *_ = setup_runtime
    database = await create(runtime)
    doc_id = await index(runtime, database['db_id'])

    async def fail(doc_id: str, content: str, title: str) -> None:
        raise KnowledgeError('KNOWLEDGE_GRAPH_LLM_TIMEOUT', 'private-password document')

    controlled_graph.insert = fail
    await runtime.start_graph_build(database['db_id'], GraphBuildRequest(llm_model_id=MODEL_ID))
    job = await graph_finished(runtime, database['db_id'])
    assert job['error']['code'] == 'KNOWLEDGE_GRAPH_LLM_TIMEOUT'
    state = await runtime.metadata.read()
    assert state['documents'][doc_id]['graph_error']['code'] == 'KNOWLEDGE_GRAPH_LLM_TIMEOUT'
    assert 'private' not in json.dumps(job)
    assert state['documents'][doc_id]['status'] == 'done'
    await runtime.close()


@pytest.mark.asyncio
async def test_enabled_database_new_index_and_rechunk_queue_graph_without_failing_vectors(
    setup_runtime: RuntimeSetup, controlled_graph: ControlledGraph
) -> None:
    runtime, _, store, _ = setup_runtime
    database = await runtime.create_database(
        DatabaseCreate(
            database_name='Graph',
            embed_model_id=MODEL_ID,
            llm_model_id=MODEL_ID,
            enable_knowledge_graph=True,
        )
    )
    assert database['graph_build']['status'] == 'idle'
    db_id = database['db_id']
    controlled_graph.release.clear()
    doc_id = await asyncio.wait_for(index(runtime, db_id), 1)
    controlled_graph.fail_documents.add(doc_id)
    await asyncio.wait_for(controlled_graph.started.wait(), 2)
    controlled_graph.release.set()
    assert (await graph_finished(runtime, db_id))['status'] == 'failed'
    assert store.rows[db_id]
    assert (await runtime.list_documents(db_id))['documents'][0]['status'] == 'done'
    hits = await runtime.retrieve_context(owner_user_id=7, knowledge_base_ids=[db_id], query='invoice')
    assert hits[0].source_id == doc_id
    controlled_graph.fail_documents.clear()
    result = await runtime.rechunk(db_id, doc_id, ChunkParameters(chunk_size=200, chunk_overlap=20))
    assert result['status'] == 'success'
    assert (await graph_finished(runtime, db_id))['status'] == 'completed'
    assert controlled_graph.inserted.count(doc_id) == EXPECTED_RETRY_ATTEMPTS
    await runtime.close()


@pytest.mark.asyncio
async def test_close_interrupts_build_and_restart_retry_cleans_partial_graph(
    setup_runtime: RuntimeSetup, controlled_graph: ControlledGraph
) -> None:
    runtime, _, store, _ = setup_runtime
    database = await create(runtime)
    db_id = database['db_id']
    doc_id = await index(runtime, db_id)
    controlled_graph.release.clear()
    await runtime.start_graph_build(db_id, GraphBuildRequest(llm_model_id=MODEL_ID))
    await asyncio.wait_for(controlled_graph.started.wait(), 2)
    await runtime.close()
    assert controlled_graph.closed
    assert (await runtime.graph_build_status(db_id))['status'] == 'interrupted'
    reopened = KnowledgeRuntime(
        runtime.data_dir, runtime.model_service_factory, runtime.owner_authorizer, store_factory=lambda _: store
    )
    assert (await reopened.graph_build_status(db_id))['status'] == 'interrupted'
    assert (await reopened.list_documents(db_id))['documents'][0]['graph_status'] == 'interrupted'
    controlled_graph.release.set()
    await reopened.start_graph_build(db_id, GraphBuildRequest())
    assert (await graph_finished(reopened, db_id))['status'] == 'completed'
    assert doc_id in controlled_graph.deleted
    await reopened.close()


@pytest.mark.asyncio
async def test_restart_marks_orphaned_job_interrupted_without_starting_models(
    setup_runtime: RuntimeSetup, controlled_graph: ControlledGraph
) -> None:
    runtime, models, store, _ = setup_runtime
    database = await create(runtime)
    db_id = database['db_id']
    doc_id = await index(runtime, db_id)
    async with runtime.metadata.transaction() as state:
        state['databases'][db_id].update(
            enable_knowledge_graph=True,
            llm_model_id=MODEL_ID,
            graph_build={
                'status': 'running',
                'job_id': 'old-job',
                'total_documents': 1,
                'processed_documents': 0,
                'failed_documents': 0,
                'current_document': {'id': doc_id, 'file_name': 'guide.txt'},
                'error': None,
                'started_at': '2026-01-01T00:00:00Z',
                'finished_at': None,
            },
        )
        state['documents'][doc_id].update(graph_status='running', graph_attempted=True)
    before = len(models.calls)
    reopened = KnowledgeRuntime(
        runtime.data_dir, runtime.model_service_factory, runtime.owner_authorizer, store_factory=lambda _: store
    )
    listed = await reopened.list_databases()
    assert listed['databases'][0]['graph_build']['status'] == 'interrupted'
    assert (await reopened.graph_build_status(db_id))['error']['code'] == 'KNOWLEDGE_GRAPH_INTERRUPTED'
    assert len(models.calls) == before
    assert not controlled_graph.inserted
    assert (await reopened.metadata.read())['databases'][db_id]['graph_build']['status'] == 'interrupted'


@pytest.mark.asyncio
async def test_stale_graph_worker_cannot_complete_replaced_job(
    setup_runtime: RuntimeSetup, controlled_graph: ControlledGraph
) -> None:
    runtime, *_ = setup_runtime
    database = await create(runtime)
    db_id = database['db_id']
    doc_id = await index(runtime, db_id)
    controlled_graph.release.clear()
    await runtime.start_graph_build(db_id, GraphBuildRequest(llm_model_id=MODEL_ID))
    await asyncio.wait_for(controlled_graph.started.wait(), 2)
    async with runtime.metadata.transaction() as state:
        state['databases'][db_id]['graph_build'].update(job_id='replacement', status='interrupted')
        state['documents'][doc_id]['graph_status'] = 'interrupted'
    controlled_graph.release.set()
    await asyncio.gather(*tuple(runtime._graph_tasks.values()))
    state = await runtime.metadata.read()
    assert state['databases'][db_id]['graph_build']['job_id'] == 'replacement'
    assert state['databases'][db_id]['graph_build']['status'] == 'interrupted'
    assert state['documents'][doc_id]['graph_status'] == 'interrupted'
    await runtime.close()


@pytest.mark.asyncio
async def test_backfill_refuses_model_change_or_unavailable_model_without_enabling(
    setup_runtime: RuntimeSetup, controlled_graph: ControlledGraph
) -> None:
    runtime, models, *_ = setup_runtime
    database = await create(runtime)
    db_id = database['db_id']
    models.disabled = True
    with pytest.raises(KnowledgeError):
        await runtime.start_graph_build(db_id, GraphBuildRequest(llm_model_id=MODEL_ID))
    assert not (await runtime.get_database(db_id))['enable_knowledge_graph']
    models.disabled = False
    started = await runtime.start_graph_build(db_id, GraphBuildRequest(llm_model_id=MODEL_ID))
    assert started['total_documents'] == 0
    assert (await graph_finished(runtime, db_id))['status'] == 'completed'
    with pytest.raises(KnowledgeError) as error:
        await runtime.start_graph_build(db_id, GraphBuildRequest(llm_model_id='mdl_423e4567e89b42d3a456426614174445'))
    assert error.value.code == 'KNOWLEDGE_GRAPH_MODEL_MIGRATION_REQUIRED'
    await runtime.close()


@pytest.mark.asyncio
async def test_failed_vector_document_backfill_does_not_claim_graph_writes_or_prevent_deletion(
    setup_runtime: RuntimeSetup, controlled_graph: ControlledGraph
) -> None:
    runtime, _, store, _ = setup_runtime
    database = await create(runtime)
    db_id = database['db_id']
    uploaded = await upload(runtime, db_id)
    store.fail = True
    result = await runtime.add_documents(db_id, [uploaded['upload_id']], ChunkParameters())
    doc_id = result['items'][0]['id']
    await runtime.start_graph_build(db_id, GraphBuildRequest(llm_model_id=MODEL_ID))
    assert (await graph_finished(runtime, db_id))['status'] == 'failed'
    document = (await runtime.metadata.read())['documents'][doc_id]
    assert document['status'] == 'failed'
    assert not document.get('graph_attempted')
    assert not controlled_graph.inserted
    await runtime.delete_document(db_id, doc_id)
    assert not controlled_graph.deleted
    await runtime.close()


@pytest.mark.asyncio
async def test_failed_graph_cleanup_is_reported_and_retry_cleans_before_insert(
    setup_runtime: RuntimeSetup, controlled_graph: ControlledGraph
) -> None:
    runtime, *_ = setup_runtime
    database = await create(runtime)
    db_id = database['db_id']
    doc_id = await index(runtime, db_id)
    controlled_graph.fail_documents.add(doc_id)
    real_delete = controlled_graph.delete_document

    async def failed_delete(document_id: str) -> None:
        raise RuntimeError('private storage address')

    controlled_graph.delete_document = failed_delete
    await runtime.start_graph_build(db_id, GraphBuildRequest(llm_model_id=MODEL_ID))
    assert (await graph_finished(runtime, db_id))['status'] == 'failed'
    document = (await runtime.metadata.read())['documents'][doc_id]
    assert document['graph_error']['code'] == 'KNOWLEDGE_GRAPH_CLEANUP_FAILED'
    assert document['graph_attempted']
    controlled_graph.delete_document = real_delete
    controlled_graph.fail_documents.clear()
    await runtime.start_graph_build(db_id, GraphBuildRequest())
    assert (await graph_finished(runtime, db_id))['status'] == 'completed'
    assert controlled_graph.deleted == [doc_id]
    await runtime.close()


@pytest.mark.asyncio
async def test_another_runtime_observes_live_job_and_repeated_start_without_duplicating(
    setup_runtime: RuntimeSetup, controlled_graph: ControlledGraph
) -> None:
    runtime, _, store, _ = setup_runtime
    database = await create(runtime)
    db_id = database['db_id']
    await index(runtime, db_id)
    controlled_graph.release.clear()
    started = await runtime.start_graph_build(db_id, GraphBuildRequest(llm_model_id=MODEL_ID))
    await asyncio.wait_for(controlled_graph.started.wait(), 2)
    other = KnowledgeRuntime(
        runtime.data_dir, runtime.model_service_factory, runtime.owner_authorizer, store_factory=lambda _: store
    )
    assert (await other.graph_build_status(db_id))['status'] == 'running'
    assert (await other.start_graph_build(db_id, GraphBuildRequest()))['job_id'] == started['job_id']
    assert len(controlled_graph.inserted) == 1
    await other.close()
    assert (await runtime.graph_build_status(db_id))['status'] == 'running'
    controlled_graph.release.set()
    await graph_finished(runtime, db_id)
    await runtime.close()


@pytest.mark.asyncio
async def test_immediate_close_persists_queued_job_as_interrupted(
    setup_runtime: RuntimeSetup, controlled_graph: ControlledGraph
) -> None:
    runtime, *_ = setup_runtime
    database = await create(runtime)
    db_id = database['db_id']
    await index(runtime, db_id)
    await runtime.start_graph_build(db_id, GraphBuildRequest(llm_model_id=MODEL_ID))
    await runtime.close()
    assert (await runtime.graph_build_status(db_id))['status'] == 'interrupted'
    assert not runtime._graph_tasks
    with pytest.raises(KnowledgeError):
        await runtime.start_graph_build(db_id, GraphBuildRequest())


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['query', 'labels', 'subgraph'])
async def test_graph_reads_and_waiting_document_deletion_leave_vector_reads_available(
    setup_runtime: RuntimeSetup, controlled_graph: ControlledGraph, operation: str
) -> None:
    runtime, *_ = setup_runtime
    database = await runtime.create_database(
        DatabaseCreate(
            database_name='Graph',
            embed_model_id=MODEL_ID,
            llm_model_id=MODEL_ID,
            enable_knowledge_graph=True,
        )
    )
    db_id = database['db_id']
    doc_id = await index(runtime, db_id)
    assert (await graph_finished(runtime, db_id))['status'] == 'completed'
    entered, release = asyncio.Event(), asyncio.Event()
    results = {'query': 'Answer', 'labels': ['Invoice'], 'subgraph': {'nodes': [], 'edges': []}}

    async def slow_graph_read(*args) -> Any:
        entered.set()
        await release.wait()
        return results[operation]

    setattr(controlled_graph, operation, slow_graph_read)
    calls = {
        'query': lambda: runtime.graph_query(db_id, 'invoice', QueryParameters()),
        'labels': lambda: runtime.graph_labels(db_id),
        'subgraph': lambda: runtime.graph_subgraph(db_id, '*', 2, 100),
    }
    reader = asyncio.create_task(calls[operation]())
    deleter = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        deleter = asyncio.create_task(runtime.delete_document(db_id, doc_id))
        await asyncio.sleep(0.02)
        assert not deleter.done()
        assert doc_id in controlled_graph.documents
        hits = await asyncio.wait_for(
            runtime.retrieve_context(owner_user_id=7, knowledge_base_ids=[db_id], query='invoice'), 0.5
        )
        assert hits[0].source_id == doc_id
    finally:
        release.set()
        await reader
        if deleter:
            await deleter
        await runtime.close()


@pytest.mark.asyncio
async def test_database_deletion_cleans_graph_workspace_after_its_last_document_is_deleted(
    setup_runtime: RuntimeSetup, controlled_graph: ControlledGraph
) -> None:
    runtime, *_ = setup_runtime
    database = await runtime.create_database(
        DatabaseCreate(
            database_name='Graph',
            embed_model_id=MODEL_ID,
            llm_model_id=MODEL_ID,
            enable_knowledge_graph=True,
        )
    )
    db_id = database['db_id']
    doc_id = await index(runtime, db_id)
    assert (await graph_finished(runtime, db_id))['status'] == 'completed'
    await runtime.delete_document(db_id, doc_id)
    assert (await runtime.list_documents(db_id))['total'] == 0
    await runtime.delete_database(db_id)
    assert controlled_graph.deleted_databases == [[]]
    assert (await runtime.list_databases())['databases'] == []
    await runtime.close()


@pytest.mark.asyncio
async def test_never_indexed_empty_graph_database_deletes_without_constructing_graph(
    setup_runtime: RuntimeSetup, controlled_graph: ControlledGraph
) -> None:
    runtime, *_ = setup_runtime
    database = await runtime.create_database(
        DatabaseCreate(
            database_name='Empty',
            embed_model_id=MODEL_ID,
            llm_model_id=MODEL_ID,
            enable_knowledge_graph=True,
        )
    )
    await runtime.delete_database(database['db_id'])
    assert not runtime._graphs
    assert not controlled_graph.deleted_databases
    await runtime.close()


@pytest.mark.asyncio
async def test_graph_build_rejects_same_dimension_changed_binding_before_enabling_or_writing(
    setup_runtime: RuntimeSetup, controlled_graph: ControlledGraph
) -> None:
    runtime, models, store, _ = setup_runtime
    database = await create(runtime)
    db_id = database['db_id']
    await index(runtime, db_id)
    original_state = await runtime.metadata.read()
    original_rows = dict(store.rows[db_id])
    models.binding = 'binding-v2'
    try:
        with pytest.raises(KnowledgeError) as error:
            await runtime.start_graph_build(db_id, GraphBuildRequest(llm_model_id=MODEL_ID))
        assert error.value.code == 'KNOWLEDGE_REINDEX_REQUIRED'
        assert await runtime.metadata.read() == original_state
        assert not controlled_graph.inserted
        assert not controlled_graph.deleted
        assert store.rows[db_id] == original_rows
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_graph_build_checks_embedding_binding_before_each_document_write(
    setup_runtime: RuntimeSetup, controlled_graph: ControlledGraph
) -> None:
    runtime, models, store, _ = setup_runtime
    database = await create(runtime)
    db_id = database['db_id']
    first = await index(runtime, db_id)
    second = await index(runtime, db_id, b'Another policy document.')
    original_rows = dict(store.rows[db_id])
    original_spec = (await runtime.metadata.read())['databases'][db_id]['index_spec']
    real_insert = controlled_graph.insert

    async def change_binding_after_first(doc_id: str, content: str, title: str) -> None:
        await real_insert(doc_id, content, title)
        if doc_id == first:
            models.binding = 'binding-v2'

    controlled_graph.insert = change_binding_after_first
    await runtime.start_graph_build(db_id, GraphBuildRequest(llm_model_id=MODEL_ID))
    result = await graph_finished(runtime, db_id)
    assert result['status'] == 'failed'
    assert result['error']['code'] == 'KNOWLEDGE_REINDEX_REQUIRED'
    assert result['failed_documents'] == 1
    assert controlled_graph.inserted == [first]
    assert not controlled_graph.deleted
    state = await runtime.metadata.read()
    assert state['documents'][second]['graph_error']['code'] == 'KNOWLEDGE_REINDEX_REQUIRED'
    assert not state['documents'][second].get('graph_attempted')
    assert state['documents'][second]['status'] == 'done'
    assert state['databases'][db_id]['index_spec'] == original_spec
    assert store.rows[db_id] == original_rows
    await runtime.close()


@pytest.mark.asyncio
async def test_graph_adapter_reindex_error_remains_explicit_in_document_and_job_progress(
    setup_runtime: RuntimeSetup, controlled_graph: ControlledGraph
) -> None:
    runtime, _, store, _ = setup_runtime
    database = await create(runtime)
    db_id = database['db_id']
    doc_id = await index(runtime, db_id)
    original_rows = dict(store.rows[db_id])

    async def changed_during_embedding(document_id: str, content: str, title: str) -> None:
        raise KnowledgeError.reindex()

    controlled_graph.insert = changed_during_embedding
    await runtime.start_graph_build(db_id, GraphBuildRequest(llm_model_id=MODEL_ID))
    result = await graph_finished(runtime, db_id)
    assert result['status'] == 'failed'
    assert result['error']['code'] == 'KNOWLEDGE_REINDEX_REQUIRED'
    document = (await runtime.metadata.read())['documents'][doc_id]
    assert document['graph_error']['code'] == 'KNOWLEDGE_REINDEX_REQUIRED'
    assert store.rows[db_id] == original_rows
    await runtime.close()


@pytest.mark.asyncio
async def test_embedding_fingerprint_keeps_existing_persisted_hash_compatible() -> None:
    resolved = await Models().resolve_model(MODEL_ID, 'embedding')
    persisted_hash = '9efa436cbd3e628326cd2e67b9d00a858116e0c8e762d905eaa8b76fcbe5ecdc'
    assert knowledge_models.embedding_fingerprint(resolved) == persisted_hash
    assert KnowledgeRuntime._fingerprint(resolved) == persisted_hash
