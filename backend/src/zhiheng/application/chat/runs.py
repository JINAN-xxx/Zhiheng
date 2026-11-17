import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.application.execution.admission import AdmissionIdentity, RunAdmission
from zhiheng.application.execution.lifecycle import RunLifecycle
from zhiheng.application.files.inputs import FileInputService
from zhiheng.infrastructure.database import Database
from zhiheng.modules.billing.public import UsageService, lock_billing_admission
from zhiheng.modules.chat.public import (
    ChatGenerationSettings,
    ChatRequestContext,
    ChatService,
    ConversationInfo,
    RegenerationTarget,
    SkillSnapshot,
)
from zhiheng.modules.files.public import FileService, PreparedAttachment
from zhiheng.modules.models.public import (
    ModelBinding,
    ModelImage,
    ModelMessage,
    ModelRequest,
    ModelService,
)
from zhiheng.modules.skills.public import resolve_selection
from zhiheng.runtime.contracts import RunSnapshot
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.errors import AppError
from zhiheng.shared.platform_permissions import require_execution_capabilities
from zhiheng.shared.principal import Principal

if TYPE_CHECKING:
    from zhiheng.application.chat.agent import ChatAgentService


@dataclass(frozen=True)
class PreparedChatInputs:
    binding: ModelBinding
    attachments: tuple[PreparedAttachment, ...]
    regeneration_message_id: UUID | None = None


class ChatRunService:
    def __init__(
        self,
        chat: ChatService,
        models: ModelService,
        usage: UsageService,
        runs: RunStore,
        files: FileService,
        *,
        admission: RunAdmission,
        database: Database,
        file_inputs: FileInputService,
        agent: 'ChatAgentService',
    ) -> None:
        self._chat, self._models, self._usage, self._runs = chat, models, usage, runs
        self._files, self._admission = files, admission
        self.database, self.file_inputs = database, file_inputs
        self.agent = agent
        self.context = agent.context

    async def admit_chat(  # noqa: PLR0913, PLR0915 - one transaction owns the complete chat admission contract
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        conversation_id: UUID,
        model_id: UUID,
        message: str,
        client_request_id: UUID,
        skill_slugs: Sequence[str] = (),
        file_ids: Sequence[UUID] = (),
        prepared: PreparedChatInputs | None = None,
        system_prompt: str = '',
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> RunSnapshot:
        await lock_billing_admission(session)
        settings = ChatGenerationSettings(
            system_prompt=system_prompt, temperature=temperature, max_output_tokens=max_output_tokens
        )
        regeneration_message_id = prepared.regeneration_message_id if prepared else None
        fingerprint = (
            self.regeneration_fingerprint(conversation_id, regeneration_message_id)
            if regeneration_message_id
            else self.fingerprint(
                'chat',
                conversation_id,
                model_id,
                message,
                skill_slugs,
                file_ids,
                **settings.generation_settings(),
            )
        )
        await self._runs.lock_request(session, principal.id, client_request_id)
        existing = await self._runs.existing_request(session, principal.id, client_request_id, fingerprint)
        if existing is not None:
            return existing
        conversation = await self._chat.require_conversation(session, principal, conversation_id, lock=True)
        if conversation.archived:
            raise AppError('conversation_archived', '已归档对话不能发送新问题', 409)
        target = (
            await self._chat.regeneration_target(session, principal, conversation_id, regeneration_message_id)
            if regeneration_message_id
            else None
        )
        if await self._runs.has_active(session, conversation_id):
            raise AppError('conversation_busy', '当前对话仍有运行中的请求', 409)
        binding = await self._models.bindings.resolve(session, model_id)
        attachments = prepared.attachments if prepared is not None else ()
        if prepared is not None and binding != prepared.binding:
            raise AppError('model_binding_changed', '模型配置已变化，请重试', 409)
        binding = self.generation_binding(binding, settings)
        if tuple(file_ids) != tuple(item.summary.file_id for item in attachments):
            raise AppError('invalid_attachments', '附件快照不一致', 422)
        if binding.model_type != 'chat':
            raise AppError('model_unavailable', '当前入口仅支持聊天模型', 422)
        selection = await resolve_selection(session, principal.id, skill_slugs)
        skill_messages = tuple(ModelMessage('system', value.content) for value in selection.revisions)
        snapshots = [
            SkillSnapshot(
                slug=value.slug, name=value.name, revision_id=value.revision_id, content_hash=value.content_hash
            )
            for value in selection.revisions
        ]
        await self._validate_attachments(
            session,
            principal,
            conversation_id,
            binding,
            attachments,
            target=target,
        )
        extra = ''.join(
            f'\n\n[Attachment: {item.summary.original_filename}]\n{item.text}'
            for item in attachments
            if item.image_data is None
        )
        images = tuple(ModelImage(item.image_data) for item in attachments if item.image_data is not None)
        messages = (
            *skill_messages,
            ModelMessage('user', message + extra, images),
        )
        system_messages = (ModelMessage('system', settings.system_prompt),) if settings.system_prompt else ()
        messages = (*system_messages, *messages)
        prepared_context = await self.agent.prepare(
            session,
            principal,
            conversation_id,
            binding,
            required=tuple(messages[:-1]),
            current=messages[-1],
            client_request_id=client_request_id,
            original_run_id=target.context_run_id if target else None,
        )
        messages = prepared_context.messages
        if target:
            snapshots = list(target.question.skill_snapshots)
            if [item.slug for item in snapshots] != list(skill_slugs):
                raise AppError('regeneration_context_unavailable', '原技能选择无法完整恢复，请重新发送问题', 409)
            await self.context.validate_frozen_skills(session, principal, snapshots, messages)
        if any(item.images for item in messages) and not binding.vision_enabled:
            raise AppError('unsupported_capability', '此模型未启用vision能力', 422)
        request = ModelRequest(binding=binding, messages=messages, required_modules=('skills',) if skill_slugs else ())
        input_estimate = (
            prepared_context.snapshot.agent_context.reservation_input_tokens
            if prepared_context and prepared_context.snapshot.agent_context
            else None
        )
        require_execution_capabilities(principal, 'chat', skills=bool(skill_slugs))
        run = await self._admission.reserve(
            session,
            identity=AdmissionIdentity(owner_id=principal.id, billing_owner_id=principal.id),
            resource_type='chat',
            resource_id=conversation_id,
            client_request_id=client_request_id,
            fingerprint=fingerprint,
            request=request,
            input_token_estimate=input_estimate if isinstance(input_estimate, int) else None,
        )
        if prepared_context is not None:
            snapshot = prepared_context.snapshot
            current_refs: list[dict[str, object]] = [
                {'file_id': str(item.summary.file_id), 'sha256': item.summary.sha256, 'question_id': 'current'}
                for item in attachments
            ]
            await self.context.memory.save_snapshot(session, run.id, snapshot, current_refs)
        request_context = ChatRequestContext(
            model_id=model_id,
            skill_slugs=list(skill_slugs),
            file_ids=list(file_ids),
            **settings.generation_settings(),
        )
        if target:
            await self._chat.add_regenerated_reply(
                session,
                conversation_id=conversation_id,
                run_id=run.id,
                target=target,
                skill_snapshots=snapshots,
                request_context=request_context,
            )
        else:
            await self._chat.add_user_message(
                session,
                conversation_id=conversation_id,
                run_id=run.id,
                content=message,
                skill_snapshots=snapshots,
                attachments=attachments,
                request_context=request_context,
            )
        await self._files.references.attach(session, principal, attachments, resource_type='chat', resource_id=run.id)
        return run

    @staticmethod
    def generation_binding(binding: ModelBinding, settings: ChatGenerationSettings) -> ModelBinding:
        if binding.model_type != 'chat' and (
            settings.system_prompt or settings.temperature is not None or settings.max_output_tokens is not None
        ):
            raise AppError('unsupported_generation_settings', '生成设置仅支持聊天模型', 422)
        if settings.max_output_tokens is not None and settings.max_output_tokens > binding.max_output_tokens:
            raise AppError('invalid_max_output_tokens', '输出长度不能超过当前模型上限', 422)
        return binding.model_copy(
            update={
                'max_output_tokens': (
                    settings.max_output_tokens if settings.max_output_tokens is not None else binding.max_output_tokens
                ),
                'options': binding.options.model_copy(
                    update={
                        'provider_output_default': settings.max_output_tokens is None,
                        **({'temperature': settings.temperature} if settings.temperature is not None else {}),
                    }
                ),
            }
        )

    @staticmethod
    def regeneration_fingerprint(conversation_id: UUID, message_id: UUID) -> str:
        return hashlib.sha256(
            json.dumps(['chat_regeneration', str(conversation_id), str(message_id)], separators=(',', ':')).encode()
        ).hexdigest()

    async def _regeneration_context(self, target: RegenerationTarget) -> ChatRequestContext:
        """重新生成只接受正式接纳时保存的选择，不猜测旧模型输入。"""
        try:
            return ChatRequestContext.model_validate(target.request_context)
        except ValidationError as error:
            raise AppError('regeneration_context_unavailable', '原请求选择无法完整恢复，请重新发送问题', 409) from error

    async def regenerate_chat(
        self,
        principal: Principal,
        conversation_id: UUID,
        message_id: UUID,
        *,
        client_request_id: UUID,
    ) -> RunSnapshot:
        value = self.regeneration_fingerprint(conversation_id, message_id)
        async with self.database.sessions.begin() as session:
            await lock_billing_admission(session)
            await self._runs.lock_request(session, principal.id, client_request_id)
            conversation = await self._chat.require_conversation(session, principal, conversation_id, lock=True)
            existing = await self._runs.existing_request(session, principal.id, client_request_id, value)
            if existing:
                return existing
            if conversation.archived:
                raise AppError('conversation_archived', '已归档对话不能重新生成', 409)
            target = await self._chat.regeneration_target(session, principal, conversation_id, message_id)
            if await self._runs.has_active(session, conversation_id):
                raise AppError('conversation_busy', '当前对话仍有运行中的请求', 409)
            selection = await self._regeneration_context(target)
            binding = await self._models.bindings.resolve(session, selection.model_id)
            self.generation_binding(binding, selection)
        attachments = await self.file_inputs.prepare_many(principal, selection.file_ids)
        async with self.database.sessions.begin() as session:
            await lock_billing_admission(session)
            return await self.admit_chat(
                session,
                principal,
                conversation_id=conversation_id,
                model_id=selection.model_id,
                message=target.question.content,
                client_request_id=client_request_id,
                skill_slugs=selection.skill_slugs,
                file_ids=selection.file_ids,
                prepared=PreparedChatInputs(binding, attachments, regeneration_message_id=message_id),
                **selection.generation_settings(),
            )

    async def _validate_attachments(
        self,
        session: AsyncSession,
        principal: Principal,
        conversation_id: UUID,
        binding: ModelBinding,
        attachments: tuple[PreparedAttachment, ...],
        *,
        target: RegenerationTarget | None = None,
    ) -> None:
        if target and [(item.summary.file_id, item.summary.sha256) for item in attachments] != [
            (item.file_id, item.sha256) for item in target.question.attachments
        ]:
            raise AppError('file_snapshot_changed', '原问题的附件已变化，无法重新生成', 409)
        for file_id in sorted({item.summary.file_id for item in attachments}):
            await self._files.require(session, principal, file_id, active=True, lock=True)
        if any(item.image_data is not None for item in attachments) and not binding.vision_enabled:
            raise AppError('unsupported_capability', '此模型未启用vision能力', 422)

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
        await lock_billing_admission(session)
        await self._chat.require_conversation(session, principal, conversation_id, lock=True)
        if await self._runs.has_active(session, conversation_id):
            raise AppError('conversation_busy', '当前对话仍有运行中的请求', 409)
        if remove:
            run_ids = await self._chat.reference_run_ids(session, principal, conversation_id)
            await self._files.references.release_resources(
                session, resource_type='chat', resource_ids=run_ids, actor_id=principal.id
            )
        if remove or archived is True:
            await self.context.cancel_conversation(session, principal, conversation_id)
        return await self._chat.edit_conversation(
            session,
            principal,
            conversation_id,
            expected_version=expected_version,
            title=title,
            archived=archived,
            remove=remove,
        )

    async def cancel(self, session: AsyncSession, principal: Principal, run_id: UUID) -> RunSnapshot:
        snapshot, release = await self._runs.cancel(session, run_id, principal.id)
        if release:
            await self._usage.settle(session, run_id, rejected_before_execution=True)
        await RunLifecycle(self._chat).project(session, snapshot)
        return snapshot

    @staticmethod
    def fingerprint(
        source: str,
        resource_id: UUID,
        model_id: UUID,
        message: str,
        skill_slugs: Sequence[str] = (),
        file_ids: Sequence[UUID] = (),
        *,
        system_prompt: str = '',
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> str:
        fields: list[object] = [source, str(resource_id), str(model_id), message]
        if skill_slugs:
            fields.append(list(skill_slugs))
        if file_ids:
            fields.append({'file_ids': [str(value) for value in file_ids]})
        if system_prompt or temperature is not None or max_output_tokens is not None:
            fields.append(
                {
                    'generation_settings': {
                        'system_prompt': system_prompt,
                        'temperature': temperature,
                        'max_output_tokens': max_output_tokens,
                    }
                }
            )
        value = json.dumps(
            fields,
            ensure_ascii=False,
            separators=(',', ':'),
        )
        return hashlib.sha256(value.encode()).hexdigest()
