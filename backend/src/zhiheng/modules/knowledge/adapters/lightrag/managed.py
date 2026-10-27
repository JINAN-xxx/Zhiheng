"""Managed ingestion preserves source identity while using real SDK KG stages."""

import asyncio
import hashlib
from typing import Any
from uuid import UUID

from lightrag.operate import extract_entities, merge_nodes_and_edges  # type: ignore[import-untyped]
from lightrag.utils import TokenLimitTruncationTally  # type: ignore[import-untyped]

from zhiheng.modules.knowledge.graph.contracts import GraphChunk, sdk_document_id
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow


async def insert_managed(rag: Any, chunks: list[GraphChunk], attempt_id: UUID) -> None:
    grouped: dict[UUID, list[GraphChunk]] = {}
    for chunk in chunks:
        grouped.setdefault(chunk.document_id, []).append(chunk)
    pipeline_status: dict[str, Any] = {'cancellation_requested': False, 'history_messages': [], 'latest_message': ''}
    lock = asyncio.Lock()
    for document_id, source_chunks in grouped.items():
        doc_id = sdk_document_id(document_id)
        path = 'managed/' + str(document_id)
        content = '\n'.join(chunk.content for chunk in source_chunks)
        now = utcnow().isoformat()
        status = {
            'content_summary': content[:100],
            'content_length': len(content),
            'file_path': path,
            'status': 'processing',
            'created_at': now,
            'updated_at': now,
            'track_id': str(attempt_id),
            'metadata': {'managed_source': str(document_id)},
            'content_hash': hashlib.sha256(content.encode()).hexdigest(),
            'chunks_count': len(source_chunks),
            'chunks_list': [chunk.sdk_id for chunk in source_chunks],
        }
        await rag.full_docs.upsert({doc_id: {'content': content, 'file_path': path, 'parse_format': 'raw'}})
        await rag.doc_status.upsert({doc_id: status})
        rows = {
            chunk.sdk_id: {
                'content': chunk.content,
                'full_doc_id': doc_id,
                'tokens': len(rag.tokenizer.encode(chunk.content)),
                'chunk_order_index': chunk.ordinal,
                'file_path': path,
            }
            for chunk in source_chunks
        }
        await rag.text_chunks.upsert(rows)
        await rag.chunks_vdb.upsert(rows)
        config = rag._build_global_config()
        tally = TokenLimitTruncationTally()
        extracted = await extract_entities(
            rows,
            config,
            pipeline_status=pipeline_status,
            pipeline_status_lock=lock,
            llm_response_cache=rag.llm_response_cache,
            text_chunks_storage=rag.text_chunks,
            truncation_tally=tally,
        )
        await merge_nodes_and_edges(
            chunk_results=extracted,
            knowledge_graph_inst=rag.chunk_entity_relation_graph,
            entity_vdb=rag.entities_vdb,
            relationships_vdb=rag.relationships_vdb,
            global_config=config,
            full_entities_storage=rag.full_entities,
            full_relations_storage=rag.full_relations,
            doc_id=doc_id,
            pipeline_status=pipeline_status,
            pipeline_status_lock=lock,
            llm_response_cache=rag.llm_response_cache,
            entity_chunks_storage=rag.entity_chunks,
            relation_chunks_storage=rag.relation_chunks,
            current_file_number=1,
            total_files=len(grouped),
            file_path=path,
            truncation_tally=tally,
        )
        if tally.as_metadata():
            raise AppError('graph_truncated', '图谱抽取或摘要出现截断', 422)
        await rag._insert_done()
        await rag.doc_status.upsert({doc_id: {**status, 'status': 'processed', 'updated_at': utcnow().isoformat()}})
