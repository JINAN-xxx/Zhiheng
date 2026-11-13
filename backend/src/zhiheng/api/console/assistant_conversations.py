"""Private assistant session adapters; draft preview and public API remain independent."""

from uuid import UUID

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from zhiheng.api.common.dependencies import ContainerDependency, PrincipalDependency
from zhiheng.modules.agents.public import AssistantConversationInfo
from zhiheng.modules.knowledge.public import PreparationInfo
from zhiheng.runtime.contracts import RunSnapshot

router = APIRouter(prefix='/api/agents/{agent_id}/conversations', tags=['agents'])


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    client_request_id: UUID


class ConversationMessage(ConversationCreate):
    message: str = Field(min_length=1, max_length=16000)

    @field_validator('message')
    @classmethod
    def safe_message(cls, value: str) -> str:
        value.encode('utf-8')
        if not value.strip() or '\x00' in value:
            raise ValueError('A nonempty UTF-8 message is required')
        return value


class ConversationVersion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(strict=True, ge=1)


class ConversationRename(ConversationVersion):
    title: str = Field(min_length=1, max_length=100)

    @field_validator('title')
    @classmethod
    def safe_title(cls, value: str) -> str:
        value = value.strip()
        value.encode('utf-8')
        if not value or '\x00' in value:
            raise ValueError('A nonempty UTF-8 title is required')
        return value


@router.get('')
async def list_conversations(
    agent_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    before: UUID | None = None,
) -> list[AssistantConversationInfo]:
    return await container.assistant_conversations.list(principal, agent_id, before)


@router.post('', status_code=201)
async def create_conversation(
    agent_id: UUID,
    body: ConversationCreate,
    container: ContainerDependency,
    principal: PrincipalDependency,
) -> AssistantConversationInfo:
    return await container.assistant_conversations.create(principal, agent_id, body.client_request_id)


@router.get('/{conversation_id}')
async def conversation_detail(
    agent_id: UUID,
    conversation_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    before: int | None = None,
) -> dict[str, object]:
    return await container.assistant_conversations.detail(principal, agent_id, conversation_id, before)


@router.post('/{conversation_id}/runs', status_code=202)
async def send_message(
    agent_id: UUID,
    conversation_id: UUID,
    body: ConversationMessage,
    container: ContainerDependency,
    principal: PrincipalDependency,
) -> RunSnapshot | PreparationInfo:
    return await container.assistant_conversations.send(
        principal, agent_id, conversation_id, body.message, body.client_request_id
    )


@router.patch('/{conversation_id}')
async def rename_conversation(
    agent_id: UUID,
    conversation_id: UUID,
    body: ConversationRename,
    container: ContainerDependency,
    principal: PrincipalDependency,
) -> AssistantConversationInfo:
    return await container.assistant_conversations.change(
        principal, agent_id, conversation_id, body.expected_version, title=body.title
    )


@router.delete('/{conversation_id}', status_code=204)
async def delete_conversation(
    agent_id: UUID,
    conversation_id: UUID,
    body: ConversationVersion,
    container: ContainerDependency,
    principal: PrincipalDependency,
) -> Response:
    await container.assistant_conversations.change(
        principal, agent_id, conversation_id, body.expected_version, remove=True
    )
    return Response(status_code=204)
