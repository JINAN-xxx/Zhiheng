from datetime import datetime
from typing import Any

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from common.vo import PageModel
from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
from plugins.ai.entity.vo.ai_model_catalog_vo import ModelCatalogPageQueryModel
from utils.page_util import PageUtil

MODEL_MUTABLE_FIELDS = frozenset(
    {
        'model_code',
        'display_name',
        'model_type',
        'capabilities',
        'context_window',
        'max_output_tokens',
        'default_parameters',
        'lifecycle_status',
        'published_at',
        'update_by',
        'update_time',
    }
)


class AiModelCatalogDao:
    """Platform 模型目录数据库操作层。"""

    @classmethod
    async def get_model_by_id(cls, db: AsyncSession, model_id: str) -> AiModelCatalog | None:
        return (await db.execute(select(AiModelCatalog).where(AiModelCatalog.model_id == model_id))).scalars().first()

    @classmethod
    async def get_model_for_update(cls, db: AsyncSession, model_id: str) -> AiModelCatalog | None:
        return (
            (
                await db.execute(
                    select(AiModelCatalog)
                    .where(AiModelCatalog.model_id == model_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .first()
        )

    @classmethod
    async def get_model_by_code(cls, db: AsyncSession, model_code: str) -> AiModelCatalog | None:
        return (
            (await db.execute(select(AiModelCatalog).where(AiModelCatalog.model_code == model_code))).scalars().first()
        )

    @classmethod
    async def list_models(
        cls,
        db: AsyncSession,
        query_object: ModelCatalogPageQueryModel,
        is_page: bool = False,
    ) -> PageModel | list[dict[str, Any]]:
        query = (
            select(AiModelCatalog)
            .where(
                AiModelCatalog.model_code.contains(query_object.model_code, autoescape=True)
                if query_object.model_code
                else True,
                AiModelCatalog.display_name.contains(query_object.display_name, autoescape=True)
                if query_object.display_name
                else True,
                AiModelCatalog.model_type == query_object.model_type if query_object.model_type else True,
                AiModelCatalog.lifecycle_status == query_object.lifecycle_status
                if query_object.lifecycle_status
                else True,
            )
            .order_by(AiModelCatalog.model_code, AiModelCatalog.model_id)
        )

        return await PageUtil.paginate(
            db,
            query,
            query_object.page_num,
            query_object.page_size,
            is_page,
        )

    @classmethod
    async def create_model(cls, db: AsyncSession, model: AiModelCatalog) -> AiModelCatalog:
        db.add(model)
        await db.flush()

        return model

    @classmethod
    async def update_model(cls, db: AsyncSession, model_id: str, values: dict[str, Any]) -> int:
        cls._validate_mutable_fields(values)
        if not values:
            return 0
        result = await db.execute(update(AiModelCatalog).where(AiModelCatalog.model_id == model_id).values(**values))

        return int(result.rowcount or 0)

    @classmethod
    async def update_unpublished_model(cls, db: AsyncSession, model_id: str, values: dict[str, Any]) -> int:
        cls._validate_mutable_fields(values)
        if not values:
            return 0
        result = await db.execute(
            update(AiModelCatalog)
            .where(
                AiModelCatalog.model_id == model_id,
                AiModelCatalog.published_at.is_(None),
            )
            .values(**values)
        )
        return int(result.rowcount or 0)

    @classmethod
    async def publish_model_if_state(
        cls,
        db: AsyncSession,
        model_id: str,
        *,
        expected_lifecycle_status: str,
        expected_published_at: datetime | None,
        values: dict[str, Any],
    ) -> int:
        cls._validate_mutable_fields(values)
        published_at_condition = (
            AiModelCatalog.published_at.is_(None)
            if expected_published_at is None
            else AiModelCatalog.published_at == expected_published_at
        )
        result = await db.execute(
            update(AiModelCatalog)
            .where(
                AiModelCatalog.model_id == model_id,
                AiModelCatalog.lifecycle_status == expected_lifecycle_status,
                published_at_condition,
            )
            .values(**values)
        )
        return int(result.rowcount or 0)

    @classmethod
    async def disable_model_if_active(cls, db: AsyncSession, model_id: str, values: dict[str, Any]) -> int:
        cls._validate_mutable_fields(values)
        result = await db.execute(
            update(AiModelCatalog)
            .where(
                AiModelCatalog.model_id == model_id,
                AiModelCatalog.lifecycle_status == 'active',
            )
            .values(**values)
        )
        return int(result.rowcount or 0)

    @classmethod
    async def delete_model(cls, db: AsyncSession, model_id: str) -> int:
        result = await db.execute(delete(AiModelCatalog).where(AiModelCatalog.model_id == model_id))

        return int(result.rowcount or 0)

    @classmethod
    async def delete_removable_model(cls, db: AsyncSession, model_id: str) -> int:
        result = await db.execute(
            delete(AiModelCatalog).where(
                AiModelCatalog.model_id == model_id,
                or_(
                    and_(
                        AiModelCatalog.lifecycle_status == 'draft',
                        AiModelCatalog.published_at.is_(None),
                    ),
                    AiModelCatalog.lifecycle_status == 'disabled',
                ),
            )
        )
        return int(result.rowcount or 0)

    @staticmethod
    def _validate_mutable_fields(values: dict[str, Any]) -> None:
        unsupported_fields = values.keys() - MODEL_MUTABLE_FIELDS
        if unsupported_fields:
            raise ValueError(f'不允许更新模型字段: {", ".join(sorted(unsupported_fields))}')
