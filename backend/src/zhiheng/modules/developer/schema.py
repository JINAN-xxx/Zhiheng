"""Migration-only declaration inventory; excluded from business public contracts."""

from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
from zhiheng.modules.developer.orm import (
    APIRequest,
    DeveloperApplication,
    DeveloperKey,
    KeyMinuteBucket,
    ResourceGrant,
    ResourceMinuteBucket,
    SourceEnrollment,
)


def schema_tables() -> tuple[Table, ...]:
    """Explicit declaration inventory used only by migration composition."""
    return (
        Base.metadata.tables[DeveloperApplication.__tablename__],
        Base.metadata.tables[DeveloperKey.__tablename__],
        Base.metadata.tables[KeyMinuteBucket.__tablename__],
        *(
            Base.metadata.tables[row.__tablename__]
            for row in (APIRequest, ResourceGrant, ResourceMinuteBucket, SourceEnrollment)
        ),
    )
