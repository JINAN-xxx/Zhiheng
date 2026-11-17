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
from zhiheng.modules.files.parsing_orm import ParsingConfiguration


def schema_tables() -> tuple[Table, ...]:
    return tuple(
        Base.metadata.tables[model.__tablename__]
        for model in (
            ParsingConfiguration,
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
