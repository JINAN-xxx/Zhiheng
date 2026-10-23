"""模型目录的持久引用检查契约。"""

from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from exceptions.exception import ServiceException
from plugins.ai.entity.do.ai_chat_platform_do import AiChatRun, AiChatSession
from plugins.ai.entity.do.ai_developer_api_do import AiApiKeyModelGrant, AiApiUsageLedger, AiModelRateCard

MODEL_REFERENCE_CHECK_UNAVAILABLE = '模型引用检查不可用，未执行删除，请稍后重试'


class ModelReferenceChecker(Protocol):
    async def has_model_references(self, model_id: str) -> bool: ...


class UnavailableModelReferenceChecker:
    """没有装配完整引用查询时拒绝删除，不能把缺少检查器视为无引用。"""

    async def has_model_references(self, model_id: str) -> bool:
        raise ServiceException(message=MODEL_REFERENCE_CHECK_UNAVAILABLE)


async def has_native_model_references(db: AsyncSession, model_id: str) -> bool:
    """平台聊天、授权和计费数据的持久引用；外键仍是并发完整性兜底。"""
    columns = (
        AiChatSession.model_id,
        AiChatRun.model_id,
        AiApiKeyModelGrant.platform_model_id,
        AiModelRateCard.platform_model_id,
        AiApiUsageLedger.platform_model_id,
    )
    for column in columns:
        if await db.scalar(select(column).where(column == model_id).limit(1)) is not None:
            return True
    return False
