import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.application.file_inputs import MAX_NORMALIZED_IMAGE_BYTES
from zhiheng.application.lifecycle import RunLifecycle
from zhiheng.modules.chat.public import ChatService, ConversationInfo, SkillSnapshot
from zhiheng.modules.developer.public import UsageService
from zhiheng.modules.files.public import FileService, PreparedAttachment
from zhiheng.modules.models.public import (
    ModelBinding,
    ModelImage,
    ModelMessage,
    ModelOptions,
    ModelRequest,
    ModelService,
)
from zhiheng.modules.skills.public import resolve_selection
from zhiheng.runtime.contracts import RunSnapshot
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

MAX_PROMPT_RESERVATION = 64000


@dataclass(frozen=True)
class PreparedChatInputs:
    binding: ModelBinding
    attachments: tuple[PreparedAttachment, ...]


class ChatRunService:
    def __init__(
        self,
        chat: ChatService,
        models: ModelService,
        usage: UsageService,
        runs: RunStore,
        files: FileService | None = None,
    ) -> None:
        self._chat, self._models, self._usage, self._runs = chat, models, usage, runs
        self._files = files

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
        skill_slugs: Sequence[str] = (),
        file_ids: Sequence[UUID] = (),
        prepared: PreparedChatInputs | None = None,
    ) -> RunSnapshot:
        fingerprint = self.fingerprint('chat', conversation_id, model_id, message, skill_slugs, file_ids)
        await self._runs.lock_request(session, principal.id, client_request_id)
        existing = await self._runs.existing_request(session, principal.id, client_request_id, fingerprint)
        if existing is not None:
            return existing
        await self._chat.require_conversation(session, principal, conversation_id, lock=True)
        if await self._runs.has_active(session, conversation_id):
            raise AppError('conversation_busy', '当前对话仍有运行中的请求', 409)
        binding = await self._models.resolve(session, model_id)
        attachments = prepared.attachments if prepared is not None else ()
        if prepared is not None and binding != prepared.binding:
            raise AppError('model_binding_changed', '模型配置已变化，请重试', 409)
        if tuple(file_ids) != tuple(item.summary.file_id for item in attachments):
            raise AppError('invalid_attachments', '附件快照不一致', 422)
        if binding.model_type != 'chat':
            raise AppError('model_unavailable', '当前入口仅支持聊天模型', 422)
        selection = await resolve_selection(session, skill_slugs)
        skill_messages = tuple(ModelMessage('system', value.content) for value in selection.revisions)
        snapshots = [
            SkillSnapshot(
                slug=value.slug, name=value.name, revision_id=value.revision_id, content_hash=value.content_hash
            )
            for value in selection.revisions
        ]
        history = await self._chat.attachment_history(session, principal, conversation_id)
        if self._files is not None:
            for file_id in sorted({item.summary.file_id for item in (*history, *attachments)}):
                await self._files.require(session, principal, file_id, active=True, lock=True)
            for item in history:
                current = await self._files.get(session, principal, item.summary.file_id)
                if current.sha256 != item.summary.sha256:
                    raise AppError('file_snapshot_changed', '历史附件已变化', 409)
        if any(item.image_data is not None for item in (*history, *attachments)) and not binding.vision_enabled:
            raise AppError('unsupported_capability', '此模型未启用vision能力', 422)
        context = await self._chat.context(session, principal, conversation_id)
        extra = ''.join(
            f'\n\n[Attachment: {item.summary.original_filename}]\n{item.text}'
            for item in attachments
            if item.image_data is None
        )
        images = tuple(ModelImage(item.image_data) for item in attachments if item.image_data is not None)
        request = ModelRequest(
            binding=binding, messages=(*skill_messages, *context, ModelMessage('user', message + extra, images))
        )
        image_bytes = sum(len(image.data_url.encode()) for item in request.messages for image in item.images)
        if image_bytes > MAX_NORMALIZED_IMAGE_BYTES:
            raise AppError('attachment_images_too_large', '本次请求含历史的安全图片数据合计最多16MiB', 422)
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
        await self._chat.add_user_message(
            session,
            conversation_id=conversation_id,
            run_id=run.id,
            content=message,
            skill_snapshots=snapshots,
            attachments=attachments,
        )
        if self._files is not None:
            await self._files.attach(session, principal, attachments, resource_type='chat', resource_id=run.id)
        return run

    async def edit_conversation(
        self,
        session: AsyncSession,
        principal: Principal,
        conversation_id: UUID,
        *,
        expected_version: int,
        title: str | None = None,
        archived: bool | None = None,
        remove: bool = False,
    ) -> ConversationInfo:
        await self._chat.require_conversation(session, principal, conversation_id, lock=True)
        if await self._runs.has_active(session, conversation_id):
            raise AppError('conversation_busy', '当前对话仍有运行中的请求', 409)
        if remove and self._files is not None:
            run_ids = await self._chat.reference_run_ids(session, principal, conversation_id)
            await self._files.release_resources(
                session, resource_type='chat', resource_ids=run_ids, actor_id=principal.id
            )
        return await self._chat.edit_conversation(
            session,
            principal,
            conversation_id,
            expected_version=expected_version,
            title=title,
            archived=archived,
            remove=remove,
        )

    async def admit_test(
        self, session: AsyncSession, principal: Principal, *, model_id: UUID, client_request_id: UUID, token_limit: int
    ) -> RunSnapshot:
        principal.require_admin()
        fingerprint = self.fingerprint('connection_test', model_id, model_id, '')
        await self._runs.lock_request(session, principal.id, client_request_id)
        existing = await self._runs.existing_request(session, principal.id, client_request_id, fingerprint)
        if existing is not None:
            return existing
        binding = await self._models.resolve(session, model_id)
        messages: tuple[ModelMessage, ...] = (ModelMessage('user', 'Reply with OK.'),)
        if binding.model_type == 'embedding':
            messages = (ModelMessage('user', 'Connection test document.'),)
        elif binding.model_type == 'rerank':
            messages = (
                ModelMessage('user', 'Connection test'),
                ModelMessage('user', 'Connection test document.'),
                ModelMessage('user', 'Other document.'),
            )
            binding = binding.model_copy(update={'options': ModelOptions(top_n=1, return_documents=False)})
        request = ModelRequest(binding=binding, messages=messages)
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
        await RunLifecycle(self._chat).project(session, snapshot)
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
        prompt_bytes = sum(
            len(message.content.encode()) + 16 + sum(image.estimated_tokens for image in message.images)
            for message in request.messages
        )
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
            tokens=prompt_bytes + (request.binding.max_output_tokens if request.binding.model_type == 'chat' else 0),
            token_limit=token_limit,
            rate=rate,
            operation=request.binding.model_type,
        )
        return run

    @staticmethod
    def fingerprint(
        source: str,
        resource_id: UUID,
        model_id: UUID,
        message: str,
        skill_slugs: Sequence[str] = (),
        file_ids: Sequence[UUID] = (),
    ) -> str:
        fields: list[object] = [source, str(resource_id), str(model_id), message]
        if skill_slugs:
            fields.append(list(skill_slugs))
        if file_ids:
            fields.append({'file_ids': [str(value) for value in file_ids]})
        value = json.dumps(
            fields,
            ensure_ascii=False,
            separators=(',', ':'),
        )
        return hashlib.sha256(value.encode()).hexdigest()
