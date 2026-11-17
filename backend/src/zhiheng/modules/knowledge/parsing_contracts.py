"""Fenced parsing attempts attached to knowledge build jobs."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from zhiheng.shared.errors import AppError

PARSE_TERMINAL = frozenset({'completed', 'failed', 'cancelled', 'submission_unknown'})


@dataclass(frozen=True)
class ParsingClaim:
    id: UUID
    parent_id: UUID
    owner_id: UUID
    file_id: UUID
    source_sha256: str
    source_version: int
    source_acl_version: int
    config: dict[str, object]
    engine: str
    phase: str
    task_id: str | None
    upstream_file_id: str | None
    artifact_id: UUID | None
    token: UUID
    created_at: datetime
    deadline: datetime
    engine_version: str | None
    parser_version: str | None


class ParsingPending(AppError):
    def __init__(self) -> None:
        super().__init__('document_parsing_pending', '文档正在解析', 202)
