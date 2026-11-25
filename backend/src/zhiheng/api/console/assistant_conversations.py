"""Private assistant session adapters; draft preview and public API remain independent."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from zhiheng.api.common.dependencies import ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.api.common.file_content import file_content_response
from zhiheng.application.agents.receipts import AssistantSubmissionReceipt
from zhiheng.modules.agents.public import AssistantConversationInfo
from zhiheng.modules.files.public import FileInfo
from zhiheng.modules.knowledge.public import PreparationInfo
from zhiheng.runtime.contracts import RunSnapshot

router = APIRouter(prefix='/api/agents/{agent_id}/conversations', tags=['agents'])


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    client_request_id: UUID


class ConversationMessage(ConversationCreate):
    message: str = Field(default='', max_length=16000)
    file_ids: list[UUID] = Field(default_factory=list)

    @field_validator('message')
    @classmethod
    def safe_message(cls, value: str) -> str:
        value.encode('utf-8')
        if '\x00' in value:
            raise ValueError('Invalid UTF-8 message')
        return value

    @model_validator(mode='after')
    def attachments(self) -> 'ConversationMessage':
        if len(set(self.file_ids)) != len(self.file_ids):
            raise ValueError('Duplicate attachment')
        if not self.message.strip() and not self.file_ids:
            raise ValueError('Message or attachments required')
        return self


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


@router.post('/attachments', status_code=201, openapi_extra={'x-transport-policy': 'binary_file'})
async def upload_attachment(
    agent_id: UUID,
    request: Request,
    container: ContainerDependency,
    principal: PrincipalDependency,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
    plaza: bool = False,
) -> FileInfo:
    return await container.assistant_conversations.upload_attachment(
        principal, agent_id, filename=filename, content=await request.body(), plaza=plaza
    )


@router.get('/{conversation_id}/attachments/{file_id}/download')
async def download_attachment(
    agent_id: UUID,
    conversation_id: UUID,
    file_id: UUID,
    request: Request,
    container: ContainerDependency,
    principal: PrincipalDependency,
) -> Response:
    info, content = await container.assistant_conversations.attachment_content(
        principal, agent_id, conversation_id, file_id
    )
    return await file_content_response(request, container, principal, info, content)


@router.get('')
async def list_conversations(
    agent_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    before: UUID | None = None,
    plaza: bool = False,
) -> list[AssistantConversationInfo]:
    return await container.assistant_conversations.list(principal, agent_id, before, plaza=plaza)


@router.post('', status_code=201)
async def create_conversation(
    agent_id: UUID,
    body: ConversationCreate,
    container: ContainerDependency,
    principal: PrincipalDependency,
    plaza: bool = False,
) -> AssistantConversationInfo:
    return await container.assistant_conversations.create(principal, agent_id, body.client_request_id, plaza=plaza)


@router.get('/submissions/{request_id}')
async def submission_receipt(
    agent_id: UUID,
    request_id: UUID,
    create_id: UUID,
    response: Response,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    conversation_id: UUID | None = None,
    plaza: bool = False,
) -> AssistantSubmissionReceipt:
    async with session.begin():
        receipt = await container.assistant_conversations.receipts.get(
            session,
            principal,
            agent_id=agent_id,
            request_id=request_id,
            create_id=create_id,
            conversation_id=conversation_id,
            plaza=plaza,
        )
    response.headers['Cache-Control'] = 'no-store'
    return receipt


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
        principal, agent_id, conversation_id, body.message, body.client_request_id, body.file_ids
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
