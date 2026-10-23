from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
from zhiheng.modules.work.contracts import (
    AddMember,
    CreateTask,
    TaskDetail,
    TaskPage,
    TaskSummary,
    UpdateTask,
    UpgradeMember,
    WorkTurn,
)
from zhiheng.modules.work.orm import WorkMember, WorkMessage, WorkTask
from zhiheng.modules.work.service import MemberSnapshot, WorkService

__all__ = [
    'AddMember',
    'CreateTask',
    'MemberSnapshot',
    'TaskDetail',
    'TaskPage',
    'TaskSummary',
    'UpdateTask',
    'UpgradeMember',
    'WorkService',
    'WorkTurn',
    'schema_tables',
]


def schema_tables() -> tuple[Table, ...]:
    return tuple(Base.metadata.tables[row.__tablename__] for row in (WorkTask, WorkMember, WorkMessage))
