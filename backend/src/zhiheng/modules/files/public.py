from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
from zhiheng.modules.files.contracts import (
    AclEntry,
    AclInfo,
    AclRequest,
    AttachmentSummary,
    AuditInfo,
    BuiltinPermission,
    FileAuditPage,
    FileAuditQuery,
    FileInfo,
    FilePage,
    FilePatch,
    FileQuery,
    FileStats,
    FileStatus,
    IssueAction,
    JobInfo,
    JobKind,
    PolicyInfo,
    PolicyRequest,
    PreparedAttachment,
    ReconcileIssueInfo,
    ReferenceInfo,
    ReminderAction,
    ReminderInfo,
    RetentionPolicyInfo,
    RetentionPolicyPatch,
    RetentionPolicyRequest,
    TransferRequest,
    VersionRequest,
)
from zhiheng.modules.files.jobs import FileJobs
from zhiheng.modules.files.orm import (
    FileAudit,
    FileGrant,
    FileJob,
    FilePolicy,
    FileReconcileIssue,
    FileRecord,
    FileReference,
    FileReminder,
    FileRetentionPolicy,
)
from zhiheng.modules.files.service import FileService, validate_filename

__all__ = [
    'AclEntry',
    'AclInfo',
    'AclRequest',
    'AttachmentSummary',
    'AuditInfo',
    'BuiltinPermission',
    'FileAuditPage',
    'FileAuditQuery',
    'FileInfo',
    'FileJobs',
    'FilePage',
    'FilePatch',
    'FileQuery',
    'FileService',
    'FileStats',
    'FileStatus',
    'IssueAction',
    'JobInfo',
    'JobKind',
    'PolicyInfo',
    'PolicyRequest',
    'PreparedAttachment',
    'ReconcileIssueInfo',
    'ReferenceInfo',
    'ReminderAction',
    'ReminderInfo',
    'RetentionPolicyInfo',
    'RetentionPolicyPatch',
    'RetentionPolicyRequest',
    'TransferRequest',
    'VersionRequest',
    'schema_tables',
    'validate_filename',
]


def schema_tables() -> tuple[Table, ...]:
    return tuple(
        Base.metadata.tables[model.__tablename__]
        for model in (
            FileRecord,
            FileGrant,
            FileReference,
            FileAudit,
            FilePolicy,
            FileReminder,
            FileJob,
            FileRetentionPolicy,
            FileReconcileIssue,
        )
    )
