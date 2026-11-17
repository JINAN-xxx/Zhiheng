"""Transactional platform credit accounts, immutable entries and reservations.

Callers own the surrounding transaction. Wallet rows are locked before reservation
or key-month rows, including settlement, so concurrent reclaim and spend serialize.
"""

from collections.abc import Awaitable, Callable
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import JSON, Select, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.billing.commands import CreditCommandJournal
from zhiheng.modules.billing.contracts import (
    CreditAccountStatePatch,
    CreditAdjustment,
    CreditPrices,
    CreditReconcile,
    CreditReservation,
    CreditState,
    CreditStatePatch,
    CreditSummary,
)
from zhiheng.modules.billing.gate import lock_billing_switch, lock_credit
from zhiheng.modules.billing.orm import (
    CreditAccount,
    CreditControl,
    CreditHold,
    CreditKeyMonth,
    CreditLedger,
    Invocation,
    UsageAccount,
)
from zhiheng.modules.billing.pricing import exact_points, points_for_usage
from zhiheng.modules.billing.projections import (
    USAGE_COUNTERS,
    ZERO,
    account_summary,
    available_points,
    reservation_info,
    sum_points,
)
from zhiheng.modules.billing.queries import CreditQueries
from zhiheng.modules.billing.rates import PRICES, CreditRates
from zhiheng.runtime.orm import Run
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal
from zhiheng.shared.time import utcnow

MAX_SOURCE_LENGTH = 24
MAX_OPERATION_LENGTH = 16
MAX_USAGE_COUNT = 2_147_483_647


class CreditService:
    def __init__(self) -> None:
        self.queries = CreditQueries()
        self.commands = CreditCommandJournal()
        self.rates = CreditRates(self.commands)

    @staticmethod
    def _failed_chat_waivers() -> Select[tuple[UUID]]:
        """Only empty, failed private chats without any confirmed usage qualify."""
        return (
            select(CreditHold.run_id)
            .join(Run, Run.id == CreditHold.run_id)
            .join(Invocation, Invocation.run_id == CreditHold.run_id)
            .where(
                CreditHold.status == 'unknown',
                CreditHold.source == 'chat',
                CreditHold.key_id.is_(None),
                Run.resource_type == 'chat',
                Run.status == 'failed',
                Run.owner_id == CreditHold.user_id,
                Run.billing_owner_id == CreditHold.user_id,
                Run.text == '',
                Run.reasoning == '',
                or_(Run.result.is_(None), Run.result == JSON.NULL),
                Run.input_tokens.is_(None),
                Run.output_tokens.is_(None),
                Run.usage_status == 'unknown',
                Invocation.user_id == CreditHold.user_id,
                Invocation.model_id == CreditHold.model_id,
                Invocation.source == 'chat',
                Invocation.key_id.is_(None),
                Invocation.points_enforced.is_(True),
                Invocation.status == 'unknown',
                *(
                    getattr(model, counter).is_(None)
                    for model in (CreditHold, Invocation)
                    for counter in USAGE_COUNTERS
                ),
            )
        )

    async def failed_chat_waivers(self, session: AsyncSession, *, limit: int = 100) -> list[UUID]:
        return list(
            await session.scalars(
                self._failed_chat_waivers().order_by(CreditHold.created_at, CreditHold.run_id).limit(limit)
            )
        )

    async def waive_failed_chat(self, session: AsyncSession, run_id: UUID) -> bool:
        # Match the executor's Run -> invocation -> account/hold lock order.
        # Recheck under all locks: a concurrent reconciliation may have completed.
        if await session.scalar(select(Run.id).where(Run.id == run_id).with_for_update()) is None:
            return False
        invocation = await session.scalar(
            select(Invocation)
            .where(Invocation.run_id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if invocation is None:
            return False
        if await session.scalar(self._failed_chat_waivers().where(CreditHold.run_id == run_id)) is None:
            return False
        tokens = await session.scalar(
            select(UsageAccount)
            .where(UsageAccount.user_id == invocation.user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if tokens is None or tokens.reserved_tokens < invocation.reserved_tokens:
            raise RuntimeError('Failed-chat Token reservation invariant failed')
        account, row = await self._locked_hold(session, run_id)
        if await session.scalar(self._failed_chat_waivers().where(CreditHold.run_id == run_id)) is None:
            return False
        # This is a commercial waiver, not evidence that the provider used zero
        # Tokens. Keep Run/invocation counters unknown and retain their estimates.
        tokens.reserved_tokens -= invocation.reserved_tokens
        account.reserved_points = sum_points(account.reserved_points, row.reserved_points.copy_negate())
        account.version += 1
        row.status, row.charged_points, row.version = 'settled', ZERO, row.version + 1
        self._entry(
            session,
            account,
            kind='failure_waive',
            amount=ZERO,
            available_delta=row.reserved_points,
            reserved_delta=row.reserved_points.copy_negate(),
            run_id=run_id,
            reason='聊天请求失败且无正文、推理或已确认用量，本次积分免计并释放预占；供应商用量仍未知。',
        )
        await session.flush()
        return True

    async def set_state(
        self,
        session: AsyncSession,
        actor: Principal,
        data: CreditStatePatch,
        *,
        before_change: Callable[[AsyncSession, bool], Awaitable[None]] | None = None,
    ) -> CreditState:
        actor.require_admin()
        await lock_billing_switch(session)
        previous = await self.commands.replay(session, actor, 'state', 'platform', data, CreditState)
        if previous is not None:
            return previous
        await session.execute(insert(CreditControl).values(id=1, enabled=False, version=0).on_conflict_do_nothing())
        row = await session.scalar(
            select(CreditControl)
            .where(CreditControl.id == 1)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise RuntimeError('Credit control invariant failed')
        self._version(row.version, data.expected_version)
        if before_change is not None and row.enabled != data.enabled:
            await before_change(session, data.enabled)
        row.enabled = data.enabled
        row.version += 1
        result = CreditState(enabled=row.enabled, version=row.version)
        await self.commands.record(session, actor, 'state', 'platform', data, result)
        return result

    async def adjust(
        self, session: AsyncSession, actor: Principal, user_id: UUID, data: CreditAdjustment
    ) -> CreditSummary:
        actor.require_permission('ai:credits:adjust')
        # Revalidate even a model_copy/model_construct created by an internal caller.
        points = exact_points(data.points, positive=True)
        if data.kind not in ('grant', 'reclaim'):
            raise AppError('invalid_adjustment', '积分调整类型无效', 422)
        previous = await self.commands.replay(session, actor, 'adjust', str(user_id), data, CreditSummary)
        if previous is not None:
            return previous
        row = await self._account(session, user_id)
        if data.kind == 'reclaim':
            if available_points(row) < points:
                raise AppError('credits_insufficient', '仅能回收未使用且未预占的积分', 409)
            row.reclaimed_points = exact_points(sum_points(row.reclaimed_points, points))
            delta = points.copy_negate()
        else:
            row.allocated_points = exact_points(sum_points(row.allocated_points, points))
            delta = points
        row.version += 1
        self._entry(
            session, row, kind=data.kind, amount=points, available_delta=delta, operator_id=actor.id, reason=data.reason
        )
        result = account_summary(row)
        await self.commands.record(session, actor, 'adjust', str(user_id), data, result)
        return result

    async def set_account_state(
        self,
        session: AsyncSession,
        actor: Principal,
        user_id: UUID,
        data: CreditAccountStatePatch,
    ) -> CreditSummary:
        actor.require_permission('ai:credits:configure')
        previous = await self.commands.replay(session, actor, 'account_state', str(user_id), data, CreditSummary)
        if previous is not None:
            return previous
        row = await self._account(session, user_id)
        self._version(row.version, data.expected_version)
        row.enabled = data.enabled
        row.version += 1
        self._entry(
            session,
            row,
            kind='resume' if data.enabled else 'pause',
            amount=ZERO,
            available_delta=ZERO,
            operator_id=actor.id,
            reason=data.reason,
        )
        result = account_summary(row)
        await self.commands.record(session, actor, 'account_state', str(user_id), data, result)
        return result

    async def preflight(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        model_id: UUID,
        key_id: UUID,
        key_limit: Decimal | None,
        input_tokens: int,
    ) -> None:
        rate = await self.rates.quote(session, model_id)
        if rate is None:
            raise AppError('credit_rate_missing', '模型尚未配置积分费率', 409)
        held = points_for_usage(rate, input_tokens=input_tokens, output_tokens=0, search_units=0)
        account = await self.queries.summary(session, user_id)
        if not account.enabled:
            raise AppError('credits_paused', '积分账户已暂停', 403)
        if account.available_points < held:
            raise AppError('credits_insufficient', '可用积分不足', 429)
        if key_limit is not None:
            month = utcnow().astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m')
            monthly = await session.get(CreditKeyMonth, (key_id, month))
            used = sum_points(monthly.used_points, monthly.reserved_points) if monthly else ZERO
            if key_limit == 0 or sum_points(used, held) > key_limit:
                raise AppError('key_points_limit_exceeded', 'API Key 月度积分上限不足', 429)

    async def reserve(  # noqa: PLR0913 -- fixed public reservation contract includes all admission inputs.
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        run_id: UUID,
        model_id: UUID,
        source: str,
        operation: str = 'chat',
        estimated_input_tokens: int = 0,
        max_output_tokens: int = 0,
        estimated_search_units: int = 0,
        key_id: UUID | None = None,
        key_limit: Decimal | None = None,
    ) -> CreditReservation:
        self._usage(estimated_input_tokens, max_output_tokens, estimated_search_units)
        if not source or len(source) > MAX_SOURCE_LENGTH or not operation or len(operation) > MAX_OPERATION_LENGTH:
            raise ValueError('Invalid reservation source or operation')
        limit = exact_points(key_limit) if key_limit is not None else None
        payload: dict[str, object] = {
            'user_id': str(user_id),
            'model_id': str(model_id),
            'source': source,
            'operation': operation,
            'estimated_input_tokens': estimated_input_tokens,
            'max_output_tokens': max_output_tokens,
            'estimated_search_units': estimated_search_units,
            'key_id': str(key_id) if key_id else None,
            'key_limit': str(limit) if limit is not None else None,
        }
        await lock_credit(session, f'run:{run_id}')
        old = await session.get(CreditHold, run_id)
        if old is not None:
            if old.request_payload != payload:
                raise AppError('idempotency_conflict', '同一运行标识不能用于不同的积分预占', 409)
            return reservation_info(old)
        account = await self._account(session, user_id)
        if not account.enabled:
            raise AppError('credits_paused', '积分账户已暂停', 403)
        rate = await self.rates.quote(session, model_id)
        if rate is None:
            raise AppError('credit_rate_missing', '模型尚未配置积分费率', 409)
        held = points_for_usage(
            rate,
            input_tokens=estimated_input_tokens,
            output_tokens=max_output_tokens,
            search_units=estimated_search_units,
        )
        exact_points(held)
        month = None
        if key_id is not None:
            if limit == 0:
                raise AppError('key_points_limit_exceeded', 'API Key 月度积分上限不足', 429)
            month = utcnow().astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m')
            monthly = await self._key_month(session, key_id, month, user_id)
            if limit is not None and sum_points(monthly.used_points, monthly.reserved_points, held) > limit:
                raise AppError('key_points_limit_exceeded', 'API Key 月度积分上限不足', 429)
            # The row is only mutated after the parent wallet has also passed.
        if available_points(account) < held:
            raise AppError('credits_insufficient', '可用积分不足', 429)
        if key_id is not None:
            monthly.reserved_points = sum_points(monthly.reserved_points, held)
        account.reserved_points = sum_points(account.reserved_points, held)
        account.version += 1
        row = CreditHold(
            run_id=run_id,
            user_id=user_id,
            model_id=model_id,
            source=source,
            operation=operation,
            reserved_points=held,
            rate_id=rate.id,
            rate_version=rate.version,
            rate_snapshot=rate.model_dump(mode='json', include=PRICES),
            request_payload=payload,
            key_id=key_id,
            key_month=month,
        )
        session.add(row)
        # Insert before its ledger entry to satisfy the run foreign key.
        await session.flush()
        self._entry(
            session,
            account,
            kind='reserve',
            amount=held,
            available_delta=held.copy_negate(),
            reserved_delta=held,
            run_id=run_id,
            reason='模型调用积分预占',
        )
        await session.flush()
        return reservation_info(row)

    async def require_reservation(self, session: AsyncSession, run_id: UUID, user_id: UUID, model_id: UUID) -> None:
        row = await session.scalar(
            select(CreditHold).where(
                CreditHold.run_id == run_id,
                CreditHold.user_id == user_id,
                CreditHold.model_id == model_id,
                CreditHold.status == 'reserved',
            )
        )
        if row is None:
            raise AppError('invocation_unavailable', '模型调用积分预占不存在或已处理', 409)

    async def settle(
        self,
        session: AsyncSession,
        run_id: UUID,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        rejected_before_execution: bool = False,
        search_units: int | None = None,
        cache_tokens: int | None = None,
        reasoning_tokens: int | None = None,
    ) -> CreditReservation:
        account, row = await self._locked_hold(session, run_id)
        if rejected_before_execution:
            self._validate_usage_facts(
                row,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_tokens=cache_tokens,
                search_units=search_units,
                reasoning_tokens=reasoning_tokens,
                rejected_before_execution=True,
            )
        if row.status != 'reserved':
            return reservation_info(row)
        return await self._settle(
            session,
            account,
            row,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            rejected_before_execution=rejected_before_execution,
            search_units=search_units,
            cache_tokens=cache_tokens,
            reasoning_tokens=reasoning_tokens,
        )

    async def reconcile(
        self,
        session: AsyncSession,
        actor: Principal,
        run_id: UUID,
        data: CreditReconcile,
    ) -> CreditReservation:
        actor.require_permission('ai:credits:reconcile')
        previous = await self.commands.replay(session, actor, 'reconcile', str(run_id), data, CreditReservation)
        if previous is not None:
            return previous
        account, row = await self._locked_hold(session, run_id)
        self._version(row.version, data.expected_version)
        self._validate_usage_facts(
            row,
            input_tokens=data.input_tokens,
            output_tokens=data.output_tokens,
            cache_tokens=data.cache_tokens,
            search_units=data.search_units,
            rejected_before_execution=data.rejected_before_execution,
        )
        if row.status != 'unknown':
            raise AppError('reconciliation_unavailable', '只有未知用量可以进行核对', 409)
        result = await self._settle(
            session,
            account,
            row,
            input_tokens=data.input_tokens,
            output_tokens=data.output_tokens,
            search_units=data.search_units,
            cache_tokens=data.cache_tokens,
            rejected_before_execution=data.rejected_before_execution,
            operator_id=actor.id,
            reason=data.reason,
            require_known=True,
        )
        await self.commands.record(session, actor, 'reconcile', str(run_id), data, result)
        return result

    async def _settle(  # noqa: PLR0913 -- normalized usage plus the reconciliation audit context.
        self,
        session: AsyncSession,
        account: CreditAccount,
        row: CreditHold,
        *,
        input_tokens: int | None,
        output_tokens: int | None,
        search_units: int | None,
        cache_tokens: int | None,
        rejected_before_execution: bool,
        reasoning_tokens: int | None = None,
        operator_id: UUID | None = None,
        reason: str = '模型调用积分结算',
        require_known: bool = False,
    ) -> CreditReservation:
        known = self._validate_usage_facts(
            row,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_tokens=cache_tokens,
            search_units=search_units,
            reasoning_tokens=reasoning_tokens,
            rejected_before_execution=rejected_before_execution,
        )
        if not rejected_before_execution:
            input_tokens = known['input_tokens'] if input_tokens is None else input_tokens
            output_tokens = known['output_tokens'] if output_tokens is None else output_tokens
            cache_tokens = known['cache_tokens'] if cache_tokens is None else cache_tokens
            search_units = known['search_units'] if search_units is None else search_units
            reasoning_tokens = known['reasoning_tokens'] if reasoning_tokens is None else reasoning_tokens
        self._usage(input_tokens, output_tokens, search_units, cache_tokens, reasoning_tokens)
        if rejected_before_execution:
            if any(
                value not in (None, 0)
                for value in (input_tokens, output_tokens, search_units, cache_tokens, reasoning_tokens)
            ):
                raise ValueError('A rejected invocation cannot contain consumed usage')
            input_tokens = output_tokens = search_units = cache_tokens = reasoning_tokens = 0
        if input_tokens is not None and (cache_tokens or 0) > input_tokens:
            raise ValueError('Cached tokens cannot exceed total input tokens')
        rate = CreditPrices.model_validate(row.rate_snapshot)
        unknown = (
            (search_units is None)
            if rate.billing_mode == 'search_unit'
            else (input_tokens is None or output_tokens is None)
        )
        if unknown and require_known:
            raise AppError('usage_incomplete', '核对必须提供可确认的实际用量', 422)
        row.input_tokens, row.output_tokens = input_tokens, output_tokens
        row.search_units, row.cache_tokens, row.reasoning_tokens = search_units, cache_tokens, reasoning_tokens
        row.usage_snapshot = {
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'cache_tokens': cache_tokens,
            'search_units': search_units,
            'reasoning_tokens': reasoning_tokens,
        }
        row.version += 1
        if unknown:
            row.status = 'unknown'
            self._entry(
                session,
                account,
                kind='unknown',
                amount=ZERO,
                available_delta=ZERO,
                run_id=row.run_id,
                reason='模型调用用量未知，保留积分预占',
            )
            await session.flush()
            return reservation_info(row)
        charge = points_for_usage(
            rate,
            input_tokens=input_tokens or 0,
            output_tokens=output_tokens or 0,
            search_units=search_units or 0,
            cache_tokens=cache_tokens or 0,
        )
        exact_points(charge)
        if row.key_id is not None:
            if row.key_month is None:
                raise RuntimeError('Credit reservation month is missing')
            month = await self._key_month(session, row.key_id, row.key_month, row.user_id)
            month.reserved_points = sum_points(month.reserved_points, row.reserved_points.copy_negate())
            month.used_points = sum_points(month.used_points, charge)
        account.reserved_points = sum_points(account.reserved_points, row.reserved_points.copy_negate())
        account.used_points = sum_points(account.used_points, charge)
        account.version += 1
        row.charged_points, row.status = charge, 'settled'
        self._entry(
            session,
            account,
            kind='release' if rejected_before_execution else 'settle',
            amount=charge,
            available_delta=sum_points(row.reserved_points, charge.copy_negate()),
            reserved_delta=row.reserved_points.copy_negate(),
            run_id=row.run_id,
            operator_id=operator_id,
            reason=reason,
        )
        await session.flush()
        return reservation_info(row)

    async def _account(self, session: AsyncSession, user_id: UUID) -> CreditAccount:
        await session.execute(
            insert(CreditAccount)
            .values(
                user_id=user_id,
                enabled=True,
                version=0,
                allocated_points=ZERO,
                reclaimed_points=ZERO,
                used_points=ZERO,
                reserved_points=ZERO,
            )
            .on_conflict_do_nothing(index_elements=['user_id'])
        )
        row = await session.scalar(
            select(CreditAccount)
            .where(CreditAccount.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise RuntimeError('Credit account invariant failed')
        return row

    async def _key_month(self, session: AsyncSession, key_id: UUID, month: str, user_id: UUID) -> CreditKeyMonth:
        await session.execute(
            insert(CreditKeyMonth)
            .values(key_id=key_id, month=month, user_id=user_id, used_points=ZERO, reserved_points=ZERO)
            .on_conflict_do_nothing(index_elements=['key_id', 'month'])
        )
        row = await session.scalar(
            select(CreditKeyMonth)
            .where(CreditKeyMonth.key_id == key_id, CreditKeyMonth.month == month)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise RuntimeError('Credit key month invariant failed')
        if row.user_id != user_id:
            raise AppError('key_owner_conflict', 'API Key 与积分账户不匹配', 409)
        return row

    async def _locked_hold(self, session: AsyncSession, run_id: UUID) -> tuple[CreditAccount, CreditHold]:
        user_id = await session.scalar(select(CreditHold.user_id).where(CreditHold.run_id == run_id))
        if user_id is None:
            raise AppError('invocation_unavailable', '模型调用积分预占不存在', 409)
        account = await self._account(session, user_id)
        row = await session.scalar(
            select(CreditHold)
            .where(CreditHold.run_id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise RuntimeError('Credit reservation invariant failed')
        return account, row

    @staticmethod
    def _version(actual: int, expected: int) -> None:
        if actual != expected:
            raise AppError('version_conflict', '数据已更新，请刷新后重试', 409)

    @staticmethod
    def _validate_usage_facts(
        row: CreditHold,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cache_tokens: int | None = None,
        search_units: int | None = None,
        reasoning_tokens: int | None = None,
        rejected_before_execution: bool = False,
    ) -> dict[str, int | None]:
        """Only unknown facts may be completed; compensation never rewrites a meter."""
        provided = {
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'cache_tokens': cache_tokens,
            'search_units': search_units,
            'reasoning_tokens': reasoning_tokens,
        }
        CreditService._usage(*provided.values())
        snapshot = row.usage_snapshot or {}
        known: dict[str, int | None] = {}
        for name in USAGE_COUNTERS:
            column, frozen = getattr(row, name), snapshot.get(name)
            if column is not None and frozen is not None and column != frozen:
                raise AppError('usage_conflict', '实际用量记录存在冲突，不能覆盖原始计量', 409)
            known[name] = column if column is not None else frozen
        if rejected_before_execution and any(
            value is not None and value > 0 for value in (*known.values(), *provided.values())
        ):
            raise AppError('usage_conflict', '已有实际用量，不能确认模型调用未执行', 409)
        for name, value in provided.items():
            if value is not None and known[name] is not None and value != known[name]:
                raise AppError('usage_conflict', '已记录的实际用量不能被覆盖，请仅补充未知用量', 409)
        return known

    @staticmethod
    def _usage(*values: int | None) -> None:
        if any(
            value is not None and (type(value) is not int or value < 0 or value > MAX_USAGE_COUNT) for value in values
        ):
            raise ValueError('Usage must be nonnegative 32-bit integers')

    @staticmethod
    def _entry(
        session: AsyncSession,
        account: CreditAccount,
        *,
        kind: str,
        amount: Decimal,
        available_delta: Decimal,
        reserved_delta: Decimal = ZERO,
        operator_id: UUID | None = None,
        run_id: UUID | None = None,
        reason: str,
    ) -> None:
        session.add(
            CreditLedger(
                user_id=account.user_id,
                operator_id=operator_id,
                kind=kind,
                amount=amount,
                available_delta=available_delta,
                reserved_delta=reserved_delta,
                available_after=available_points(account),
                reserved_after=account.reserved_points,
                run_id=run_id,
                reason=reason,
            )
        )
