from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
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
from plugins.ai.entity.do.ai_resource_binding_do import AiResourceBinding
from plugins.ai.entity.vo.ai_developer_api_vo import ApplicationPageQueryModel, UsageLedgerPageQueryModel
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


@dataclass(frozen=True, slots=True)
class DeveloperKeyAuthRecord:
    key_id: str
    application_id: str
    key_prefix: str
    secret_digest: str = field(repr=False)
    digest_version: str
    key_status: str
    expires_at: datetime | None
    retire_at: datetime | None
    application_status: str


@dataclass(frozen=True, slots=True)
class PublicModelRecord:
    platform_model_id: str
    model_code: str
    published_at: datetime
    model_type: str = ''


@dataclass(frozen=True, slots=True)
class InvocationSnapshotRecord:
    """统一授权快照：Application/Key/Grant/Model/Binding/Rate Card/Quota。"""

    application_id: str
    api_key_id: str
    platform_model_id: str
    model_code: str
    model_type: str
    binding_id: str
    engine_type: str
    engine_model_snapshot: str
    max_output_tokens: int | None
    rate_card_id: str
    rate_card_version: int
    billing_mode: str
    input_price_per_token: Decimal
    output_price_per_token: Decimal
    cache_price_per_token: Decimal | None
    search_price_per_unit: Decimal | None
    currency: str
    rpm_limit: int | None
    tpm_limit: int | None
    concurrency_limit: int | None
    monthly_budget: Decimal | None


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
    async def get_key_auth_record(cls, db: AsyncSession, key_prefix: str) -> DeveloperKeyAuthRecord | None:
        row = (
            await db.execute(
                select(
                    AiApiKey.api_key_id,
                    AiApiKey.application_id,
                    AiApiKey.key_prefix,
                    AiApiKey.secret_digest,
                    AiApiKey.digest_version,
                    AiApiKey.status,
                    AiApiKey.expires_at,
                    AiApiKey.retire_at,
                    AiApplication.status,
                )
                .join(AiApplication, AiApplication.application_id == AiApiKey.application_id)
                .where(AiApiKey.key_prefix == key_prefix)
            )
        ).first()
        if row is None:
            return None
        return DeveloperKeyAuthRecord(
            key_id=row[0],
            application_id=row[1],
            key_prefix=row[2],
            secret_digest=row[3],
            digest_version=row[4],
            key_status=row[5],
            expires_at=row[6],
            retire_at=row[7],
            application_status=row[8],
        )

    @classmethod
    async def list_public_models_for_key(cls, db: AsyncSession, api_key_id: str) -> list[PublicModelRecord]:
        rows = (
            await db.execute(
                select(
                    AiModelCatalog.model_id,
                    AiModelCatalog.model_code,
                    AiModelCatalog.published_at,
                    AiModelCatalog.model_type,
                )
                .join(
                    AiApiKeyModelGrant,
                    AiApiKeyModelGrant.platform_model_id == AiModelCatalog.model_id,
                )
                .join(AiApiKey, AiApiKey.api_key_id == AiApiKeyModelGrant.api_key_id)
                .join(AiApplication, AiApplication.application_id == AiApiKey.application_id)
                .where(
                    AiApiKey.api_key_id == api_key_id,
                    AiApiKey.status.in_({'active', 'retiring'}),
                    AiApplication.status == 'active',
                    AiApiKeyModelGrant.status == 'active',
                    AiModelCatalog.lifecycle_status == 'active',
                    AiModelCatalog.published_at.is_not(None),
                )
                .order_by(AiModelCatalog.model_code, AiModelCatalog.model_id)
            )
        ).all()
        return [
            PublicModelRecord(platform_model_id=row[0], model_code=row[1], published_at=row[2], model_type=row[3])
            for row in rows
            if row[2] is not None
        ]

    @classmethod
    async def get_public_model_for_key(
        cls,
        db: AsyncSession,
        api_key_id: str,
        model_code: str,
    ) -> PublicModelRecord | None:
        row = (
            await db.execute(
                select(
                    AiModelCatalog.model_id,
                    AiModelCatalog.model_code,
                    AiModelCatalog.published_at,
                    AiModelCatalog.model_type,
                )
                .join(
                    AiApiKeyModelGrant,
                    AiApiKeyModelGrant.platform_model_id == AiModelCatalog.model_id,
                )
                .join(AiApiKey, AiApiKey.api_key_id == AiApiKeyModelGrant.api_key_id)
                .join(AiApplication, AiApplication.application_id == AiApiKey.application_id)
                .where(
                    AiApiKey.api_key_id == api_key_id,
                    AiApiKey.status.in_({'active', 'retiring'}),
                    AiApplication.status == 'active',
                    AiApiKeyModelGrant.status == 'active',
                    AiModelCatalog.lifecycle_status == 'active',
                    AiModelCatalog.published_at.is_not(None),
                    AiModelCatalog.model_code == model_code,
                )
            )
        ).first()
        if row is None or row[2] is None:
            return None
        return PublicModelRecord(platform_model_id=row[0], model_code=row[1], published_at=row[2], model_type=row[3])

    @classmethod
    async def get_invocation_snapshot(
        cls,
        db: AsyncSession,
        api_key_id: str,
        model_code: str,
        expected_type: str,
        now: datetime,
    ) -> InvocationSnapshotRecord | None:
        """在单次只读事务中读取统一授权与计费快照。"""
        # PG timestamp 列为 naive，绑定参数需归一化为 naive UTC。
        normalized_now = now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo is not None else now
        row = (
            await db.execute(
                select(
                    AiApiKey.application_id,
                    AiModelCatalog.model_id,
                    AiModelCatalog.model_code,
                    AiModelCatalog.model_type,
                    AiModelCatalog.max_output_tokens,
                    AiResourceBinding.binding_id,
                    AiResourceBinding.engine_type,
                    AiResourceBinding.engine_resource_id,
                    AiModelRateCard.rate_card_id,
                    AiModelRateCard.version,
                    AiModelRateCard.billing_mode,
                    AiModelRateCard.input_price_per_token,
                    AiModelRateCard.output_price_per_token,
                    AiModelRateCard.cache_price_per_token,
                    AiModelRateCard.search_price_per_unit,
                    AiModelRateCard.currency,
                    AiApiQuotaPolicy.rpm_limit,
                    AiApiQuotaPolicy.tpm_limit,
                    AiApiQuotaPolicy.concurrency_limit,
                    AiApiQuotaPolicy.monthly_budget,
                )
                .join(AiApplication, AiApplication.application_id == AiApiKey.application_id)
                .join(AiApiKeyModelGrant, AiApiKeyModelGrant.api_key_id == AiApiKey.api_key_id)
                .join(AiModelCatalog, AiModelCatalog.model_id == AiApiKeyModelGrant.platform_model_id)
                .join(AiResourceBinding, AiResourceBinding.resource_id == AiModelCatalog.model_id)
                .join(AiModelRateCard, AiModelRateCard.platform_model_id == AiModelCatalog.model_id)
                .outerjoin(AiApiQuotaPolicy, AiApiQuotaPolicy.api_key_id == AiApiKey.api_key_id)
                .where(
                    AiApiKey.api_key_id == api_key_id,
                    AiApiKey.status.in_({'active', 'retiring'}),
                    AiApplication.status == 'active',
                    AiApiKeyModelGrant.status == 'active',
                    AiModelCatalog.lifecycle_status == 'active',
                    AiModelCatalog.published_at.is_not(None),
                    AiModelCatalog.model_code == model_code,
                    AiModelCatalog.model_type == expected_type,
                    AiModelRateCard.billing_mode == ('search_unit' if expected_type == 'rerank' else 'token'),
                    AiResourceBinding.binding_status == 'active',
                    AiModelRateCard.status == 'active',
                    AiModelRateCard.effective_from <= normalized_now,
                    or_(AiModelRateCard.effective_to.is_(None), AiModelRateCard.effective_to > normalized_now),
                )
                .order_by(AiModelRateCard.version.desc(), AiModelRateCard.effective_from.desc())
            )
        ).first()
        if row is None:
            return None
        return InvocationSnapshotRecord(
            application_id=row[0],
            api_key_id=api_key_id,
            platform_model_id=row[1],
            model_code=row[2],
            model_type=row[3],
            max_output_tokens=row[4],
            binding_id=row[5],
            engine_type=row[6],
            engine_model_snapshot=row[7],
            rate_card_id=row[8],
            rate_card_version=row[9],
            billing_mode=row[10],
            input_price_per_token=row[11],
            output_price_per_token=row[12],
            cache_price_per_token=row[13],
            search_price_per_unit=row[14],
            currency=row[15],
            rpm_limit=row[16],
            tpm_limit=row[17],
            concurrency_limit=row[18],
            monthly_budget=row[19],
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
        normalized_now = now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo is not None else now
        return (
            (
                await db.execute(
                    select(AiModelRateCard)
                    .where(
                        AiModelRateCard.platform_model_id == platform_model_id,
                        AiModelRateCard.status == 'active',
                        AiModelRateCard.effective_from <= normalized_now,
                        or_(AiModelRateCard.effective_to.is_(None), AiModelRateCard.effective_to > normalized_now),
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

    LEDGER_FINALIZE_FIELDS = frozenset(
        {
            'status',
            'input_tokens',
            'output_tokens',
            'cache_tokens',
            'reasoning_tokens',
            'search_units',
            'raw_cost',
            'billable_cost',
            'cost_estimated',
            'error_code',
            'upstream_request_id',
            'finished_at',
            'updated_at',
        }
    )

    @classmethod
    async def finalize_usage_ledger(cls, db: AsyncSession, request_id: str, values: dict[str, Any]) -> int:
        unsupported = set(values).difference(cls.LEDGER_FINALIZE_FIELDS)
        if unsupported:
            raise ValueError(f'不允许更新 Usage Ledger 字段: {", ".join(sorted(unsupported))}')
        if not values:
            return 0
        result = await db.execute(
            update(AiApiUsageLedger).where(AiApiUsageLedger.request_id == request_id).values(**values)
        )
        return int(result.rowcount or 0)

    @classmethod
    async def list_started_ledgers_before(
        cls,
        db: AsyncSession,
        before: datetime,
        limit: int = 200,
    ) -> list[AiApiUsageLedger]:
        return list(
            (
                await db.execute(
                    select(AiApiUsageLedger)
                    .where(
                        AiApiUsageLedger.status == 'started',
                        AiApiUsageLedger.started_at < before,
                    )
                    .order_by(AiApiUsageLedger.started_at)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    @classmethod
    async def list_rate_cards_by_model(cls, db: AsyncSession, model_id: str) -> list[AiModelRateCard]:
        return list(
            (
                await db.execute(
                    select(AiModelRateCard)
                    .where(AiModelRateCard.platform_model_id == model_id)
                    .order_by(AiModelRateCard.version.desc())
                )
            )
            .scalars()
            .all()
        )

    @classmethod
    async def get_rate_card_by_id(cls, db: AsyncSession, rate_card_id: str) -> AiModelRateCard | None:
        return (
            (await db.execute(select(AiModelRateCard).where(AiModelRateCard.rate_card_id == rate_card_id)))
            .scalars()
            .first()
        )

    @classmethod
    async def disable_rate_card(cls, db: AsyncSession, rate_card_id: str) -> int:
        result = await db.execute(
            update(AiModelRateCard).where(AiModelRateCard.rate_card_id == rate_card_id).values(status='disabled')
        )
        return int(result.rowcount or 0)

    @classmethod
    async def list_usage_ledgers(
        cls,
        db: AsyncSession,
        application_id: str,
        query_object: object,
    ) -> PageModel:
        """按 Application 分页查询 Usage Ledger（展示用途，不含敏感快照）。"""
        if not isinstance(query_object, UsageLedgerPageQueryModel):
            raise TypeError('用量查询参数必须使用 UsageLedgerPageQueryModel')
        query = select(AiApiUsageLedger).where(AiApiUsageLedger.application_id == application_id)
        if query_object.operation_type is not None:
            query = query.where(AiApiUsageLedger.operation_type == query_object.operation_type)
        if query_object.status is not None:
            query = query.where(AiApiUsageLedger.status == query_object.status)
        query = query.order_by(AiApiUsageLedger.started_at.desc(), AiApiUsageLedger.request_id)
        return await PageUtil.paginate(
            db,
            query,
            query_object.page_num,
            query_object.page_size,
            True,
        )

    @staticmethod
    def _validate_fields(values: dict[str, Any], allowed_fields: frozenset[str], resource_name: str) -> None:
        unsupported_fields = values.keys() - allowed_fields
        if unsupported_fields:
            raise ValueError(f'不允许更新{resource_name}字段: {", ".join(sorted(unsupported_fields))}')
