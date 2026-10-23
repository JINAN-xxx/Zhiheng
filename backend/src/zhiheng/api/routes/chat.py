from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from zhiheng.api.dependencies import ContainerDependency, PrincipalDependency, SessionDependency
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
    skill_slugs: list[SkillSlug] = Field(default_factory=list, max_length=5)


@router.get('')
async def conversations(
    container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> list[ConversationInfo]:
    return await container.chat.list_conversations(session, principal)


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
        return await container.admission.admit_chat(
            session,
            principal,
            conversation_id=conversation_id,
            model_id=body.model_id,
            message=body.message,
            skill_slugs=body.skill_slugs,
            client_request_id=body.client_request_id,
            token_limit=container.settings.token_limit,
        )
