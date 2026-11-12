import json
from typing import Any
from uuid import uuid4

import pytest

from zhiheng.modules.knowledge.adapters import milvus
from zhiheng.modules.knowledge.vector import VectorSpec
from zhiheng.shared.errors import AppError


def spec(version: int = 2) -> VectorSpec:
    base, revision, attempt = uuid4(), uuid4(), uuid4()
    return VectorSpec(base, revision, attempt, f'zh_k_{base.hex}_{revision.hex}_{attempt.hex}', 'a' * 64, 3, version)


def test_version_one_identity_stays_compatible() -> None:
    identity = spec(1)
    assert 'retrieval_version' not in json.loads(identity.description())
    assert json.loads(spec().description())['retrieval_version'] == 2


async def test_hybrid_uses_equal_filters_and_forty_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, dict[str, Any]]] = []

    class Client:
        def hybrid_search(self, collection: str, requests: list[Any], **kwargs: Any) -> list[list[dict[str, Any]]]:
            calls.append((requests, kwargs))
            return [[{'id': 'preserved'}]]

    store = milvus.MilvusStore('mock://only')
    monkeypatch.setattr(store, '_validate', lambda *args, **kwargs: None)

    async def call(operation: Any, **kwargs: Any) -> Any:
        return operation(Client())

    monkeypatch.setattr(store, '_call', call)
    documents = [uuid4(), uuid4()]
    result = await store.hybrid_search(spec(), [1.0, 0.0, 0.0], '项目编号 10086', 40, documents)
    assert result == [{'id': 'preserved'}]
    requests, options = calls[0]
    assert [request.limit for request in requests] == [40, 40]
    assert [request.anns_field for request in requests] == ['vector', 'sparse']
    assert requests[0].expr == requests[1].expr
    assert all(str(value) in requests[0].expr for value in documents)
    assert requests[1].data == ['项目编号 10086']
    assert options['limit'] == 40
    assert options['ranker'].dict()['params']['weights'] == [0.7, 0.3]


async def test_legacy_generation_cannot_claim_hybrid() -> None:
    with pytest.raises(AppError, match='基础检索'):
        await milvus.MilvusStore('mock://only').hybrid_search(spec(1), [1.0, 0.0, 0.0], 'term', 40, [uuid4()])


def test_v2_collection_schema_has_real_chinese_bm25(monkeypatch: pytest.MonkeyPatch) -> None:
    from pymilvus import DataType, FunctionType

    identity = spec()
    schemas: list[Any] = []
    indexes: list[Any] = []

    class Client:
        def has_collection(self, *args: Any, **kwargs: Any) -> bool:
            return False

        def create_collection(self, *args: Any, **kwargs: Any) -> None:
            schemas.append(kwargs['schema'].to_dict())

        def list_indexes(self, *args: Any, **kwargs: Any) -> list[str]:
            return []

        def prepare_index_params(self) -> Any:
            return milvus.MilvusClient.prepare_index_params()

        def create_index(self, collection: str, params: Any, **kwargs: Any) -> None:
            indexes.extend(row.to_dict() for row in params)

        def load_collection(self, *args: Any, **kwargs: Any) -> None:
            pass

        def query(self, *args: Any, **kwargs: Any) -> list[dict[str, int]]:
            return [{'count(*)': 0}]

    store = milvus.MilvusStore('mock://only')
    monkeypatch.setattr(store, '_validate', lambda *args, **kwargs: None)

    async def call(operation: Any, **kwargs: Any) -> Any:
        return operation(Client())

    monkeypatch.setattr(store, '_call', call)
    import asyncio

    asyncio.run(store.build(identity, []))
    fields = {row['name']: row for row in schemas[0]['fields']}
    assert fields['text']['params']['enable_analyzer'] is True
    assert json.loads(fields['text']['params']['analyzer_params'])['tokenizer'] == 'jieba'
    assert fields['sparse']['type'] == DataType.SPARSE_FLOAT_VECTOR
    assert schemas[0]['functions'][0]['type'] == FunctionType.BM25
    assert schemas[0]['functions'][0]['input_field_names'] == ['text']
    assert any(row['index_type'] == 'SPARSE_INVERTED_INDEX' and row['metric_type'] == 'BM25' for row in indexes)
