# ruff: noqa: PLC0415
# Storage SDKs stay optional and are loaded only when selected.
"""Optional stores adapted from f976247; no credentials or query-time rebuilds."""

import asyncio
import importlib.util
import json
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from plugins.ai.knowledge.models import KnowledgeError

MILVUS_INITIALIZATION_TIMEOUT_SECONDS = 60
MILVUS_REQUEST_TIMEOUT_SECONDS = 8


def available_types() -> dict:
    from config.env import AppConfig

    def installed(name: str) -> bool:
        return importlib.util.find_spec(name) is not None

    milvus_configured = bool(os.getenv('KNOWLEDGE_MILVUS_URI') or os.getenv('MILVUS_URI'))
    graph_configured = AppConfig.app_workers == 1 and all(
        os.getenv(key) for key in ('MILVUS_URI', 'NEO4J_URI', 'NEO4J_USERNAME', 'NEO4J_PASSWORD')
    )
    return {
        'chroma': {'available': installed('chromadb'), 'description': '本地持久化向量知识库'},
        'milvus': {'available': installed('pymilvus') and milvus_configured, 'description': 'Milvus 向量知识库'},
        'lightrag': {
            'available': installed('lightrag') and installed('pymilvus') and installed('neo4j') and graph_configured,
            'description': 'Milvus 向量与 Neo4j 知识图谱（需要单 worker）',
        },
    }


def require_graph() -> None:
    if not available_types()['lightrag']['available']:
        raise KnowledgeError('KNOWLEDGE_GRAPH_UNAVAILABLE', '知识图谱依赖或连接配置尚未就绪', 503)


def create_store(kind: str, data_dir: Path) -> Any:
    if kind not in available_types() or not available_types()[kind]['available']:
        raise KnowledgeError.unavailable()
    return ChromaStore(data_dir / 'chroma') if kind == 'chroma' else MilvusStore()


async def _blocking(operation: Callable[[], Any]) -> Any:
    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        finally:
            raise


class ChromaStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._client = None

    def _get_client(self) -> Any:
        if self._client is None:
            import chromadb
            from chromadb.config import Settings

            self._client = chromadb.PersistentClient(
                path=str(self.directory), settings=Settings(anonymized_telemetry=False)
            )
        return self._client

    def _collection(self, db_id: str, spec: dict, *, create: bool = False) -> Any:
        from chromadb.errors import NotFoundError

        client = self._get_client()
        try:
            collection = client.get_collection(db_id, embedding_function=None)
        except NotFoundError:
            if not create:
                raise KnowledgeError.unavailable() from None
            collection = client.create_collection(
                db_id, embedding_function=None, metadata={**spec, 'hnsw:space': 'cosine'}
            )
        if any((collection.metadata or {}).get(key) != value for key, value in spec.items()):
            raise KnowledgeError.reindex()
        return collection

    async def upsert(self, db_id: str, chunks: list[dict], vectors: list[list[float]], spec: dict) -> None:
        def write() -> None:
            collection = self._collection(db_id, spec, create=True)
            for offset in range(0, len(chunks), 128):
                batch = chunks[offset : offset + 128]
                collection.upsert(
                    ids=[chunk['id'] for chunk in batch],
                    documents=[chunk['content'] for chunk in batch],
                    embeddings=vectors[offset : offset + 128],
                    metadatas=[{'doc_id': chunk['doc_id']} for chunk in batch],
                )

        await _blocking(write)

    async def query(self, db_id: str, vector: list[float], top_k: int, spec: dict) -> list[dict]:
        def search() -> list[dict]:
            collection = self._collection(db_id, spec)
            count = collection.count()
            if not count:
                return []
            result = collection.query(
                query_embeddings=[vector], n_results=min(count, top_k), include=['documents', 'metadatas', 'distances']
            )
            return [
                {'id': identifier, 'doc_id': metadata['doc_id'], 'content': content, 'score': 1 - distance}
                for identifier, metadata, content, distance in zip(
                    result['ids'][0],
                    result['metadatas'][0],
                    result['documents'][0],
                    result['distances'][0],
                    strict=True,
                )
            ]

        return await _blocking(search)

    async def delete_chunks(self, db_id: str, chunk_ids: Sequence[str]) -> None:
        if not chunk_ids:
            return
        from chromadb.errors import NotFoundError

        def delete() -> None:
            try:
                collection = self._get_client().get_collection(db_id, embedding_function=None)
            except NotFoundError:
                return
            for offset in range(0, len(chunk_ids), 128):
                collection.delete(ids=chunk_ids[offset : offset + 128])

        await _blocking(delete)

    async def delete_database(self, db_id: str) -> None:
        from chromadb.errors import NotFoundError

        def delete() -> None:
            try:
                self._get_client().delete_collection(db_id)
            except NotFoundError:
                pass

        await _blocking(delete)

    async def close(self) -> None:
        self._client = None


class MilvusStore:
    def __init__(self) -> None:
        self._client = None

    def _get_client(self) -> Any:
        if self._client is None:
            from pymilvus import MilvusClient

            self._client = MilvusClient(
                uri=os.getenv('KNOWLEDGE_MILVUS_URI') or os.getenv('MILVUS_URI'),
                token=os.getenv('KNOWLEDGE_MILVUS_TOKEN') or os.getenv('MILVUS_TOKEN', ''),
                db_name=os.getenv('KNOWLEDGE_MILVUS_DB_NAME') or os.getenv('MILVUS_DB_NAME', 'default'),
                timeout=MILVUS_REQUEST_TIMEOUT_SECONDS,
            )
        return self._client

    def _collection(self, db_id: str, spec: dict, *, create: bool = False) -> Any:
        from pymilvus import DataType

        client = self._get_client()
        if not client.has_collection(db_id, timeout=MILVUS_REQUEST_TIMEOUT_SECONDS):
            if not create:
                raise KnowledgeError.unavailable()
            schema = client.create_schema(
                auto_id=False, enable_dynamic_field=True, description=json.dumps(spec, sort_keys=True)
            )
            schema.add_field('id', DataType.VARCHAR, is_primary=True, max_length=128)
            schema.add_field('embedding', DataType.FLOAT_VECTOR, dim=spec['dimension'])
            client.create_collection(
                db_id,
                schema=schema,
                consistency_level='Strong',
                timeout=MILVUS_INITIALIZATION_TIMEOUT_SECONDS,
            )
        description = client.describe_collection(db_id, timeout=MILVUS_REQUEST_TIMEOUT_SECONDS)
        try:
            if json.loads(description.get('description', '{}')) != spec:
                raise KnowledgeError.reindex()
        except ValueError:
            raise KnowledgeError.reindex() from None
        if create:
            self._prepare_collection_for_write(client, db_id)
        return client

    @staticmethod
    def _prepare_collection_for_write(client: Any, db_id: str) -> None:
        # Collection creation, index creation and loading can time out independently.
        # Resume missing steps only after the persisted embedding specification matches.
        indexes = client.list_indexes(db_id, field_name='embedding', timeout=MILVUS_REQUEST_TIMEOUT_SECONDS)
        if not indexes:
            index = client.prepare_index_params()
            index.add_index(
                field_name='embedding', index_type='HNSW', metric_type='COSINE', params={'M': 16, 'efConstruction': 256}
            )
            client.create_index(db_id, index, timeout=MILVUS_INITIALIZATION_TIMEOUT_SECONDS)
        # Loading an already loaded collection is idempotent and also resumes interrupted loads.
        client.load_collection(db_id, timeout=MILVUS_INITIALIZATION_TIMEOUT_SECONDS)

    async def upsert(self, db_id: str, chunks: list[dict], vectors: list[list[float]], spec: dict) -> None:
        def write() -> None:
            client = self._collection(db_id, spec, create=True)
            for offset in range(0, len(chunks), 128):
                client.upsert(
                    db_id,
                    data=[
                        dict(chunk, embedding=vector)
                        for chunk, vector in zip(
                            chunks[offset : offset + 128], vectors[offset : offset + 128], strict=True
                        )
                    ],
                    timeout=15,
                )

        await _blocking(write)

    async def query(self, db_id: str, vector: list[float], top_k: int, spec: dict) -> list[dict]:
        def search() -> list[dict]:
            client = self._collection(db_id, spec)
            result = client.search(
                db_id,
                data=[vector],
                limit=top_k,
                anns_field='embedding',
                search_params={'metric_type': 'COSINE', 'params': {'ef': 64}},
                output_fields=['doc_id', 'content'],
                timeout=MILVUS_REQUEST_TIMEOUT_SECONDS,
            )
            return [dict(hit['entity'], id=hit['id'], score=hit['distance']) for hit in result[0]]

        return await _blocking(search)

    async def delete_chunks(self, db_id: str, chunk_ids: Sequence[str]) -> None:
        if chunk_ids:

            def delete() -> None:
                client = self._get_client()
                if client.has_collection(db_id, timeout=MILVUS_REQUEST_TIMEOUT_SECONDS):
                    client.delete(db_id, ids=chunk_ids, timeout=15)

            await _blocking(delete)

    async def delete_database(self, db_id: str) -> None:
        def delete() -> None:
            client = self._get_client()
            if client.has_collection(db_id, timeout=MILVUS_REQUEST_TIMEOUT_SECONDS):
                client.drop_collection(db_id, timeout=15)

        await _blocking(delete)

    async def close(self) -> None:
        if self._client is not None:
            await _blocking(self._client.close)
            self._client = None
