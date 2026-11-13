"""Create a native Run and its usage reservation in the caller's transaction."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.billing.public import UsageService
from zhiheng.modules.models.public import ModelRequest, ModelService
from zhiheng.runtime.contracts import RunSnapshot
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.errors import AppError
from zhiheng.shared.platform_permissions import require_capability, require_execution_capabilities
from zhiheng.shared.principal import Principal

MAX_PROMPT_RESERVATION = 64000


class RunAdmission:
    def __init__(self, models: ModelService, usage: UsageService, runs: RunStore) -> None:
        self._models, self._usage, self._runs = models, usage, runs

    async def reserve(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        resource_type: str,
        resource_id: UUID,
        client_request_id: UUID,
        fingerprint: str,
        request: ModelRequest,
        token_limit: int,
    ) -> RunSnapshot:
        if resource_type in {'chat', 'chat_summary', 'agent_chat', 'agent_preview', 'work_turn'}:
            require_execution_capabilities(principal, resource_type)
            for module in request.required_modules:
                require_capability(
                    principal, module, 'query' if module == 'knowledge' else 'use' if module == 'skills' else 'read'
                )
        prompt_bytes = sum(
            len(message.content.encode()) + 16 + sum(image.estimated_tokens for image in message.images)
            for message in request.messages
        )
        if resource_type != 'chat' and prompt_bytes > MAX_PROMPT_RESERVATION:
            raise AppError('context_too_long', '对话上下文过长，请新建对话')
        run = await self._runs.create(
            session,
            owner_id=principal.id,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=client_request_id,
            fingerprint=fingerprint,
            payload=request.model_dump(mode='json'),
        )
        rate = None
        try:
            rate = await self._models.latest_rate(session, request.binding.model_id)
        except AppError as error:
            if error.code != 'model_not_found':
                raise
        await self._usage.reserve(
            session,
            user_id=principal.id,
            run_id=run.id,
            model_id=request.binding.model_id,
            source=resource_type,
            tokens=prompt_bytes
            + (request.binding.output_reservation_tokens if request.binding.model_type == 'chat' else 0),
            estimated_input_tokens=prompt_bytes,
            max_output_tokens=request.binding.output_reservation_tokens if request.binding.model_type == 'chat' else 0,
            estimated_search_units=max(1, len(request.messages) - 1) if request.binding.model_type == 'rerank' else 0,
            token_limit=token_limit,
            rate=rate,
            operation=request.binding.model_type,
        )
        return run
