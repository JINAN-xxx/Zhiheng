from uuid import UUID

from fastapi import APIRouter, Response, status
from fastapi.responses import JSONResponse

from zhiheng.api.dependencies import ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.modules.chat.public import (
    CreatedShareInfo,
    FeedbackInfo,
    FeedbackRequest,
    PublicSharedMessage,
    ShareCreateRequest,
    ShareInfo,
    SharePreview,
)
from zhiheng.shared.errors import AppError

router = APIRouter(prefix='/api/chat', tags=['message actions'])
public_router = APIRouter(prefix='/api/shared/messages', tags=['shared messages'])
PUBLIC_SHARE_HEADERS = {'Cache-Control': 'no-store', 'X-Robots-Tag': 'noindex'}


@router.put('/messages/{message_id}/feedback')
async def set_feedback(
    message_id: UUID,
    body: FeedbackRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> FeedbackInfo:
    async with session.begin():
        return await container.message_actions.set_feedback(session, principal, message_id, value=body.value)


@router.get('/conversations/{conversation_id}/feedback')
async def conversation_feedback(
    conversation_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> list[FeedbackInfo]:
    return await container.message_actions.conversation_feedback(session, principal, conversation_id)


@router.get('/messages/{message_id}/share-preview')
async def share_preview(
    message_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
    include_question: bool = True,
) -> SharePreview:
    async with session.begin():
        return await container.message_actions.share_preview(
            session, principal, message_id, include_question=include_question
        )


@router.post('/messages/{message_id}/shares', status_code=201)
async def create_share(
    message_id: UUID,
    body: ShareCreateRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> CreatedShareInfo:
    async with session.begin():
        return await container.message_actions.create_share(
            session,
            principal,
            message_id,
            include_question=body.include_question,
            expires_in_days=body.expires_in_days,
            expected_content_hash=body.expected_content_hash,
        )


@router.get('/messages/{message_id}/shares')
async def list_shares(
    message_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> list[ShareInfo]:
    async with session.begin():
        return await container.message_actions.list_shares(session, principal, message_id)


@router.delete('/shares/{share_id}', status_code=204)
async def revoke_share(
    share_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> Response:
    async with session.begin():
        await container.message_actions.revoke_share(session, principal, share_id)
    return Response(status_code=204)


@public_router.get('/{token:path}', response_model=PublicSharedMessage)
async def read_shared(token: str, container: ContainerDependency, session: SessionDependency) -> JSONResponse:
    try:
        async with session.begin():
            value = await container.message_actions.read_shared(session, token=token)
    except AppError as error:
        if error.status_code != status.HTTP_404_NOT_FOUND:
            raise
        return JSONResponse(
            {'error': {'code': error.code, 'message': error.message}},
            status_code=404,
            headers=PUBLIC_SHARE_HEADERS,
        )
    return JSONResponse(value.model_dump(mode='json'), headers=PUBLIC_SHARE_HEADERS)
