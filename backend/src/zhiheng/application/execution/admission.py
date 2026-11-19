"""Atomically create a native Run, Outbox and its usage reservation.

Callers authorize their resource, freeze the request and own the transaction and
idempotency locks. Admission never substitutes the caller for the billing owner.
"""

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.billing.public import UsageService
from zhiheng.modules.models.public import ModelRequest, ModelService
from zhiheng.runtime.contracts import RunSnapshot
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.errors import AppError
from zhiheng.shared.time import utcnow

MAX_PROMPT_RESERVATION = 64000


@dataclass(frozen=True, kw_only=True)
class AdmissionIdentity:
    """Persisted execution and billing identities, after domain authorization."""

    owner_id: UUID
    billing_owner_id: UUID
    key_id: UUID | None = None


class RunAdmission:
    def __init__(
        self, models: ModelService, usage: UsageService, runs: RunStore, *, total_timeout_seconds: float = 600
    ) -> None:
        self._models, self._usage, self._runs = models, usage, runs
        self._total_timeout = total_timeout_seconds

    async def reserve(  # noqa: PLR0913 - explicit execution identity, frozen request and reservation policy
        self,
        session: AsyncSession,
        *,
        identity: AdmissionIdentity,
        resource_type: str,
        resource_id: UUID,
        client_request_id: UUID,
        fingerprint: str,
        request: ModelRequest,
        usage_source: str | None = None,
        input_token_estimate: int | None = None,
        input_token_limit: int | None = None,
        search_unit_estimate: int | None = None,
    ) -> RunSnapshot:
        # This financial safety estimate intentionally uses UTF-8 bytes, not the
        # model context counter or provider tokenizer. Actual usage settles later.
        input_tokens = sum(
            len(message.content.encode()) + 16 + sum(image.estimated_tokens for image in message.images)
            for message in request.messages
        )
        if input_token_estimate is not None:
            input_tokens = max(input_tokens, input_token_estimate)
        if input_token_limit is not None and input_tokens > input_token_limit:
            raise AppError('context_too_long', '对话上下文过长，请新建对话')
        output_tokens = request.binding.output_reservation_tokens if request.binding.model_type == 'chat' else 0
        search_units = (
            search_unit_estimate
            if search_unit_estimate is not None
            else max(1, len(request.messages) - 1)
            if request.binding.model_type == 'rerank'
            else 0
        )
        deadline = utcnow() + timedelta(
            seconds=min(self._total_timeout, request.execution_timeout_seconds or self._total_timeout)
        )
        request = request.model_copy(
            update={
                'execution_deadline': min(deadline, request.execution_deadline)
                if request.execution_deadline
                else deadline
            }
        )
        # UsageService has its own atomic metering/credit operation. This outer
        # savepoint also removes Run/Outbox if reservation fails and a caller
        # catches that failure while committing unrelated work in its transaction.
        async with session.begin_nested():
            run = await self._runs.create(
                session,
                owner_id=identity.owner_id,
                billing_owner_id=identity.billing_owner_id,
                resource_type=resource_type,
                resource_id=resource_id,
                request_id=client_request_id,
                fingerprint=fingerprint,
                payload=request.model_dump(mode='json'),
            )
            rate = await self._usage.money_rate(session, self._models, request.binding.model_id)
            await self._usage.reserve(
                session,
                user_id=identity.billing_owner_id,
                run_id=run.id,
                model_id=request.binding.model_id,
                source=resource_type if usage_source is None else usage_source,
                tokens=input_tokens + output_tokens,
                estimated_input_tokens=input_tokens,
                max_output_tokens=output_tokens,
                estimated_search_units=search_units,
                key_id=identity.key_id,
                rate=rate,
                operation=request.binding.model_type,
            )
        return run
