"""Database resources are constructed only by explicit process composition."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from zhiheng.shared.errors import AppError

SCHEMA_REVISION = '0005_work'


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)
        self.sessions = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def require_schema(self) -> None:
        try:
            async with self.engine.connect() as connection:
                revision = await connection.scalar(text('SELECT version_num FROM alembic_version'))
        except Exception as exc:
            raise AppError('schema_unavailable', '数据库尚未初始化或暂时不可用', 503) from exc
        if revision != SCHEMA_REVISION:
            raise AppError('schema_incompatible', '数据库版本与应用不匹配，请运行迁移命令', 503)

    async def close(self) -> None:
        await self.engine.dispose()
