from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from zhiheng.modules.files.public import AclEntry, BuiltinPermission
from zhiheng.runtime.contracts import RunError, RunSnapshot

PARSER_VERSION = 'native-2'
MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_TEXT_BYTES = 2_000_000
MAX_IMAGE_URL_BYTES = 2_000_000


class ParseResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    text: str
    metadata: dict[str, str | int]
    parser_version: str = PARSER_VERSION
    image_data: str | None = None
    source_sha256: str | None = None


class ParsePort(Protocol):
    async def parse(self, content: bytes, *, filename: str) -> ParseResult: ...


NATIVE_FORMATS = ('pdf', 'docx', 'xlsx', 'txt', 'md', 'csv', 'json', 'html', 'htm')
JOB_TERMINAL = frozenset({'completed', 'partial', 'failed', 'cancelled', 'interrupted'})
PREPARATION_TERMINAL = frozenset({'admitted', 'completed', 'failed', 'cancelled', 'interrupted'})


@dataclass(frozen=True)
class BaseAccess:
    id: UUID
    owner_id: UUID
    embed_model_id: UUID
    version: int
    active_job_id: UUID | None


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


def safe_name(value: str) -> str:
    value.encode('utf-8')
    if not value.strip() or '\x00' in value:
        raise ValueError('Invalid name')
    return value.strip()


class ChunkParameters(Input):
    chunk_size: int = Field(default=1000, strict=True, ge=100, le=4000)
    chunk_overlap: int = Field(default=200, strict=True, ge=0, le=1000)

    @model_validator(mode='after')
    def overlap(self) -> 'ChunkParameters':
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError('Overlap must be less than chunk size')
        return self


class BaseCreate(ChunkParameters):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default='', max_length=2000)
    embed_model_id: UUID | None = None
    access: Literal['private'] = 'private'
    enable_knowledge_graph: bool = Field(default=False, strict=True)
    llm_model_id: UUID | None = None

    @field_validator('name', 'description')
    @classmethod
    def text(cls, value: str) -> str:
        value.encode('utf-8')
        if '\x00' in value or (not value.strip() and value):
            raise ValueError('Invalid text')
        return value.strip()


class Version(Input):
    expected_version: int = Field(strict=True, ge=1)


class BasePatch(Version):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=2000)

    @field_validator('name', 'description')
    @classmethod
    def text(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is None:
            return None
        value.encode('utf-8')
        if info.field_name == 'name':
            return safe_name(value)
        if '\x00' in value:
            raise ValueError('Invalid description')
        return value.strip()


class KnowledgeSettings(BasePatch, ChunkParameters):
    expected_version: int = Field(strict=True, ge=1)
    embed_model_id: UUID
    rerank_model_id: UUID | None = None
    use_default_rerank: bool = True
    confirm_rebuild: bool = False
    client_request_id: UUID

    @model_validator(mode='after')
    def required_reranker(self) -> 'KnowledgeSettings':
        if not self.use_default_rerank and self.rerank_model_id is None:
            raise ValueError('必须指定重排模型，或使用默认重排模型')
        if self.use_default_rerank and self.rerank_model_id is not None:
            raise ValueError('使用默认重排时无需填写单库模型')
        return self


class BaseInfo(BaseCreate):
    availability: Literal['available'] = 'available'
    rerank_model_id: UUID | None = None
    use_default_rerank: bool = True
    embed_model_id: UUID
    document_policy: Literal['legacy', 'library'] = 'legacy'
    sharing_scope: Literal['private', 'users'] = 'private'
    owner_name: str = ''
    id: UUID
    owner_id: UUID
    version: int
    acl_version: int
    active_revision: UUID | None
    active_job_id: UUID | None
    index_status: str
    document_count: int
    last_error: RunError | None
    graph_status: Literal['disabled', 'pending', 'ready', 'building', 'stale', 'failed']
    created_at: datetime
    updated_at: datetime
    can_manage: bool


class UnavailableBaseInfo(BaseModel):
    """Only existing members can see the unavailable library's historical identity."""

    id: UUID
    name: str
    owner_id: UUID
    owner_name: str
    availability: Literal['owner_disabled', 'owner_deleted']


class BaseAcl(Input):
    expected_version: int = Field(strict=True, ge=1)
    access: Literal['private']
    entries: list[AclEntry] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def timezone(self) -> 'BaseAcl':
        if any(entry.expires_at is not None and entry.expires_at.utcoffset() is None for entry in self.entries):
            raise ValueError('ACL expiry requires a timezone')
        return self


class BaseAclInfo(BaseModel):
    base: BaseInfo
    entries: list[AclEntry]
    builtin_permissions: list[BuiltinPermission]


class FolderCreate(Input):
    name: str = Field(min_length=1, max_length=100)
    parent_id: UUID | None = None
    _valid_name = field_validator('name')(safe_name)


class FolderPatch(Version):
    name: str = Field(min_length=1, max_length=100)
    _valid_name = field_validator('name')(safe_name)


class FolderInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    base_id: UUID
    parent_id: UUID | None
    name: str
    version: int
    created_at: datetime
    updated_at: datetime


class DocumentImport(ChunkParameters):
    file_ids: list[UUID] = Field(min_length=1, max_length=20)
    folder_id: UUID | None = None
    client_request_id: UUID

    @field_validator('file_ids')
    @classmethod
    def unique(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError('Duplicate source files')
        return value


class JobRequest(Version):
    client_request_id: UUID
    confirm_references: bool = False


class RechunkRequest(ChunkParameters, JobRequest):
    pass


class DocumentInfo(BaseModel):
    parsing_engine: str | None = None
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    base_id: UUID
    file_id: UUID
    file_name: str
    file_size: int
    mime: str
    folder_id: UUID | None
    version: int
    status: str
    active_revision: UUID | None
    job_id: UUID | None
    chunk_count: int | None
    chunk_size: int
    chunk_overlap: int
    error: RunError | None
    graph_status: Literal['disabled', 'pending', 'ready', 'building', 'stale', 'failed'] = 'disabled'
    graph_error: None = None
    created_at: datetime
    updated_at: datetime


class DocumentPage(BaseModel):
    items: list[DocumentInfo]
    total: int


class DocumentResult(BaseModel):
    document_id: UUID
    file_id: UUID
    status: str
    chunk_count: int | None = None
    error: RunError | None = None


class JobInfo(BaseModel):
    id: UUID
    base_id: UUID
    operation: str
    status: str
    phase: str
    version: int
    attempt_id: UUID
    index_revision: UUID
    total_documents: int
    completed_documents: int
    failed_documents: int
    chunk_count: int | None
    document_results: list[DocumentResult]
    error: RunError | None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    cancel_allowed: bool


class AttachmentImport(Input):
    file_id: UUID
    client_request_id: UUID
    folder_id: UUID | None = None


class AttachmentImportInfo(BaseModel):
    document: DocumentInfo
    job: JobInfo
    already_exists: bool = False


class MigrationRequest(Version):
    expected_acl_version: int = Field(strict=True, ge=1)
    confirm_shared_access: bool = Field(strict=True)
    client_request_id: UUID


class QueryRequest(Input):
    query: str = Field(min_length=1, max_length=8000)
    top_k: int = Field(default=8, strict=True, ge=1, le=8)
    similarity_threshold: float = Field(default=0, ge=-1, le=1, allow_inf_nan=False)
    client_request_id: UUID

    @field_validator('query')
    @classmethod
    def text(cls, value: str) -> str:
        value.encode('utf-8')
        if not value.strip() or '\x00' in value:
            raise ValueError('Invalid query')
        return value


class Hit(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    chunk_id: UUID
    document_id: UUID
    file_id: UUID
    source_id: UUID
    knowledge_base_id: UUID
    index_revision: UUID
    title: str
    content: str
    score: float = Field(allow_inf_nan=False)
    vector_score: float | None = Field(default=None, allow_inf_nan=False)
    fusion_score: float | None = Field(default=None, allow_inf_nan=False)
    rerank_score: float | None = Field(default=None, allow_inf_nan=False)


class PreparationInfo(BaseModel):
    kind: Literal['knowledge_preparation'] = 'knowledge_preparation'
    diagnostics: dict[str, object] = Field(default_factory=dict)
    id: UUID
    resource_type: str
    resource_id: UUID
    status: str
    phase: str
    run: RunSnapshot | None = None
    hits: list[Hit] = Field(default_factory=list)
    error: RunError | None
    created_at: datetime
    updated_at: datetime
    polling_url: str | None = None
