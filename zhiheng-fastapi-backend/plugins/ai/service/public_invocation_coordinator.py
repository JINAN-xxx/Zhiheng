import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from secrets import token_urlsafe
from typing import Any, Literal, Protocol

import anyio
from sqlalchemy.ext.asyncio import AsyncSession

from plugins.ai.dao.ai_developer_api_dao import AiDeveloperApiDao, InvocationSnapshotRecord
from plugins.ai.domain.model_service_errors import ModelServiceError
from plugins.ai.domain.public_api_errors import PublicApiError
from plugins.ai.domain.resource_id import new_resource_id
from plugins.ai.entity.do.ai_developer_api_do import AiApiUsageLedger
from plugins.ai.infrastructure.developer_key_authenticator import DeveloperApiIdentity

OperationType = Literal['chat', 'embedding', 'rerank']
LedgerStatus = Literal['completed', 'failed', 'cancelled', 'abandoned']

LEASE_TTL_SECONDS = 900
WINDOW_TTL_SECONDS = 120
RECONCILE_AFTER_SECONDS = 900
RECONCILE_BATCH_SIZE = 200
DECEMBER = 12

_LEASE_KEY_PREFIX = 'ai:quota:lease:'
_RPM_KEY_PREFIX = 'ai:quota:rpm:'
_TPM_KEY_PREFIX = 'ai:quota:tpm:'
_CONCURRENCY_KEY_PREFIX = 'ai:quota:concurrency:'
_BUDGET_KEY_PREFIX = 'ai:quota:budget:'

_RESERVE_SCRIPT = """
-- KEYS[1] rpm窗口  KEYS[2] tpm窗口  KEYS[3] 并发  KEYS[4] 预算  KEYS[5] 租约
-- ARGV[1] rpm上限  ARGV[2] tpm上限  ARGV[3] 并发上限  ARGV[4] 预算上限
-- ARGV[5] 预留Token  ARGV[6] 预留费用  ARGV[7] 租约值  ARGV[8] 租约TTL
-- ARGV[9] 分钟窗口TTL  ARGV[10] 月度预算TTL
local function over_limit(limit, current)
  if tonumber(limit) > 0 and current > tonumber(limit) then
    return true
  end
  return false
end
local rpm = redis.call('INCR', KEYS[1])
if rpm == 1 then redis.call('EXPIRE', KEYS[1], ARGV[9]) end
if over_limit(ARGV[1], rpm) then
  redis.call('DECR', KEYS[1])
  return {'rate_limit_exceeded'}
end
local tpm = redis.call('INCRBY', KEYS[2], ARGV[5])
if tpm == tonumber(ARGV[5]) then redis.call('EXPIRE', KEYS[2], ARGV[9]) end
if over_limit(ARGV[2], tpm) then
  redis.call('DECRBY', KEYS[2], ARGV[5])
  redis.call('DECR', KEYS[1])
  return {'rate_limit_exceeded'}
end
local concurrency = redis.call('INCR', KEYS[3])
if concurrency == 1 then redis.call('EXPIRE', KEYS[3], ARGV[9]) end
if over_limit(ARGV[3], concurrency) then
  redis.call('DECR', KEYS[3])
  redis.call('DECRBY', KEYS[2], ARGV[5])
  redis.call('DECR', KEYS[1])
  return {'concurrency_limit_exceeded'}
end
if tonumber(ARGV[4]) > 0 then
  local budget = tonumber(redis.call('INCRBYFLOAT', KEYS[4], ARGV[6]))
  if budget > tonumber(ARGV[4]) then
    redis.call('INCRBYFLOAT', KEYS[4], -tonumber(ARGV[6]))
    redis.call('DECR', KEYS[3])
    redis.call('DECRBY', KEYS[2], ARGV[5])
    redis.call('DECR', KEYS[1])
    return {'budget_exceeded'}
  end
end
if tonumber(ARGV[4]) > 0 then redis.call('EXPIRE', KEYS[4], ARGV[10]) end
redis.call('SET', KEYS[5], ARGV[7], 'EX', ARGV[8])
return {'ok'}
""".strip()

_SETTLE_SCRIPT = """
-- KEYS[1] 租约  KEYS[2] tpm窗口  KEYS[3] 预算  KEYS[4] 并发
-- ARGV[1] 租约值  ARGV[2] 预留Token  ARGV[3] 实际Token  ARGV[4] 预留费用  ARGV[5] 实际费用
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
  return 0
end
redis.call('DEL', KEYS[1])
redis.call('INCRBY', KEYS[2], tonumber(ARGV[3]) - tonumber(ARGV[2]))
redis.call('INCRBYFLOAT', KEYS[3], -tonumber(ARGV[4]))
redis.call('INCRBYFLOAT', KEYS[3], tonumber(ARGV[5]))
redis.call('DECR', KEYS[4])
return 1
""".strip()

_RELEASE_SCRIPT = """
-- KEYS[1] 租约  KEYS[2] tpm窗口  KEYS[3] 预算  KEYS[4] 并发
-- ARGV[1] 租约值  ARGV[2] 预留Token  ARGV[3] 预留费用
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
  return 0
end
redis.call('DEL', KEYS[1])
redis.call('DECRBY', KEYS[2], ARGV[2])
redis.call('INCRBYFLOAT', KEYS[3], -tonumber(ARGV[3]))
redis.call('DECR', KEYS[4])
return 1
""".strip()

_QUOTA_FAILURE_KINDS = frozenset({'rate_limit_exceeded', 'concurrency_limit_exceeded', 'budget_exceeded'})


@dataclass(frozen=True, slots=True)
class Reservation:
    request_id: str
    lease_token: str
    reserved_tokens: int
    reserved_cost: Decimal
    minute_window: str
    month_window: str


@dataclass(frozen=True, slots=True)
class RuntimeUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_tokens: int | None = None
    reasoning_tokens: int | None = None
    search_units: int | None = None
    raw_cost: Decimal | None = None
    upstream_request_id: str | None = None


@dataclass(frozen=True, slots=True)
class SettledResult:
    request_id: str
    status: LedgerStatus
    input_tokens: int | None
    output_tokens: int | None
    cache_tokens: int | None
    reasoning_tokens: int | None
    search_units: int | None
    billable_cost: Decimal
    cost_estimated: bool
    error_code: str | None

    @property
    def total_tokens(self) -> int:
        return (self.input_tokens or 0) + (self.output_tokens or 0) + (self.cache_tokens or 0)


@dataclass(frozen=True, slots=True)
class InvocationResult:
    snapshot: InvocationSnapshotRecord
    settled: SettledResult
    payload: object | None = None


@dataclass(frozen=True, slots=True)
class OperationResult:
    """普通调用操作结果：规范化 Usage 与类型化业务值。"""

    usage: RuntimeUsage | None = None
    value: object | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _database_time(value: datetime) -> datetime:
    """PG timestamp 列为 naive，绑定参数需归一化为 naive UTC。"""
    if value.tzinfo is None or value.utcoffset() is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _minute_window(now: datetime) -> str:
    return str(int(now.timestamp()) // 60)


def _month_window(now: datetime) -> str:
    return now.strftime('%Y-%m')


def _budget_ttl_seconds(now: datetime, lease_ttl_seconds: int) -> int:
    if now.month == DECEMBER:
        next_month = now.replace(year=now.year + 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        next_month = now.replace(month=now.month + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((next_month - now).total_seconds()) + lease_ttl_seconds + WINDOW_TTL_SECONDS)


def _limit_arg(value: int | None) -> str:
    return '0' if value is None else str(value)


def _decimal_arg(value: Decimal | None) -> str:
    return '0' if value is None else format(value, 'f')


def _quota_failure_error(kind: str) -> PublicApiError:
    if kind == 'concurrency_limit_exceeded':
        return PublicApiError.concurrency_limit_exceeded()
    if kind == 'budget_exceeded':
        return PublicApiError.budget_exceeded()
    return PublicApiError.rate_limit_exceeded()


def _decode_kind(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode('utf-8')
    if isinstance(value, str):
        return value
    return ''


class PublicQuotaEngine:
    """Redis 分布式配额：原子预留、结算与释放，Redis 不可用时 fail closed。"""

    def __init__(
        self,
        redis: Any,
        *,
        lease_ttl_seconds: int = LEASE_TTL_SECONDS,
        window_ttl_seconds: int = WINDOW_TTL_SECONDS,
        clock: Callable[[], datetime] = _utc_now,
        token_factory: Callable[[], str] = token_urlsafe,
    ) -> None:
        self._redis = redis
        self._lease_ttl_seconds = lease_ttl_seconds
        self._window_ttl_seconds = window_ttl_seconds
        self._clock = clock
        self._token_factory = token_factory

    def _keys(self, snapshot: InvocationSnapshotRecord, request_id: str, now: datetime) -> list[str]:
        key_id = snapshot.api_key_id
        return [
            f'{_RPM_KEY_PREFIX}{key_id}:{_minute_window(now)}',
            f'{_TPM_KEY_PREFIX}{key_id}:{_minute_window(now)}',
            f'{_CONCURRENCY_KEY_PREFIX}{key_id}',
            f'{_BUDGET_KEY_PREFIX}{key_id}:{_month_window(now)}',
            f'{_LEASE_KEY_PREFIX}{request_id}',
        ]

    @staticmethod
    def _key_id(snapshot_or_key_id: InvocationSnapshotRecord | str) -> str:
        return snapshot_or_key_id if isinstance(snapshot_or_key_id, str) else snapshot_or_key_id.api_key_id

    def _settlement_keys(
        self,
        snapshot_or_key_id: InvocationSnapshotRecord | str,
        reservation: Reservation,
    ) -> list[str]:
        key_id = self._key_id(snapshot_or_key_id)
        return [
            f'{_LEASE_KEY_PREFIX}{reservation.request_id}',
            f'{_TPM_KEY_PREFIX}{key_id}:{reservation.minute_window}',
            f'{_BUDGET_KEY_PREFIX}{key_id}:{reservation.month_window}',
            f'{_CONCURRENCY_KEY_PREFIX}{key_id}',
        ]

    async def reserve(
        self,
        snapshot: InvocationSnapshotRecord,
        *,
        request_id: str,
        reserved_tokens: int,
        reserved_cost: Decimal,
    ) -> Reservation:
        now = self._clock()
        lease_token = self._token_factory()
        if not isinstance(lease_token, str) or not lease_token:
            raise TypeError('租约 token 必须是非空字符串')
        args = [
            _limit_arg(snapshot.rpm_limit),
            _limit_arg(snapshot.tpm_limit),
            _limit_arg(snapshot.concurrency_limit),
            _decimal_arg(snapshot.monthly_budget),
            str(reserved_tokens),
            _decimal_arg(reserved_cost),
            lease_token,
            str(self._lease_ttl_seconds),
            str(self._lease_ttl_seconds + self._window_ttl_seconds),
            str(_budget_ttl_seconds(now, self._lease_ttl_seconds)),
        ]
        try:
            outcome = await self._redis.eval(_RESERVE_SCRIPT, 5, *self._keys(snapshot, request_id, now), *args)
        except Exception:
            raise PublicApiError.service_unavailable() from None
        kind = _decode_kind(outcome[0]) if isinstance(outcome, list) and outcome else ''
        if kind != 'ok':
            if kind not in _QUOTA_FAILURE_KINDS:
                raise PublicApiError.service_unavailable()
            raise _quota_failure_error(kind)
        return Reservation(
            request_id=request_id,
            lease_token=lease_token,
            reserved_tokens=reserved_tokens,
            reserved_cost=reserved_cost,
            minute_window=_minute_window(now),
            month_window=_month_window(now),
        )

    async def settle(
        self,
        snapshot: InvocationSnapshotRecord,
        reservation: Reservation,
        *,
        actual_tokens: int,
        actual_cost: Decimal,
        now: datetime | None = None,
    ) -> bool:
        del now
        keys = self._settlement_keys(snapshot, reservation)
        args = [
            reservation.lease_token,
            str(reservation.reserved_tokens),
            str(actual_tokens),
            _decimal_arg(reservation.reserved_cost),
            _decimal_arg(actual_cost),
        ]
        try:
            outcome = await self._redis.eval(_SETTLE_SCRIPT, 4, *keys, *args)
        except Exception:
            raise PublicApiError.service_unavailable() from None
        return outcome == 1

    async def release(
        self,
        snapshot: InvocationSnapshotRecord,
        reservation: Reservation,
        *,
        now: datetime | None = None,
    ) -> bool:
        del now
        keys = self._settlement_keys(snapshot, reservation)
        args = [
            reservation.lease_token,
            str(reservation.reserved_tokens),
            _decimal_arg(reservation.reserved_cost),
        ]
        try:
            outcome = await self._redis.eval(_RELEASE_SCRIPT, 4, *keys, *args)
        except Exception:
            return False
        return outcome == 1


class SnapshotDao(Protocol):
    @classmethod
    async def get_invocation_snapshot(
        cls,
        db: AsyncSession,
        api_key_id: str,
        model_code: str,
        expected_type: str,
        now: datetime,
    ) -> InvocationSnapshotRecord | None: ...


class PublicInvocationCoordinator:
    """统一调用协调器：授权快照、Redis 配额预留、Usage Ledger 与调用生命周期。

    Chat、Embedding、Rerank 三类调用都必须经过本协调器，不允许 Controller 直接代理 Runtime。
    """

    def __init__(
        self,
        db: AsyncSession,
        redis: Any,
        *,
        session_factory: Callable[[], AsyncSession],
        quota: PublicQuotaEngine,
        dao: type[SnapshotDao] = AiDeveloperApiDao,
        clock: Callable[[], datetime] = _utc_now,
        request_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._db = db
        self._redis = redis
        self._session_factory = session_factory
        self._quota = quota
        self._dao = dao
        self._clock = clock
        self._request_id_factory = request_id_factory or (lambda: new_resource_id('use'))

    async def resolve_invocation(
        self,
        identity: DeveloperApiIdentity,
        model_code: str,
        expected_type: str,
    ) -> InvocationSnapshotRecord:
        try:
            record = await self._dao.get_invocation_snapshot(
                self._db,
                identity.key_id,
                model_code,
                expected_type,
                self._clock(),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._rollback_preserving_error()
            raise PublicApiError.service_unavailable() from None
        await self._rollback_visible()
        if record is None:
            raise PublicApiError.model_not_found()
        return record

    async def run(
        self,
        identity: DeveloperApiIdentity,
        model_code: str,
        expected_type: str,
        *,
        operation_type: OperationType,
        input_bytes: int,
        output_ceiling: int | None = None,
        operation: Callable[[InvocationSnapshotRecord], Awaitable[OperationResult]],
    ) -> InvocationResult:
        """普通调用：预留配额、写入 started Ledger、执行、结算。"""
        snapshot = await self.resolve_invocation(identity, model_code, expected_type)
        reservation = await self._reserve(snapshot, operation_type, input_bytes, output_ceiling)
        if not await self._write_started_ledger(snapshot, reservation, operation_type):
            await self._quota.release(snapshot, reservation)
            raise PublicApiError.service_unavailable()
        try:
            operation_result = await operation(snapshot)
        except asyncio.CancelledError:
            await self._finalize(snapshot, reservation, operation_type, status='cancelled', usage=None)
            raise
        except ModelServiceError as error:
            await self._finalize(
                snapshot,
                reservation,
                operation_type,
                status='failed',
                usage=None,
                error_code=error.code,
            )
            raise
        except Exception:
            await self._finalize(
                snapshot,
                reservation,
                operation_type,
                status='failed',
                usage=None,
                error_code='INTERNAL',
            )
            raise
        usage = operation_result.usage if isinstance(operation_result, OperationResult) else None
        settled = await self._finalize(
            snapshot,
            reservation,
            operation_type,
            status='completed',
            usage=usage,
        )
        return InvocationResult(
            snapshot=snapshot,
            settled=settled,
            payload=operation_result.value if isinstance(operation_result, OperationResult) else operation_result,
        )

    async def run_stream(
        self,
        identity: DeveloperApiIdentity,
        model_code: str,
        expected_type: str,
        *,
        operation_type: OperationType,
        input_bytes: int,
        output_ceiling: int | None = None,
        operation: Callable[[InvocationSnapshotRecord], Awaitable[AsyncIterator[object]]],
    ) -> 'CoordinatorStream':
        """流式调用：预留配额与 started Ledger 后返回受协调的流，结束时结算。"""
        snapshot = await self.resolve_invocation(identity, model_code, expected_type)
        reservation = await self._reserve(snapshot, operation_type, input_bytes, output_ceiling)
        if not await self._write_started_ledger(snapshot, reservation, operation_type):
            await self._quota.release(snapshot, reservation)
            raise PublicApiError.service_unavailable()
        stream: AsyncIterator[object] | None = None
        try:
            stream = await operation(snapshot)
            return CoordinatorStream(self, snapshot, reservation, operation_type, stream)
        except (asyncio.CancelledError, Exception) as error:
            # Setup owns the reservation until it hands off a usable stream.
            with anyio.CancelScope(shield=True):
                try:
                    close = getattr(stream, 'aclose', None)
                    if close is not None:
                        await close()
                except BaseException:
                    pass
                try:
                    await self._finalize(
                        snapshot,
                        reservation,
                        operation_type,
                        status='cancelled' if isinstance(error, asyncio.CancelledError) else 'failed',
                        usage=None,
                        error_code=error.code if isinstance(error, ModelServiceError) else None,
                    )
                except BaseException:
                    # Keep the setup error; reconciliation handles storage failures.
                    pass
            raise

    async def _reserve(
        self,
        snapshot: InvocationSnapshotRecord,
        operation_type: OperationType,
        input_bytes: int,
        output_ceiling: int | None,
    ) -> Reservation:
        request_id = self._request_id_factory()
        if operation_type == 'chat':
            ceiling = output_ceiling if output_ceiling is not None else (snapshot.max_output_tokens or 0)
        else:
            ceiling = 0
        reserved_tokens = input_bytes + ceiling
        reserved_cost = self._reserved_cost(snapshot, operation_type, reserved_tokens)
        return await self._quota.reserve(
            snapshot,
            request_id=request_id,
            reserved_tokens=reserved_tokens,
            reserved_cost=reserved_cost,
        )

    async def _write_started_ledger(
        self,
        snapshot: InvocationSnapshotRecord,
        reservation: Reservation,
        operation_type: OperationType,
    ) -> bool:
        now = _database_time(self._clock())
        try:
            async with self._session_factory() as session:
                ledger = AiApiUsageLedger(
                    request_id=reservation.request_id,
                    application_id=snapshot.application_id,
                    api_key_id=snapshot.api_key_id,
                    platform_model_id=snapshot.platform_model_id,
                    model_code=snapshot.model_code,
                    binding_id=snapshot.binding_id,
                    engine_type=snapshot.engine_type,
                    engine_model_snapshot=snapshot.engine_model_snapshot,
                    rate_card_id=snapshot.rate_card_id,
                    rate_card_version=snapshot.rate_card_version,
                    operation_type=operation_type,
                    reserved_tokens=reservation.reserved_tokens,
                    reserved_cost=reservation.reserved_cost,
                    quota_lease_token=reservation.lease_token,
                    quota_minute_window=reservation.minute_window,
                    quota_month_window=reservation.month_window,
                    input_tokens=None,
                    output_tokens=None,
                    cache_tokens=None,
                    reasoning_tokens=None,
                    search_units=None,
                    raw_cost=None,
                    billable_cost=None,
                    currency=snapshot.currency,
                    cost_estimated=False,
                    status='started',
                    error_code=None,
                    upstream_request_id=None,
                    idempotency_key=None,
                    request_hash=None,
                    started_at=now,
                    finished_at=None,
                    created_at=now,
                    updated_at=now,
                )
                await AiDeveloperApiDao.create_usage_ledger(session, ledger)
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            return False
        return True

    async def _finalize(
        self,
        snapshot: InvocationSnapshotRecord,
        reservation: Reservation,
        operation_type: OperationType,
        *,
        status: LedgerStatus,
        usage: RuntimeUsage | None,
        error_code: str | None = None,
    ) -> SettledResult:
        settled = self._settle_amounts(snapshot, operation_type, reservation, usage, status)
        resolved_error_code = error_code if error_code is not None else (status if status != 'completed' else None)
        now = _database_time(self._clock())
        values: dict[str, object] = {
            'status': settled.status,
            'input_tokens': settled.input_tokens,
            'output_tokens': settled.output_tokens,
            'cache_tokens': settled.cache_tokens,
            'reasoning_tokens': settled.reasoning_tokens,
            'search_units': settled.search_units,
            'raw_cost': usage.raw_cost if usage is not None else None,
            'billable_cost': settled.billable_cost,
            'cost_estimated': settled.cost_estimated,
            'error_code': resolved_error_code,
            'upstream_request_id': usage.upstream_request_id if usage is not None else None,
            'finished_at': now,
            'updated_at': now,
        }
        try:
            await self._quota.settle(
                snapshot,
                reservation,
                actual_tokens=settled.total_tokens,
                actual_cost=settled.billable_cost,
            )
        except PublicApiError:
            # Redis 结算失败：Ledger 保持 started，Reconciler 后续恢复。
            return settled
        await self._update_ledger(reservation.request_id, values)
        return settled

    async def _update_ledger(self, request_id: str, values: Mapping[str, object]) -> bool:
        try:
            async with self._session_factory() as session:
                await AiDeveloperApiDao.finalize_usage_ledger(session, request_id, dict(values))
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            return False
        return True

    def _reserved_cost(
        self,
        snapshot: InvocationSnapshotRecord,
        operation_type: OperationType,
        reserved_tokens: int,
    ) -> Decimal:
        if snapshot.billing_mode == 'search_unit':
            unit_price = snapshot.search_price_per_unit
            return unit_price if unit_price is not None else Decimal('0')
        input_price = snapshot.input_price_per_token
        if operation_type == 'chat':
            # 保守上限：全部预留量按最高单价计费。
            output_price = snapshot.output_price_per_token
            cache_price = snapshot.cache_price_per_token
            highest = max(input_price, output_price, cache_price if cache_price is not None else Decimal('0'))
            return highest * reserved_tokens
        return input_price * reserved_tokens

    def _settle_amounts(
        self,
        snapshot: InvocationSnapshotRecord,
        operation_type: OperationType,
        reservation: Reservation,
        usage: RuntimeUsage | None,
        status: LedgerStatus,
    ) -> SettledResult:
        if snapshot.billing_mode == 'search_unit':
            search_units = usage.search_units if usage is not None and usage.search_units is not None else 1
            unit_price = snapshot.search_price_per_unit or Decimal('0')
            return SettledResult(
                request_id=reservation.request_id,
                status=status,
                input_tokens=usage.input_tokens if usage is not None else None,
                output_tokens=0,
                cache_tokens=None,
                reasoning_tokens=None,
                search_units=search_units,
                billable_cost=unit_price * search_units,
                cost_estimated=usage is None or usage.search_units is None,
                error_code=None,
            )
        if usage is None or usage.input_tokens is None:
            # Usage 缺失或调用失败：按预留量保守结算并标记估算，避免免费放行。
            return SettledResult(
                request_id=reservation.request_id,
                status=status,
                input_tokens=reservation.reserved_tokens,
                output_tokens=0,
                cache_tokens=None,
                reasoning_tokens=None,
                search_units=None,
                billable_cost=self._reserved_cost(snapshot, operation_type, reservation.reserved_tokens),
                cost_estimated=True,
                error_code=None,
            )
        cache_tokens = usage.cache_tokens
        cache_price = (
            cache_tokens * snapshot.cache_price_per_token
            if cache_tokens is not None and snapshot.cache_price_per_token is not None
            else Decimal('0')
        )
        billable = (
            usage.input_tokens * snapshot.input_price_per_token
            + (usage.output_tokens or 0) * snapshot.output_price_per_token
            + cache_price
        )
        return SettledResult(
            request_id=reservation.request_id,
            status=status,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_tokens=cache_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            search_units=None,
            billable_cost=billable,
            cost_estimated=False,
            error_code=None,
        )

    async def _rollback_visible(self) -> None:
        if self._db.in_transaction():
            await self._db.rollback()

    async def _rollback_preserving_error(self) -> None:
        try:
            await self._rollback_visible()
        except BaseException:
            pass


class CoordinatorStream:
    """包装运行时事件流，在完成、失败或取消时统一结算 Ledger 与配额。"""

    def __init__(
        self,
        coordinator: PublicInvocationCoordinator,
        snapshot: InvocationSnapshotRecord,
        reservation: Reservation,
        operation_type: OperationType,
        stream: AsyncIterator[object],
    ) -> None:
        self._coordinator = coordinator
        self._snapshot = snapshot
        self._reservation = reservation
        self._operation_type = operation_type
        self._iterator = stream.__aiter__()
        self._lock = asyncio.Lock()
        self._finalized = False
        self._terminal_outcome: tuple[LedgerStatus, str | None] | None = None
        self._saw_done = False
        self._usage: RuntimeUsage | None = None

    def __aiter__(self) -> 'CoordinatorStream':
        return self

    async def __anext__(self) -> object:
        async with self._lock:
            if self._finalized:
                raise StopAsyncIteration
            try:
                event = await anext(self._iterator)
            except StopAsyncIteration:
                await self._finalize_locked(status='completed', error_code=None, active_error=None)
                raise
            except asyncio.CancelledError as error:
                await self._finalize_locked(status='cancelled', error_code=None, active_error=error)
                raise
            except ModelServiceError as error:
                await self._finalize_locked(status='failed', error_code=error.code, active_error=error)
                raise
            except Exception as error:
                await self._finalize_locked(status='failed', error_code='INTERNAL', active_error=error)
                raise
            self._track_usage(event)
            return event

    async def aclose(self) -> None:
        # A disconnect can already be cancelling the caller while it waits for
        # the reader's lock, so protect lock acquisition as well as cleanup.
        with anyio.CancelScope(shield=True):
            async with self._lock:
                if self._finalized:
                    return
                status: LedgerStatus = 'completed' if self._saw_done else 'cancelled'
                await self._finalize_locked(status=status, error_code=None, active_error=None)

    def _track_usage(self, event: object) -> None:
        if getattr(event, 'event_type', None) == 'done':
            self._saw_done = True
        usage = getattr(event, 'usage', None)
        if usage is None:
            return
        self._usage = RuntimeUsage(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_tokens=usage.cache_read_tokens + usage.cache_write_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            raw_cost=usage.raw_cost,
            upstream_request_id=getattr(event, 'request_id', None),
        )

    async def _finalize_locked(
        self,
        *,
        status: LedgerStatus,
        error_code: str | None,
        active_error: BaseException | None,
    ) -> None:
        if self._finalized:
            return
        with anyio.CancelScope(shield=True):
            close_cancellation: asyncio.CancelledError | None = None
            try:
                close = getattr(self._iterator, 'aclose', None)
                if close is not None:
                    await close()
            except asyncio.CancelledError as error:
                close_cancellation = error
                if active_error is None:
                    status = 'cancelled'
            except BaseException:
                # 关闭错误不得覆盖活动异常；无活动异常时按关闭失败结算。
                if active_error is None:
                    status = 'failed'
                    error_code = error_code or 'INTERNAL'
            # A settlement retry must keep the original effective outcome,
            # including close failures, without retaining exception tracebacks.
            if self._terminal_outcome is None:
                self._terminal_outcome = (status, error_code)
            status, error_code = self._terminal_outcome
            try:
                await self._coordinator._finalize(
                    self._snapshot,
                    self._reservation,
                    self._operation_type,
                    status=status,
                    usage=self._usage,
                    error_code=error_code,
                )
            except BaseException:
                if active_error is None:
                    raise
            else:
                self._finalized = True
            if close_cancellation is not None and active_error is None:
                raise close_cancellation


class PublicQuotaReconciler:
    """恢复超时 started Ledger：置为 abandoned 并保守结算，租约过期由 Redis TTL 兜底。"""

    def __init__(
        self,
        *,
        session_factory: Callable[[], AsyncSession],
        quota: PublicQuotaEngine,
        dao: type[Any] = AiDeveloperApiDao,
        clock: Callable[[], datetime] = _utc_now,
        stale_after_seconds: int = RECONCILE_AFTER_SECONDS,
        batch_size: int = RECONCILE_BATCH_SIZE,
    ) -> None:
        self._session_factory = session_factory
        self._quota = quota
        self._dao = dao
        self._clock = clock
        self._stale_after_seconds = stale_after_seconds
        self._batch_size = batch_size

    async def reconcile_once(self) -> int:
        """将超时 started Ledger 置为 abandoned 并保守结算，返回处理数量。"""
        now = self._clock()
        before = now - timedelta(seconds=self._stale_after_seconds)
        recovered = 0
        try:
            async with self._session_factory() as session:
                ledgers = await self._dao.list_started_ledgers_before(session, before, self._batch_size)
                for ledger in ledgers:
                    if not self._has_reservation_snapshot(ledger):
                        continue
                    reservation = Reservation(
                        request_id=ledger.request_id,
                        lease_token=ledger.quota_lease_token,
                        reserved_tokens=ledger.reserved_tokens,
                        reserved_cost=ledger.reserved_cost,
                        minute_window=ledger.quota_minute_window,
                        month_window=ledger.quota_month_window,
                    )
                    await self._quota.settle(
                        ledger.api_key_id,
                        reservation,
                        actual_tokens=reservation.reserved_tokens,
                        actual_cost=reservation.reserved_cost,
                    )
                    values: dict[str, object] = {
                        'status': 'abandoned',
                        'input_tokens': reservation.reserved_tokens,
                        'output_tokens': 0,
                        'billable_cost': reservation.reserved_cost,
                        'cost_estimated': True,
                        'error_code': 'RECONCILED_ABANDONED',
                        'finished_at': now,
                        'updated_at': now,
                    }
                    await self._dao.finalize_usage_ledger(session, ledger.request_id, values)
                    recovered += 1
                await session.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            return recovered
        return recovered

    @staticmethod
    def _has_reservation_snapshot(ledger: AiApiUsageLedger) -> bool:
        return (
            ledger.reserved_tokens is not None
            and ledger.reserved_cost is not None
            and bool(ledger.quota_lease_token)
            and bool(ledger.quota_minute_window)
            and bool(ledger.quota_month_window)
        )
