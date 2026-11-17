"""Credit management receipts share one operator/request namespace and transaction."""

from decimal import Decimal
from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.billing.contracts import CreditRequest, CreditWrite
from zhiheng.modules.billing.gate import lock_credit
from zhiheng.modules.billing.orm import CreditCommand
from zhiheng.modules.billing.pricing import exact_points
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

Result = TypeVar('Result', bound=BaseModel)


class CreditCommandJournal:
    async def replay(
        self,
        session: AsyncSession,
        actor: Principal,
        kind: str,
        target: str,
        data: CreditRequest,
        result_type: type[Result],
    ) -> Result | None:
        if not data.client_request_id.strip() or (isinstance(data, CreditWrite) and not data.reason.strip()):
            raise AppError('invalid_request', '操作原因与幂等标识不能为空', 422)
        await lock_credit(session, f'command:{actor.id}:{data.client_request_id}')
        row = await session.scalar(
            select(CreditCommand).where(
                CreditCommand.operator_id == actor.id, CreditCommand.client_request_id == data.client_request_id
            )
        )
        if row is None:
            return None
        if row.kind != kind or row.target != target or row.payload != self._payload(data):
            raise AppError('idempotency_conflict', '同一幂等标识不能用于不同的操作', 409)
        return result_type.model_validate(row.result)

    async def record(
        self,
        session: AsyncSession,
        actor: Principal,
        kind: str,
        target: str,
        data: CreditRequest,
        result: BaseModel,
    ) -> None:
        session.add(
            CreditCommand(
                operator_id=actor.id,
                client_request_id=data.client_request_id,
                kind=kind,
                target=target,
                payload=self._payload(data),
                result=result.model_dump(mode='json'),
                reason=data.reason,
            )
        )
        await session.flush()

    @staticmethod
    def _payload(data: CreditRequest) -> dict[str, Any]:
        result = data.model_dump(mode='json')
        # Equivalent decimal spellings represent the same request payload.
        for name, value in data.model_dump().items():
            if isinstance(value, Decimal):
                result[name] = str(exact_points(value))
        return result
