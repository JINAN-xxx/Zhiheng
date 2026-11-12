import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from zhiheng.application.execution.admission import RunAdmission
from zhiheng.application.execution.lifecycle import RunLifecycle
from zhiheng.application.files.inputs import MAX_NORMALIZED_IMAGE_BYTES, FileInputService
from zhiheng.application.knowledge.preparation import PreparationStart
from zhiheng.modules.billing.public import UsageService, lock_billing_admission
from zhiheng.modules.chat.public import (
    ChatGenerationSettings,
    ChatRequestContext,
    ChatService,
    ConversationInfo,
    RegenerationTarget,
    SkillSnapshot,
    source_fingerprint,
)
from zhiheng.modules.files.public import FileService, PreparedAttachment
from zhiheng.modules.knowledge.public import PreparationInfo, PreparedKnowledge, RetrievalState
from zhiheng.modules.knowledge.public import fingerprint as dependency_fingerprint
from zhiheng.modules.models.public import (
    ModelBinding,
    ModelImage,
    ModelMessage,
    ModelOptions,
    ModelRequest,
    ModelService,
    ModelTrialInput,
)
from zhiheng.modules.skills.public import resolve_selection
from zhiheng.runtime.contracts import RunSnapshot
from zhiheng.runtime.runs import RunStore
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal

if TYPE_CHECKING:
    from zhiheng.application.chat.context import ChatContextService
    from zhiheng.application.knowledge.preparation import KnowledgePreparation


@dataclass(frozen=True)
class PreparedChatInputs:
    binding: ModelBinding
    attachments: tuple[PreparedAttachment, ...]
    knowledge: PreparedKnowledge | None = None
    regeneration_message_id: UUID | None = None


class ChatRunService:
    def __init__(
        self,
        chat: ChatService,
        models: ModelService,
        usage: UsageService,
        runs: RunStore,
        files: FileService | None = None,
        *,
        admission: RunAdmission,
    ) -> None:
        self._chat, self._models, self._usage, self._runs = chat, models, usage, runs
        self._files, self._admission = files, admission
        self.knowledge: KnowledgePreparation | None = None
        self.file_inputs: FileInputService | None = None
        self.context: ChatContextService | None = None

    async def admit_chat(  # noqa: PLR0912, PLR0913, PLR0915 - one transaction owns the complete chat admission contract
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
        system_prompt: str = '',
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> RunSnapshot:
        await lock_billing_admission(session)
        settings = ChatGenerationSettings(
            system_prompt=system_prompt, temperature=temperature, max_output_tokens=max_output_tokens
        )
        regeneration_message_id = prepared.regeneration_message_id if prepared else None
        knowledge_base_ids = (
            [base.base_id for base in prepared.knowledge.bases] if prepared and prepared.knowledge else []
        )
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
                knowledge_base_ids,
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
        knowledge = prepared.knowledge if prepared else None
        if self.knowledge:
            await self.knowledge.store.ensure_idle(
                session, 'chat', conversation_id, knowledge.intent_id if knowledge else None
            )
        if await self._runs.has_active(session, conversation_id):
            raise AppError('conversation_busy', '当前对话仍有运行中的请求', 409)
        binding = await self._models.resolve(session, model_id)
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
        if (
            target
            and self.context is not None
            and [item.model_dump(exclude={'score'}) for item in target.question.knowledge_sources]
            != [item.model_dump(exclude={'score'}) for item in (knowledge.hits if knowledge else ())]
        ):
            raise AppError('regeneration_sources_changed', '原知识来源已变化，请重新发送问题后使用当前资料', 409)
        context = (
            await self._chat.context(
                session, principal, conversation_id, exclude_question_id=target.question.id if target else None
            )
            if self.context is None
            else ()
        )
        extra = ''.join(
            f'\n\n[Attachment: {item.summary.original_filename}]\n{item.text}'
            for item in attachments
            if item.image_data is None
        )
        images = tuple(ModelImage(item.image_data) for item in attachments if item.image_data is not None)
        messages = (
            *skill_messages,
            *context,
            ModelMessage('user', message + extra, images),
        )
        messages = knowledge.apply_context(messages) if knowledge else messages
        system_messages = (ModelMessage('system', settings.system_prompt),) if settings.system_prompt else ()
        messages = (*system_messages, *messages)
        prepared_context = None
        if self.context is not None:
            prepared_context = await self.context.prepare(
                session,
                principal,
                conversation_id,
                binding,
                required=tuple(messages[:-1]),
                current=messages[-1],
                exclude_question_id=target.question.id if target else None,
                original_run_id=target.question.run_id if target else None,
            )
            messages = prepared_context.package.messages
            if target:
                snapshots = list(target.question.skill_snapshots)
                if [item.slug for item in snapshots] != list(skill_slugs):
                    raise AppError('regeneration_context_unavailable', '原技能选择无法完整恢复，请重新发送问题', 409)
                await self.context.validate_frozen_skills(session, principal, snapshots, messages)
        if any(item.images for item in messages) and not binding.vision_enabled:
            raise AppError('unsupported_capability', '此模型未启用vision能力', 422)
        request = ModelRequest(binding=binding, messages=messages)
        if knowledge and self.knowledge:
            await self.knowledge.check(session, knowledge, principal)
        image_bytes = sum(len(image.data_url.encode()) for item in request.messages for image in item.images)
        if image_bytes > MAX_NORMALIZED_IMAGE_BYTES:
            raise AppError('attachment_images_too_large', '本次请求含历史的安全图片数据合计最多16MiB', 422)
        run = await self._admission.reserve(
            session,
            principal,
            resource_type='chat',
            resource_id=conversation_id,
            client_request_id=client_request_id,
            fingerprint=fingerprint,
            request=request,
            token_limit=token_limit,
        )
        if prepared_context is not None:
            snapshot = prepared_context.snapshot
            current_refs: list[dict[str, object]] = [
                {'file_id': str(item.summary.file_id), 'sha256': item.summary.sha256, 'question_id': 'current'}
                for item in attachments
            ]
            assert self.context is not None
            await self.context.memory.save_snapshot(session, run.id, snapshot, current_refs)
        request_context = ChatRequestContext(
            model_id=model_id,
            skill_slugs=list(skill_slugs),
            file_ids=list(file_ids),
            knowledge_base_ids=knowledge_base_ids,
            **settings.generation_settings(),
        )
        if target:
            await self._chat.add_regenerated_reply(
                session,
                conversation_id=conversation_id,
                run_id=run.id,
                target=target,
                skill_snapshots=snapshots,
                knowledge_sources=knowledge.hits if knowledge else (),
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
                knowledge_sources=knowledge.hits if knowledge else (),
                request_context=request_context,
            )
        if self._files is not None:
            await self._files.attach(session, principal, attachments, resource_type='chat', resource_id=run.id)
        if knowledge and self.knowledge:
            await self.knowledge.bind(session, knowledge, principal, run.id)
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
        return dependency_fingerprint(['chat_regeneration', str(conversation_id), str(message_id)])

    async def _regeneration_context(
        self, session: AsyncSession, conversation_id: UUID, target: RegenerationTarget
    ) -> ChatRequestContext:
        if target.request_context:
            try:
                return ChatRequestContext.model_validate(target.request_context)
            except ValidationError as error:
                raise AppError(
                    'regeneration_context_unavailable', '原请求选择无法完整恢复，请重新发送问题', 409
                ) from error
        try:
            request = ModelRequest.model_validate(
                await self._runs.resource_payload(session, target.question.run_id, 'chat', conversation_id)
            )
        except (ValidationError, AppError) as error:
            if isinstance(error, AppError) and error.code != 'run_not_found':
                raise
            raise AppError('regeneration_context_unavailable', '原请求快照无法完整恢复，请重新发送问题', 409) from error
        # Before request_context existed, retrieval added an extra system message and
        # wrapped the current question. Hits cannot reconstruct bases with no hits.
        systems = [message for message in request.messages if message.role == 'system']
        if (
            target.question.knowledge_sources
            or len(systems) != len(target.question.skill_snapshots)
            or not request.messages
            or request.messages[-1].content.startswith('[知识资料]\n')
        ):
            raise AppError('regeneration_context_unavailable', '原知识库选择无法完整恢复，请重新发送问题', 409)
        return ChatRequestContext(
            model_id=request.binding.model_id,
            skill_slugs=[skill.slug for skill in target.question.skill_snapshots],
            file_ids=[attachment.file_id for attachment in target.question.attachments],
        )

    async def regenerate_chat(
        self,
        principal: Principal,
        conversation_id: UUID,
        message_id: UUID,
        *,
        client_request_id: UUID,
        token_limit: int,
    ) -> RunSnapshot | PreparationInfo:
        assert self.knowledge is not None and self.file_inputs is not None
        value = self.regeneration_fingerprint(conversation_id, message_id)
        async with self.knowledge.database.sessions.begin() as session:
            await lock_billing_admission(session)
            await self._runs.lock_request(session, principal.id, client_request_id)
            conversation = await self._chat.require_conversation(session, principal, conversation_id, lock=True)
            existing = await self._runs.existing_request(session, principal.id, client_request_id, value)
            if existing:
                return existing
            intent = await self.knowledge.store.existing(session, principal.id, client_request_id, value)
            if intent:
                return await self.knowledge.info(session, principal, intent.id)
            if conversation.archived:
                raise AppError('conversation_archived', '已归档对话不能重新生成', 409)
            target = await self._chat.regeneration_target(session, principal, conversation_id, message_id)
            await self.knowledge.store.ensure_idle(session, 'chat', conversation_id)
            if await self._runs.has_active(session, conversation_id):
                raise AppError('conversation_busy', '当前对话仍有运行中的请求', 409)
            selection = await self._regeneration_context(session, conversation_id, target)
            if not selection.knowledge_base_ids:
                binding = await self._models.resolve(session, selection.model_id)
                self.generation_binding(binding, selection)
        if selection.knowledge_base_ids:
            return await self.prepare_knowledge_chat(
                principal,
                conversation_id,
                model_id=selection.model_id,
                message=target.question.content,
                client_request_id=client_request_id,
                skill_slugs=selection.skill_slugs,
                file_ids=selection.file_ids,
                knowledge_base_ids=selection.knowledge_base_ids,
                regeneration_message_id=message_id,
                **selection.generation_settings(),
            )
        attachments = await self.file_inputs.prepare_many(principal, selection.file_ids)
        async with self.knowledge.database.sessions.begin() as session:
            await lock_billing_admission(session)
            return await self.admit_chat(
                session,
                principal,
                conversation_id=conversation_id,
                model_id=selection.model_id,
                message=target.question.content,
                client_request_id=client_request_id,
                token_limit=token_limit,
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
        history = (
            await self._chat.attachment_history(
                session,
                principal,
                conversation_id,
                exclude_question_id=target.question.id if target else None,
            )
            if self.context is None
            else ()
        )
        if self._files is not None:
            for file_id in sorted({item.summary.file_id for item in (*history, *attachments)}):
                await self._files.require(session, principal, file_id, active=True, lock=True)
            for item in history:
                current = await self._files.get(session, principal, item.summary.file_id)
                if current.sha256 != item.summary.sha256:
                    raise AppError('file_snapshot_changed', '历史附件已变化', 409)
        if any(item.image_data is not None for item in (*history, *attachments)) and not binding.vision_enabled:
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
        if self.knowledge:
            await self.knowledge.store.ensure_idle(session, 'chat', conversation_id)
        if await self._runs.has_active(session, conversation_id):
            raise AppError('conversation_busy', '当前对话仍有运行中的请求', 409)
        if remove and self._files is not None:
            run_ids = await self._chat.reference_run_ids(session, principal, conversation_id)
            await self._files.release_resources(
                session, resource_type='chat', resource_ids=run_ids, actor_id=principal.id
            )
            await self._files.release_resources(
                session, resource_type='knowledge_run', resource_ids=run_ids, actor_id=principal.id
            )
        if self.context is not None and (remove or archived is True):
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

    async def admit_test(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        model_id: UUID,
        client_request_id: UUID,
        token_limit: int,
        trial: ModelTrialInput | None = None,
    ) -> RunSnapshot:
        await lock_billing_admission(session)
        principal.require_permission('ai:models:test')
        fingerprint = (
            self.fingerprint('model_trial', model_id, model_id, trial.model_dump_json())
            if trial is not None
            else self.fingerprint('connection_test', model_id, model_id, '')
        )
        await self._runs.lock_request(session, principal.id, client_request_id)
        existing = await self._runs.existing_request(session, principal.id, client_request_id, fingerprint)
        if existing is not None:
            return existing
        binding = await self._models.resolve_for_test(session, principal, model_id)
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

    @staticmethod
    def fingerprint(
        source: str,
        resource_id: UUID,
        model_id: UUID,
        message: str,
        skill_slugs: Sequence[str] = (),
        file_ids: Sequence[UUID] = (),
        knowledge_base_ids: Sequence[UUID] = (),
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
        if knowledge_base_ids:
            fields.append({'knowledge_base_ids': [str(value) for value in knowledge_base_ids]})
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

    async def prepare_knowledge_chat(  # noqa: PLR0913 - persists the same chat admission contract
        self,
        principal: Principal,
        conversation_id: UUID,
        *,
        model_id: UUID,
        message: str,
        client_request_id: UUID,
        skill_slugs: Sequence[str],
        file_ids: Sequence[UUID],
        knowledge_base_ids: Sequence[UUID],
        regeneration_message_id: UUID | None = None,
        system_prompt: str = '',
        temperature: float | None = None,
        max_output_tokens: int | None = None,
    ) -> RunSnapshot | PreparationInfo:
        assert self.knowledge is not None and self.file_inputs is not None
        settings = ChatGenerationSettings(
            system_prompt=system_prompt, temperature=temperature, max_output_tokens=max_output_tokens
        )
        prep = self.knowledge
        value = (
            self.regeneration_fingerprint(conversation_id, regeneration_message_id)
            if regeneration_message_id
            else self.fingerprint(
                'chat',
                conversation_id,
                model_id,
                message,
                skill_slugs,
                file_ids,
                knowledge_base_ids,
                **settings.generation_settings(),
            )
        )
        async with prep.database.sessions.begin() as session:
            await lock_billing_admission(session)
            await self._runs.lock_request(session, principal.id, client_request_id)
            await self._chat.require_conversation(session, principal, conversation_id, lock=True)
            existing = await self._runs.existing_request(session, principal.id, client_request_id, value)
            if existing:
                return existing
            intent = await prep.store.existing(session, principal.id, client_request_id, value)
            if intent:
                return prep.store.info(intent)
            await prep.store.ensure_idle(session, 'chat', conversation_id)
            if await self._runs.has_active(session, conversation_id):
                raise AppError('conversation_busy', '当前对话仍有运行中的请求', 409)
            binding = await prep.binding_for(session, principal, model_id, list(knowledge_base_ids))
            self.generation_binding(binding, settings)
            dependencies = await self._knowledge_dependencies(
                session, principal, conversation_id, skill_slugs, binding, regeneration_message_id
            )
        attachments = await self.file_inputs.prepare_many(principal, list(file_ids))
        request: dict[str, object] = {
            'query': message,
            'model_id': str(model_id),
            'message': message,
            'client_request_id': str(client_request_id),
            'skill_slugs': list(skill_slugs),
            'file_ids': [str(item) for item in file_ids],
            'knowledge_base_ids': [str(item) for item in knowledge_base_ids],
            'attachments': [item.model_dump(mode='json') for item in attachments],
            **settings.generation_settings(),
        }
        if regeneration_message_id:
            request['regeneration_message_id'] = str(regeneration_message_id)
        async with prep.database.sessions.begin() as session:
            await lock_billing_admission(session)
            await self._runs.lock_request(session, principal.id, client_request_id)
            await self._chat.require_conversation(session, principal, conversation_id, lock=True)
            existing = await self._runs.existing_request(session, principal.id, client_request_id, value)
            if existing:
                return existing
            intent = await prep.store.existing(session, principal.id, client_request_id, value)
            if intent:
                return prep.store.info(intent)
            await prep.store.ensure_idle(session, 'chat', conversation_id)
            if await self._runs.has_active(session, conversation_id):
                raise AppError('conversation_busy', '当前对话仍有运行中的请求', 409)
            current = await self._knowledge_dependencies(
                session,
                principal,
                conversation_id,
                skill_slugs,
                await prep.binding_for(session, principal, model_id, list(knowledge_base_ids)),
                regeneration_message_id,
            )
            if dependencies != current:
                raise AppError('knowledge_preparation_stale', '会话或模型依赖已变化', 409)
            return await prep.start(
                session,
                principal=principal,
                body=PreparationStart(
                    owner_id=principal.id,
                    resource_type='chat',
                    resource_id=conversation_id,
                    request_id=client_request_id,
                    value=value,
                    request=request,
                    base_ids=list(knowledge_base_ids),
                    dependencies=dependencies,
                ),
            )

    async def _knowledge_dependencies(
        self,
        session: AsyncSession,
        principal: Principal,
        conversation_id: UUID,
        skill_slugs: Sequence[str],
        binding: ModelBinding,
        regeneration_message_id: UUID | None = None,
    ) -> dict[str, object]:
        conversation = await self._chat.require_conversation(session, principal, conversation_id)
        selection = await resolve_selection(session, principal.id, skill_slugs)
        target = (
            await self._chat.regeneration_target(session, principal, conversation_id, regeneration_message_id)
            if regeneration_message_id
            else None
        )
        if self.context is not None:
            turns = await self._chat.context_turns(
                session,
                principal,
                conversation_id,
                exclude_question_id=target.question.id if target else None,
                load_inputs=False,
            )
            context_hash = source_fingerprint(turns)
        else:
            context = await self._chat.context(
                session,
                principal,
                conversation_id,
                exclude_question_id=target.question.id if target else None,
            )
            context_hash = dependency_fingerprint([asdict(item) for item in context])
        return {
            'conversation_version': conversation.version,
            'skills': [
                SkillSnapshot(
                    slug=item.slug, name=item.name, revision_id=item.revision_id, content_hash=item.content_hash
                ).model_dump(mode='json')
                for item in selection.revisions
            ],
            'context_hash': context_hash,
            'main_request': ModelRequest(binding=binding, messages=()).model_dump(mode='json'),
        }

    async def finalize_knowledge(
        self, session: AsyncSession, state: RetrievalState, knowledge: PreparedKnowledge
    ) -> RunSnapshot:
        assert self.knowledge is not None
        principal = await self.knowledge.knowledge.identity.principal_for_user(session, state.actor_id)
        request = state.request
        settings = ChatGenerationSettings.model_validate(
            {key: request[key] for key in ('system_prompt', 'temperature', 'max_output_tokens') if key in request}
        )
        binding = ModelRequest.model_validate(state.dependencies['main_request']).binding
        current = await self._knowledge_dependencies(
            session,
            principal,
            state.resource_id,
            request['skill_slugs'],
            binding,
            UUID(request['regeneration_message_id']) if request.get('regeneration_message_id') else None,
        )
        if any(current[key] != state.dependencies[key] for key in current):
            raise AppError('knowledge_preparation_stale', '会话或技能已变化', 409)
        attachments = tuple(PreparedAttachment.model_validate(item) for item in request['attachments'])
        return await self.admit_chat(
            session,
            principal,
            conversation_id=state.resource_id,
            model_id=UUID(request['model_id']),
            message=request['message'],
            client_request_id=UUID(request['client_request_id']),
            token_limit=self.knowledge.token_limit,
            skill_slugs=request['skill_slugs'],
            file_ids=[UUID(item) for item in request['file_ids']],
            prepared=PreparedChatInputs(
                binding,
                attachments,
                knowledge,
                UUID(request['regeneration_message_id']) if request.get('regeneration_message_id') else None,
            ),
            **settings.generation_settings(),
        )

    async def check_knowledge_dependencies(self, session: AsyncSession, state: RetrievalState) -> None:
        assert self.knowledge is not None
        principal = await self.knowledge.knowledge.identity.principal_for_user(session, state.actor_id)
        binding = ModelRequest.model_validate(state.dependencies['main_request']).binding
        current = await self._knowledge_dependencies(
            session,
            principal,
            state.resource_id,
            state.request['skill_slugs'],
            binding,
            UUID(state.request['regeneration_message_id']) if state.request.get('regeneration_message_id') else None,
        )
        if any(current[key] != state.dependencies[key] for key in current):
            raise AppError('knowledge_preparation_stale', '会话或技能依赖已变化', 409)
