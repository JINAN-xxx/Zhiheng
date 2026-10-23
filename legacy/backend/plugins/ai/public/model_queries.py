"""模型目录的只读查询契约，事务生命周期由调用方负责。"""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from plugins.ai.dao.ai_model_catalog_dao import AiModelCatalogDao
from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
from plugins.ai.entity.do.ai_resource_binding_do import AiResourceBinding


@dataclass(frozen=True, slots=True)
class PublishedChatModel:
    """已发布且启用的聊天模型快照，不携带 ORM 状态或引擎凭据。"""

    model_id: str
    model_code: str
    max_output_tokens: int | None


@dataclass(frozen=True, slots=True)
class ModelOption:
    """绑定有效的模型选项，供知识库等业务选择模型能力。"""

    model_id: str
    display_name: str
    model_code: str


async def get_published_chat_model(db: AsyncSession, model_id: str) -> PublishedChatModel | None:
    """查询发布状态；调用授权、运行时绑定与上游健康仍由各自能力验证。"""
    model = await AiModelCatalogDao.get_model_by_id(db, model_id)
    return _published_chat_snapshot(model)


async def get_published_chat_model_for_update(db: AsyncSession, model_id: str) -> PublishedChatModel | None:
    """写入持久引用时锁定模型，锁的释放由调用方提交/回滚负责。"""
    model = await AiModelCatalogDao.get_model_for_update(db, model_id)
    return _published_chat_snapshot(model)


async def lock_model_reference(db: AsyncSession, model_id: str) -> bool:
    """保护已有冻结配置的引用写入，允许停用模型但拒绝不存在的模型。"""
    return await AiModelCatalogDao.get_model_for_update(db, model_id) is not None


def _published_chat_snapshot(model: AiModelCatalog | None) -> PublishedChatModel | None:
    if model is None or model.model_type != 'chat' or model.lifecycle_status != 'active' or model.published_at is None:
        return None
    return PublishedChatModel(
        model_id=model.model_id,
        model_code=model.model_code,
        max_output_tokens=model.max_output_tokens,
    )


async def list_active_bound_models(db: AsyncSession, *, model_type: Literal['embedding', 'chat']) -> list[ModelOption]:
    """保持知识库现有筛选规则：启用模型及绑定，不额外要求发布时间。"""
    rows = (
        (
            await db.execute(
                select(AiModelCatalog.model_id, AiModelCatalog.display_name, AiModelCatalog.model_code)
                .join(
                    AiResourceBinding,
                    (AiResourceBinding.resource_id == AiModelCatalog.model_id)
                    & (AiResourceBinding.resource_type == 'model'),
                )
                .where(
                    AiModelCatalog.model_type == model_type,
                    AiModelCatalog.lifecycle_status == 'active',
                    AiResourceBinding.binding_status == 'active',
                )
                .order_by(AiModelCatalog.display_name)
            )
        )
        .mappings()
        .all()
    )
    return [ModelOption(**row) for row in rows]
