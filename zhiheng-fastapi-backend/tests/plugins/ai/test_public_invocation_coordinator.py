import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import fakeredis.aioredis
import pytest
import pytest_asyncio
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from plugins.ai.dao.ai_developer_api_dao import InvocationSnapshotRecord
from plugins.ai.domain.public_api_errors import PublicApiError
from plugins.ai.entity.do.ai_developer_api_do import AiApiUsageLedger
from plugins.ai.infrastructure.developer_key_authenticator import DeveloperApiIdentity
from plugins.ai.service.public_invocation_coordinator import (
    LEASE_TTL_SECONDS,
    OperationResult,
    PublicInvocationCoordinator,
    PublicQuotaEngine,
    PublicQuotaReconciler,
    Reservation,
    RuntimeUsage,
)

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
APPLICATION_ID = 'app_00000000000040008000000000000001'
KEY_ID = 'key_00000000000040008000000000000001'
MODEL_ID = 'mdl_00000000000040008000000000000001'
BINDING_ID = 'bnd_00000000000040008000000000000001'
RATE_CARD_ID = 'rate_00000000000040008000000000000001'
MODEL_CODE = 'deepseek-chat'


def _snapshot(
    *,
    model_type: str = 'chat',
    billing_mode: str = 'token',
    rpm_limit: int | None = None,
    tpm_limit: int | None = None,
    concurrency_limit: int | None = None,
    monthly_budget: Decimal | None = None,
) -> InvocationSnapshotRecord:
    return InvocationSnapshotRecord(
        application_id=APPLICATION_ID,
        api_key_id=KEY_ID,
        platform_model_id=MODEL_ID,
        model_code=MODEL_CODE,
        model_type=model_type,
        binding_id=BINDING_ID,
        engine_type='litellm',
        engine_model_snapshot='runtime-deepseek-chat',
        max_output_tokens=1024,
        rate_card_id=RATE_CARD_ID,
        rate_card_version=1,
        billing_mode=billing_mode,
        input_price_per_token=Decimal('0.000001'),
        output_price_per_token=Decimal('0.000002'),
        cache_price_per_token=None,
        search_price_per_unit=Decimal('0.001') if billing_mode == 'search_unit' else None,
        currency='USD',
        rpm_limit=rpm_limit,
        tpm_limit=tpm_limit,
        concurrency_limit=concurrency_limit,
        monthly_budget=monthly_budget,
    )


def _identity() -> DeveloperApiIdentity:
    return DeveloperApiIdentity(application_id=APPLICATION_ID, key_id=KEY_ID)


class StubSnapshotDao:
    """返回固定快照的测试 DAO，避免最小 Ledger fixture 依赖全部关联表。"""

    snapshot: InvocationSnapshotRecord = _snapshot()

    @classmethod
    async def get_invocation_snapshot(
        cls,
        db: object,
        api_key_id: str,
        model_code: str,
        expected_type: str,
        now: datetime,
    ) -> InvocationSnapshotRecord | None:
        del db, now
        if api_key_id != KEY_ID or model_code != MODEL_CODE:
            return None
        if cls.snapshot.model_type != expected_type:
            return None
        return cls.snapshot


async def _plain_operation_result(snapshot: InvocationSnapshotRecord) -> OperationResult:
    del snapshot
    return OperationResult(usage=None, value=None)


@pytest.mark.asyncio
async def test_quota_reserve_settle_and_release_are_atomic_and_lease_scoped() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    engine = PublicQuotaEngine(redis, clock=lambda: NOW)
    snapshot = _snapshot(rpm_limit=10, tpm_limit=1000, concurrency_limit=5)

    first = await engine.reserve(
        snapshot,
        request_id='use_00000000000040008000000000000001',
        reserved_tokens=100,
        reserved_cost=Decimal('0.0002'),
    )
    second = await engine.reserve(
        snapshot,
        request_id='use_00000000000040008000000000000002',
        reserved_tokens=200,
        reserved_cost=Decimal('0.0004'),
    )

    rpm = await redis.get(f'ai:quota:rpm:{KEY_ID}:{int(NOW.timestamp()) // 60}')
    tpm = await redis.get(f'ai:quota:tpm:{KEY_ID}:{int(NOW.timestamp()) // 60}')
    assert int(rpm) == 2
    assert int(tpm) == 300

    # 结算第一条：实际用量低于预留量。
    settled = await engine.settle(
        snapshot,
        first,
        actual_tokens=80,
        actual_cost=Decimal('0.00016'),
        now=NOW,
    )
    assert settled is True
    assert int(await redis.get(f'ai:quota:tpm:{KEY_ID}:{int(NOW.timestamp()) // 60}')) == 280

    # 释放第二条。
    released = await engine.release(snapshot, second, now=NOW)
    assert released is True
    assert int(await redis.get(f'ai:quota:tpm:{KEY_ID}:{int(NOW.timestamp()) // 60}')) == 80

    # 租约已删除，重复结算或释放返回 False。
    assert await engine.settle(snapshot, first, actual_tokens=1, actual_cost=Decimal('0'), now=NOW) is False
    assert await engine.release(snapshot, second, now=NOW) is False
    await redis.aclose()


@pytest.mark.asyncio
async def test_monthly_budget_survives_until_after_utc_month_end() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    engine = PublicQuotaEngine(redis, clock=lambda: NOW)

    await engine.reserve(
        _snapshot(monthly_budget=Decimal('10')),
        request_id='use_00000000000040008000000000000003',
        reserved_tokens=1,
        reserved_cost=Decimal('0.1'),
    )

    budget_key = f'ai:quota:budget:{KEY_ID}:{NOW:%Y-%m}'
    seconds_until_month_end = int((datetime(2026, 9, 1, tzinfo=timezone.utc) - NOW).total_seconds())
    assert await redis.ttl(budget_key) >= seconds_until_month_end + LEASE_TTL_SECONDS
    minute_window = int(NOW.timestamp()) // 60
    assert await redis.ttl(f'ai:quota:tpm:{KEY_ID}:{minute_window}') >= LEASE_TTL_SECONDS
    await redis.aclose()


@pytest.mark.asyncio
async def test_quota_settlement_uses_the_original_reservation_windows() -> None:
    redis = fakeredis.aioredis.FakeRedis()
    engine = PublicQuotaEngine(redis, clock=lambda: NOW)
    snapshot = _snapshot(tpm_limit=1000, monthly_budget=Decimal('10'))
    reservation = await engine.reserve(
        snapshot,
        request_id='use_00000000000040008000000000000004',
        reserved_tokens=100,
        reserved_cost=Decimal('0.1'),
    )
    later = NOW + timedelta(minutes=1)

    assert await engine.settle(
        snapshot,
        reservation,
        actual_tokens=80,
        actual_cost=Decimal('0.08'),
        now=later,
    )

    old_tpm_key = f'ai:quota:tpm:{KEY_ID}:{int(NOW.timestamp()) // 60}'
    new_tpm_key = f'ai:quota:tpm:{KEY_ID}:{int(later.timestamp()) // 60}'
    assert await redis.get(old_tpm_key) == b'80'
    assert await redis.get(new_tpm_key) is None
    await redis.aclose()


@pytest.mark.asyncio
async def test_quota_limits_reject_with_fixed_error_kinds_and_roll_back() -> None:
    async def scenario(
        snapshot: InvocationSnapshotRecord,
        *,
        first_tokens: int,
        second_tokens: int,
        first_cost: Decimal,
        second_cost: Decimal,
        expected_code: str,
    ) -> None:
        redis = fakeredis.aioredis.FakeRedis()
        engine = PublicQuotaEngine(redis, clock=lambda: NOW)
        await engine.reserve(
            snapshot=snapshot,
            request_id='use_00000000000040008000000000000001',
            reserved_tokens=first_tokens,
            reserved_cost=first_cost,
        )
        with pytest.raises(PublicApiError) as raised:
            await engine.reserve(
                snapshot=snapshot,
                request_id='use_00000000000040008000000000000002',
                reserved_tokens=second_tokens,
                reserved_cost=second_cost,
            )
        assert raised.value.code == expected_code
        # 被拒绝的请求必须完整回滚：并发、TPM、RPM、预算计数恢复。
        concurrency = await redis.get(f'ai:quota:concurrency:{KEY_ID}')
        tpm = await redis.get(f'ai:quota:tpm:{KEY_ID}:{int(NOW.timestamp()) // 60}')
        rpm = await redis.get(f'ai:quota:rpm:{KEY_ID}:{int(NOW.timestamp()) // 60}')
        assert int(concurrency) == 1
        assert int(tpm) == first_tokens
        assert int(rpm) == 1
        if expected_code == 'budget_exceeded':
            budget = await redis.get(f'ai:quota:budget:{KEY_ID}:2026-08')
            assert Decimal(budget.decode()) == first_cost
        await redis.aclose()

    await scenario(
        _snapshot(rpm_limit=1),
        first_tokens=1,
        second_tokens=1,
        first_cost=Decimal('0'),
        second_cost=Decimal('0'),
        expected_code='rate_limit_exceeded',
    )
    await scenario(
        _snapshot(tpm_limit=10),
        first_tokens=6,
        second_tokens=6,
        first_cost=Decimal('0'),
        second_cost=Decimal('0'),
        expected_code='rate_limit_exceeded',
    )
    await scenario(
        _snapshot(concurrency_limit=1),
        first_tokens=1,
        second_tokens=1,
        first_cost=Decimal('0'),
        second_cost=Decimal('0'),
        expected_code='concurrency_limit_exceeded',
    )
    await scenario(
        _snapshot(monthly_budget=Decimal('0.0002')),
        first_tokens=1,
        second_tokens=1,
        first_cost=Decimal('0.00015'),
        second_cost=Decimal('0.00015'),
        expected_code='budget_exceeded',
    )


@pytest.mark.asyncio
async def test_redis_unavailable_fails_closed() -> None:
    class BrokenRedis:
        async def eval(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError('redis down')

    engine = PublicQuotaEngine(BrokenRedis())
    with pytest.raises(PublicApiError) as raised:
        await engine.reserve(
            snapshot=_snapshot(),
            request_id='use_00000000000040008000000000000001',
            reserved_tokens=1,
            reserved_cost=Decimal('0'),
        )
    assert raised.value.code == 'service_unavailable'


@pytest_asyncio.fixture
async def ledger_database() -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')

    @event.listens_for(engine.sync_engine, 'connect')
    def configure_sqlite(connection: Any, _record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()

    session_maker = async_sessionmaker(engine, expire_on_commit=True)
    async with engine.begin() as connection:
        # Ledger 外键引用的父表必须存在且包含对应主键行。
        from plugins.ai.entity.do.ai_developer_api_do import (
            AiApiKey,
            AiApiKeyModelGrant,
            AiApiQuotaPolicy,
            AiApplication,
            AiModelRateCard,
        )
        from plugins.ai.entity.do.ai_model_catalog_do import AiModelCatalog
        from plugins.ai.entity.do.ai_resource_binding_do import AiResourceBinding

        for table in (
            AiModelCatalog.__table__,
            AiResourceBinding.__table__,
            AiApplication.__table__,
            AiApiKey.__table__,
            AiApiKeyModelGrant.__table__,
            AiApiQuotaPolicy.__table__,
            AiModelRateCard.__table__,
            AiApiUsageLedger.__table__,
        ):
            await connection.run_sync(table.create)
    async with session_maker() as session:
        snapshot = StubSnapshotDao.snapshot
        session.add(
            AiApplication(
                application_id=APPLICATION_ID,
                application_code='billing-prod',
                application_name='billing-prod',
                owner_user_id=7,
                status='active',
                version=1,
                created_by='tester',
                created_at=NOW,
                updated_by='tester',
                updated_at=NOW,
            )
        )
        await session.commit()
        session.add_all(
            (
                AiApiKey(
                    api_key_id=KEY_ID,
                    application_id=APPLICATION_ID,
                    key_name='key',
                    key_prefix='sk-zh_test',
                    secret_digest='d' * 64,
                    digest_version='v1',
                    fingerprint='abcd',
                    status='active',
                    created_by='tester',
                    created_at=NOW,
                    updated_by='tester',
                    updated_at=NOW,
                ),
                AiModelCatalog(
                    model_id=MODEL_ID,
                    model_code=MODEL_CODE,
                    display_name=MODEL_CODE,
                    model_type='chat',
                    capabilities={},
                    max_output_tokens=1024,
                    default_parameters={},
                    lifecycle_status='active',
                    published_at=NOW,
                    create_by='tester',
                    create_time=NOW,
                    update_by='tester',
                    update_time=NOW,
                ),
            )
        )
        await session.commit()
        session.add_all(
            (
                AiApiKeyModelGrant(
                    grant_id='grt_00000000000040008000000000000001',
                    api_key_id=KEY_ID,
                    platform_model_id=MODEL_ID,
                    status='active',
                    created_by='tester',
                    created_at=NOW,
                    updated_by='tester',
                    updated_at=NOW,
                ),
                AiResourceBinding(
                    binding_id=BINDING_ID,
                    resource_type='model',
                    resource_id=MODEL_ID,
                    engine_type='litellm',
                    engine_resource_id='runtime-deepseek-chat',
                    binding_status='active',
                    engine_metadata={},
                    version=1,
                    create_by='tester',
                    create_time=NOW,
                    update_by='tester',
                    update_time=NOW,
                ),
                AiModelRateCard(
                    rate_card_id=RATE_CARD_ID,
                    platform_model_id=MODEL_ID,
                    version=1,
                    billing_mode='token',
                    input_price_per_token=Decimal('0.000001'),
                    output_price_per_token=Decimal('0.000002'),
                    cache_price_per_token=None,
                    search_price_per_unit=None,
                    currency='USD',
                    effective_from=NOW - timedelta(days=1),
                    effective_to=None,
                    status='active',
                    created_by='tester',
                    created_at=NOW,
                    updated_by='tester',
                    updated_at=NOW,
                ),
                AiApiQuotaPolicy(
                    quota_id='qta_00000000000040008000000000000001',
                    api_key_id=KEY_ID,
                    rpm_limit=None,
                    tpm_limit=None,
                    concurrency_limit=None,
                    monthly_budget=None,
                    warning_ratio=Decimal('0.8000'),
                    hard_limit_ratio=Decimal('1.0000'),
                    currency='USD',
                    version=1,
                    created_by='tester',
                    created_at=NOW,
                    updated_by='tester',
                    updated_at=NOW,
                ),
            )
        )
        await session.commit()
    yield engine, session_maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_coordinator_run_writes_started_and_completed_ledger_and_settles_quota(
    ledger_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, session_maker = ledger_database
    redis = fakeredis.aioredis.FakeRedis()
    quota = PublicQuotaEngine(redis, clock=lambda: NOW, token_factory=lambda: 'lease-token')

    async with session_maker() as db:
        coordinator = PublicInvocationCoordinator(
            db,
            redis,
            session_factory=lambda: session_maker(),
            quota=quota,
            dao=StubSnapshotDao,
            clock=lambda: NOW,
            request_id_factory=lambda: 'use_00000000000040008000000000000001',
        )
        snapshot = _snapshot()

        async def operation(snapshot: InvocationSnapshotRecord) -> OperationResult:
            del snapshot
            return OperationResult(
                usage=RuntimeUsage(input_tokens=4, output_tokens=3),
                value='payload-value',
            )

        result = await coordinator.run(
            _identity(),
            MODEL_CODE,
            'chat',
            operation_type='chat',
            input_bytes=10,
            output_ceiling=64,
            operation=operation,
        )

    assert result.payload == 'payload-value'
    assert result.settled.status == 'completed'
    assert result.settled.input_tokens == 4
    assert result.settled.output_tokens == 3
    assert result.settled.cost_estimated is False

    async with session_maker() as session:
        ledger = (
            (
                await session.execute(
                    select(AiApiUsageLedger).where(
                        AiApiUsageLedger.request_id == 'use_00000000000040008000000000000001'
                    )
                )
            )
            .scalars()
            .first()
        )
    assert ledger is not None
    assert ledger.status == 'completed'
    assert ledger.operation_type == 'chat'
    assert ledger.input_tokens == 4
    assert ledger.output_tokens == 3
    assert ledger.billable_cost == Decimal('0.000004') + Decimal('0.000006')
    # 租约已结算删除。
    assert await redis.get('ai:quota:lease:use_00000000000040008000000000000001') is None
    await redis.aclose()


@pytest.mark.asyncio
async def test_coordinator_run_releases_reservation_when_started_ledger_write_fails(
    ledger_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, session_maker = ledger_database
    redis = fakeredis.aioredis.FakeRedis()
    quota = PublicQuotaEngine(redis, clock=lambda: NOW, token_factory=lambda: 'lease-token')

    def broken_factory() -> AsyncSession:
        raise RuntimeError('db unavailable')

    async with session_maker() as db:
        coordinator = PublicInvocationCoordinator(
            db,
            redis,
            session_factory=broken_factory,
            quota=quota,
            dao=StubSnapshotDao,
            clock=lambda: NOW,
            request_id_factory=lambda: 'use_00000000000040008000000000000001',
        )
        with pytest.raises(PublicApiError) as raised:
            await coordinator.run(
                _identity(),
                MODEL_CODE,
                'chat',
                operation_type='chat',
                input_bytes=10,
                operation=_plain_operation_result,
            )
    assert raised.value.code == 'service_unavailable'
    assert await redis.get('ai:quota:lease:use_00000000000040008000000000000001') is None
    assert int(await redis.get(f'ai:quota:concurrency:{KEY_ID}')) == 0
    await redis.aclose()


@pytest.mark.asyncio
async def test_coordinator_run_marks_failed_and_settles_conservatively_on_runtime_error(
    ledger_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, session_maker = ledger_database
    redis = fakeredis.aioredis.FakeRedis()
    quota = PublicQuotaEngine(redis, clock=lambda: NOW, token_factory=lambda: 'lease-token')

    from plugins.ai.domain.model_service_errors import ModelServiceError

    async with session_maker() as db:
        StubSnapshotDao.snapshot = _snapshot()
        coordinator = PublicInvocationCoordinator(
            db,
            redis,
            session_factory=lambda: session_maker(),
            quota=quota,
            dao=StubSnapshotDao,
            clock=lambda: NOW,
            request_id_factory=lambda: 'use_00000000000040008000000000000001',
        )
        with pytest.raises(ModelServiceError) as raised:
            await coordinator.run(
                _identity(),
                MODEL_CODE,
                'chat',
                operation_type='chat',
                input_bytes=10,
                operation=lambda snapshot: (_ for _ in ()).throw(ModelServiceError.provider_auth_failed()),
            )
    assert raised.value.code == 'MODEL_PROVIDER_AUTH_FAILED'

    async with session_maker() as session:
        ledger = (
            (
                await session.execute(
                    select(AiApiUsageLedger).where(
                        AiApiUsageLedger.request_id == 'use_00000000000040008000000000000001'
                    )
                )
            )
            .scalars()
            .first()
        )
    assert ledger is not None
    assert ledger.status == 'failed'
    assert ledger.cost_estimated is True
    assert ledger.error_code == 'MODEL_PROVIDER_AUTH_FAILED'
    assert ledger.input_tokens == 1034  # 保守预留量（输入 bytes + 模型最大输出）
    await redis.aclose()


@pytest.mark.asyncio
async def test_search_unit_settlement_does_not_charge_tokens(
    ledger_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, session_maker = ledger_database
    redis = fakeredis.aioredis.FakeRedis()
    quota = PublicQuotaEngine(redis, clock=lambda: NOW, token_factory=lambda: 'lease-token')

    async with session_maker() as db:
        coordinator = PublicInvocationCoordinator(
            db,
            redis,
            session_factory=lambda: session_maker(),
            quota=quota,
            dao=StubSnapshotDao,
            clock=lambda: NOW,
            request_id_factory=lambda: 'use_00000000000040008000000000000001',
        )
        StubSnapshotDao.snapshot = _snapshot(model_type='rerank', billing_mode='search_unit')

        async def operation(snapshot: InvocationSnapshotRecord) -> OperationResult:
            del snapshot
            return OperationResult(usage=RuntimeUsage(input_tokens=22, search_units=2), value=None)

        result = await coordinator.run(
            _identity(),
            MODEL_CODE,
            'rerank',
            operation_type='rerank',
            input_bytes=100,
            operation=operation,
        )

    assert result.settled.search_units == 2
    assert result.settled.billable_cost == Decimal('0.002')
    assert result.settled.cost_estimated is False

    async with session_maker() as session:
        ledger = (
            (
                await session.execute(
                    select(AiApiUsageLedger).where(
                        AiApiUsageLedger.request_id == 'use_00000000000040008000000000000001'
                    )
                )
            )
            .scalars()
            .first()
        )
    assert ledger is not None
    assert ledger.search_units == 2
    assert ledger.billable_cost == Decimal('0.002')
    await redis.aclose()


@pytest.mark.asyncio
async def test_missing_usage_settles_with_estimated_conservative_cost(
    ledger_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, session_maker = ledger_database
    redis = fakeredis.aioredis.FakeRedis()
    quota = PublicQuotaEngine(redis, clock=lambda: NOW, token_factory=lambda: 'lease-token')

    async with session_maker() as db:
        coordinator = PublicInvocationCoordinator(
            db,
            redis,
            session_factory=lambda: session_maker(),
            quota=quota,
            dao=StubSnapshotDao,
            clock=lambda: NOW,
            request_id_factory=lambda: 'use_00000000000040008000000000000001',
        )
        StubSnapshotDao.snapshot = _snapshot(model_type='embedding')
        result = await coordinator.run(
            _identity(),
            MODEL_CODE,
            'embedding',
            operation_type='embedding',
            input_bytes=100,
            operation=_plain_operation_result,
        )

    assert result.settled.cost_estimated is True
    assert result.settled.input_tokens == 100

    async with session_maker() as session:
        ledger = (
            (
                await session.execute(
                    select(AiApiUsageLedger).where(
                        AiApiUsageLedger.request_id == 'use_00000000000040008000000000000001'
                    )
                )
            )
            .scalars()
            .first()
        )
    assert ledger is not None
    assert ledger.cost_estimated is True
    assert ledger.billable_cost == Decimal('0.0001')
    await redis.aclose()


@pytest.mark.asyncio
async def test_reconciler_marks_stale_started_ledgers_abandoned(
    ledger_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, session_maker = ledger_database
    stale_now = NOW
    started_at = NOW - timedelta(seconds=3600)
    async with session_maker() as session:
        session.add(
            AiApiUsageLedger(
                request_id='use_00000000000040008000000000000001',
                application_id=APPLICATION_ID,
                api_key_id=KEY_ID,
                platform_model_id=MODEL_ID,
                model_code=MODEL_CODE,
                binding_id=BINDING_ID,
                engine_type='litellm',
                engine_model_snapshot='runtime-deepseek-chat',
                rate_card_id=RATE_CARD_ID,
                rate_card_version=1,
                operation_type='chat',
                reserved_tokens=100,
                reserved_cost=Decimal('0.0001'),
                quota_lease_token='stale-lease-token',
                quota_minute_window=str(int(started_at.timestamp()) // 60),
                quota_month_window=started_at.strftime('%Y-%m'),
                currency='USD',
                cost_estimated=False,
                status='started',
                started_at=started_at,
                created_at=started_at,
                updated_at=started_at,
            )
        )
        await session.commit()

    redis = fakeredis.aioredis.FakeRedis()
    quota = PublicQuotaEngine(redis, clock=lambda: stale_now)
    reconciler = PublicQuotaReconciler(
        session_factory=lambda: session_maker(),
        quota=quota,
        clock=lambda: stale_now,
        stale_after_seconds=900,
    )
    recovered = await reconciler.reconcile_once()

    assert recovered == 1
    async with session_maker() as session:
        ledger = (
            (
                await session.execute(
                    select(AiApiUsageLedger).where(
                        AiApiUsageLedger.request_id == 'use_00000000000040008000000000000001'
                    )
                )
            )
            .scalars()
            .first()
        )
    assert ledger is not None
    assert ledger.status == 'abandoned'
    assert ledger.cost_estimated is True
    assert ledger.error_code == 'RECONCILED_ABANDONED'
    assert ledger.input_tokens == 100
    assert ledger.billable_cost == Decimal('0.0001')
    assert await redis.exists(f'ai:quota:lease:{ledger.request_id}') == 0
    await redis.aclose()


@pytest.mark.asyncio
async def test_reconciler_skips_recent_started_ledgers(
    ledger_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, session_maker = ledger_database
    async with session_maker() as session:
        session.add(
            AiApiUsageLedger(
                request_id='use_00000000000040008000000000000002',
                application_id=APPLICATION_ID,
                api_key_id=KEY_ID,
                platform_model_id=MODEL_ID,
                model_code=MODEL_CODE,
                binding_id=BINDING_ID,
                engine_type='litellm',
                engine_model_snapshot='runtime-deepseek-chat',
                rate_card_id=RATE_CARD_ID,
                rate_card_version=1,
                operation_type='chat',
                currency='USD',
                cost_estimated=False,
                status='started',
                started_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        await session.commit()

    redis = fakeredis.aioredis.FakeRedis()
    reconciler = PublicQuotaReconciler(
        session_factory=lambda: session_maker(),
        quota=PublicQuotaEngine(redis, clock=lambda: NOW),
        clock=lambda: NOW,
        stale_after_seconds=900,
    )
    recovered = await reconciler.reconcile_once()

    assert recovered == 0
    async with session_maker() as session:
        ledger = (
            (
                await session.execute(
                    select(AiApiUsageLedger).where(
                        AiApiUsageLedger.request_id == 'use_00000000000040008000000000000002'
                    )
                )
            )
            .scalars()
            .first()
        )
    assert ledger is not None
    assert ledger.status == 'started'
    await redis.aclose()


@pytest.mark.asyncio
async def test_cancellation_during_run_finalizes_cancelled_ledger(
    ledger_database: tuple[AsyncEngine, async_sessionmaker[AsyncSession]],
) -> None:
    _, session_maker = ledger_database
    redis = fakeredis.aioredis.FakeRedis()
    quota = PublicQuotaEngine(redis, clock=lambda: NOW, token_factory=lambda: 'lease-token')

    async with session_maker() as db:
        StubSnapshotDao.snapshot = _snapshot()
        coordinator = PublicInvocationCoordinator(
            db,
            redis,
            session_factory=lambda: session_maker(),
            quota=quota,
            dao=StubSnapshotDao,
            clock=lambda: NOW,
            request_id_factory=lambda: 'use_00000000000040008000000000000001',
        )
        with pytest.raises(asyncio.CancelledError):
            await coordinator.run(
                _identity(),
                MODEL_CODE,
                'chat',
                operation_type='chat',
                input_bytes=10,
                operation=lambda snapshot: (_ for _ in ()).throw(asyncio.CancelledError()),
            )

    async with session_maker() as session:
        ledger = (
            (
                await session.execute(
                    select(AiApiUsageLedger).where(
                        AiApiUsageLedger.request_id == 'use_00000000000040008000000000000001'
                    )
                )
            )
            .scalars()
            .first()
        )
    assert ledger is not None
    assert ledger.status == 'cancelled'
    assert ledger.cost_estimated is True
    await redis.aclose()


@pytest.mark.asyncio
async def test_scheduled_reconciler_builds_and_closes_owned_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    from plugins.ai import jobs

    class FakeRedis:
        close_count = 0

        async def aclose(self) -> None:
            self.close_count += 1

    redis = FakeRedis()
    session_factory = object()
    captured: dict[str, object] = {}

    class FakeRedisUtil:
        @staticmethod
        async def create_redis_pool(*, log_enabled: bool) -> FakeRedis:
            assert log_enabled is False
            return redis

    class FakeReconciler:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def reconcile_once(self) -> int:
            return 3

    monkeypatch.setattr(jobs, 'AsyncSessionLocal', session_factory)
    monkeypatch.setattr(jobs, 'RedisUtil', FakeRedisUtil)
    monkeypatch.setattr(jobs, 'PublicQuotaReconciler', FakeReconciler)

    assert await jobs.reconcile_public_quota() == 3
    assert captured['session_factory'] is session_factory
    assert isinstance(captured['quota'], PublicQuotaEngine)
    assert redis.close_count == 1
