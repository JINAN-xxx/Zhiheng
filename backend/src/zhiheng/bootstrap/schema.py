"""The complete schema is assembled explicitly through each module's public inventory."""

from sqlalchemy import MetaData

from zhiheng.infrastructure.database import Base
from zhiheng.modules.agents.schema import schema_tables as agent_tables
from zhiheng.modules.billing.schema import schema_tables as billing_tables
from zhiheng.modules.chat.schema import schema_tables as chat_tables
from zhiheng.modules.developer.schema import schema_tables as developer_tables
from zhiheng.modules.files.schema import schema_tables as file_tables
from zhiheng.modules.identity.schema import schema_tables as identity_tables
from zhiheng.modules.knowledge.schema import schema_tables as knowledge_tables
from zhiheng.modules.models.schema import schema_tables as model_tables
from zhiheng.modules.operations.schema import schema_tables as operation_tables
from zhiheng.modules.skills.schema import schema_tables as skill_tables
from zhiheng.modules.work.schema import schema_tables as work_tables
from zhiheng.runtime.orm import Outbox, Run


def schema_metadata() -> MetaData:
    metadata = MetaData()
    tables = (
        *identity_tables(),
        *model_tables(),
        *developer_tables(),
        *billing_tables(),
        *chat_tables(),
        *skill_tables(),
        *agent_tables(),
        *work_tables(),
        *file_tables(),
        *knowledge_tables(),
        *operation_tables(),
        Base.metadata.tables[Run.__tablename__],
        Base.metadata.tables[Outbox.__tablename__],
    )
    for table in tables:
        table.to_metadata(metadata)
    return metadata
