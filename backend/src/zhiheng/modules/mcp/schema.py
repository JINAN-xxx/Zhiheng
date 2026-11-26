from sqlalchemy import Table

from zhiheng.infrastructure.database import Base
from zhiheng.modules.mcp.orm import McpCall, McpServer


def schema_tables() -> tuple[Table, ...]:
    return tuple(Base.metadata.tables[model.__tablename__] for model in (McpServer, McpCall))
