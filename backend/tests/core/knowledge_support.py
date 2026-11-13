"""Positive fixtures explicitly obtain complete knowledge dependencies and invitation membership."""

from uuid import uuid4

import httpx


async def knowledge_models(owner: httpx.AsyncClient, *models: str) -> list[str]:
    defaults = await owner.get('/api/knowledge/settings')
    assert defaults.status_code == 200, defaults.text
    ranker = defaults.json()['default_rerank_model_id']
    assert ranker
    return list(dict.fromkeys([*models, ranker]))


async def share_library(owner: httpx.AsyncClient, recipient: httpx.AsyncClient, base_id: str) -> dict:
    path = '/api/knowledge/bases/' + base_id
    acl = await owner.get(path + '/acl')
    assert acl.status_code == 200, acl.text
    created = await owner.post(
        path + '/invitations',
        json={'expected_acl_version': acl.json()['base']['acl_version'], 'client_request_id': str(uuid4())},
    )
    assert created.status_code == 201, created.text
    accepted = await recipient.post('/api/knowledge/invitations/accept', json={'token': created.json()['token']})
    assert accepted.status_code == 200, accepted.text
    return accepted.json()


async def remove_library_member(owner: httpx.AsyncClient, recipient: httpx.AsyncClient, base_id: str) -> None:
    path = '/api/knowledge/bases/' + base_id
    acl = await owner.get(path + '/acl')
    user = await recipient.get('/api/auth/me')
    removed = await owner.request(
        'DELETE',
        path + '/members/' + user.json()['id'],
        json={'expected_acl_version': acl.json()['base']['acl_version']},
    )
    assert removed.status_code == 204, removed.text


async def legacy_grant(app: object, base_id: str, user_id: str | None = None, *, effect: str = 'allow') -> str:
    """Construct historical persisted membership only inside an exclusive legacy fixture database."""
    from uuid import UUID

    from sqlalchemy import select, text

    from zhiheng.modules.identity.orm import IdentityUser
    from zhiheng.modules.knowledge.orm import KnowledgeBase, KnowledgeGrant

    async with app.state.container.database.sessions.begin() as session:
        name = await session.scalar(text('SELECT current_database()'))
        assert name.startswith('core_test_')
        base = await session.get(KnowledgeBase, UUID(base_id))
        assert base.document_policy == 'legacy'
        identifier = (
            UUID(user_id)
            if user_id
            else await session.scalar(select(IdentityUser.id).where(IdentityUser.username == 'member'))
        )
        row = await session.get(KnowledgeGrant, (base.id, identifier))
        if row is None:
            session.add(KnowledgeGrant(base_id=base.id, user_id=identifier, effect=effect))
        else:
            row.effect = effect
        base.acl_version += 1
        return str(identifier)
