from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from plugins.ai.entity.do.ai_resource_binding_do import AiResourceBinding

BINDING_MUTABLE_FIELDS = frozenset(
    {
        'engine_resource_id',
        'binding_status',
        'engine_metadata',
        'update_by',
        'update_time',
        'version',
    }
)


class AiResourceBindingDao:
    """Platform 资源与运行时绑定数据库操作层。"""

    @classmethod
    async def get_binding_by_id(cls, db: AsyncSession, binding_id: str) -> AiResourceBinding | None:
        return (
            (await db.execute(select(AiResourceBinding).where(AiResourceBinding.binding_id == binding_id)))
            .scalars()
            .first()
        )

    @classmethod
    async def get_current_binding(cls, db: AsyncSession, model_id: str) -> AiResourceBinding | None:
        return (
            (
                await db.execute(
                    select(AiResourceBinding).where(
                        AiResourceBinding.resource_type == 'model',
                        AiResourceBinding.resource_id == model_id,
                    )
                )
            )
            .scalars()
            .first()
        )

    @classmethod
    async def get_current_binding_for_update(cls, db: AsyncSession, model_id: str) -> AiResourceBinding | None:
        return (
            (
                await db.execute(
                    select(AiResourceBinding)
                    .where(
                        AiResourceBinding.resource_type == 'model',
                        AiResourceBinding.resource_id == model_id,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .first()
        )

    @classmethod
    async def create_binding(cls, db: AsyncSession, binding: AiResourceBinding) -> AiResourceBinding:
        db.add(binding)
        await db.flush()

        return binding

    @classmethod
    async def delete_binding_by_resource(cls, db: AsyncSession, model_id: str) -> int:
        result = await db.execute(
            delete(AiResourceBinding).where(
                AiResourceBinding.resource_type == 'model',
                AiResourceBinding.resource_id == model_id,
            )
        )

        return int(result.rowcount or 0)

    @classmethod
    async def update_binding_with_version(
        cls,
        db: AsyncSession,
        binding_id: str,
        *,
        expected_version: int,
        values: dict[str, Any],
    ) -> int:
        unsupported_fields = values.keys() - BINDING_MUTABLE_FIELDS
        if unsupported_fields:
            raise ValueError(f'不允许更新绑定字段: {", ".join(sorted(unsupported_fields))}')
        update_values = {**values, 'version': expected_version + 1}
        result = await db.execute(
            update(AiResourceBinding)
            .where(
                AiResourceBinding.binding_id == binding_id,
                AiResourceBinding.version == expected_version,
            )
            .values(**update_values)
        )

        return int(result.rowcount or 0)
