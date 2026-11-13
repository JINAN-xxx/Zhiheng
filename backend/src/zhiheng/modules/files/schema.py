"""Migration-only declaration inventory; excluded from business public contracts."""

from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
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
