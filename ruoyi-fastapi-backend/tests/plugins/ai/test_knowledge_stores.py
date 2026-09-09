from pathlib import Path

import pytest

from plugins.ai.knowledge.models import KnowledgeError
from plugins.ai.knowledge.stores import ChromaStore

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
