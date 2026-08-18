from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from common.vo import PageModel
from plugins.ai.entity.do.ai_developer_api_do import (
    AiApiKey,
    AiApiKeyModelGrant,
    AiApiQuotaPolicy,
    AiApiUsageLedger,
    AiApplication,
    AiModelRateCard,
)
from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
from plugins.ai.entity.vo.ai_developer_api_vo import ApplicationPageQueryModel
from utils.page_util import PageUtil

APPLICATION_UPDATE_FIELDS = frozenset(
    {
        'application_name',
        'updated_by',
        'updated_at',
    }
)
APPLICATION_DISABLE_FIELDS = frozenset({'disabled_at', 'updated_by', 'updated_at'})
KEY_MUTABLE_FIELDS = frozenset(
    {
        'status',
        'expires_at',
        'retire_at',
        'last_used_at',
        'revoked_at',
        'updated_by',
        'updated_at',
    }
)
QUOTA_MUTABLE_FIELDS = frozenset(
    {
        'rpm_limit',
        'tpm_limit',
        'concurrency_limit',
        'monthly_budget',
        'warning_ratio',
        'hard_limit_ratio',
        'currency',
        'updated_by',
        'updated_at',
    }
)


class AiDeveloperApiDao:
    """Developer API 管理面与调用事实数据库操作层。"""

    @classmethod
    async def create_application(cls, db: AsyncSession, application: AiApplication) -> AiApplication:
        db.add(application)
        await db.flush()
        return application

    @classmethod
    async def get_application_by_id(cls, db: AsyncSession, application_id: str) -> AiApplication | None:
        return (
            (await db.execute(select(AiApplication).where(AiApplication.application_id == application_id)))
            .scalars()
            .first()
        )

    @classmethod
    async def get_application_by_code(cls, db: AsyncSession, application_code: str) -> AiApplication | None:
        return (
            (await db.execute(select(AiApplication).where(AiApplication.application_code == application_code)))
            .scalars()
            .first()
        )

    @classmethod
    async def get_application_for_update(cls, db: AsyncSession, application_id: str) -> AiApplication | None:
        return (
            (
                await db.execute(
                    select(AiApplication)
                    .where(AiApplication.application_id == application_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .first()
        )

    @classmethod
    async def list_applications(
        cls,
        db: AsyncSession,
        query_object: ApplicationPageQueryModel,
        *,
        owner_user_id: int | None,
        is_page: bool = False,
    ) -> PageModel | list[dict[str, Any]]:
        query = (
            select(AiApplication)
            .where(
                AiApplication.owner_user_id == owner_user_id if owner_user_id is not None else True,
                AiApplication.application_code.contains(query_object.application_code, autoescape=True)
                if query_object.application_code
                else True,
                AiApplication.application_name.contains(query_object.application_name, autoescape=True)
                if query_object.application_name
                else True,
                AiApplication.status == query_object.status if query_object.status else True,
            )
            .order_by(AiApplication.application_code, AiApplication.application_id)
        )
        return await PageUtil.paginate(
            db,
            query,
            query_object.page_num,
            query_object.page_size,
            is_page,
        )

    @classmethod
    async def update_application_with_version(
        cls,
        db: AsyncSession,
        application_id: str,
        *,
        expected_version: int,
        values: dict[str, Any],
    ) -> int:
        cls._validate_fields(values, APPLICATION_UPDATE_FIELDS, '应用')
        if not values:
            return 0
        result = await db.execute(
            update(AiApplication)
            .where(
                AiApplication.application_id == application_id,
                AiApplication.version == expected_version,
            )
            .values(**values, version=expected_version + 1)
        )
        return int(result.rowcount or 0)

    @classmethod
    async def disable_application_if_active(
        cls,
        db: AsyncSession,
        application_id: str,
        values: dict[str, Any],
    ) -> int:
        cls._validate_fields(values, APPLICATION_DISABLE_FIELDS, '应用停用')
        result = await db.execute(
            update(AiApplication)
            .where(
                AiApplication.application_id == application_id,
                AiApplication.status == 'active',
            )
            .values(**values, status='disabled', version=AiApplication.version + 1)
        )
        return int(result.rowcount or 0)

    @classmethod
    async def create_key(cls, db: AsyncSession, api_key: AiApiKey) -> AiApiKey:
        db.add(api_key)
        await db.flush()
        return api_key

    @classmethod
    async def get_key_by_id(
        cls,
        db: AsyncSession,
        application_id: str,
        api_key_id: str,
    ) -> AiApiKey | None:
        return (
            (
                await db.execute(
                    select(AiApiKey).where(
                        AiApiKey.application_id == application_id,
                        AiApiKey.api_key_id == api_key_id,
                    )
                )
            )
            .scalars()
            .first()
        )

    @classmethod
    async def get_key_for_update(
        cls,
        db: AsyncSession,
        application_id: str,
        api_key_id: str,
    ) -> AiApiKey | None:
        return (
            (
                await db.execute(
                    select(AiApiKey)
                    .where(
                        AiApiKey.application_id == application_id,
                        AiApiKey.api_key_id == api_key_id,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .first()
        )

    @classmethod
    async def list_keys_by_application(cls, db: AsyncSession, application_id: str) -> list[AiApiKey]:
        return list(
            (
                await db.execute(
                    select(AiApiKey)
                    .where(AiApiKey.application_id == application_id)
                    .order_by(AiApiKey.created_at, AiApiKey.api_key_id)
                )
            )
            .scalars()
            .all()
        )

    @classmethod
    async def transition_key_status(
        cls,
        db: AsyncSession,
        application_id: str,
        api_key_id: str,
        *,
        expected_statuses: Collection[str],
        values: dict[str, Any],
    ) -> int:
        cls._validate_fields(values, KEY_MUTABLE_FIELDS, 'API Key')
        if not expected_statuses or not values:
            return 0
        result = await db.execute(
            update(AiApiKey)
            .where(
                AiApiKey.application_id == application_id,
                AiApiKey.api_key_id == api_key_id,
                AiApiKey.status.in_(expected_statuses),
            )
            .values(**values)
        )
        return int(result.rowcount or 0)

    @classmethod
    async def replace_grants(
        cls,
        db: AsyncSession,
        api_key_id: str,
        grants: Sequence[AiApiKeyModelGrant],
    ) -> int:
        await db.execute(delete(AiApiKeyModelGrant).where(AiApiKeyModelGrant.api_key_id == api_key_id))
        db.add_all(grants)
        await db.flush()
        return len(grants)

    @classmethod
    async def list_grants(cls, db: AsyncSession, api_key_id: str) -> list[AiApiKeyModelGrant]:
        return list(
            (
                await db.execute(
                    select(AiApiKeyModelGrant)
                    .where(AiApiKeyModelGrant.api_key_id == api_key_id)
                    .order_by(AiApiKeyModelGrant.platform_model_id, AiApiKeyModelGrant.grant_id)
                )
            )
            .scalars()
            .all()
        )

    @classmethod
    async def find_active_model_ids(cls, db: AsyncSession, model_ids: Collection[str]) -> set[str]:
        if not model_ids:
            return set()
        result = await db.execute(
            select(AiModelCatalog.model_id).where(
                AiModelCatalog.model_id.in_(model_ids),
                AiModelCatalog.lifecycle_status == 'active',
            )
        )
        return set(result.scalars().all())

    @classmethod
    async def create_quota(cls, db: AsyncSession, quota: AiApiQuotaPolicy) -> AiApiQuotaPolicy:
        db.add(quota)
        await db.flush()
        return quota

    @classmethod
    async def get_quota_by_key(cls, db: AsyncSession, api_key_id: str) -> AiApiQuotaPolicy | None:
        return (
            (await db.execute(select(AiApiQuotaPolicy).where(AiApiQuotaPolicy.api_key_id == api_key_id)))
            .scalars()
            .first()
        )

    @classmethod
    async def update_quota_with_version(
        cls,
        db: AsyncSession,
        api_key_id: str,
        *,
        expected_version: int,
        values: dict[str, Any],
    ) -> int:
        cls._validate_fields(values, QUOTA_MUTABLE_FIELDS, '配额')
        if not values:
            return 0
        result = await db.execute(
            update(AiApiQuotaPolicy)
            .where(
                AiApiQuotaPolicy.api_key_id == api_key_id,
                AiApiQuotaPolicy.version == expected_version,
            )
            .values(**values, version=expected_version + 1)
        )
        return int(result.rowcount or 0)

    @classmethod
    async def create_rate_card(cls, db: AsyncSession, rate_card: AiModelRateCard) -> AiModelRateCard:
        db.add(rate_card)
        await db.flush()
        return rate_card

    @classmethod
    async def get_active_rate_card(
        cls,
        db: AsyncSession,
        platform_model_id: str,
        now: datetime,
    ) -> AiModelRateCard | None:
        return (
            (
                await db.execute(
                    select(AiModelRateCard)
                    .where(
                        AiModelRateCard.platform_model_id == platform_model_id,
                        AiModelRateCard.status == 'active',
                        AiModelRateCard.effective_from <= now,
                        or_(AiModelRateCard.effective_to.is_(None), AiModelRateCard.effective_to > now),
                    )
                    .order_by(AiModelRateCard.version.desc(), AiModelRateCard.effective_from.desc())
                )
            )
            .scalars()
            .first()
        )

    @classmethod
    async def create_usage_ledger(cls, db: AsyncSession, ledger: AiApiUsageLedger) -> AiApiUsageLedger:
        db.add(ledger)
        await db.flush()
        return ledger

    @classmethod
    async def get_usage_ledger(cls, db: AsyncSession, request_id: str) -> AiApiUsageLedger | None:
        return (
            (await db.execute(select(AiApiUsageLedger).where(AiApiUsageLedger.request_id == request_id)))
            .scalars()
            .first()
        )

    @staticmethod
    def _validate_fields(values: dict[str, Any], allowed_fields: frozenset[str], resource_name: str) -> None:
        unsupported_fields = values.keys() - allowed_fields
        if unsupported_fields:
            raise ValueError(f'不允许更新{resource_name}字段: {", ".join(sorted(unsupported_fields))}')
