from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
from zhiheng.modules.skills.contracts import (
    DraftInfo,
    ResourceSkillInfo,
    RevisionContent,
    RevisionInfo,
    SkillAccess,
    SkillConfirmation,
    SkillImportAction,
    SkillInfo,
    SkillPage,
    SkillRevisionValue,
    SkillSelection,
    SkillSlug,
    SkillStatus,
)
from zhiheng.modules.skills.orm import Skill, SkillDraft, SkillLegacyBinding, SkillRevision
from zhiheng.modules.skills.package import SkillPackageParser
from zhiheng.modules.skills.service import (
    MAX_SELECTION_BYTES,
    SkillService,
    require_available,
    require_available_groups,
    require_available_set,
    require_frozen_available,
    resolve_selection,
)

__all__ = [
    'MAX_SELECTION_BYTES',
    'DraftInfo',
    'ResourceSkillInfo',
    'RevisionContent',
    'RevisionInfo',
    'SkillAccess',
    'SkillConfirmation',
    'SkillImportAction',
    'SkillInfo',
    'SkillPackageParser',
    'SkillPage',
    'SkillRevisionValue',
    'SkillSelection',
    'SkillService',
    'SkillSlug',
    'SkillStatus',
    'require_available',
    'require_available_groups',
    'require_available_set',
    'require_frozen_available',
    'resolve_selection',
    'schema_tables',
]


def schema_tables() -> tuple[Table, ...]:
    return tuple(
        Base.metadata.tables[row.__tablename__] for row in (Skill, SkillRevision, SkillDraft, SkillLegacyBinding)
    )
