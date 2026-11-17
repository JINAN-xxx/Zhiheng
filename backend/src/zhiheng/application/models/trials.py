"""Connection checks and model trials share native execution admission and metering."""

import hashlib
import json
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.application.execution.admission import MAX_PROMPT_RESERVATION, AdmissionIdentity, RunAdmission
from zhiheng.modules.billing.public import lock_billing_admission
from zhiheng.modules.models.public import ModelMessage, ModelOptions, ModelRequest, ModelService, ModelTrialInput
from zhiheng.runtime.contracts import RunSnapshot
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


class ModelTrialService:
    def __init__(self, models: ModelService, runs: RunStore, admission: RunAdmission) -> None:
        self._models, self._runs, self._admission = models, runs, admission

    async def admit(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        model_id: UUID,
        client_request_id: UUID,
        trial: ModelTrialInput | None = None,
    ) -> RunSnapshot:
        await lock_billing_admission(session)
        principal.require_permission('platform:models:test')
        fingerprint = (
            self._fingerprint('model_trial', model_id, model_id, trial.model_dump_json())
            if trial is not None
            else self._fingerprint('connection_test', model_id, model_id, '')
        )
        await self._runs.lock_request(session, principal.id, client_request_id)
        existing = await self._runs.existing_request(session, principal.id, client_request_id, fingerprint)
        if existing is not None:
            return existing
        binding = await self._models.bindings.resolve_for_test(session, principal, model_id)
        if trial is not None:
            if binding.model_type != 'rerank' and trial.documents:
                raise AppError('invalid_model_trial', '仅重排序模型接受候选资料', 422)
            if binding.model_type == 'rerank' and not trial.documents:
                raise AppError('invalid_model_trial', '请提供至少一份候选资料', 422)
            messages = tuple(ModelMessage('user', text) for text in (trial.prompt, *trial.documents))
            if binding.model_type == 'rerank':
                binding = binding.model_copy(
                    update={'options': ModelOptions(top_n=min(3, len(trial.documents)), return_documents=False)}
                )
        else:
            messages = (ModelMessage('user', 'Reply with OK.'),)
        if trial is None and binding.model_type == 'embedding':
            messages = (ModelMessage('user', 'Connection test document.'),)
        elif trial is None and binding.model_type == 'rerank':
            messages = (
                ModelMessage('user', 'Connection test'),
                ModelMessage('user', 'Connection test document.'),
                ModelMessage('user', 'Other document.'),
            )
            binding = binding.model_copy(update={'options': ModelOptions(top_n=1, return_documents=False)})
        request = ModelRequest(binding=binding, messages=messages)
        return await self._admission.reserve(
            session,
            identity=AdmissionIdentity(owner_id=principal.id, billing_owner_id=principal.id),
            resource_type='connection_test',
            resource_id=model_id,
            client_request_id=client_request_id,
            fingerprint=fingerprint,
            request=request,
            input_token_limit=MAX_PROMPT_RESERVATION,
        )

    @staticmethod
    def _fingerprint(source: str, resource_id: UUID, model_id: UUID, message: str) -> str:
        value = json.dumps(
            [source, str(resource_id), str(model_id), message], ensure_ascii=False, separators=(',', ':')
        )
        return hashlib.sha256(value.encode()).hexdigest()
