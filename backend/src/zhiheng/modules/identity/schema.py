"""Migration-only declaration inventory; excluded from business public contracts."""

from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
from zhiheng.modules.identity.admin_orm import (
    IdentityDepartment,
    IdentityRole,
    IdentityUserRole,
)
from zhiheng.modules.identity.orm import IdentitySession, IdentityUser


def schema_tables() -> tuple[Table, ...]:
    """Explicit declaration inventory used only by migration composition."""
    return tuple(
        Base.metadata.tables[model.__tablename__]
        for model in (
            IdentityDepartment,
            IdentityRole,
            IdentityUser,
            IdentityUserRole,
            IdentitySession,
        )
    )
