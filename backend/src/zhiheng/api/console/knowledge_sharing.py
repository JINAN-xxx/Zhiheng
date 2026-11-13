from uuid import UUID

from fastapi import APIRouter, Depends, Response

from zhiheng.api.common.contracts import EmptyRequest
from zhiheng.api.common.dependencies import ContainerDependency, PrincipalDependency, SessionDependency
from zhiheng.modules.knowledge.public import (
    BaseAclInfo,
    CreatedInvitation,
    InvitationAccepted,
    InvitationCreate,
    InvitationInfo,
    InvitationPreview,
    InvitationToken,
    MemberRemoval,
    SharingMember,
)


def private_response(response: Response) -> None:
    response.headers['Cache-Control'] = 'private, no-store'


router = APIRouter(prefix='/api/knowledge', tags=['Knowledge sharing'], dependencies=[Depends(private_response)])


@router.post('/bases/{base_id}/invitations', status_code=201)
async def create(
    base_id: UUID,
    body: InvitationCreate,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> CreatedInvitation:
    async with session.begin():
        return await container.knowledge_sharing.create(session, principal, base_id, body)


@router.get('/bases/{base_id}/invitations')
async def invitations(
    base_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> list[InvitationInfo]:
    async with session.begin():
        return await container.knowledge_sharing.invitations(session, principal, base_id)


@router.post('/bases/{base_id}/invitations/{invitation_id}/revoke')
async def revoke(
    base_id: UUID,
    invitation_id: UUID,
    body: EmptyRequest,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> InvitationInfo:
    async with session.begin():
        return await container.knowledge_sharing.revoke(session, principal, base_id, invitation_id)


@router.get('/bases/{base_id}/invitations/{invitation_id}/link')
async def invitation_link(
    base_id: UUID,
    invitation_id: UUID,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> CreatedInvitation:
    async with session.begin():
        return await container.knowledge_sharing.link(session, principal, base_id, invitation_id)


@router.get('/bases/{base_id}/members')
async def members(
    base_id: UUID, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> list[SharingMember]:
    async with session.begin():
        return await container.knowledge_sharing.members(session, principal, base_id)


@router.delete('/bases/{base_id}/members/{user_id}', status_code=204)
async def remove(
    base_id: UUID,
    user_id: UUID,
    body: MemberRemoval,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> Response:
    async with session.begin():
        await container.knowledge_sharing.remove_member(session, principal, base_id, user_id, body)
    return Response(status_code=204)


@router.post('/invitations/preview')
async def preview(
    body: InvitationToken, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> InvitationPreview:
    async with session.begin():
        return await container.knowledge_sharing.preview(session, principal, body.token)


@router.post('/invitations/accept')
async def accept(
    body: InvitationToken, container: ContainerDependency, principal: PrincipalDependency, session: SessionDependency
) -> InvitationAccepted:
    async with session.begin():
        return await container.knowledge_sharing.accept(session, principal, body.token)


@router.post('/bases/{base_id}/sharing/cleanup')
async def cleanup_legacy(
    base_id: UUID,
    body: MemberRemoval,
    container: ContainerDependency,
    principal: PrincipalDependency,
    session: SessionDependency,
) -> BaseAclInfo:
    async with session.begin():
        return await container.knowledge_sharing.cleanup_legacy(session, principal, base_id, body)
