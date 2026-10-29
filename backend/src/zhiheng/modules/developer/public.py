from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
from zhiheng.modules.developer.applications import ApplicationService
from zhiheng.modules.developer.contracts import (
    ApplicationCreate,
    ApplicationInfo,
    ApplicationPatch,
    IssuedKey,
    KeyCreate,
    KeyInfo,
    KeyRotate,
    LegacyReservation,
    UsageOutcome,
    UsageSummary,
)
from zhiheng.modules.developer.keys import DeveloperIdentity, KeyService
from zhiheng.modules.developer.orm import DeveloperApplication, DeveloperKey, Invocation, KeyMinuteBucket, UsageAccount
from zhiheng.modules.developer.service import UsageService

__all__ = [
    'ApplicationCreate',
    'ApplicationInfo',
    'ApplicationPatch',
    'ApplicationService',
    'DeveloperIdentity',
    'IssuedKey',
    'KeyCreate',
    'KeyInfo',
    'KeyRotate',
    'KeyService',
    'LegacyReservation',
    'UsageOutcome',
    'UsageService',
    'UsageSummary',
    'schema_tables',
]


def schema_tables() -> tuple[Table, ...]:
    """Explicit declaration inventory used only by migration composition."""
    return (
        Base.metadata.tables[UsageAccount.__tablename__],
        Base.metadata.tables[Invocation.__tablename__],
        Base.metadata.tables[DeveloperApplication.__tablename__],
        Base.metadata.tables[DeveloperKey.__tablename__],
        Base.metadata.tables[KeyMinuteBucket.__tablename__],
    )
