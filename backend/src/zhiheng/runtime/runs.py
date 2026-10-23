import hashlib
from datetime import timedelta
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.runtime.contracts import ClaimedRun, RunError, RunSnapshot, RunStatus, RunUsage
from zhiheng.runtime.orm import Outbox, Run
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow

ACTIVE_STATUSES = ('running', 'cancel_requested')


class RunStore:
    async def lock_request(self, session: AsyncSession, owner_id: UUID, request_id: UUID) -> None:
        key = int.from_bytes(hashlib.sha256(owner_id.bytes + request_id.bytes).digest()[:8], 'big', signed=True)
        await session.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': key})

    async def existing_request(
        self, session: AsyncSession, owner_id: UUID, request_id: UUID, fingerprint: str
    ) -> RunSnapshot | None:
        run = await session.scalar(select(Run).where(Run.owner_id == owner_id, Run.client_request_id == request_id))
        if run is None:
            return None
        if run.fingerprint != fingerprint:
            raise AppError('idempotency_conflict', '请求标识已用于其他内容', 409)
        return self._snapshot(run)

    async def create(
        self,
        session: AsyncSession,
        *,
        owner_id: UUID,
        resource_type: str,
        resource_id: UUID,
        request_id: UUID,
        fingerprint: str,
        payload: dict[str, object],
    ) -> RunSnapshot:
        run = Run(
            owner_id=owner_id,
            resource_type=resource_type,
            resource_id=resource_id,
            client_request_id=request_id,
            fingerprint=fingerprint,
            payload=payload,
        )
        session.add(run)
        await session.flush()
        session.add(Outbox(run_id=run.id))
        return self._snapshot(run)

    async def has_active(self, session: AsyncSession, resource_id: UUID) -> bool:
        return (
            await session.scalar(
                select(Run.id)
                .where(
                    Run.resource_type == 'chat',
                    Run.resource_id == resource_id,
                    Run.status.in_(('pending', *ACTIVE_STATUSES)),
                )
                .limit(1)
            )
            is not None
        )

    async def snapshot(self, session: AsyncSession, run_id: UUID, owner_id: UUID) -> RunSnapshot:
        row = await session.scalar(select(Run).where(Run.id == run_id, Run.owner_id == owner_id))
        if row is None:
            raise AppError('run_not_found', '运行不存在', 404)
        return self._snapshot(row)

    async def claim(self, session: AsyncSession, run_id: UUID, lease_seconds: int) -> ClaimedRun | None:
        row = await session.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if row is None or row.status != 'pending':
            return None
        row.status = 'running'
        row.attempt_id = uuid4()
        row.attempt_no += 1
        row.revision += 1
        row.lease_until = utcnow() + timedelta(seconds=lease_seconds)
        return ClaimedRun(row.id, row.resource_type, row.resource_id, row.owner_id, row.attempt_id, row.payload)

    async def control(
        self, session: AsyncSession, run_id: UUID, attempt_id: UUID, lease_seconds: int
    ) -> Literal['active', 'cancel', 'lost']:
        row = await session.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if not self._owns(row, attempt_id):
            return 'lost'
        assert row is not None
        if row.status == 'cancel_requested':
            return 'cancel'
        row.lease_until = utcnow() + timedelta(seconds=lease_seconds)
        return 'active'

    async def start_provider(self, session: AsyncSession, run_id: UUID, attempt_id: UUID) -> bool:
        row = await session.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if not self._owns(row, attempt_id) or row is None or row.status != 'running':
            return False
        row.provider_started = True
        return True

    async def lock_execution(self, session: AsyncSession, run_id: UUID, attempt_id: UUID) -> None:
        row = await session.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if not self._owns(row, attempt_id):
            raise AppError('execution_lost', '执行权已失效', 409)

    async def project(self, session: AsyncSession, run_id: UUID, attempt_id: UUID, content: str) -> bool:
        row = await session.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if not self._owns(row, attempt_id) or row is None:
            return False
        row.text = content
        row.projection_seq += 1
        row.revision += 1
        return True

    async def finish(
        self,
        session: AsyncSession,
        run_id: UUID,
        attempt_id: UUID,
        *,
        status: RunStatus,
        content: str,
        usage: RunUsage,
        error: RunError | None = None,
    ) -> RunSnapshot:
        row = await session.scalar(select(Run).where(Run.id == run_id).with_for_update())
        if not self._owns(row, attempt_id) or row is None:
            raise AppError('execution_lost', '执行权已失效', 409)
        row.status = 'cancelled' if row.status == 'cancel_requested' else status
        if row.status == 'cancelled':
            error = None
        row.text = content
        row.input_tokens, row.output_tokens, row.usage_status = usage.input_tokens, usage.output_tokens, usage.status
        row.error_code = error.code if error is not None else None
        row.error_message = error.message if error is not None else None
        row.revision += 1
        row.projection_seq += 1
        row.lease_until = None
        return self._snapshot(row)

    async def cancel(self, session: AsyncSession, run_id: UUID, owner_id: UUID) -> tuple[RunSnapshot, bool]:
        row = await session.scalar(select(Run).where(Run.id == run_id, Run.owner_id == owner_id).with_for_update())
        if row is None:
            raise AppError('run_not_found', '运行不存在', 404)
        release = row.status == 'pending'
        if release:
            row.status = 'cancelled'
            row.input_tokens, row.output_tokens, row.usage_status = 0, 0, 'known'
        elif row.status == 'running':
            row.status = 'cancel_requested'
        if release or row.status == 'cancel_requested':
            row.revision += 1
        return self._snapshot(row), release

    async def recover(self, session: AsyncSession) -> list[tuple[UUID, bool]]:
        rows = await session.scalars(
            select(Run)
            .where(
                Run.status.in_(ACTIVE_STATUSES),
                Run.lease_until < utcnow(),
            )
            .with_for_update(skip_locked=True)
            .limit(100)
        )
        recovered = []
        for row in rows:
            row.status = 'interrupted'
            row.usage_status = 'unknown' if row.provider_started else 'known'
            row.input_tokens = None if row.provider_started else 0
            row.output_tokens = None if row.provider_started else 0
            row.error_code, row.error_message = 'execution_interrupted', '执行进程失联，请核对本次运行'
            row.revision += 1
            row.lease_until = None
            recovered.append((row.id, row.provider_started))
        return recovered

    @staticmethod
    def _owns(row: Run | None, attempt_id: UUID) -> bool:
        return (
            row is not None
            and row.attempt_id == attempt_id
            and row.status in ACTIVE_STATUSES
            and row.lease_until is not None
            and row.lease_until > utcnow()
        )

    @staticmethod
    def _snapshot(row: Run) -> RunSnapshot:
        return RunSnapshot(
            id=row.id,
            resource_type=cast("Literal['chat', 'connection_test']", row.resource_type),
            resource_id=row.resource_id,
            status=cast('RunStatus', row.status),
            attempt_id=row.attempt_id,
            attempt_no=row.attempt_no,
            revision=row.revision,
            projection_seq=row.projection_seq,
            text=row.text,
            created_at=row.created_at,
            error=RunError(code=row.error_code, message=row.error_message or '运行失败') if row.error_code else None,
            usage=RunUsage(
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                status=cast("Literal['pending', 'known', 'unknown']", row.usage_status),
            ),
        )
