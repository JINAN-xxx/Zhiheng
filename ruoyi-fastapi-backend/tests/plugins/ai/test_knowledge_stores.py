import asyncio
import json
import os
import sys
import threading
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from plugins.ai.knowledge.models import KnowledgeError
from plugins.ai.knowledge.stores import ChromaStore, MilvusStore, _blocking

pytestmark = pytest.mark.asyncio


async def test_real_chroma_persists_explicit_vectors_and_never_replaces_incompatible_index(tmp_path: Path) -> None:
    pytest.importorskip('chromadb')
    store = ChromaStore(tmp_path / 'chroma')
    spec = {'model_id': 'mdl_demo', 'fingerprint': 'fingerprint-one', 'dimension': 3}
    chunks = [{'id': 'doc_chunk_0', 'doc_id': 'doc_demo', 'content': '报销需要发票。'}]
    await store.upsert('kb_example', chunks, [[1.0, 0.0, 0.5]], spec)
    result = await ChromaStore(tmp_path / 'chroma').query('kb_example', [1.0, 0.0, 0.5], 8, spec)
    assert result[0]['content'] == chunks[0]['content']
    assert result[0]['doc_id'] == 'doc_demo'
    with pytest.raises(KnowledgeError) as caught:
        await store.query('kb_example', [1.0, 0.0, 0.5], 8, {**spec, 'fingerprint': 'changed'})
    assert caught.value.code == 'KNOWLEDGE_REINDEX_REQUIRED'
    assert (await store.query('kb_example', [1.0, 0.0, 0.5], 8, spec))[0]['id'] == 'doc_chunk_0'
    await store.delete_chunks('kb_example', ['doc_chunk_0'])
    assert await store.query('kb_example', [1.0, 0.0, 0.5], 8, spec) == []
    await store.delete_database('kb_example')
    with pytest.raises(KnowledgeError):
        await store.query('kb_example', [1.0, 0.0, 0.5], 8, spec)


MILVUS_SPEC = {'model_id': 'mdl_demo', 'fingerprint': 'fingerprint-one', 'dimension': 3}
MILVUS_CHUNKS = [{'id': 'doc_chunk_0', 'doc_id': 'doc_demo', 'content': '报销需要发票。'}]
MILVUS_VECTORS = [[1.0, 0.0, 0.5]]


class FakeIndexParameters:
    def add_index(self, **kwargs: object) -> None:
        self.configuration = kwargs


class FakeMilvusClient:
    def __init__(self, failure_stage: str | None = None, *, exists: bool = False) -> None:
        self.failure_stage = failure_stage
        self.exists = exists
        self.index_exists = False
        self.loaded = False
        self.spec = dict(MILVUS_SPEC)
        self.calls: list[tuple[str, object]] = []

    def _fail_once(self, stage: str) -> None:
        if self.failure_stage == stage:
            self.failure_stage = None
            raise TimeoutError('Partial initialization timeout')

    def has_collection(self, name: str, **kwargs: object) -> bool:
        return self.exists

    def create_schema(self, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(add_field=self._add_field)

    def _add_field(self, *args: object, **kwargs: object) -> None:
        return None

    def prepare_index_params(self) -> FakeIndexParameters:
        return FakeIndexParameters()

    def create_collection(self, name: str, **kwargs: Any) -> None:
        self.calls.append(('create_collection', kwargs.get('timeout')))
        self.exists = True
        self._fail_once('collection')
        if kwargs.get('index_params') is not None:
            self.create_index(name, kwargs['index_params'], timeout=kwargs.get('timeout'))
            self.load_collection(name, timeout=kwargs.get('timeout'))

    def describe_collection(self, name: str, **kwargs: object) -> dict[str, str]:
        return {'description': json.dumps(self.spec)}

    def list_indexes(self, name: str, **kwargs: object) -> list[str]:
        self.calls.append(('list_indexes', kwargs.get('timeout')))
        return ['embedding'] if self.index_exists else []

    def create_index(self, name: str, index_params: object, **kwargs: object) -> None:
        self.calls.append(('create_index', kwargs.get('timeout')))
        self._fail_once('index')
        self.index_exists = True

    def load_collection(self, name: str, **kwargs: object) -> None:
        self.calls.append(('load_collection', kwargs.get('timeout')))
        assert self.index_exists
        self._fail_once('load')
        self.loaded = True

    def upsert(self, name: str, **kwargs: object) -> None:
        self.calls.append(('upsert', kwargs.get('timeout')))
        if not self.index_exists or not self.loaded:
            raise RuntimeError('Collection initialization was not recovered')

    def search(self, name: str, **kwargs: object) -> list[list[dict[str, object]]]:
        self.calls.append(('search', kwargs.get('timeout')))
        return [[{'id': 'doc_chunk_0', 'distance': 0.9, 'entity': {'doc_id': 'doc_demo', 'content': '报销需要发票。'}}]]


def _milvus_store(monkeypatch: pytest.MonkeyPatch, client: FakeMilvusClient) -> MilvusStore:
    monkeypatch.setitem(sys.modules, 'pymilvus', SimpleNamespace(DataType=SimpleNamespace(VARCHAR=1, FLOAT_VECTOR=2)))
    store = MilvusStore()
    store._client = client
    return store


@pytest.mark.parametrize('failure_stage', ['collection', 'index', 'load'])
async def test_milvus_write_recovers_partial_collection_initialization(
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    client = FakeMilvusClient(failure_stage)
    store = _milvus_store(monkeypatch, client)
    with pytest.raises(TimeoutError):
        await store.upsert('kb_example', MILVUS_CHUNKS, MILVUS_VECTORS, MILVUS_SPEC)
    assert client.exists
    await store.upsert('kb_example', MILVUS_CHUNKS, MILVUS_VECTORS, MILVUS_SPEC)
    assert client.loaded
    assert client.index_exists
    assert [name for name, _ in client.calls].count('create_collection') == 1
    expected_index_attempts = 2 if failure_stage == 'index' else 1
    assert [name for name, _ in client.calls].count('create_index') == expected_index_attempts


async def test_milvus_query_does_not_create_index_or_load_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeMilvusClient(exists=True)
    store = _milvus_store(monkeypatch, client)
    rows = await store.query('kb_example', MILVUS_VECTORS[0], 8, MILVUS_SPEC)
    assert rows[0]['content'] == MILVUS_CHUNKS[0]['content']
    assert [name for name, _ in client.calls] == ['search']
    client.exists = False
    with pytest.raises(KnowledgeError):
        await store.query('kb_example', MILVUS_VECTORS[0], 8, MILVUS_SPEC)
    assert [name for name, _ in client.calls] == ['search']


async def test_milvus_write_rejects_mismatched_spec_before_initialization(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeMilvusClient(exists=True)
    client.spec['fingerprint'] = 'different'
    store = _milvus_store(monkeypatch, client)
    with pytest.raises(KnowledgeError) as raised:
        await store.upsert('kb_example', MILVUS_CHUNKS, MILVUS_VECTORS, MILVUS_SPEC)
    assert raised.value.code == 'KNOWLEDGE_REINDEX_REQUIRED'
    assert client.calls == []


async def test_milvus_initialization_has_a_longer_timeout_than_search(monkeypatch: pytest.MonkeyPatch) -> None:
    client = FakeMilvusClient()
    store = _milvus_store(monkeypatch, client)
    await store.upsert('kb_example', MILVUS_CHUNKS, MILVUS_VECTORS, MILVUS_SPEC)
    await store.query('kb_example', MILVUS_VECTORS[0], 8, MILVUS_SPEC)
    search_timeout = next(timeout for name, timeout in client.calls if name == 'search')
    initializations = [
        timeout for name, timeout in client.calls if name in {'create_collection', 'create_index', 'load_collection'}
    ]
    assert initializations
    assert all(timeout > search_timeout for timeout in initializations)


async def test_real_milvus_write_recovers_unindexed_and_unloaded_collections() -> None:
    uri = os.getenv('KNOWLEDGE_MILVUS_TEST_URI')
    if not uri:
        pytest.skip('Set KNOWLEDGE_MILVUS_TEST_URI for the isolated Milvus integration test')
    sdk = pytest.importorskip('pymilvus')
    client = sdk.MilvusClient(uri=uri, timeout=60)
    collection_name = 'kb_recovery_test_' + uuid4().hex
    store = MilvusStore()
    store._client = client
    try:
        schema = client.create_schema(auto_id=False, enable_dynamic_field=True, description=json.dumps(MILVUS_SPEC))
        schema.add_field('id', sdk.DataType.VARCHAR, is_primary=True, max_length=128)
        schema.add_field('embedding', sdk.DataType.FLOAT_VECTOR, dim=MILVUS_SPEC['dimension'])
        client.create_collection(collection_name, schema=schema, consistency_level='Strong', timeout=60)
        assert client.list_indexes(collection_name, timeout=8) == []
        await store.upsert(collection_name, MILVUS_CHUNKS, MILVUS_VECTORS, MILVUS_SPEC)
        assert (await store.query(collection_name, MILVUS_VECTORS[0], 8, MILVUS_SPEC))[0]['id'] == 'doc_chunk_0'
        client.release_collection(collection_name, timeout=60)
        await store.upsert(collection_name, MILVUS_CHUNKS, MILVUS_VECTORS, MILVUS_SPEC)
        assert (await store.query(collection_name, MILVUS_VECTORS[0], 8, MILVUS_SPEC))[0]['content'] == MILVUS_CHUNKS[
            0
        ]['content']
        with pytest.raises(KnowledgeError):
            await store.upsert(
                collection_name, MILVUS_CHUNKS, MILVUS_VECTORS, {**MILVUS_SPEC, 'fingerprint': 'different'}
            )
        assert client.has_collection(collection_name, timeout=8)
    finally:
        try:
            if client.has_collection(collection_name, timeout=8):
                client.drop_collection(collection_name, timeout=60)
        finally:
            await store.close()


async def test_blocking_waits_for_worker_after_repeated_cancellation_and_preserves_context() -> None:
    request_context = ContextVar('blocking_test_context', default='missing')
    request_context.set('request-value')
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    exited = threading.Event()
    worker_context = []

    def operation() -> None:
        worker_context.append(request_context.get())
        loop.call_soon_threadsafe(started.set)
        try:
            assert release.wait(timeout=2)
        finally:
            exited.set()

    task = asyncio.create_task(_blocking(operation))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0.01)
            assert not task.done()
            assert not exited.is_set()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert exited.is_set()
    assert worker_context == ['request-value']
