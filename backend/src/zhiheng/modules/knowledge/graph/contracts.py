"""Strict, public graph values; physical storage identities never cross HTTP."""

import hashlib
import json
import re
from base64 import b32encode
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from zhiheng.modules.models.public import ModelMessage
from zhiheng.runtime.contracts import RunError
from zhiheng.shared.errors import AppError

GraphOperation = Literal['build', 'preview', 'query', 'gc']
GraphModelRole = Literal['extract', 'keyword', 'query', 'vlm', 'embedding', 'rerank']
GraphModelOperation = Literal['generation', 'keyword', 'embedding', 'rerank']
GraphStatus = Literal[
    'pending', 'running', 'waiting_model', 'draining', 'completed', 'failed', 'cancelled', 'interrupted'
]
CONTEXT_MAX_BYTES = 24000
GRAPH_TERMINAL = frozenset({'completed', 'failed', 'cancelled', 'interrupted'})


class GraphValue(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class GraphConfig(GraphValue):
    version: int = Field(strict=True, ge=1)
    llm_model_id: UUID
    reasoning_strategy: Literal['auto', 'default', 'low', 'disabled'] = 'default'
    timeout_seconds: int = Field(default=240, strict=True, ge=30, le=600)
    max_output_tokens: int = Field(default=16384, strict=True, ge=1024, le=32768)
    concurrency: int = Field(default=2, strict=True, ge=1, le=4)


class GraphConfigPatch(GraphValue):
    expected_version: int = Field(strict=True, ge=0)
    llm_model_id: UUID
    reasoning_strategy: Literal['auto', 'default', 'low', 'disabled'] = 'default'
    timeout_seconds: int = Field(default=240, strict=True, ge=30, le=600)
    max_output_tokens: int = Field(default=16384, strict=True, ge=1024, le=32768)
    concurrency: int = Field(default=2, strict=True, ge=1, le=4)


class GraphConfigInfo(GraphValue):
    config: GraphConfig | None
    available: bool
    active_revision: UUID | None
    requires_rebuild: bool
    can_manage: bool
    supported_reasoning_strategies: tuple[Literal['auto', 'default'], ...]


class GraphBuildRequest(GraphValue):
    client_request_id: UUID
    expected_version: int = Field(strict=True, ge=1)
    rebuild: bool = Field(default=False, strict=True)


class GraphPreviewRequest(GraphValue):
    client_request_id: UUID
    expected_version: int = Field(strict=True, ge=1)
    document_id: UUID | None = None


class GraphQueryRequest(GraphValue):
    client_request_id: UUID
    query: str = Field(min_length=1, max_length=8000)
    mode: Literal['mix', 'local', 'global', 'hybrid', 'naive'] = 'mix'
    top_k: int = Field(default=8, strict=True, ge=1, le=8)

    @field_validator('query')
    @classmethod
    def text(cls, value: str) -> str:
        value.encode('utf-8')
        if not value.strip() or '\x00' in value:
            raise ValueError('Invalid query')
        return value


class GraphSubgraphRequest(GraphValue):
    node_label: str = Field(default='*', min_length=1, max_length=512)
    max_depth: int = Field(default=2, strict=True, ge=1, le=5)
    max_nodes: int = Field(default=100, strict=True, ge=1, le=200)

    @field_validator('node_label')
    @classmethod
    def valid_label(cls, value: str) -> str:
        value.encode('utf-8')
        if not value.strip() or '\x00' in value:
            raise ValueError('Invalid node label')
        return value


class GraphNode(GraphValue):
    id: str
    entity_name: str
    entity_type: str
    description: str


class GraphEdge(GraphValue):
    id: str
    source: str
    target: str
    description: str


class GraphSource(GraphValue):
    document_id: UUID
    file_id: UUID
    chunk_id: UUID
    ordinal: int = Field(strict=True, ge=0)
    content_hash: str = Field(pattern='^[a-f0-9]{64}$')
    content: str


class GraphSubgraph(GraphValue):
    revision: UUID
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    is_truncated: bool


class GraphLabels(GraphValue):
    items: list[str] = Field(max_length=200)
    truncated: bool


class GraphQueryResult(GraphValue):
    context: str
    entities: list[GraphNode]
    relationships: list[GraphEdge]
    sources: list[GraphSource]
    diagnostics: dict[str, object] = Field(default_factory=dict)

    @field_validator('context')
    @classmethod
    def context_limit(cls, value: str) -> str:
        if len(value.encode('utf-8')) > CONTEXT_MAX_BYTES:
            raise ValueError('Context exceeds 24000 UTF8 bytes')
        return value


class GraphPreviewResult(GraphValue):
    entities: int = Field(strict=True, ge=0)
    relations: int = Field(strict=True, ge=0)
    chunks: int = Field(strict=True, ge=0)
    seconds: float = Field(ge=0, allow_inf_nan=False)


class GraphProgress(GraphValue):
    extracted_chunks: int | None = None
    total_chunks: int | None = None
    entities: int | None = None
    relations: int | None = None


class GraphJobInfo(GraphValue):
    id: UUID
    base_id: UUID
    operation: GraphOperation
    status: GraphStatus
    phase: str
    version: int
    attempt_id: UUID
    graph_revision: UUID | None
    processed_documents: int | None
    total_documents: int | None
    progress: GraphProgress
    error: RunError | None
    result: GraphPreviewResult | GraphQueryResult | None
    cancel_allowed: bool
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None


class GraphChunk(GraphValue):
    id: UUID
    document_id: UUID
    file_id: UUID
    index_revision: UUID
    document_revision: UUID
    ordinal: int = Field(strict=True, ge=0)
    content: str = Field(min_length=1)
    content_hash: str = Field(pattern='^[a-f0-9]{64}$')
    sdk_id: str = Field(max_length=64)

    @model_validator(mode='after')
    def managed_identity(self) -> 'GraphChunk':
        if hashlib.sha256(self.content.encode()).hexdigest() != self.content_hash or self.sdk_id != sdk_chunk_id(
            self.document_id, self.id
        ):
            raise ValueError('Managed chunk identity or content hash mismatch')
        return self


class GraphDocumentSource(GraphValue):
    document_id: UUID
    document_version: int = Field(strict=True, ge=1)
    document_revision: UUID
    file_id: UUID
    file_owner_id: UUID
    version: int = Field(strict=True, ge=1)
    acl_version: int = Field(strict=True, ge=1)
    sha256: str = Field(pattern='^[a-f0-9]{64}$')
    artifact_file_id: UUID
    artifact_version: int = Field(strict=True, ge=1)
    artifact_acl_version: int = Field(strict=True, ge=1)
    artifact_sha256: str = Field(pattern='^[a-f0-9]{64}$')
    parser_version: str = Field(min_length=1, max_length=100)


class GraphFrozen(GraphValue):
    base_id: UUID
    base_owner_id: UUID
    config: GraphConfig
    llm_binding: dict[str, object]
    embedding_binding: dict[str, object]
    index_revision: UUID
    dimension: int = Field(strict=True, ge=1, le=65536)
    base_version: int
    base_acl_version: int
    sources: list[GraphDocumentSource]
    chunks: list[GraphChunk]
    sdk_version: str = '1.5.7'
    prompt_version: str = 'sdk-1.5.7'
    tokenizer_asset_sha256: str = '446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d'
    tokenizer_spec_sha256: str = '1764e80429b8e033159307c9a2bf270a516406156c4bee22b0f87c6f9c6d0918'
    content_fingerprint: str = Field(pattern='^[a-f0-9]{64}$')
    fingerprint: str = Field(pattern='^[a-f0-9]{64}$')


def sdk_document_id(document_id: UUID) -> str:
    return 'd' + b32encode(document_id.bytes).decode('ascii').rstrip('=').lower()


def sdk_chunk_id(document_id: UUID, chunk_id: UUID) -> str:
    return sdk_document_id(document_id) + '-c' + b32encode(chunk_id.bytes).decode('ascii').rstrip('=').lower()


VectorRole = Literal['entities', 'relationships', 'chunks']
MAX_GRAPH_DIMENSION = 65536


@dataclass(frozen=True)
class GraphVectorManifest:
    base_id: UUID
    revision: UUID
    attempt_id: UUID
    fingerprint: str
    dimension: int
    workspace: str

    def __post_init__(self) -> None:
        if (
            self.workspace != 'g_' + self.attempt_id.hex
            or not re.fullmatch('[a-f0-9]{64}', self.fingerprint)
            or not 1 <= self.dimension <= MAX_GRAPH_DIMENSION
        ):
            raise AppError('graph_manifest_invalid', '图谱版本完整身份无效', 503)

    def dump(self) -> dict[str, object]:
        return {
            'base_id': str(self.base_id),
            'revision': str(self.revision),
            'attempt_id': str(self.attempt_id),
            'fingerprint': self.fingerprint,
            'dimension': self.dimension,
            'workspace': self.workspace,
        }

    @classmethod
    def load(cls, value: dict[str, object]) -> 'GraphVectorManifest':
        dimension = value['dimension']
        if type(dimension) is not int:
            raise AppError('graph_manifest_invalid', '图谱版本身份无效', 503)
        return cls(
            UUID(str(value['base_id'])),
            UUID(str(value['revision'])),
            UUID(str(value['attempt_id'])),
            str(value['fingerprint']),
            dimension,
            str(value['workspace']),
        )

    def collection(self, role: VectorRole) -> str:
        return 'zh_g_' + hashlib.sha256((self.description(role)).encode()).hexdigest()

    def description(self, role: VectorRole) -> str:
        return json.dumps(
            {
                'sdk': '1.5.7',
                'base': str(self.base_id),
                'revision': str(self.revision),
                'attempt': str(self.attempt_id),
                'fingerprint': self.fingerprint,
                'dimension': self.dimension,
                'workspace': self.workspace,
                'role': role,
            },
            sort_keys=True,
        )


@dataclass(frozen=True)
class GraphSDKCall:
    role: GraphModelRole
    operation: GraphModelOperation
    messages: tuple[ModelMessage, ...]
    output_tokens: int | None = None
    json_object: bool = False


@dataclass(frozen=True)
class GraphSDKOutcome:
    result: dict[str, object]
    manifest: dict[str, object]
    stopped: bool
