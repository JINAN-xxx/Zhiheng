# ruff: noqa: PLC0415
# LightRAG and its optional dependencies load only when graph features are used.
"""LightRAG graph construction/exploration adapted from source f976247.

Generated answers remain a management feature; Agent retrieval uses the parallel
vector index with verified document/chunk membership in KnowledgeRuntime.
"""

import asyncio
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from module_Knowledge.src.knowledge.kb_utils import normalize_embedding_text
from plugins.ai.domain.model_runtime import ModelMessage
from plugins.ai.knowledge.models import KnowledgeError, QueryParameters
from plugins.ai.knowledge.stores import require_graph


class KnowledgeGraph:
    def __init__(self, data_dir: Path, database: dict, model_service_factory: Any) -> None:
        require_graph()
        self.directory = data_dir / 'graph'
        self.database = database
        self.model_service_factory = model_service_factory
        self.rag = None

    async def _ensure(self) -> Any:
        if self.rag is not None:
            return self.rag
        import numpy as np
        from lightrag import LightRAG
        from lightrag.kg.shared_storage import initialize_pipeline_status
        from lightrag.utils import EmbeddingFunc

        async def embed(texts: Sequence[str], **kwargs) -> Any:
            async with self.model_service_factory() as service:
                result = await asyncio.wait_for(
                    service.embed(self.database['embed_model_id'], [normalize_embedding_text(text) for text in texts]),
                    timeout=30,
                )
            return np.array([vector.values for vector in sorted(result.response.vectors, key=lambda item: item.index)])

        async def complete(
            prompt: str, system_prompt: str | None = None, history_messages: list[dict] | None = None, **kwargs
        ) -> str:
            messages = []
            if system_prompt:
                messages.append(ModelMessage(role='system', content=system_prompt))
            messages.extend(
                ModelMessage(role=item['role'], content=item['content']) for item in (history_messages or [])
            )
            messages.append(ModelMessage(role='user', content=prompt))
            async with self.model_service_factory() as service:
                result = await asyncio.wait_for(service.chat(self.database['llm_model_id'], messages), timeout=45)
            return result.response.text

        spec = self.database.get('index_spec')
        if not spec:
            raise KnowledgeError.unavailable()
        rag = LightRAG(
            working_dir=str(self.directory),
            workspace=self.database['db_id'],
            embedding_func=EmbeddingFunc(embedding_dim=spec['dimension'], max_token_size=4096, func=embed),
            llm_model_func=complete,
            vector_storage='MilvusVectorDBStorage',
            kv_storage='JsonKVStorage',
            graph_storage='Neo4JStorage',
            doc_status_storage='JsonDocStatusStorage',
            chunk_token_size=self.database['chunk_size'],
            chunk_overlap_token_size=self.database['chunk_overlap'],
            default_llm_timeout=45,
            addon_params={'language': 'Chinese'},
        )
        await rag.initialize_storages()
        await initialize_pipeline_status(workspace=self.database['db_id'])
        self.rag = rag
        return rag

    async def insert(self, doc_id: str, content: str, title: str) -> None:
        rag = await self._ensure()
        await rag.ainsert(input=content, ids=doc_id, file_paths=title)
        status = await rag.doc_status.get_by_id(doc_id)
        if not status or str(status.get('status', '')).lower() not in {'processed', 'docstatus.processed'}:
            raise KnowledgeError('KNOWLEDGE_GRAPH_INDEX_FAILED', '知识图谱索引未完成，请重试', 503)

    async def delete_document(self, doc_id: str) -> None:
        rag = await self._ensure()
        result = await rag.adelete_by_doc_id(doc_id)
        if result is not None and getattr(result, 'status', 'success') not in {'success', 'not_found'}:
            raise KnowledgeError.unavailable()

    async def labels(self) -> list[str]:
        return await (await self._ensure()).get_graph_labels()

    async def subgraph(self, node_label: str, max_depth: int, max_nodes: int) -> dict:
        graph = await (await self._ensure()).get_knowledge_graph(
            node_label=node_label,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )
        nodes = [
            {
                'id': node.id,
                'entity_name': node.properties.get('entity_id') or node.properties.get('name') or node.id,
                'entity_type': node.properties.get('entity_type', 'unknown'),
            }
            for node in graph.nodes
        ]
        edges = [
            {'id': edge.id, 'source': edge.source, 'target': edge.target, 'type': edge.type} for edge in graph.edges
        ]
        return {'nodes': nodes, 'edges': edges, 'is_truncated': graph.is_truncated}

    async def query(self, query: str, params: QueryParameters) -> str:
        from lightrag import QueryParam

        return await (await self._ensure()).aquery(
            query,
            QueryParam(
                mode=params.mode,
                only_need_context=params.only_need_context,
                top_k=params.top_k,
            ),
        )

    async def delete_database(self, document_ids: Sequence[str]) -> None:
        for doc_id in document_ids:
            await self.delete_document(doc_id)
        await self.close()
        path = self.directory / self.database['db_id']
        if path.exists():
            await asyncio.to_thread(shutil.rmtree, path)

    async def close(self) -> None:
        if self.rag is not None:
            await self.rag.finalize_storages()
            self.rag = None
