"""Official PostgreSQL checkpoint resources for authorized chat executions.

Business ownership and the effective head belong to the chat domain. Every read
uses an explicit checkpoint identity, including a non-matching root for reseeding;
a failed or cancelled branch must never become history merely by being latest.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection, Error
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from sqlalchemy.engine import make_url

from zhiheng.shared.errors import AppError

CheckpointSchema = Literal['chat_checkpoints', 'assistant_checkpoints']
CHECKPOINT_MIGRATION_VERSION = 9
MAX_CHECKPOINT_ID_LENGTH = 128
ROOT_CHECKPOINT_ID = '00000000-0000-0000-0000-000000000000'


class CheckpointStore:
    def __init__(self, database_url: str, schema: CheckpointSchema = 'chat_checkpoints') -> None:
        url = make_url(database_url).set(drivername='postgresql')
        self._conninfo = make_conninfo(
            url.render_as_string(hide_password=False),
            options=f'-csearch_path={schema},pg_catalog',
            connect_timeout=5,
        )

    @staticmethod
    def config(conversation_id: UUID | str, base_checkpoint_id: str | None = None) -> RunnableConfig:
        """No valid head means a new root, never the newest physical checkpoint."""
        if base_checkpoint_id is not None and (
            not base_checkpoint_id.strip() or len(base_checkpoint_id) > MAX_CHECKPOINT_ID_LENGTH
        ):
            raise AppError('chat_checkpoint_invalid', '对话检查点标识无效', 409)
        return {
            'configurable': {
                'thread_id': str(conversation_id),
                'checkpoint_ns': '',
                'checkpoint_id': base_checkpoint_id or ROOT_CHECKPOINT_ID,
            }
        }

    @staticmethod
    def result_checkpoint_id(config: RunnableConfig) -> str:
        value = config.get('configurable', {}).get('checkpoint_id')
        if (
            not isinstance(value, str)
            or not value.strip()
            or len(value) > MAX_CHECKPOINT_ID_LENGTH
            or value == ROOT_CHECKPOINT_ID
        ):
            raise AppError('chat_checkpoint_missing', '对话结果缺少有效检查点', 503)
        return value

    @asynccontextmanager
    async def saver(self) -> AsyncIterator[AsyncPostgresSaver]:
        """Open the official saver without running setup or modifying its schema."""
        try:
            connection = await AsyncConnection.connect(
                self._conninfo, autocommit=True, prepare_threshold=0, row_factory=dict_row
            )
        except Error as error:
            raise AppError('chat_checkpoint_unavailable', '对话检查点暂时不可用', 503) from error
        async with connection:
            try:
                cursor = await connection.execute('SELECT v FROM checkpoint_migrations ORDER BY v')
                versions = [row['v'] for row in await cursor.fetchall()]
            except Error as error:
                raise AppError(
                    'chat_checkpoint_schema_unavailable', '对话检查点尚未初始化，请运行迁移命令', 503
                ) from error
            if versions != list(range(CHECKPOINT_MIGRATION_VERSION + 1)):
                raise AppError('chat_checkpoint_schema_incompatible', '对话检查点版本不匹配，请运行迁移命令', 503)
            yield AsyncPostgresSaver(connection)

    async def delete_thread(self, conversation_id: UUID | str) -> None:
        """Caller has authorized a tombstoned, inactive conversation before IO."""
        async with self.saver() as saver:
            # The public delete strategy removes checkpoints, writes and blobs in
            # one transaction; interruption cannot leave a half-deleted thread.
            await saver.aprune([str(conversation_id)], strategy='delete')
