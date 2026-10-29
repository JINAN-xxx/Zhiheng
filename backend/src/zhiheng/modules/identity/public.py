from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
from zhiheng.modules.identity.admin_contracts import AdminKind, PasswordReset
from zhiheng.modules.identity.admin_orm import (
    IdentityDepartment,
    IdentityMenu,
    IdentityPost,
    IdentityRole,
    IdentityUserPost,
    IdentityUserRole,
)
from zhiheng.modules.identity.admin_permissions import permission_catalog
from zhiheng.modules.identity.admin_service import IdentityAdminService
from zhiheng.modules.identity.contracts import IdentityInfo
from zhiheng.modules.identity.orm import IdentitySession, IdentityUser
from zhiheng.modules.identity.profile_contracts import PasswordChange, ProfileInfo, ProfilePatch
from zhiheng.modules.identity.service import IdentityService

"""Identity use cases; callers receive principals, never identity ORM objects."""


__all__ = [
    'AdminKind',
    'IdentityAdminService',
    'IdentityInfo',
    'IdentityService',
    'PasswordChange',
    'PasswordReset',
    'ProfileInfo',
    'ProfilePatch',
    'permission_catalog',
    'schema_tables',
]


def schema_tables() -> tuple[Table, ...]:
    """Explicit declaration inventory used only by migration composition."""
    return tuple(
        Base.metadata.tables[model.__tablename__]
        for model in (
            IdentityDepartment,
            IdentityPost,
            IdentityRole,
            IdentityMenu,
            IdentityUser,
            IdentityUserRole,
            IdentityUserPost,
            IdentitySession,
        )
    )
