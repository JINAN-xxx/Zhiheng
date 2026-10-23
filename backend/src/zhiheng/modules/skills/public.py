from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
from zhiheng.modules.skills.contracts import (
    DraftInfo,
    SkillInfo,
    SkillPage,
    SkillRevisionValue,
    SkillSelection,
    SkillSlug,
    SkillStatus,
)
from zhiheng.modules.skills.orm import Skill, SkillDraft, SkillRevision
from zhiheng.modules.skills.service import MAX_SELECTION_BYTES, SkillService, require_available, resolve_selection

__all__ = [
    'MAX_SELECTION_BYTES',
    'DraftInfo',
    'SkillInfo',
    'SkillPage',
    'SkillRevisionValue',
    'SkillSelection',
    'SkillService',
    'SkillSlug',
    'SkillStatus',
    'require_available',
    'resolve_selection',
    'schema_tables',
]


def schema_tables() -> tuple[Table, ...]:
    return tuple(Base.metadata.tables[row.__tablename__] for row in (Skill, SkillRevision, SkillDraft))
