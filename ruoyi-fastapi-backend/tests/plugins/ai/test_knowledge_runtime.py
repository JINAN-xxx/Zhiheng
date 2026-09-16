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
from plugins.ai.knowledge import stores
from plugins.ai.knowledge.models import ChunkParameters, DatabaseCreate, KnowledgeError
from plugins.ai.knowledge.runtime import KnowledgeRuntime
from plugins.ai.service.model_service import ModelService

MODEL_ID = 'mdl_423e4567e89b42d3a456426614174444'
TEST_OWNER_ID = 7
EXPECTED_MAX_PASSAGES = 8
EXPECTED_MAX_CONTEXT_BYTES = 24000


class Models:
    def __init__(self) -> None:
        self.binding = 'binding-v1'
        self.disabled = False
        self.calls = []

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
    monkeypatch.setattr(stores, 'available_types', lambda: {
        'chroma': {'available': True},
        'milvus': {'available': milvus_available},
        'lightrag': {'available': graph_available},
    })
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
