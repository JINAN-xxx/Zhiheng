"""Validated file content shared by chat assembly and managed-file adapters."""

from dataclasses import dataclass
from uuid import UUID

MAX_CHAT_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_CHAT_ATTACHMENT_TEXT_CHARS = 40_000
MAX_CHAT_ATTACHMENTS_TEXT_CHARS = 60_000
CHAT_ATTACHMENT_EXTENSIONS = frozenset({'.pdf', '.docx', '.txt', '.md', '.xlsx', '.csv'})


@dataclass(frozen=True, slots=True)
class ResolvedPlatformImage:
    file_id: UUID
    mime_type: str
    size_bytes: int
    data_url: str
    file_name: str = ''


@dataclass(frozen=True, slots=True)
class ResolvedPlatformAttachment:
    file_id: UUID
    file_name: str
    mime_type: str
    size_bytes: int
    text: str
