from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
from zhiheng.modules.identity.contracts import IdentityInfo
from zhiheng.modules.identity.orm import IdentitySession, IdentityUser
from zhiheng.modules.identity.service import IdentityService

"""Identity use cases; callers receive principals, never identity ORM objects."""


__all__ = ['IdentityInfo', 'IdentityService', 'schema_tables']


def schema_tables() -> tuple[Table, ...]:
    """Explicit declaration inventory used only by migration composition."""
    return (Base.metadata.tables[IdentityUser.__tablename__], Base.metadata.tables[IdentitySession.__tablename__])
