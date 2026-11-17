"""Public calls use the same Run/outbox and personal usage reservation as chat."""

import hashlib
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.application.execution.admission import AdmissionIdentity, RunAdmission
from zhiheng.modules.billing.public import UsageService, lock_billing_admission
from zhiheng.modules.developer.public import DeveloperIdentity, KeyService
from zhiheng.modules.models.public import ModelMessage, ModelOptions, ModelRequest, ModelService
from zhiheng.runtime.contracts import RunSnapshot
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.errors import AppError


class PublicRunService:
    def __init__(
        self, keys: KeyService, models: ModelService, usage: UsageService, runs: RunStore, *, admission: RunAdmission
    ) -> None:
        self._keys, self._models, self._usage, self._runs = keys, models, usage, runs
        self.admission = admission

    async def cancel(self, session: AsyncSession, run_id: UUID, owner_id: UUID) -> None:
        _, release = await self._runs.cancel(session, run_id, owner_id)
        if release:
            await self._usage.settle(session, run_id, rejected_before_execution=True)

    async def admit(
        self,
        session: AsyncSession,
        token: str,
        *,
        model_id: UUID,
        operation: str,
        messages: tuple[ModelMessage, ...],
        options: dict[str, object],
    ) -> tuple[RunSnapshot, DeveloperIdentity]:
        await lock_billing_admission(session)
        identity = await self._keys.authenticate(session, token, lock=True)
        await self._keys.authorize_identity(session, identity, [model_id], scope='models:invoke', lock=True)
        key = await self._keys.info_for_identity(session, identity)
        if key is None or model_id not in key.model_ids:
            raise AppError('model_not_found', 'Model is unavailable', 404)
        try:
            binding = await self._models.bindings.resolve(session, model_id)
        except AppError:
            raise AppError('model_not_found', 'Model is unavailable', 404) from None
        if binding.model_type != operation:
            raise AppError('model_not_found', 'Model is unavailable', 404)
        max_tokens = options.pop('max_tokens', binding.max_output_tokens)
        assert isinstance(max_tokens, int)
        if max_tokens > binding.max_output_tokens:
            raise AppError('invalid_request', 'max_tokens exceeds the selected model limit', 400)
        binding = binding.model_copy(
            update={'options': ModelOptions.model_validate(options), 'max_output_tokens': max_tokens}
        )
        request = ModelRequest(binding=binding, messages=messages)
        run = await self.admission.reserve(
            session,
            identity=AdmissionIdentity(
                owner_id=identity.owner_id, billing_owner_id=identity.owner_id, key_id=identity.key_id
            ),
            resource_type='public',
            resource_id=identity.application_id,
            client_request_id=uuid4(),
            fingerprint=hashlib.sha256(request.model_dump_json().encode()).hexdigest(),
            request=request,
        )
        return run, identity
