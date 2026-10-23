from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from zhiheng.api.dependencies import ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.application.chat_runs import PreparedChatInputs
from zhiheng.modules.chat.public import ConversationInfo, MessageInfo
from zhiheng.modules.skills.public import SkillSlug
from zhiheng.runtime.contracts import RunSnapshot

router = APIRouter(prefix='/api/chat/conversations', tags=['chat'])


class ConversationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(default='新对话', min_length=1, max_length=100)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model_id: UUID
    message: str = Field(min_length=1, max_length=16000)
    client_request_id: UUID
    file_ids: list[UUID] = Field(default_factory=list, max_length=15)

    @field_validator('file_ids')
    @classmethod
    def unique_files(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError('Duplicate attachment')
        return value

    skill_slugs: list[SkillSlug] = Field(default_factory=list, max_length=5)


@router.get('')
async def conversations(
    container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency, archived: bool = False
) -> list[ConversationInfo]:
    return await container.chat.list_conversations(session, principal, archived=archived)


@router.post('', status_code=201)
async def create_conversation(
    body: ConversationRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> ConversationInfo:
    async with session.begin():
        return await container.chat.create(session, principal, body.title)


@router.get('/{conversation_id}/messages')
async def messages(
    conversation_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> list[MessageInfo]:
    return await container.chat.messages(session, principal, conversation_id)


@router.post('/{conversation_id}/runs', status_code=202)
async def create_run(
    conversation_id: UUID,
    body: ChatRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> RunSnapshot:
    async with session.begin():
        await container.chat.require_conversation(session, principal, conversation_id)
        fingerprint = container.admission.fingerprint(
            'chat', conversation_id, body.model_id, body.message, body.skill_slugs, body.file_ids
        )
        existing = await container.runs.existing_request(session, principal.id, body.client_request_id, fingerprint)
        if existing is not None:
            return existing
        binding = await container.models.resolve(session, body.model_id)
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
    return await container.chat.require_conversation(session, principal, conversation_id)


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
