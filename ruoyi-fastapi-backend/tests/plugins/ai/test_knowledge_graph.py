import asyncio
import copy
import os
import sys
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from config.env import AppConfig, DataBaseConfig
from plugins.ai.knowledge import graph as graph_module
from plugins.ai.knowledge import stores
from plugins.ai.knowledge.models import KnowledgeError, QueryParameters, embedding_fingerprint

MODEL_CONCURRENCY = 2
PROVIDER_BATCH_LIMIT = 8
EXTRACTION_OUTPUT_LIMIT = 16384
PREVIEW_CHUNK_SIZE = 1000
CONFIGURED_GRAPH_TIMEOUT = 120
MODEL_SNAPSHOT = SimpleNamespace(
    platform_model_id='mdl_embedding',
    binding_id='binding-one',
    engine_model_snapshot='remote-embedding',
    default_parameters={},
)
ENV_KEYS = (
    'POSTGRES_HOST',
    'POSTGRES_PORT',
    'POSTGRES_USER',
    'POSTGRES_PASSWORD',
    'POSTGRES_DATABASE',
    'POSTGRES_WORKSPACE',
    'MILVUS_URI',
    'MILVUS_TOKEN',
    'MILVUS_DB_NAME',
    'MILVUS_WORKSPACE',
    'KNOWLEDGE_MILVUS_URI',
    'KNOWLEDGE_MILVUS_TOKEN',
    'KNOWLEDGE_MILVUS_DB_NAME',
)


@pytest.fixture
def configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in {
        'db_type': 'postgresql',
        'db_host': 'pg.internal',
        'db_port': 5432,
        'db_username': 'platform',
        'db_password': 'private-password',
        'db_database': 'platform_db',
    }.items():
        monkeypatch.setattr(DataBaseConfig, key, value)
    monkeypatch.setattr(AppConfig, 'app_workers', 1)
    monkeypatch.setenv('KNOWLEDGE_MILVUS_URI', 'http://milvus.internal:19530')
    monkeypatch.setenv('KNOWLEDGE_MILVUS_TOKEN', 'private-token')
    monkeypatch.setattr(
        stores.importlib.util,
        'find_spec',
        lambda name: object() if name in {'lightrag', 'pymilvus', 'asyncpg'} else None,
    )


def test_graph_available_with_platform_postgres_and_milvus_aliases(configuration: None) -> None:
    result = stores.available_types()['lightrag']
    assert result['available'] is True
    assert 'PostgreSQL' in result['description']
    assert 'private' not in repr(result)


@pytest.mark.parametrize('key', ['POSTGRES_WORKSPACE', 'MILVUS_WORKSPACE'])
def test_global_workspace_override_is_rejected(configuration: None, monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    monkeypatch.setenv(key, 'shared')
    assert stores.available_types()['lightrag']['available'] is False


def test_graph_requires_postgres_dependency_and_single_worker(
    configuration: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(AppConfig, 'app_workers', 2)
    assert not stores.available_types()['lightrag']['available']
    monkeypatch.setattr(AppConfig, 'app_workers', 1)
    monkeypatch.setattr(stores.importlib.util, 'find_spec', lambda name: object() if name != 'asyncpg' else None)
    assert not stores.available_types()['lightrag']['available']


class FakeStorage:
    def __init__(self) -> None:
        self.finalize = AsyncMock()
        self.drop = AsyncMock(return_value={'status': 'success'})
        self.get_by_id = AsyncMock(return_value=None)
        self.drop_pending_index_ops = AsyncMock()
        self._initialized = False
        self._client = None
        self.index_config = SimpleNamespace(index_type='AUTOINDEX')
        self._create_milvus_client = Mock(return_value=Mock())
        self._create_collection_if_not_exist = Mock()

    async def initialize(self) -> None:
        if not self._initialized:
            self._client = self._create_milvus_client()
            self._create_collection_if_not_exist()
            self._initialized = True

    @staticmethod
    def _get_milvus_connection_kwargs() -> dict:
        return {'uri': 'http://milvus.internal:19530', 'db_name': 'default'}


class FakeRag:
    instances = []
    fail_initialize = False

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.workspace = kwargs['workspace']
        self.initialized = False
        self.closed = False
        for name in (
            'full_docs',
            'text_chunks',
            'full_entities',
            'full_relations',
            'entity_chunks',
            'relation_chunks',
            'entities_vdb',
            'relationships_vdb',
            'chunks_vdb',
            'chunk_entity_relation_graph',
            'llm_response_cache',
            'doc_status',
        ):
            storage = FakeStorage()
            storage.workspace = self.workspace
            storage.final_namespace = f'{self.workspace}_{name}'
            setattr(self, name, storage)
        self.ainsert = AsyncMock(side_effect=self._insert)
        self.adelete_by_doc_id = AsyncMock(return_value=SimpleNamespace(status='success'))
        self.get_graph_labels = AsyncMock(return_value=['Invoice', 'Policy'])
        self.get_knowledge_graph = AsyncMock(
            return_value=SimpleNamespace(
                nodes=[SimpleNamespace(id='Invoice', properties={'entity_type': 'document'})],
                edges=[SimpleNamespace(id='edge', source='Invoice', target='Policy', type='related')],
                is_truncated=False,
            )
        )
        self.aquery = AsyncMock(return_value='invoice context')
        self.instances.append(self)

    async def _insert(self, **kwargs: Any) -> None:
        self.doc_status.get_by_id.return_value = {'status': 'processed'}

    async def initialize_storages(self) -> None:
        for name in graph_module._VECTOR_STORAGE_NAMES:
            await getattr(self, name).initialize()
        await asyncio.sleep(0)
        if self.fail_initialize:
            raise RuntimeError('private-password private-token')
        self.initialized = True

    async def finalize_storages(self) -> None:
        # LightRAG skips storage finalization when initialize_storages failed halfway.
        if self.initialized:
            for value in vars(self).values():
                if isinstance(value, FakeStorage):
                    await value.finalize()
        self.closed = True


@pytest.fixture
def fake_lightrag(monkeypatch: pytest.MonkeyPatch, configuration: None) -> type[FakeRag]:
    monkeypatch.setattr(graph_module, 'require_graph', lambda: None)
    monkeypatch.setattr(graph_module, 'load_graph_tokenizer', lambda: SimpleNamespace(model_name='test'), raising=False)
    FakeRag.instances = []
    FakeRag.fail_initialize = False
    module = ModuleType('lightrag')
    module.LightRAG = FakeRag
    module.QueryParam = SimpleNamespace
    utils = ModuleType('lightrag.utils')
    utils.EmbeddingFunc = SimpleNamespace
    shared = ModuleType('lightrag.kg.shared_storage')
    shared.initialize_pipeline_status = AsyncMock()
    initialization_lock = asyncio.Lock()
    shared.get_data_init_lock = lambda: initialization_lock
    milvus_impl = ModuleType('lightrag.kg.milvus_impl')
    milvus_impl.INDEX_VERSION_REQUIREMENTS = {}
    monkeypatch.setitem(sys.modules, 'lightrag.kg.milvus_impl', milvus_impl)
    monkeypatch.setitem(sys.modules, 'lightrag', module)
    monkeypatch.setitem(sys.modules, 'lightrag.utils', utils)
    monkeypatch.setitem(sys.modules, 'lightrag.kg.shared_storage', shared)
    milvus = ModuleType('pymilvus')
    milvus.MilvusClient = Mock(return_value=Mock(has_collection=Mock(return_value=True)))
    monkeypatch.setitem(sys.modules, 'pymilvus', milvus)
    return FakeRag


@pytest.fixture
def graph(tmp_path: Path, fake_lightrag: type[FakeRag]) -> Any:
    service = SimpleNamespace(
        resolve_model=AsyncMock(return_value=MODEL_SNAPSHOT),
        embed=AsyncMock(
            return_value=SimpleNamespace(
                platform_model_id=MODEL_SNAPSHOT.platform_model_id,
                binding_id=MODEL_SNAPSHOT.binding_id,
                engine_model_snapshot=MODEL_SNAPSHOT.engine_model_snapshot,
                response=SimpleNamespace(
                    vectors=[
                        SimpleNamespace(index=1, values=[0.0, 1.0, 0.0]),
                        SimpleNamespace(index=0, values=[1.0, 0.0, 0.0]),
                    ]
                ),
            )
        ),
        chat=AsyncMock(return_value=SimpleNamespace(response=SimpleNamespace(text='answer'))),
    )

    @asynccontextmanager
    async def factory() -> AsyncIterator[SimpleNamespace]:
        yield service

    instance = graph_module.KnowledgeGraph(
        tmp_path,
        {
            'db_id': 'kb_demo',
            'embed_model_id': 'mdl_embedding',
            'llm_model_id': 'mdl_chat',
            'index_spec': {
                'model_id': 'mdl_embedding',
                'fingerprint': embedding_fingerprint(MODEL_SNAPSHOT),
                'dimension': 3,
            },
            'chunk_size': 1000,
            'chunk_overlap': 200,
        },
        factory,
    )
    instance.test_service = service
    return instance


@pytest.mark.asyncio
async def test_adapter_initializes_pg_stores_once_and_maps_configuration(
    graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('POSTGRES_HOST', 'operator-pg.internal')
    first, second = await asyncio.gather(graph._ensure(), graph._ensure())
    assert first is second
    assert len(FakeRag.instances) == 1
    assert first.kwargs['graph_storage'] == 'PGTableGraphStorage'
    assert first.kwargs['kv_storage'] == 'PGKVStorage'
    assert first.kwargs['doc_status_storage'] == 'PGDocStatusStorage'
    assert first.kwargs['vector_storage'] == 'MilvusVectorDBStorage'
    assert first.kwargs['embedding_func'].model_name == 'mdl_embedding'
    assert first.kwargs['llm_model_name'] == 'mdl_chat'
    assert first.kwargs['llm_model_max_async'] == MODEL_CONCURRENCY
    assert first.kwargs['embedding_func_max_async'] == MODEL_CONCURRENCY
    assert os.environ['POSTGRES_HOST'] == 'operator-pg.internal'
    assert os.environ['POSTGRES_PASSWORD'] == 'private-password'
    assert os.environ['MILVUS_URI'] == 'http://milvus.internal:19530'
    assert os.environ['MILVUS_DB_NAME'] == 'default'
    assert os.environ['MILVUS_TOKEN'] == 'private-token'
    assert 'private' not in repr(graph.database)
    await graph.close()


@pytest.mark.asyncio
async def test_model_callbacks_use_published_models_and_validate_vectors(graph: Any) -> None:
    rag = await graph._ensure()
    embedding = rag.kwargs['embedding_func']
    result = await embedding.func([' first\ntext ', 'second'])
    assert result.tolist() == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    assert graph.test_service.embed.call_args.args[0] == 'mdl_embedding'
    assert (
        await rag.kwargs['llm_model_func'](
            'question',
            system_prompt='system',
            history_messages=[
                {'role': 'assistant', 'content': 'history'},
            ],
        )
        == 'answer'
    )
    model_id, messages = graph.test_service.chat.call_args.args
    assert model_id == 'mdl_chat'
    assert [message.role for message in messages] == ['system', 'assistant', 'user']
    graph.test_service.embed.return_value.response.vectors = [SimpleNamespace(index=0, values=[1.0])]
    with pytest.raises(KnowledgeError):
        await embedding.func(['one'])
    await graph.close()


@pytest.mark.asyncio
async def test_graph_embeddings_split_provider_batches_and_keep_input_order(graph: Any) -> None:
    async def provider_embed(model_id: str, texts: list[str]) -> SimpleNamespace:
        assert len(texts) <= PROVIDER_BATCH_LIMIT, 'Proxy indices restart after each provider batch of eight'
        return SimpleNamespace(
            platform_model_id=MODEL_SNAPSHOT.platform_model_id,
            binding_id=MODEL_SNAPSHOT.binding_id,
            engine_model_snapshot=MODEL_SNAPSHOT.engine_model_snapshot,
            response=SimpleNamespace(
                vectors=[
                    SimpleNamespace(index=i, values=[float(text), 0.0, 1.0])
                    for i, text in reversed(list(enumerate(texts)))
                ]
            ),
        )

    graph.test_service.embed.side_effect = provider_embed
    rag = await graph._ensure()
    result = await rag.kwargs['embedding_func'].func([str(i) for i in range(19)])
    assert result.tolist() == [[float(i), 0.0, 1.0] for i in range(19)]
    assert [len(call.args[1]) for call in graph.test_service.embed.call_args_list] == [8, 8, 3]
    await graph.close()


@pytest.mark.asyncio
async def test_graph_embedding_batch_rejects_binding_changes(graph: Any) -> None:
    calls = 0

    async def provider_embed(model_id: str, texts: list[str]) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            platform_model_id=MODEL_SNAPSHOT.platform_model_id,
            binding_id=MODEL_SNAPSHOT.binding_id if calls == 1 else 'changed-binding',
            engine_model_snapshot=MODEL_SNAPSHOT.engine_model_snapshot,
            response=SimpleNamespace(
                vectors=[SimpleNamespace(index=i, values=[1.0, 0.0, 0.0]) for i in range(len(texts))]
            ),
        )

    graph.test_service.embed.side_effect = provider_embed
    rag = await graph._ensure()
    with pytest.raises(KnowledgeError) as error:
        await rag.kwargs['embedding_func'].func(['text'] * 9)
    assert error.value.code == 'KNOWLEDGE_REINDEX_REQUIRED'
    await graph.close()


@pytest.mark.asyncio
async def test_model_failure_survives_lightrag_swallowing_error_and_retry_clears_it(graph: Any) -> None:
    rag = await graph._ensure()
    graph.test_service.chat.side_effect = asyncio.TimeoutError('private-token prompt text')

    async def swallow(**kwargs: Any) -> None:
        try:
            await rag.kwargs['llm_model_func']('private-document')
        except Exception:
            rag.doc_status.get_by_id.return_value = {'status': 'failed'}

    rag.ainsert.side_effect = swallow
    with pytest.raises(KnowledgeError) as caught:
        await graph.insert('doc_timeout', 'content', 'file.txt')
    assert caught.value.code == 'KNOWLEDGE_GRAPH_LLM_TIMEOUT'
    assert 'private' not in str(caught.value)
    graph.test_service.chat.side_effect = None
    rag.ainsert.side_effect = rag._insert
    rag.doc_status.get_by_id.return_value = None
    await graph.insert('doc_retry', 'content', 'file.txt')
    await graph.close()


@pytest.mark.asyncio
async def test_graph_llm_output_is_bounded_and_empty_output_is_failure(graph: Any) -> None:
    rag = await graph._ensure()
    await rag.kwargs['llm_model_func']('question')
    assert graph.test_service.chat.call_args.kwargs['parameters']['max_tokens'] == EXTRACTION_OUTPUT_LIMIT
    graph.test_service.chat.return_value.response.text = ''
    with pytest.raises(KnowledgeError) as caught:
        await rag.kwargs['llm_model_func']('question')
    assert caught.value.code == 'KNOWLEDGE_GRAPH_EMPTY_RESPONSE'
    await graph.close()


@pytest.mark.asyncio
async def test_graph_disables_thinking_only_for_configured_models(graph: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    rag = await graph._ensure()
    monkeypatch.setenv('KNOWLEDGE_GRAPH_DISABLE_THINKING_MODEL_IDS', 'mdl_another')
    await rag.kwargs['llm_model_func']('Extract')
    assert 'thinking_mode' not in graph.test_service.chat.call_args.kwargs['parameters']
    monkeypatch.setenv('KNOWLEDGE_GRAPH_DISABLE_THINKING_MODEL_IDS', ' mdl_another, mdl_chat ')
    await rag.kwargs['llm_model_func']('Extract')
    assert graph.test_service.chat.call_args.kwargs['parameters']['thinking_mode'] == 'disabled'
    await graph.close()


@pytest.mark.asyncio
async def test_graph_configuration_routes_glm_low_effort_and_uses_own_workspace(graph: Any) -> None:
    model_id = 'mdl_423e4567e89b42d3a456426614174444'
    graph.database.update(
        llm_model_id=model_id,
        _graph_workspace='kb_isolated_revision',
        _graph_config={
            'llm_model_id': model_id,
            'reasoning_strategy': 'auto',
            'timeout_seconds': 120,
            'max_output_tokens': 8192,
            'concurrency': 1,
        },
    )
    graph.test_service.resolve_model.return_value = SimpleNamespace(
        platform_model_id=model_id,
        binding_id='binding-glm',
        engine_model_snapshot='opaque-alias',
        model_code='zai-org-glm-5.3',
        default_parameters={},
    )
    rag = await graph._ensure()
    assert rag.workspace == 'kb_isolated_revision'
    assert rag.kwargs['llm_model_max_async'] == 1
    assert rag.kwargs['default_llm_timeout'] == CONFIGURED_GRAPH_TIMEOUT
    await rag.kwargs['llm_model_func']('Extract')
    assert graph.test_service.chat.call_args.kwargs['parameters'] == {'max_tokens': 8192, 'reasoning_effort': 'low'}
    await graph.close()


@pytest.mark.asyncio
async def test_graph_rejects_chat_binding_change_during_model_request(graph: Any) -> None:
    model_id = 'mdl_423e4567e89b42d3a456426614174444'
    snapshot = {
        'platform_model_id': model_id,
        'binding_id': 'original-binding',
        'engine_model_snapshot': 'alias',
        'model_code': 'unknown',
        'default_parameters': {},
    }
    graph.database.update(llm_model_id=model_id, _graph_snapshot=snapshot, _graph_config={'llm_model_id': model_id})
    graph.test_service.resolve_model.return_value = SimpleNamespace(**snapshot)
    graph.test_service.chat.return_value = SimpleNamespace(
        response=SimpleNamespace(text='answer'),
        platform_model_id=model_id,
        binding_id='changed-binding',
        engine_model_snapshot='alias',
    )
    with pytest.raises(KnowledgeError) as caught:
        await graph._complete('Extract')
    assert caught.value.code == 'KNOWLEDGE_GRAPH_MODEL_CHANGED'


@pytest.mark.asyncio
async def test_graph_preview_extracts_only_one_token_chunk_and_counts_committed_anchors(graph: Any) -> None:
    rag = await graph._ensure()
    rag.tokenizer = SimpleNamespace(encode=list, decode=''.join)
    rag.ainsert_custom_chunks = AsyncMock()
    rag.doc_status.get_by_id.return_value = {'status': 'processed'}
    rag.full_entities.get_by_id.return_value = {'entity_names': ['A', 'B'], 'count': 2}
    rag.full_relations.get_by_id.return_value = {'relation_pairs': [['A', 'B']], 'count': 1}
    result = await graph.preview('doc_sample', 'abc' * 1000, 'sample.txt')
    assert result == {'entities': 2, 'relations': 1, 'chunks': 1}
    request = rag.ainsert_custom_chunks.call_args.kwargs
    assert len(request['text_chunks']) == 1
    assert len(request['text_chunks'][0]) == PREVIEW_CHUNK_SIZE
    assert request['full_text'] == request['text_chunks'][0]
    await graph.close()


@pytest.mark.asyncio
async def test_graph_rejects_default_parameter_race_with_unchanged_binding(graph: Any) -> None:
    model_id = 'mdl_423e4567e89b42d3a456426614174444'
    snapshot = {
        'platform_model_id': model_id,
        'binding_id': 'binding',
        'engine_model_snapshot': 'alias',
        'model_code': 'unknown',
        'default_parameters': {'temperature': 0.1},
    }
    graph.database.update(llm_model_id=model_id, _graph_snapshot=snapshot, _graph_config={'llm_model_id': model_id})
    graph.test_service.resolve_model.return_value = SimpleNamespace(**snapshot)
    changed = SimpleNamespace(**dict(snapshot, default_parameters={'temperature': 1.9}))
    graph.test_service.chat.return_value = SimpleNamespace(
        response=SimpleNamespace(text='answer'),
        platform_model_id=model_id,
        binding_id='binding',
        engine_model_snapshot='alias',
        resolved_model=changed,
    )
    with pytest.raises(KnowledgeError) as caught:
        await graph._complete('Extract')
    assert caught.value.code == 'KNOWLEDGE_GRAPH_MODEL_CHANGED'


@pytest.mark.asyncio
async def test_preview_tokenization_runs_outside_the_event_loop(graph: Any) -> None:
    rag = await graph._ensure()
    main_thread = threading.get_ident()

    def encode(content: str) -> list[str]:
        assert threading.get_ident() != main_thread, 'Long document tokenization must not block requests'
        return list(content)

    rag.tokenizer = SimpleNamespace(encode=encode, decode=''.join)
    rag.ainsert_custom_chunks = AsyncMock()
    rag.doc_status.get_by_id.return_value = {'status': 'processed'}
    result = await graph.preview('doc_sample', 'content', 'sample.txt')
    assert result['chunks'] == 1
    await graph.close()


@pytest.mark.asyncio
async def test_lightrag_config_copy_does_not_copy_live_storage_clients(graph: Any) -> None:
    rag = await graph._ensure()

    class LiveStorage:
        def __deepcopy__(self, memo: dict) -> Any:
            raise AssertionError('Copying a live graph or storage is unsafe')

    rag.live_storage = LiveStorage()
    copied = copy.deepcopy(rag.kwargs['llm_model_func'])
    assert await copied('prompt') == 'answer'
    await graph.close()


@pytest.mark.asyncio
async def test_truncated_model_output_is_not_accepted_as_complete_extraction(graph: Any) -> None:
    rag = await graph._ensure()
    graph.test_service.chat.return_value.response.finish_reason = 'length'
    with pytest.raises(KnowledgeError) as error:
        await rag.kwargs['llm_model_func']('Extract')
    assert error.value.code == 'KNOWLEDGE_GRAPH_OUTPUT_TRUNCATED'
    await graph.close()


@pytest.mark.asyncio
async def test_late_callback_does_not_attach_previous_document_error_to_next_insert(graph: Any) -> None:
    rag = await graph._ensure()
    late_release = asyncio.Event()
    late_tasks = []
    graph.test_service.chat.side_effect = asyncio.TimeoutError('private previous prompt')

    async def late_model_call() -> None:
        await late_release.wait()
        try:
            await rag.kwargs['llm_model_func']('previous doc')
        except KnowledgeError:
            pass

    async def insert(**kwargs: Any) -> None:
        if kwargs['ids'] == 'first':
            late_tasks.append(asyncio.create_task(late_model_call()))
        else:
            late_release.set()
            await asyncio.gather(*late_tasks)
        rag.doc_status.get_by_id.return_value = {'status': 'failed'}

    rag.ainsert.side_effect = insert
    try:
        for identifier in ('first', 'second'):
            with pytest.raises(KnowledgeError) as error:
                await graph.insert(identifier, 'content', 'file.txt')
            assert error.value.code == 'KNOWLEDGE_GRAPH_INDEX_FAILED'
    finally:
        late_release.set()
        await asyncio.gather(*late_tasks)
        await graph.close()


@pytest.mark.asyncio
async def test_initialization_failure_closes_partial_storages_and_can_retry(graph: Any) -> None:
    FakeRag.fail_initialize = True
    with pytest.raises(KnowledgeError) as caught:
        await graph._ensure()
    assert 'private' not in str(caught.value)
    assert graph.rag is None
    failed = FakeRag.instances[0]
    failed.full_docs.finalize.assert_awaited_once()
    failed.entities_vdb.finalize.assert_awaited_once()
    FakeRag.fail_initialize = False
    assert await graph._ensure() is not failed
    await graph.close()


@pytest.mark.asyncio
async def test_insert_processed_document_is_idempotent_and_incomplete_is_failure(graph: Any) -> None:
    await graph.insert('doc_one', 'invoice policy', 'invoice.txt')
    rag = graph.rag
    await graph.insert('doc_one', 'invoice policy', 'invoice.txt')
    rag.ainsert.assert_awaited_once_with(input='invoice policy', ids='doc_one', file_paths='invoice.txt')
    rag.doc_status.get_by_id.return_value = {'status': 'failed'}
    rag.ainsert.side_effect = None
    with pytest.raises(KnowledgeError) as caught:
        await graph.insert('doc_two', 'other', 'other.txt')
    assert caught.value.code == 'KNOWLEDGE_GRAPH_INDEX_FAILED'
    await graph.close()


@pytest.mark.asyncio
async def test_delete_failure_preserves_state_and_is_safe_to_retry(graph: Any) -> None:
    rag = await graph._ensure()
    rag.adelete_by_doc_id.return_value = SimpleNamespace(status='fail', message='private-password')
    with pytest.raises(KnowledgeError) as caught:
        await graph.delete_document('doc_one')
    assert 'private' not in str(caught.value)
    rag.adelete_by_doc_id.return_value = SimpleNamespace(status='not_found')
    await graph.delete_document('doc_one')
    await graph.close()


@pytest.mark.asyncio
async def test_graph_reads_use_workspace_and_close_does_not_delete_persisted_data(graph: Any) -> None:
    assert await graph.labels() == ['Invoice', 'Policy']
    result = await graph.subgraph('Invoice', 2, 20)
    assert result['nodes'] == [{'id': 'Invoice', 'entity_name': 'Invoice', 'entity_type': 'document'}]
    assert result['edges'][0]['target'] == 'Policy'
    assert await graph.query('policy', QueryParameters()) == 'invoice context'
    rag = graph.rag
    await graph.close()
    assert graph.rag is None
    rag.full_docs.finalize.assert_awaited_once()
    rag.full_docs.drop.assert_not_awaited()
    reopened = await graph._ensure()
    assert reopened.workspace == rag.workspace == 'kb_demo'
    await graph.close()


@pytest.mark.asyncio
async def test_delete_database_drops_only_initialized_workspace_stores(graph: Any) -> None:
    await graph.delete_database(['doc_one'])
    rag = FakeRag.instances[0]
    rag.full_docs.drop.assert_awaited_once()
    rag.llm_response_cache.drop.assert_awaited_once()
    rag.chunk_entity_relation_graph.drop.assert_awaited_once()
    assert graph.rag is None


@pytest.mark.asyncio
async def test_initialization_cleanup_failure_keeps_original_error_private(
    graph: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeRag.fail_initialize = True
    monkeypatch.setattr(FakeRag, 'finalize_storages', AsyncMock(side_effect=RuntimeError('private-token')))
    with pytest.raises(KnowledgeError) as caught:
        await graph._ensure()
    assert 'private' not in str(caught.value)
    assert graph.rag is None


@pytest.mark.asyncio
async def test_cancelled_initialization_releases_partial_clients(graph: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    started = asyncio.Event()

    async def initialize(rag: FakeRag) -> None:
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(FakeRag, 'initialize_storages', initialize)
    task = asyncio.create_task(graph._ensure())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert graph.rag is None
    FakeRag.instances[0].full_docs.finalize.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_stops_model_queues_before_releasing_storage(graph: Any) -> None:
    rag = await graph._ensure()
    events = []

    async def shutdown(**kwargs: Any) -> None:
        events.append('queue')

    async def finalize() -> None:
        events.append('storage')

    rag.embedding_func = SimpleNamespace(func=SimpleNamespace(shutdown=shutdown))
    rag.full_docs.finalize.side_effect = finalize
    await graph.close()
    assert events == ['queue', 'storage']


@pytest.mark.asyncio
async def test_pinned_lightrag_constructs_native_pg_and_milvus_without_connections(
    configuration: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Exercise the installed SDK constructor, model queue and real storage classes.
    # Network initialization is separately covered by the infrastructure smoke test.
    lightrag = pytest.importorskip('lightrag')
    pytest.importorskip('asyncpg')
    pytest.importorskip('pymilvus')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(lightrag.LightRAG, 'initialize_storages', AsyncMock())
    milvus_impl = pytest.importorskip('lightrag.kg.milvus_impl')
    monkeypatch.setattr(milvus_impl.MilvusVectorDBStorage, '_create_milvus_client', Mock(return_value=Mock()))
    monkeypatch.setattr(milvus_impl.MilvusVectorDBStorage, '_create_collection_if_not_exist', Mock())
    monkeypatch.setattr(
        sys.modules[lightrag.LightRAG.__module__],
        'TiktokenTokenizer',
        Mock(side_effect=AssertionError('LightRAG must receive an explicit tokenizer')),
    )
    service = SimpleNamespace(
        resolve_model=AsyncMock(return_value=MODEL_SNAPSHOT),
        embed=AsyncMock(
            return_value=SimpleNamespace(
                platform_model_id=MODEL_SNAPSHOT.platform_model_id,
                binding_id=MODEL_SNAPSHOT.binding_id,
                engine_model_snapshot=MODEL_SNAPSHOT.engine_model_snapshot,
                response=SimpleNamespace(
                    vectors=[
                        SimpleNamespace(index=0, values=[1.0, 0.0, 0.0]),
                    ]
                ),
            )
        ),
    )

    @asynccontextmanager
    async def factory() -> AsyncIterator[SimpleNamespace]:
        yield service

    graph = graph_module.KnowledgeGraph(
        tmp_path,
        {
            'db_id': 'kb_real_api',
            'embed_model_id': 'mdl_embedding',
            'llm_model_id': 'mdl_chat',
            'index_spec': {
                'model_id': 'mdl_embedding',
                'fingerprint': embedding_fingerprint(MODEL_SNAPSHOT),
                'dimension': 3,
            },
            'chunk_size': 1000,
            'chunk_overlap': 200,
        },
        factory,
    )
    rag = await graph._ensure()
    assert type(rag.chunk_entity_relation_graph).__name__ == 'PGTableGraphStorage'
    assert type(rag.full_docs).__name__ == 'PGKVStorage'
    assert type(rag.doc_status).__name__ == 'PGDocStatusStorage'
    assert type(rag.entities_vdb).__name__ == 'MilvusVectorDBStorage'
    assert rag.entities_vdb.workspace == 'kb_real_api'
    assert rag.chunk_entity_relation_graph.workspace == 'kb_real_api'
    assert 'mdl_embedding' in rag.entities_vdb.final_namespace
    create_schema = milvus_impl.MilvusVectorDBStorage._create_collection_if_not_exist
    assert create_schema.call_count == len(graph_module._VECTOR_STORAGE_NAMES)
    for name in graph_module._VECTOR_STORAGE_NAMES:
        storage = getattr(rag, name)
        assert storage._initialized
        await storage.initialize()  # Real pinned SDK must take only its fast path.
    assert create_schema.call_count == len(graph_module._VECTOR_STORAGE_NAMES)
    assert (await rag.embedding_func(['invoice'])).tolist() == [[1.0, 0.0, 0.0]]
    await graph.close()


@pytest.mark.asyncio
async def test_database_cleanup_does_not_rebuild_document_entities_and_resumes_failed_drop(graph: Any) -> None:
    rag = await graph._ensure()
    rag.adelete_by_doc_id.side_effect = RuntimeError('models offline')
    rag.full_docs.drop.side_effect = [{'status': 'error'}, {'status': 'success'}]
    with pytest.raises(KnowledgeError):
        await graph.delete_database(['doc_one'])
    assert graph.rag is rag
    await graph.delete_database(['doc_one'])
    rag.adelete_by_doc_id.assert_not_awaited()
    assert graph.rag is None


@pytest.mark.asyncio
async def test_permanent_delete_uses_real_sdk_names_without_recreating_collections(
    configuration: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    lightrag = pytest.importorskip('lightrag')
    pytest.importorskip('asyncpg')
    pymilvus = pytest.importorskip('pymilvus')
    base = pytest.importorskip('lightrag.base')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(lightrag.LightRAG, 'initialize_storages', AsyncMock())
    milvus_impl = pytest.importorskip('lightrag.kg.milvus_impl')
    monkeypatch.setattr(milvus_impl.MilvusVectorDBStorage, '_create_milvus_client', Mock(return_value=Mock()))
    monkeypatch.setattr(milvus_impl.MilvusVectorDBStorage, '_create_collection_if_not_exist', Mock())
    monkeypatch.setattr(
        sys.modules[lightrag.LightRAG.__module__],
        'TiktokenTokenizer',
        Mock(side_effect=AssertionError('LightRAG must receive an explicit tokenizer')),
    )
    graph = graph_module.KnowledgeGraph(
        tmp_path,
        {
            'db_id': 'kb_sdk_delete',
            'embed_model_id': 'mdl_embedding',
            'llm_model_id': 'mdl_chat',
            'index_spec': {
                'model_id': 'mdl_embedding',
                'fingerprint': embedding_fingerprint(MODEL_SNAPSHOT),
                'dimension': 3,
            },
            'chunk_size': 1000,
            'chunk_overlap': 200,
        },
        None,
    )
    rag = await graph._ensure()
    rag._storages_status = base.StoragesStatus.INITIALIZED
    foreign_collection = 'kb_foreign_entities'
    collections = {foreign_collection}
    events = []
    closed_before_drop = []
    vector_storages = [rag.entities_vdb, rag.relationships_vdb, rag.chunks_vdb]
    owned_names = {storage.final_namespace for storage in vector_storages}
    collections.update(owned_names)

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            self.closed = False

        def has_collection(self, name: str, **kwargs: Any) -> bool:
            return name in collections

        def drop_collection(self, name: str, **kwargs: Any) -> None:
            assert name in owned_names
            closed_before_drop.append(all(storage._client.closed for storage in vector_storages))
            events.append(('drop', name))
            collections.discard(name)

        def close(self) -> None:
            self.closed = True
            events.append(('close', None))

    monkeypatch.setattr(pymilvus, 'MilvusClient', Client)
    for storage in vector_storages:
        storage._client = Client()
        monkeypatch.setattr(storage, '_create_collection_with_schema', Mock(side_effect=collections.add))
        monkeypatch.setattr(storage, '_ensure_collection_loaded', Mock())
    for name in graph_module._STORAGE_NAMES:
        storage = getattr(rag, name)
        if storage not in vector_storages:
            monkeypatch.setattr(storage, 'drop', AsyncMock(return_value={'status': 'success'}))
    await graph.delete_database(['doc_one'])
    assert collections == {foreign_collection}
    assert all(closed_before_drop)
    assert {name for action, name in events if action == 'drop'} == owned_names
    for storage in vector_storages:
        storage._create_collection_with_schema.assert_not_called()
        storage._ensure_collection_loaded.assert_not_called()
    assert graph.rag is None


@pytest.mark.asyncio
async def test_embedding_rejects_changed_binding_before_remote_embedding(graph: Any) -> None:
    rag = await graph._ensure()
    graph.test_service.resolve_model.return_value = SimpleNamespace(
        **{**vars(MODEL_SNAPSHOT), 'binding_id': 'new-binding-same-dimensions'},
    )
    with pytest.raises(KnowledgeError) as caught:
        await rag.kwargs['embedding_func'].func(['one', 'two'])
    assert caught.value.code == 'KNOWLEDGE_REINDEX_REQUIRED'
    graph.test_service.embed.assert_not_awaited()
    await graph.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['binding_id', 'engine_model_snapshot', 'platform_model_id'])
async def test_embedding_rejects_binding_race_in_returned_result(graph: Any, field: str) -> None:
    rag = await graph._ensure()
    setattr(graph.test_service.embed.return_value, field, 'changed-after-resolution')
    with pytest.raises(KnowledgeError) as caught:
        await rag.kwargs['embedding_func'].func(['one', 'two'])
    assert caught.value.code == 'KNOWLEDGE_REINDEX_REQUIRED'
    await graph.close()


@pytest.mark.asyncio
async def test_embedding_resolves_and_embeds_in_separate_model_service_sessions(graph: Any) -> None:
    opened = []
    resolver = SimpleNamespace(resolve_model=graph.test_service.resolve_model)
    embedder = SimpleNamespace(embed=graph.test_service.embed)

    @asynccontextmanager
    async def factory() -> AsyncIterator[SimpleNamespace]:
        service = resolver if not opened else embedder
        opened.append(service)
        yield service

    graph.model_service_factory = factory
    rag = await graph._ensure()
    assert (await rag.kwargs['embedding_func'].func(['one', 'two'])).shape == (2, 3)
    assert opened == [resolver, embedder]
    await graph.close()


@pytest.mark.asyncio
async def test_permanent_delete_stops_when_model_queue_cannot_be_stopped(graph: Any) -> None:
    rag = await graph._ensure()
    rag.embedding_func = SimpleNamespace(
        func=SimpleNamespace(shutdown=AsyncMock(side_effect=RuntimeError('queue busy')))
    )
    with pytest.raises(KnowledgeError):
        await graph.delete_database([])
    rag.full_docs.drop.assert_not_awaited()
    assert graph.rag is rag
    rag.embedding_func.func.shutdown.side_effect = None
    await graph.close()


@pytest.mark.asyncio
async def test_tokenizer_loads_outside_event_loop_and_is_passed_to_constructor(
    graph: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tokenizer = SimpleNamespace(model_name='gpt-4o-mini')
    loader_threads = []
    event_loop_thread = threading.get_ident()

    def load() -> SimpleNamespace:
        loader_threads.append(threading.get_ident())
        return tokenizer

    monkeypatch.setattr(graph_module, 'load_graph_tokenizer', load, raising=False)
    rag = await graph._ensure()
    assert loader_threads and loader_threads[0] != event_loop_thread
    assert rag.kwargs['tokenizer'] is tokenizer
    await graph.close()


@pytest.mark.asyncio
async def test_missing_tokenizer_cache_fails_before_storage_initialization(
    graph: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader = Mock(side_effect=KnowledgeError.unavailable())
    monkeypatch.setattr(graph_module, 'load_graph_tokenizer', loader, raising=False)
    with pytest.raises(KnowledgeError):
        await graph._ensure()
    assert FakeRag.instances == []
    assert graph.rag is None


@pytest.mark.asyncio
async def test_cold_milvus_initialization_keeps_event_loop_responsive_and_sdk_lock_on_loop(
    graph: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()
    entered = asyncio.Event()
    release = threading.Event()
    rag = graph._create_rag(SimpleNamespace())
    worker_threads = []

    def initialize() -> None:
        worker_threads.append(threading.get_ident())
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(timeout=1), 'event loop stalled during Milvus initialization'

    rag.entities_vdb._create_collection_if_not_exist.side_effect = initialize
    monkeypatch.setattr(graph, '_create_rag', lambda tokenizer: rag)
    task = asyncio.create_task(graph._ensure())
    await asyncio.wait_for(entered.wait(), timeout=2)
    lock = sys.modules['lightrag.kg.shared_storage'].get_data_init_lock()
    assert lock.locked()
    release.set()
    assert await task is rag
    assert worker_threads and worker_threads[0] != loop_thread
    assert not lock.locked()
    for name in graph_module._VECTOR_STORAGE_NAMES:
        storage = getattr(rag, name)
        storage._create_collection_if_not_exist.assert_called_once()
        assert storage._initialized
    await graph.close()


@pytest.mark.asyncio
async def test_cancelling_cold_milvus_waits_for_thread_before_releasing_client(
    graph: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()
    rag = graph._create_rag(SimpleNamespace())

    def initialize() -> None:
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(timeout=1), 'event loop stalled during Milvus initialization'

    rag.entities_vdb._create_collection_if_not_exist.side_effect = initialize
    monkeypatch.setattr(graph, '_create_rag', lambda tokenizer: rag)
    task = asyncio.create_task(graph._ensure())
    await asyncio.wait_for(entered.wait(), timeout=2)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    rag.entities_vdb.finalize.assert_not_awaited()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    rag.entities_vdb.finalize.assert_awaited_once()
    assert graph.rag is None


@pytest.mark.asyncio
async def test_failed_cold_milvus_does_not_mark_store_initialized_and_releases_clients(
    graph: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rag = graph._create_rag(SimpleNamespace())
    rag.entities_vdb._create_collection_if_not_exist.side_effect = RuntimeError('private-token')
    monkeypatch.setattr(graph, '_create_rag', lambda tokenizer: rag)
    with pytest.raises(KnowledgeError) as caught:
        await graph._ensure()
    assert 'private' not in str(caught.value)
    assert not rag.entities_vdb._initialized
    rag.entities_vdb.finalize.assert_awaited_once()
    assert graph.rag is None


@pytest.mark.asyncio
async def test_initialization_timeout_then_repeated_shutdown_cancellation_drains_worker_before_finalizing(
    graph: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    exited = threading.Event()
    rag = graph._create_rag(SimpleNamespace())
    unsafe_finalizations = []

    def initialize() -> None:
        loop.call_soon_threadsafe(started.set)
        try:
            assert release.wait(timeout=2)
        finally:
            exited.set()

    async def finalize() -> None:
        unsafe_finalizations.append(not exited.is_set())

    rag.entities_vdb._create_collection_if_not_exist.side_effect = initialize
    rag.entities_vdb.finalize.side_effect = finalize
    monkeypatch.setattr(graph, '_create_rag', lambda tokenizer: rag)
    monkeypatch.setattr(graph_module, '_INITIALIZATION_TIMEOUT', 0.03)
    task = asyncio.create_task(graph._ensure())
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        await asyncio.sleep(0.06)  # wait_for has issued its first cancellation.
        for _ in range(3):
            task.cancel()  # shutdown can interrupt wait_for's cancellation wait on Python 3.10.
            await asyncio.sleep(0.01)
            assert not task.done()
            assert unsafe_finalizations == []
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert exited.is_set()
    assert unsafe_finalizations == [False]
    assert graph.rag is None
