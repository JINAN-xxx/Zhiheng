import hashlib
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from zhiheng.api.common.dependencies import ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.api.common.pagination import MAX_CURSOR_LENGTH, decode_cursor, encode_cursor
from zhiheng.application.chat.runs import PreparedChatInputs
from zhiheng.modules.chat.public import ChatContextInfo, ChatGenerationSettings, ConversationInfo, MessageInfo
from zhiheng.modules.knowledge.public import PreparationInfo
from zhiheng.modules.skills.public import SkillSlug
from zhiheng.runtime.contracts import RunSnapshot
from zhiheng.shared.errors import AppError

router = APIRouter(prefix='/api/chat/conversations', tags=['chat'])
HISTORY_HEADERS = {
    'X-Next-Cursor': {
        'description': '下一页游标；空字符串表示已到末页。游标仅适用于当前用户与筛选范围。',
        'schema': {'type': 'string'},
    }
}


class ConversationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(default='新对话', min_length=1, max_length=100)


class ChatRequest(ChatGenerationSettings):
    model_config = ConfigDict(extra='forbid')
    model_id: UUID
    message: str = Field(min_length=1, max_length=16000)
    client_request_id: UUID
    file_ids: list[UUID] = Field(default_factory=list)
    knowledge_base_ids: list[UUID] = Field(default_factory=list, max_length=20)

    @field_validator('message')
    @classmethod
    def encodable_message(cls, value: str) -> str:
        value.encode('utf-8')
        if '\x00' in value:
            raise ValueError('Invalid message')
        return value

    @field_validator('file_ids')
    @classmethod
    def unique_files(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError('Duplicate attachment')
        return value

    @field_validator('knowledge_base_ids')
    @classmethod
    def unique_knowledge(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError('Duplicate knowledge bases')
        return value

    skill_slugs: list[SkillSlug] = Field(default_factory=list, max_length=5)


class RegenerateRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    client_request_id: UUID


@router.get('', responses={200: {'headers': HISTORY_HEADERS}})
async def conversations(
    response: Response,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    archived: bool = False,
    search: Annotated[str, Query(max_length=200)] = '',
    cursor: Annotated[str | None, Query(max_length=MAX_CURSOR_LENGTH)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> list[ConversationInfo]:
    search = search.strip()
    if '\x00' in search:
        raise AppError('invalid_search', '搜索内容无效', 422)
    binding = hashlib.sha256(f'chat:{principal.id}:{archived}:{search}'.encode()).hexdigest()
    values = await container.chat.list_conversations(
        session,
        principal,
        archived=archived,
        search=search,
        after=decode_cursor(cursor, binding),
        limit=limit + 1,
    )
    more = len(values) > limit
    values = values[:limit]
    response.headers['X-Next-Cursor'] = (
        encode_cursor(binding, values[-1].last_activity_at or values[-1].created_at, values[-1].id) if more else ''
    )
    for value in values:
        value.preparation_id = await container.knowledge_preparation.store.current(session, 'chat', value.id)
    return values


@router.post('', status_code=201)
async def create_conversation(
    body: ConversationRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> ConversationInfo:
    async with session.begin():
        return await container.chat.create(session, principal, body.title)


@router.get('/{conversation_id}/messages', responses={200: {'headers': HISTORY_HEADERS}})
async def messages(
    conversation_id: UUID,
    response: Response,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    cursor: Annotated[str | None, Query(max_length=MAX_CURSOR_LENGTH)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
) -> list[MessageInfo]:
    binding = f'chat-messages:{principal.id}:{conversation_id}'
    page = await container.chat.message_history(
        session, principal, conversation_id, before=decode_cursor(cursor, binding), limit=limit
    )
    values = page.items
    response.headers['X-Next-Cursor'] = encode_cursor(binding, *page.next_before) if page.next_before else ''
    feedback = await container.message_actions.feedback_for_messages(
        session, principal, conversation_id=conversation_id, message_ids=[value.id for value in values]
    )
    for value in values:
        value.feedback = feedback.get(value.id)
    for value in values:
        value.knowledge_sources = await container.knowledge_preparation.visible_hits(
            session, principal, value.knowledge_sources
        )
    return values


@router.get('/{conversation_id}/context')
async def context_info(
    conversation_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> ChatContextInfo:
    async with session.begin():
        return await container.chat_context.info(session, principal, conversation_id)


@router.post('/{conversation_id}/context/retry', status_code=202)
async def retry_context(
    conversation_id: UUID,
    body: RegenerateRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> ChatContextInfo:
    async with session.begin():
        return await container.chat_context.retry(session, principal, conversation_id, body.client_request_id)


@router.post('/{conversation_id}/runs', status_code=202)
async def create_run(
    conversation_id: UUID,
    body: ChatRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> RunSnapshot | PreparationInfo:
    if body.knowledge_base_ids:
        return await container.admission.prepare_knowledge_chat(principal, conversation_id, **body.model_dump())
    async with session.begin():
        await container.chat.require_conversation(session, principal, conversation_id)
        fingerprint = container.admission.fingerprint(
            'chat',
            conversation_id,
            body.model_id,
            body.message,
            body.skill_slugs,
            body.file_ids,
            **body.generation_settings(),
        )
        existing = await container.runs.existing_request(session, principal.id, body.client_request_id, fingerprint)
        if existing is not None:
            return existing
        binding = await container.models.resolve(session, body.model_id)
        container.admission.generation_binding(binding, body)
    attachments = await container.file_inputs.prepare_many(principal, body.file_ids)
    async with session.begin():
        return await container.admission.admit_chat(
            session,
            principal,
            conversation_id=conversation_id,
            model_id=body.model_id,
            message=body.message,
            skill_slugs=body.skill_slugs,
            file_ids=body.file_ids,
            prepared=PreparedChatInputs(binding, attachments),
            client_request_id=body.client_request_id,
            token_limit=container.settings.token_limit,
            **body.generation_settings(),
        )


@router.post('/{conversation_id}/messages/{message_id}/regenerate', status_code=202)
async def regenerate_message(
    conversation_id: UUID,
    message_id: UUID,
    body: RegenerateRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
) -> RunSnapshot | PreparationInfo:
    return await container.admission.regenerate_chat(
        principal,
        conversation_id,
        message_id,
        client_request_id=body.client_request_id,
        token_limit=container.settings.token_limit,
    )


class ConversationVersion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(strict=True, ge=1)


class ConversationPatch(ConversationVersion):
    title: str | None = Field(default=None, min_length=1, max_length=100)
    archived: bool | None = Field(default=None, strict=True)

    @model_validator(mode='after')
    def has_change(self) -> 'ConversationPatch':
        if self.title is None and self.archived is None:
            raise ValueError('Provide title or archived')
        return self


@router.get('/{conversation_id}')
async def conversation_detail(
    conversation_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> ConversationInfo:
    value = await container.chat.require_conversation(session, principal, conversation_id)
    preparation_id = await container.knowledge_preparation.store.current(session, 'chat', conversation_id)
    return value.model_copy(update={'preparation_id': preparation_id})


@router.patch('/{conversation_id}')
async def edit_conversation(
    conversation_id: UUID,
    body: ConversationPatch,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> ConversationInfo:
    async with session.begin():
        return await container.admission.edit_conversation(session, principal, conversation_id, **body.model_dump())


@router.delete('/{conversation_id}', status_code=204)
async def delete_conversation(
    conversation_id: UUID,
    body: ConversationVersion,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> Response:
    async with session.begin():
        await container.admission.edit_conversation(
            session, principal, conversation_id, expected_version=body.expected_version, remove=True
        )
    return Response(status_code=204)
