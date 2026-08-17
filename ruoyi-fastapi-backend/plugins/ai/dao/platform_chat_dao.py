from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from common.vo import PageModel
from plugins.ai.entity.do.ai_chat_platform_do import AiChatMessage, AiChatPreferences, AiChatRun, AiChatSession
from utils.page_util import PageUtil

SESSION_MUTABLE_FIELDS = frozenset({'title', 'status', 'updated_at'})
RUN_MUTABLE_FIELDS = frozenset(
    {
        'status',
        'engine_request_id',
        'input_tokens',
        'output_tokens',
        'reasoning_tokens',
        'cache_read_tokens',
        'cache_write_tokens',
        'raw_cost',
        'error_code',
        'error_message',
        'started_at',
        'finished_at',
        'updated_at',
    }
)
PREFERENCE_MUTABLE_FIELDS = frozenset(
    {
        'temperature',
        'add_history',
        'history_message_count',
        'system_prompt',
        'metrics_visible',
        'vision_enabled',
        'image_max_size_mb',
        'updated_at',
    }
)


class PlatformChatDao:
    """Platform Chat 持久化操作；事务由 Service 管理。"""

    @classmethod
    async def list_sessions(
        cls,
        db: AsyncSession,
        *,
        user_id: int,
        page_num: int,
        page_size: int,
    ) -> PageModel:
        query = (
            select(AiChatSession)
            .where(AiChatSession.user_id == user_id)
            .order_by(AiChatSession.updated_at.desc(), AiChatSession.session_id.desc())
        )
        result = await PageUtil.paginate(db, query, page_num, page_size, is_page=True)
        if not isinstance(result, PageModel):
            raise TypeError('分页查询返回类型无效')
        return result

    @classmethod
    async def get_owned_session(cls, db: AsyncSession, session_id: str, *, user_id: int) -> AiChatSession | None:
        return (
            (
                await db.execute(
                    select(AiChatSession).where(
                        AiChatSession.session_id == session_id,
                        AiChatSession.user_id == user_id,
                    )
                )
            )
            .scalars()
            .first()
        )

    @classmethod
    async def get_owned_session_for_update(
        cls,
        db: AsyncSession,
        session_id: str,
        *,
        user_id: int,
    ) -> AiChatSession | None:
        return (
            (
                await db.execute(
                    select(AiChatSession)
                    .where(
                        AiChatSession.session_id == session_id,
                        AiChatSession.user_id == user_id,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .first()
        )

    @classmethod
    async def create_session(cls, db: AsyncSession, session: AiChatSession) -> AiChatSession:
        db.add(session)
        await db.flush()
        return session

    @classmethod
    async def update_session(
        cls,
        db: AsyncSession,
        session_id: str,
        *,
        user_id: int,
        values: dict[str, Any],
    ) -> int:
        cls._validate_fields(values, SESSION_MUTABLE_FIELDS, '会话')
        if not values:
            return 0
        result = await db.execute(
            update(AiChatSession)
            .where(
                AiChatSession.session_id == session_id,
                AiChatSession.user_id == user_id,
            )
            .values(**values)
        )
        return int(result.rowcount or 0)

    @classmethod
    async def create_message(cls, db: AsyncSession, message: AiChatMessage) -> AiChatMessage:
        db.add(message)
        await db.flush()
        return message

    @classmethod
    async def get_message_by_client_id(
        cls,
        db: AsyncSession,
        session_id: str,
        client_message_id: str,
    ) -> AiChatMessage | None:
        return (
            (
                await db.execute(
                    select(AiChatMessage).where(
                        AiChatMessage.session_id == session_id,
                        AiChatMessage.client_message_id == client_message_id,
                    )
                )
            )
            .scalars()
            .first()
        )

    @classmethod
    async def list_history_messages(
        cls,
        db: AsyncSession,
        session_id: str,
        *,
        limit: int,
    ) -> list[AiChatMessage]:
        if limit <= 0:
            return []
        result = await db.execute(
            select(AiChatMessage)
            .where(
                AiChatMessage.session_id == session_id,
                AiChatMessage.status == 'complete',
            )
            .order_by(AiChatMessage.created_at.desc(), AiChatMessage.message_id.desc())
            .limit(limit)
        )
        return list(reversed(result.scalars().all()))

    @classmethod
    async def create_run(cls, db: AsyncSession, run: AiChatRun) -> AiChatRun:
        db.add(run)
        await db.flush()
        return run

    @classmethod
    async def get_owned_run(cls, db: AsyncSession, run_id: str, *, user_id: int) -> AiChatRun | None:
        return (
            (
                await db.execute(
                    select(AiChatRun)
                    .join(AiChatSession, AiChatSession.session_id == AiChatRun.session_id)
                    .where(
                        AiChatRun.run_id == run_id,
                        AiChatSession.user_id == user_id,
                    )
                )
            )
            .scalars()
            .first()
        )

    @classmethod
    async def transition_run_status(
        cls,
        db: AsyncSession,
        run_id: str,
        *,
        expected_statuses: set[str],
        values: dict[str, Any],
    ) -> int:
        cls._validate_fields(values, RUN_MUTABLE_FIELDS, '运行')
        if not expected_statuses or not values:
            return 0
        result = await db.execute(
            update(AiChatRun)
            .where(
                AiChatRun.run_id == run_id,
                AiChatRun.status.in_(expected_statuses),
            )
            .values(**values)
        )
        return int(result.rowcount or 0)

    @classmethod
    async def get_preferences(cls, db: AsyncSession, user_id: int) -> AiChatPreferences | None:
        return (
            (await db.execute(select(AiChatPreferences).where(AiChatPreferences.user_id == user_id))).scalars().first()
        )

    @classmethod
    async def create_preferences(cls, db: AsyncSession, preferences: AiChatPreferences) -> AiChatPreferences:
        db.add(preferences)
        await db.flush()
        return preferences

    @classmethod
    async def update_preferences(cls, db: AsyncSession, user_id: int, values: dict[str, Any]) -> int:
        cls._validate_fields(values, PREFERENCE_MUTABLE_FIELDS, '对话偏好')
        if not values:
            return 0
        result = await db.execute(
            update(AiChatPreferences).where(AiChatPreferences.user_id == user_id).values(**values)
        )
        return int(result.rowcount or 0)

    @staticmethod
    def _validate_fields(values: dict[str, Any], allowed: frozenset[str], resource_name: str) -> None:
        unsupported = values.keys() - allowed
        if unsupported:
            raise ValueError(f'不允许更新{resource_name}字段: {", ".join(sorted(unsupported))}')
