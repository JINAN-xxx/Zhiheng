"""The complete schema is assembled explicitly through each module's public inventory."""

from sqlalchemy import MetaData

from zhiheng.infrastructure.database import Base
from zhiheng.modules.chat.public import schema_tables as chat_tables
from zhiheng.modules.developer.public import schema_tables as developer_tables
from zhiheng.modules.identity.public import schema_tables as identity_tables
from zhiheng.modules.models.public import schema_tables as model_tables
from zhiheng.runtime.orm import Outbox, Run


def schema_metadata() -> MetaData:
    metadata = MetaData()
    tables = (
        *identity_tables(),
        *model_tables(),
        *developer_tables(),
        *chat_tables(),
        Base.metadata.tables[Run.__tablename__],
        Base.metadata.tables[Outbox.__tablename__],
    )
    for table in tables:
        table.to_metadata(metadata)
    return metadata
