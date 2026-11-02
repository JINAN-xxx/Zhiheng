from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

FileStatus = Literal['active', 'deleted', 'purging', 'purged']
FileAccess = Literal['private', 'public']
JobKind = Literal['expiry', 'reminders', 'purge', 'reconcile']


@dataclass(frozen=True)
class FileAccountingFact:
    id: UUID
    owner_id: UUID
    size: int
    status: str
    source_file_id: UUID | None


class FileInfo(BaseModel):
    id: UUID
    original_filename: str
    display_name: str
    size: int
    mime: str
    sha256: str
    owner_id: UUID
    uploader_id: UUID
    access: FileAccess
    uploader_access_enabled: bool
    status: FileStatus
    acl_version: int
    version: int
    expires_at: datetime | None
    deleted_at: datetime | None
    retention_until: datetime | None
    created_at: datetime
    updated_at: datetime
    reference_count: int
    storage_status: Literal['normal', 'missing', 'quarantined', 'invalid', 'unknown']
    acl_entry_count: int
    acl_nearest_expire_at: datetime | None
    source_file_id: UUID | None
    parser_version: str | None


class FilePage(BaseModel):
    items: list[FileInfo]
    total: int
    page: int
    page_size: int


class FileStats(BaseModel):
    total_count: int
    total_bytes: int
    active_count: int
    deleted_count: int
    public_count: int
    public_bytes: int
    private_bytes: int
    expired_count: int
    retention_expiring_count: int
    acl_expiring_count: int


class VersionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(strict=True, ge=1)


class FilePatch(VersionRequest):
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    expires_at: datetime | None = None
    uploader_access_enabled: bool | None = Field(default=None, strict=True)


class AclRequest(VersionRequest):
    access: FileAccess
    entries: list[AclEntry] = Field(default_factory=list, max_length=100)


class AclInfo(BaseModel):
    file: FileInfo
    entries: list[AclEntry]
    builtin_permissions: list[BuiltinPermission]


class TransferRequest(VersionRequest):
    reason: str = Field(default='', max_length=500)
    owner_id: UUID
    uploader_access_enabled: bool = Field(default=False, strict=True)


class PolicyInfo(BaseModel):
    retention_days: int
    reminder_days: int
    version: int


class PolicyRequest(VersionRequest):
    retention_days: int = Field(strict=True, ge=0, le=3650)
    reminder_days: int = Field(strict=True, ge=0, le=365)


class ReferenceInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    resource_type: str
    resource_id: UUID
    retain_until: datetime | None
    version: int
    created_at: datetime


class AuditInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    action: str
    actor_id: UUID | None
    created_at: datetime
    details: dict[str, object]


class ReminderInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    file_id: UUID
    kind: Literal['expired', 'expiring']
    is_read: bool
    original_filename: str
    owner_id: UUID
    expires_at: datetime | None
    reference_count: int
    read_by: UUID | None
    read_at: datetime | None
    created_at: datetime


class JobInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    kind: JobKind
    status: Literal['running', 'succeeded', 'failed']
    started_at: datetime
    finished_at: datetime | None
    scanned: int
    changed: int
    errors: list[str]


class AttachmentSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    file_id: UUID
    original_filename: str
    mime: str
    size: int
    sha256: str
    artifact_file_id: UUID | None = None
    parser_version: str | None = None


class PreparedAttachment(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')
    summary: AttachmentSummary
    version: int
    acl_version: int
    text: str = ''
    image_data: str | None = None


class SourceSnapshot(BaseModel):
    """A business source reference, independent of user message attachments."""

    model_config = ConfigDict(frozen=True, extra='forbid')
    file_id: UUID
    version: int
    acl_version: int
    sha256: str
    artifact_file_id: UUID | None = None


class AclEntry(BaseModel):
    model_config = ConfigDict(extra='forbid')
    user_id: UUID
    effect: Literal['allow', 'deny'] = 'allow'
    expires_at: datetime | None = None


class BuiltinPermission(BaseModel):
    source: Literal['admin', 'owner', 'uploader']
    enabled: bool
    deny_overridable: bool


class RetentionPolicyRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    resource_type: str = Field(min_length=1, max_length=32, pattern=r'^[a-z][a-z0-9_]*$')
    retention_days: int = Field(strict=True, ge=1, le=36500)
    enabled: bool = Field(default=True, strict=True)
    remark: str = Field(default='', max_length=500)


class RetentionPolicyInfo(RetentionPolicyRequest):
    version: int
    updated_at: datetime


class RetentionPolicyPatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    retention_days: int = Field(strict=True, ge=1, le=36500)
    enabled: bool = Field(default=True, strict=True)
    remark: str = Field(default='', max_length=500)
    expected_version: int = Field(strict=True, ge=1)


class ReminderAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(strict=True, ge=1)
    expires_at: datetime
    reason: str = Field(min_length=1, max_length=500)


class ReconcileIssueInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    job_id: UUID
    file_id: UUID | None
    kind: Literal['missing', 'orphan', 'size_or_hash', 'invalidkey']
    status: Literal['open', 'resolved', 'ignored', 'quarantined']
    version: int
    size: int | None
    detail: str
    expected_size: int | None
    actual_size: int | None
    created_at: datetime
    updated_at: datetime


class IssueAction(VersionRequest):
    action: Literal['ignore', 'quarantine', 'register', 'repair']
    original_filename: str | None = Field(default=None, min_length=1, max_length=255)
    reason: str = Field(default='', max_length=500)


AclRequest.model_rebuild()
AclInfo.model_rebuild()


class FileQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    search: str = Field(default='', max_length=500)
    status: Literal['all', 'active', 'deleted', 'purging', 'purged'] = 'active'
    access: FileAccess | None = None
    expiration_status: Literal['permanent', 'valid', 'expiring', 'expired'] | None = None
    owner_id: UUID | None = None
    owner_search: str = Field(default='', max_length=80)
    uploader_search: str = Field(default='', max_length=80)
    mime: str | None = Field(default=None, max_length=100)
    created_from: datetime | None = None
    created_to: datetime | None = None
    expired: bool | None = None


class FileAuditQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    action: str | None = Field(default=None, max_length=32)
    actor_id: UUID | None = None
    outcome: Literal['allowed', 'denied', 'completed'] | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None


class FileAuditPage(BaseModel):
    items: list[AuditInfo]
    total: int
    page: int
    page_size: int
