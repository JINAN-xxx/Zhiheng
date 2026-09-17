import hashlib
import json
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from plugins.ai.entity.vo.ai_model_catalog_vo import ModelResourceId

Name = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)]


def embedding_fingerprint(resolved: Any) -> str:
    """Keep the persisted index fingerprint stable across runtime and graph storage."""
    parts = [
        resolved.platform_model_id,
        resolved.binding_id,
        resolved.engine_model_snapshot,
        (resolved.default_parameters or {}).get('dimensions'),
    ]
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


class KnowledgeError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code

    def public_dict(self) -> dict[str, str]:
        return {'code': self.code, 'message': self.message}

    @classmethod
    def unavailable(cls) -> 'KnowledgeError':
        return cls('KNOWLEDGE_UNAVAILABLE', '知识库服务暂不可用，请检查存储和模型配置', 503)

    @classmethod
    def missing(cls) -> 'KnowledgeError':
        return cls('KNOWLEDGE_NOT_FOUND', '知识库或文档不存在', 404)

    @classmethod
    def reindex(cls) -> 'KnowledgeError':
        return cls('KNOWLEDGE_REINDEX_REQUIRED', '模型绑定或向量规格已变化，请重建知识库索引', 409)


@dataclass(frozen=True)
class KnowledgeHit:
    content: str
    source_id: str
    title: str
    knowledge_base_id: str
    score: float = 0.0


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class ChunkParameters(StrictModel):
    content_type: Literal['file'] = 'file'
    chunk_size: int = Field(1000, ge=100, le=4000)
    chunk_overlap: int = Field(200, ge=0, le=1000)

    @model_validator(mode='after')
    def validate_overlap(self) -> 'ChunkParameters':
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError('重叠长度必须小于分块长度')
        return self


class DatabaseCreate(ChunkParameters):
    database_name: Name
    description: str = Field('', max_length=2000)
    embed_model_id: ModelResourceId
    kb_type: Literal['milvus'] = 'milvus'
    enable_knowledge_graph: bool = False
    llm_model_id: ModelResourceId | None = None

    @model_validator(mode='after')
    def validate_graph(self) -> 'DatabaseCreate':
        if self.enable_knowledge_graph and not self.llm_model_id:
            raise ValueError('启用知识图谱时必须选择对话模型')
        return self


class DatabaseUpdate(StrictModel):
    name: Name
    description: str = Field('', max_length=2000)


class GraphBuildRequest(StrictModel):
    llm_model_id: ModelResourceId | None = None


class DocumentAdd(StrictModel):
    items: list[str] = Field(min_length=1, max_length=20)
    params: ChunkParameters = Field(default_factory=ChunkParameters)


class QueryParameters(StrictModel):
    top_k: int = Field(8, ge=1, le=8)
    mode: Literal['local', 'global', 'hybrid', 'naive', 'mix'] = 'mix'
    only_need_context: bool = True
    similarity_threshold: float = Field(0.0, ge=-1.0, le=1.0)


class KnowledgeQuery(StrictModel):
    query: str = Field(min_length=1, max_length=8000)
    meta: QueryParameters = Field(default_factory=QueryParameters)


class FolderCreate(StrictModel):
    name: Name
    db_id: str
    parent_id: str | None = None


class FolderUpdate(StrictModel):
    name: Name
