"""Synthetic retrieval checks on one registered collection; no embedding model or business-quality claim."""

import hashlib
import os
from uuid import uuid4

import pytest

from tests.core.resources import resource
from zhiheng.modules.knowledge.adapters.milvus import MilvusStore
from zhiheng.modules.knowledge.vector import VectorSpec


async def test_real_milvus_chinese_bm25_and_dense_fusion() -> None:
    uri = os.getenv('ZHIHENG_TEST_MILVUS_URI')
    if not uri:
        pytest.skip('Separate Milvus URI required')
    base, revision, attempt = uuid4(), uuid4(), uuid4()
    spec = VectorSpec(base, revision, attempt, f'zh_k_{base.hex}_{revision.hex}_{attempt.hex}', 'a' * 64, 2, 2)
    store = MilvusStore(uri)
    documents = [uuid4(), uuid4(), uuid4()]
    texts = [
        '星河项目编号 ZX-10086，默认服务端口 8080。',
        '归档规范采用 UTF-8，资料状态为已发布。',
        '巡检计划每周执行，与项目代号无关。',
    ]
    vectors = [[0.6, 0.8], [0.0, 1.0], [-1.0, 0.0]]
    rows = [
        {
            'id': str(uuid4()),
            'document_id': str(document),
            'content_hash': hashlib.sha256(text.encode()).hexdigest(),
            'vector': vector,
            'text': text,
        }
        for document, text, vector in zip(documents, texts, vectors, strict=True)
    ]
    resource('intent', 'milvus_collection', spec.collection_name, description=spec.description(), database='default')
    try:
        await store.build(spec, rows)
        resource('created', 'milvus_collection', spec.collection_name)
        dense = await store.search(spec, [0.0, 1.0], 40, documents)
        dense_ids = [hit['id'] for hit in dense]
        assert dense_ids[:2] == [rows[1]['id'], rows[0]['id']]
        for query in ['星河项目', 'ZX-10086', '8080']:
            # The dense vector deliberately prefers another source. Unique
            # lexical terms must actually change the fused ranking.
            result = await store.hybrid_search(spec, [0.0, 1.0], query, 40, documents)
            ids = [hit['id'] for hit in result]
            assert ids[0] == rows[0]['id'], (query, ids, dense_ids)
            assert ids.index(rows[0]['id']) < ids.index(rows[1]['id'])
            assert len(result) <= 40 and len(set(ids)) == len(ids)
            assert all(hit['entity']['content_hash'] in {row['content_hash'] for row in rows} for hit in result)
        # These vectors are controlled fixtures representing the intended
        # semantic match, rather than embeddings from a real model.
        semantic = await store.hybrid_search(spec, [0.6, 0.8], '哪个入口能够连接服务', 40, documents)
        assert semantic[0]['id'] == rows[0]['id']
        exact = await store.hybrid_search(spec, [0.0, 1.0], 'UTF-8', 40, documents)
        assert exact[0]['id'] == rows[1]['id']
        unrelated = await store.hybrid_search(spec, [-1.0, 0.0], '今天的水果价格', 40, documents)
        # ANN still returns neighbors for an unrelated query. Assert only the
        # controlled ordering and closed provenance, without inventing relevance.
        assert unrelated[0]['id'] == rows[2]['id']
        assert {hit['id'] for hit in unrelated} <= {row['id'] for row in rows}
        # The lexical winner must be excluded from both paths by the same ACL.
        result = await store.hybrid_search(spec, [0.0, 1.0], '星河项目 ZX-10086', 40, [documents[1]])
        assert result and all(hit['entity']['document_id'] == str(documents[1]) for hit in result)
        assert rows[0]['id'] not in {hit['id'] for hit in result}
    finally:
        await store.drop(spec)
        resource('deleted', 'milvus_collection', spec.collection_name)
