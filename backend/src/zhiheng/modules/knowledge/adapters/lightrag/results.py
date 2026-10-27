"""Converge SDK response shapes into strict provenance-bearing public values."""

import hashlib
import json
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from zhiheng.modules.knowledge.graph.contracts import (
    GraphEdge,
    GraphFrozen,
    GraphNode,
    GraphQueryResult,
    GraphSource,
    GraphSubgraph,
)
from zhiheng.shared.errors import AppError

CONTEXT_MAX_BYTES = 24000


class _Entity(BaseModel):
    model_config = ConfigDict(extra='ignore')
    entity_name: str
    entity_type: str = 'UNKNOWN'
    description: str = ''
    source_id: str = ''


class _Relation(BaseModel):
    model_config = ConfigDict(extra='ignore')
    src_id: str
    tgt_id: str
    description: str = ''
    source_id: str = ''


class _Chunk(BaseModel):
    model_config = ConfigDict(extra='ignore')
    chunk_id: str
    content: str


class _Data(BaseModel):
    model_config = ConfigDict(extra='ignore')
    entities: list[_Entity] = Field(default_factory=list)
    relationships: list[_Relation] = Field(default_factory=list)
    chunks: list[_Chunk] = Field(default_factory=list)


def query_result(raw: object, frozen: GraphFrozen) -> GraphQueryResult:
    if not isinstance(raw, dict) or raw.get('status') != 'success':
        raise AppError('graph_query_empty', '图谱查询没有得到可用上下文', 409)
    data = _Data.model_validate(raw.get('data'))
    chunks = {chunk.sdk_id: chunk for chunk in frozen.chunks}
    requested = {row.chunk_id for row in data.chunks}
    for source_id in [row.source_id for row in data.entities] + [row.source_id for row in data.relationships]:
        requested.update(part for part in source_id.split('<SEP>') if part)
    if not requested <= chunks.keys():
        raise AppError('graph_provenance_invalid', '图谱结果引用未授权来源', 503)
    entities = [
        GraphNode(
            id=row.entity_name, entity_name=row.entity_name, entity_type=row.entity_type, description=row.description
        )
        for row in data.entities
    ]
    relations = [
        GraphEdge(
            id=hashlib.sha256((row.src_id + '\0' + row.tgt_id).encode()).hexdigest(),
            source=row.src_id,
            target=row.tgt_id,
            description=row.description,
        )
        for row in data.relationships
    ]
    sources = [
        GraphSource(
            document_id=chunks[key].document_id,
            file_id=chunks[key].file_id,
            chunk_id=chunks[key].id,
            ordinal=chunks[key].ordinal,
            content_hash=chunks[key].content_hash,
            content=chunks[key].content,
        )
        for key in sorted(requested)
    ]
    context = json.dumps(
        {
            'entities': [e.model_dump() for e in entities],
            'relationships': [r.model_dump() for r in relations],
            'chunks': [s.content for s in sources],
        },
        ensure_ascii=False,
        separators=(',', ':'),
    )
    # Context is explicitly bounded and derived from this single SDK retrieval;
    # structured sources remain complete provenance rather than byte-sliced JSON.
    encoded = context.encode('utf-8')
    if len(encoded) > CONTEXT_MAX_BYTES:
        context = encoded[:CONTEXT_MAX_BYTES].decode('utf-8', errors='ignore')
    return GraphQueryResult(context=context, entities=entities, relationships=relations, sources=sources)


def subgraph_result(raw: Any, revision: UUID) -> GraphSubgraph:
    nodes = []
    for row in raw.nodes:
        properties = row.properties
        nodes.append(
            GraphNode(
                id=str(row.id),
                entity_name=str(properties.get('entity_name', row.id)),
                entity_type=str(properties.get('entity_type', 'UNKNOWN')),
                description=str(properties.get('description', '')),
            )
        )
    edges = [
        GraphEdge(
            id=str(row.id),
            source=str(row.source),
            target=str(row.target),
            description=str(row.properties.get('description', '')),
        )
        for row in raw.edges
    ]
    return GraphSubgraph(revision=revision, nodes=nodes, edges=edges, is_truncated=bool(raw.is_truncated))
