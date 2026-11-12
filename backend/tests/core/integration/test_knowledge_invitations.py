"""No directory enumeration; invitations grant only explicit authenticated membership."""

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import func, select

from tests.core.integration.test_knowledge import embedding_model
from tests.core.provider import ProviderServer
from zhiheng.modules.identity.admin_orm import IdentityRole, IdentityUserRole
from zhiheng.modules.identity.orm import IdentityUser
from zhiheng.modules.knowledge.orm import KnowledgeBase, KnowledgeGrant, KnowledgeInvitation
from zhiheng.shared.time import utcnow


@dataclass
class SharingFixture:
    owner: httpx.AsyncClient
    first: httpx.AsyncClient
    second: httpx.AsyncClient
    base: dict[str, Any]
    first_id: str
    second_id: str

    @property
    def path(self) -> str:
        return '/api/knowledge/bases/' + self.base['id']


@pytest_asyncio.fixture
async def sharing(admin: httpx.AsyncClient, app: FastAPI, provider: ProviderServer) -> AsyncIterator[SharingFixture]:
    model = await embedding_model(admin, provider)
    async with app.state.container.database.sessions.begin() as session:
        for name in ('joining-one', 'joining-two'):
            await app.state.container.accounts.create_user(
                session, username=name, password='platform-test-password', is_admin=False
            )
    clients = []
    for name in ('member', 'joining-one', 'joining-two'):
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
        response = await client.post('/api/auth/login', json={'username': name, 'password': 'platform-test-password'})
        assert response.status_code == 200, response.text
        clients.append(client)
    owner, first, second = clients
    created = await owner.post(
        '/api/knowledge/bases', json={'name': 'Private invitation library', 'embed_model_id': model}
    )
    assert created.status_code == 201, created.text
    try:
        yield SharingFixture(
            owner,
            first,
            second,
            created.json(),
            (await first.get('/api/auth/me')).json()['id'],
            (await second.get('/api/auth/me')).json()['id'],
        )
    finally:
        await asyncio.gather(*(client.aclose() for client in clients))


async def invite(fixture: SharingFixture) -> dict[str, Any]:
    acl = (await fixture.owner.get(fixture.path + '/acl')).json()
    result = await fixture.owner.post(
        fixture.path + '/invitations',
        json={'expected_acl_version': acl['base']['acl_version'], 'client_request_id': str(uuid4())},
    )
    assert result.status_code == 201, result.text
    return result.json()


async def test_resource_ownership_does_not_grant_a_user_directory(sharing: SharingFixture, app: FastAPI) -> None:
    for query in ('', 'join', 'joining-one', '%', '_'):
        response = await sharing.owner.get(sharing.path + '/users', params={'search': query})
        assert response.status_code == 404, response.text
        assert 'joining-one' not in response.text
    body = {
        'expected_version': sharing.base['acl_version'],
        'access': 'private',
        'entries': [{'user_id': sharing.first_id, 'effect': 'allow'}],
    }
    assert (await sharing.owner.patch(sharing.path + '/acl', json=body)).status_code == 405
    owner_id = (await sharing.owner.get('/api/auth/me')).json()['id']
    async with app.state.container.database.sessions.begin() as session:
        role = IdentityRole(
            id=uuid4(),
            code='storage-without-directory',
            name='Storage operator',
            permissions=['ai:storage:read'],
            is_active=True,
        )
        session.add(role)
        await session.flush()
        session.add(IdentityUserRole(user_id=UUID(owner_id), role_id=role.id))
    assert (await sharing.owner.get('/api/files/users')).status_code == 403
    assert (await sharing.owner.get(sharing.path + '/members')).json() == []


async def test_link_is_idempotent_hashed_and_membership_requires_acceptance(
    sharing: SharingFixture, app: FastAPI
) -> None:
    key = str(uuid4())
    body = {'expected_acl_version': sharing.base['acl_version'], 'client_request_id': key}
    first = await sharing.owner.post(sharing.path + '/invitations', json=body)
    replay = await sharing.owner.post(sharing.path + '/invitations', json=body)
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    issued = first.json()
    token = issued['token']
    identifier = issued['invitation']['id']
    assert len(token) == 43
    listing = await sharing.owner.get(sharing.path + '/invitations')
    assert token not in listing.text and 'token' not in listing.text
    copied = await sharing.owner.get(sharing.path + '/invitations/' + identifier + '/link')
    assert copied.json()['token'] == token
    async with app.state.container.database.sessions() as session:
        row = await session.get(KnowledgeInvitation, UUID(identifier))
        assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
        assert token not in json.dumps({c.name: str(getattr(row, c.name)) for c in row.__table__.columns})
        assert timedelta(hours=23) < row.expires_at - utcnow() <= timedelta(hours=24)
        assert (
            await session.scalar(
                select(func.count())
                .select_from(KnowledgeGrant)
                .where(KnowledgeGrant.base_id == UUID(sharing.base['id']))
            )
            == 0
        )
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as anonymous:
        assert (await anonymous.post('/api/knowledge/invitations/preview', json={'token': token})).status_code == 401
        assert (await anonymous.post('/api/knowledge/invitations/accept', json={'token': token})).status_code == 401
    assert (await sharing.first.get(sharing.path)).status_code == 404
    preview = await sharing.first.post('/api/knowledge/invitations/preview', json={'token': token})
    assert preview.status_code == 200 and not preview.json()['already_member']
    assert (await sharing.first.get(sharing.path)).status_code == 404
    accepted = await sharing.first.post('/api/knowledge/invitations/accept', json={'token': token})
    assert accepted.status_code == 200 and not accepted.json()['already_member']
    assert (await sharing.first.get(sharing.path)).status_code == 200
    assert (
        await sharing.first.patch(sharing.path, json={'name': 'Not allowed', 'expected_version': 1})
    ).status_code == 404
    assert (await sharing.first.get(sharing.path + '/members')).status_code == 404
    assert (await sharing.first.post(sharing.path + '/invitations', json=body)).status_code == 404
    members = (await sharing.owner.get(sharing.path + '/members')).json()
    assert [member['name'] for member in members] == ['joining-one']


async def test_concurrent_redemption_has_one_winner_and_replay_is_safe(sharing: SharingFixture, app: FastAPI) -> None:
    token = (await invite(sharing))['token']
    responses = await asyncio.gather(
        *(
            actor.post('/api/knowledge/invitations/accept', json={'token': token})
            for actor in (sharing.first, sharing.second)
        )
    )
    assert sorted(response.status_code for response in responses) == [200, 404]
    winner = sharing.first if responses[0].status_code == 200 else sharing.second
    retry = await winner.post('/api/knowledge/invitations/accept', json={'token': token})
    assert retry.status_code == 200 and retry.json()['already_member']
    async with app.state.container.database.sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(KnowledgeGrant)
                .where(KnowledgeGrant.base_id == UUID(sharing.base['id']))
            )
            == 1
        )
        assert (await session.get(KnowledgeBase, UUID(sharing.base['id']))).acl_version == 2


@pytest.mark.parametrize('state', ['expired', 'revoked', 'owner_disabled', 'library_deleted'])
async def test_invalidated_invites_cannot_reveal_or_grant_access(
    sharing: SharingFixture, app: FastAPI, state: str
) -> None:
    issued = await invite(sharing)
    token = issued['token']
    async with app.state.container.database.sessions.begin() as session:
        row = await session.get(KnowledgeInvitation, UUID(issued['invitation']['id']))
        if state == 'expired':
            row.expires_at = utcnow() - timedelta(seconds=1)
        elif state == 'revoked':
            row.revoked_at = utcnow()
        elif state == 'owner_disabled':
            (await session.get(IdentityUser, row.owner_id)).is_active = False
        else:
            (await session.get(KnowledgeBase, row.base_id)).deleted_at = utcnow()
    for operation in ('preview', 'accept'):
        response = await sharing.first.post('/api/knowledge/invitations/' + operation, json={'token': token})
        assert response.status_code == 404, response.text
        assert sharing.base['name'] not in response.text
    assert (await sharing.first.get(sharing.path)).status_code == 404


async def test_member_removal_invalidates_old_links_and_access(sharing: SharingFixture) -> None:
    first = await invite(sharing)
    assert (
        await sharing.first.post('/api/knowledge/invitations/accept', json={'token': first['token']})
    ).status_code == 200
    waiting = await invite(sharing)
    acl = (await sharing.owner.get(sharing.path + '/acl')).json()
    removed = await sharing.owner.request(
        'DELETE',
        sharing.path + '/members/' + sharing.first_id,
        json={'expected_acl_version': acl['base']['acl_version']},
    )
    assert removed.status_code == 204, removed.text
    assert (await sharing.first.get(sharing.path)).status_code == 404
    for token in (first['token'], waiting['token']):
        assert (await sharing.first.post('/api/knowledge/invitations/accept', json={'token': token})).status_code == 404
    replacement = await invite(sharing)
    assert (
        await sharing.first.post('/api/knowledge/invitations/accept', json={'token': replacement['token']})
    ).status_code == 200


async def test_existing_member_does_not_consume_another_invitation(sharing: SharingFixture, app: FastAPI) -> None:
    first = await invite(sharing)
    await sharing.first.post('/api/knowledge/invitations/accept', json={'token': first['token']})
    second = await invite(sharing)
    response = await sharing.first.post('/api/knowledge/invitations/accept', json={'token': second['token']})
    assert response.status_code == 200 and response.json()['already_member']
    async with app.state.container.database.sessions() as session:
        assert (await session.get(KnowledgeInvitation, UUID(second['invitation']['id']))).accepted_by is None
    assert (
        await sharing.second.post('/api/knowledge/invitations/accept', json={'token': second['token']})
    ).status_code == 200


async def test_manual_and_global_sharing_are_removed_for_every_role(
    sharing: SharingFixture, admin: httpx.AsyncClient
) -> None:
    issued = await invite(sharing)
    assert (await admin.get(sharing.path + '/invitations')).status_code == 404
    assert (
        await sharing.first.post(sharing.path + '/invitations/' + issued['invitation']['id'] + '/revoke', json={})
    ).status_code == 404
    for actor in (sharing.owner, admin):
        assert (await actor.get(sharing.path + '/users')).status_code == 404
        for access in ('private', 'public'):
            response = await actor.patch(
                sharing.path + '/acl',
                json={
                    'expected_version': 1,
                    'access': access,
                    'entries': [{'user_id': sharing.first_id, 'effect': 'allow'}],
                },
            )
            assert response.status_code == 405
        rejected = await actor.post(
            '/api/knowledge/bases',
            json={
                'name': 'Must remain unavailable',
                'embed_model_id': sharing.base['embed_model_id'],
                'access': 'public',
            },
        )
        assert rejected.status_code == 422
        assert (
            await actor.post(
                sharing.path + '/sharing/cleanup',
                json={'expected_acl_version': 1, 'entries': [{'user_id': sharing.first_id, 'effect': 'allow'}]},
            )
        ).status_code == 422
    assert (await sharing.first.get(sharing.path)).status_code == 404
    assert (
        await sharing.first.post('/api/knowledge/invitations/accept', json={'token': issued['token']})
    ).status_code == 200


async def test_owner_can_revoke_and_recopy_only_active_links(sharing: SharingFixture) -> None:
    issued = await invite(sharing)
    identifier = issued['invitation']['id']
    revoked = await sharing.owner.post(sharing.path + '/invitations/' + identifier + '/revoke', json={})
    assert revoked.status_code == 200 and revoked.json()['status'] == 'revoked'
    assert (await sharing.owner.get(sharing.path + '/invitations/' + identifier + '/link')).status_code == 404
    assert (
        await sharing.first.post('/api/knowledge/invitations/preview', json={'token': issued['token']})
    ).status_code == 404


async def test_expired_allow_can_be_renewed_but_deny_is_never_overridden(sharing: SharingFixture, app: FastAPI) -> None:
    token = (await invite(sharing))['token']
    async with app.state.container.database.sessions.begin() as session:
        session.add(
            KnowledgeGrant(
                base_id=UUID(sharing.base['id']),
                user_id=UUID(sharing.first_id),
                effect='allow',
                expires_at=utcnow() - timedelta(days=1),
            )
        )
        session.add(KnowledgeGrant(base_id=UUID(sharing.base['id']), user_id=UUID(sharing.second_id), effect='deny'))
    assert (await sharing.second.post('/api/knowledge/invitations/accept', json={'token': token})).status_code == 404
    assert (await sharing.first.post('/api/knowledge/invitations/accept', json={'token': token})).status_code == 200
    async with app.state.container.database.sessions() as session:
        grant = await session.get(KnowledgeGrant, (UUID(sharing.base['id']), UUID(sharing.first_id)))
        assert grant.effect == 'allow' and grant.expires_at is None


async def test_invitation_accept_rejects_cross_origin_requests(sharing: SharingFixture) -> None:
    token = (await invite(sharing))['token']
    response = await sharing.first.post(
        '/api/knowledge/invitations/accept', json={'token': token}, headers={'Origin': 'https://outside.invalid'}
    )
    assert response.status_code == 403
    assert (await sharing.first.get(sharing.path)).status_code == 404


async def test_legacy_cleanup_only_removes_rules_and_preserves_permanent_members(
    sharing: SharingFixture, admin: httpx.AsyncClient, app: FastAPI
) -> None:
    issued = await invite(sharing)
    admin_id = UUID((await admin.get('/api/auth/me')).json()['id'])
    async with app.state.container.database.sessions.begin() as session:
        (await session.get(KnowledgeBase, UUID(sharing.base['id']))).document_policy = 'legacy'
        session.add_all(
            [
                KnowledgeGrant(
                    base_id=UUID(sharing.base['id']),
                    user_id=UUID(sharing.first_id),
                    effect='allow',
                    expires_at=utcnow() - timedelta(days=1),
                ),
                KnowledgeGrant(base_id=UUID(sharing.base['id']), user_id=UUID(sharing.second_id), effect='deny'),
                KnowledgeGrant(base_id=UUID(sharing.base['id']), user_id=admin_id, effect='allow'),
            ]
        )
    endpoint = sharing.path + '/sharing/cleanup'
    assert (await admin.post(endpoint, json={'expected_acl_version': 1})).status_code == 404
    assert (await sharing.owner.post(endpoint, json={'expected_acl_version': 99})).status_code == 409
    result = await sharing.owner.post(endpoint, json={'expected_acl_version': 1})
    assert result.status_code == 200, result.text
    assert result.json()['entries'] == [{'user_id': str(admin_id), 'effect': 'allow', 'expires_at': None}]
    assert (await sharing.first.get(sharing.path)).status_code == 404
    assert (await sharing.second.get(sharing.path)).status_code == 404
    assert (await admin.get(sharing.path)).status_code == 200
    assert (
        await sharing.first.post('/api/knowledge/invitations/accept', json={'token': issued['token']})
    ).status_code == 404
    replay = await sharing.owner.post(endpoint, json={'expected_acl_version': 2})
    assert replay.status_code == 200 and replay.json()['base']['acl_version'] == 2
