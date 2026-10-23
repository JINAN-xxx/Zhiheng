import hashlib
import json
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.modules.chat.public import ChatService
from zhiheng.modules.developer.public import UsageService
from zhiheng.modules.models.public import ModelMessage, ModelRequest, ModelService
from zhiheng.runtime.contracts import RunSnapshot
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

MAX_PROMPT_RESERVATION = 64000


class ChatRunService:
    def __init__(self, chat: ChatService, models: ModelService, usage: UsageService, runs: RunStore) -> None:
        self._chat, self._models, self._usage, self._runs = chat, models, usage, runs

    async def admit_chat(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        conversation_id: UUID,
        model_id: UUID,
        message: str,
        client_request_id: UUID,
        token_limit: int,
    ) -> RunSnapshot:
        fingerprint = self._fingerprint('chat', conversation_id, model_id, message)
        await self._runs.lock_request(session, principal.id, client_request_id)
        existing = await self._runs.existing_request(session, principal.id, client_request_id, fingerprint)
        if existing is not None:
            return existing
        await self._chat.require_conversation(session, principal, conversation_id, lock=True)
        if await self._runs.has_active(session, conversation_id):
            raise AppError('conversation_busy', '当前对话仍有运行中的请求', 409)
        binding = await self._models.resolve(session, model_id)
        context = await self._chat.context(session, principal, conversation_id)
        request = ModelRequest(binding=binding, messages=(*context, ModelMessage('user', message)))
        run = await self._reserve(
            session,
            principal,
            resource_type='chat',
            resource_id=conversation_id,
            client_request_id=client_request_id,
            fingerprint=fingerprint,
            request=request,
            token_limit=token_limit,
        )
        await self._chat.add_user_message(session, conversation_id=conversation_id, run_id=run.id, content=message)
        return run

    async def admit_test(
        self, session: AsyncSession, principal: Principal, *, model_id: UUID, client_request_id: UUID, token_limit: int
    ) -> RunSnapshot:
        principal.require_admin()
        fingerprint = self._fingerprint('connection_test', model_id, model_id, '')
        await self._runs.lock_request(session, principal.id, client_request_id)
        existing = await self._runs.existing_request(session, principal.id, client_request_id, fingerprint)
        if existing is not None:
            return existing
        request = ModelRequest(
            binding=await self._models.resolve(session, model_id), messages=(ModelMessage('user', 'Reply with OK.'),)
        )
        return await self._reserve(
            session,
            principal,
            resource_type='connection_test',
            resource_id=model_id,
            client_request_id=client_request_id,
            fingerprint=fingerprint,
            request=request,
            token_limit=token_limit,
        )

    async def cancel(self, session: AsyncSession, principal: Principal, run_id: UUID) -> RunSnapshot:
        snapshot, release = await self._runs.cancel(session, run_id, principal.id)
        if release:
            await self._usage.settle(session, run_id, rejected_before_execution=True)
        return snapshot

    async def _reserve(
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
        prompt_bytes = sum(len(message.content.encode()) + 16 for message in request.messages)
        if prompt_bytes > MAX_PROMPT_RESERVATION:
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
        await self._usage.reserve(
            session,
            user_id=principal.id,
            run_id=run.id,
            model_id=request.binding.model_id,
            source=resource_type,
            tokens=prompt_bytes + request.binding.max_output_tokens,
            token_limit=token_limit,
        )
        return run

    @staticmethod
    def _fingerprint(source: str, resource_id: UUID, model_id: UUID, message: str) -> str:
        value = json.dumps(
            [source, str(resource_id), str(model_id), message], ensure_ascii=False, separators=(',', ':')
        )
        return hashlib.sha256(value.encode()).hexdigest()
