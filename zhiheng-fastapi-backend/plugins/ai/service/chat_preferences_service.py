from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from plugins.ai.dao.platform_chat_dao import PlatformChatDao
from plugins.ai.entity.do.ai_chat_platform_do import AiChatPreferences
from plugins.ai.entity.vo.ai_platform_chat_vo import PlatformChatPreferenceModel

ResultT = TypeVar('ResultT')


class PreferencesDao(Protocol):
    async def get_preferences(self, db: object, user_id: int) -> AiChatPreferences | None: ...

    async def create_preferences(self, db: object, preferences: AiChatPreferences) -> AiChatPreferences: ...

    async def update_preferences(self, db: object, user_id: int, values: dict[str, Any]) -> int: ...


class ChatPreferencesService:
    """Platform Chat 用户偏好服务。"""

    def __init__(self, db: AsyncSession, *, preferences_dao: PreferencesDao = PlatformChatDao) -> None:
        self._db = db
        self._dao = preferences_dao

    async def get_preferences(self, *, user_id: int) -> PlatformChatPreferenceModel:
        async def operation() -> PlatformChatPreferenceModel:
            stored = await self._dao.get_preferences(self._db, user_id)
            if stored is None:
                return PlatformChatPreferenceModel()
            return PlatformChatPreferenceModel.model_validate(
                {
                    'temperature': stored.temperature,
                    'addHistory': stored.add_history,
                    'historyMessageCount': stored.history_message_count,
                    'systemPrompt': stored.system_prompt,
                    'metricsVisible': stored.metrics_visible,
                    'visionEnabled': stored.vision_enabled,
                    'imageMaxSizeMb': stored.image_max_size_mb,
                }
            )

        return await self._run_read(operation)

    async def update_preferences(
        self,
        *,
        user_id: int,
        preferences: PlatformChatPreferenceModel,
        now: datetime,
    ) -> PlatformChatPreferenceModel:
        values = preferences.model_dump()

        async def operation() -> PlatformChatPreferenceModel:
            stored = await self._dao.get_preferences(self._db, user_id)
            if stored is None:
                await self._dao.create_preferences(
                    self._db,
                    AiChatPreferences(
                        user_id=user_id,
                        **values,
                        created_at=now,
                        updated_at=now,
                    ),
                )
            else:
                await self._dao.update_preferences(self._db, user_id, {**values, 'updated_at': now})
            return preferences.model_copy(deep=True)

        return await self._run_write(operation)

    async def _run_read(self, operation: Callable[[], Awaitable[ResultT]]) -> ResultT:
        try:
            result = await operation()
        except BaseException:
            await self._rollback_preserving_error()
            raise
        await self._db.rollback()
        return result

    async def _run_write(self, operation: Callable[[], Awaitable[ResultT]]) -> ResultT:
        try:
            result = await operation()
            await self._db.commit()
        except BaseException:
            await self._rollback_preserving_error()
            raise
        return result

    async def _rollback_preserving_error(self) -> None:
        try:
            await self._db.rollback()
        except Exception:
            pass
