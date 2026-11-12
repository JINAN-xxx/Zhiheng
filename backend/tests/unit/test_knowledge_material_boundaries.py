from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from zhiheng.modules.files.orm import FileRecord
from zhiheng.modules.files.service import FileService
from zhiheng.modules.knowledge.contracts import BaseCreate
from zhiheng.modules.knowledge.orm import KnowledgeBase
from zhiheng.modules.knowledge.service import KnowledgeService
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


def principal(*, admin: bool = False) -> Principal:
    return Principal(uuid4(), 'reader', admin)


def file_record(owner_id: UUID) -> FileRecord:
    return FileRecord(
        id=uuid4(),
        owner_id=owner_id,
        uploader_id=owner_id,
        access='public',
        status='active',
        storage_status='normal',
        version=1,
        acl_version=1,
        source_file_id=None,
        expires_at=None,
    )


async def test_library_file_access_checks_base_even_for_file_owner_and_admin() -> None:
    user = principal(admin=True)
    row = file_record(user.id)
    service = FileService(SimpleNamespace(), SimpleNamespace())
    service.library_access = AsyncMock(side_effect=AppError('knowledge_not_found', 'denied', 404))
    session = SimpleNamespace(scalar=AsyncMock(return_value=row))
    with pytest.raises(AppError, match='denied'):
        await service._require(session, user, row.id)
    service.library_access.assert_awaited_once()


async def test_library_file_shared_read_ignores_legacy_file_deny() -> None:
    user = principal()
    row = file_record(uuid4())
    row.access = 'private'
    service = FileService(SimpleNamespace(), SimpleNamespace())
    service.library_access = AsyncMock(return_value=True)
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=row), get=AsyncMock(return_value=SimpleNamespace(effect='deny', expires_at=None))
    )
    assert await service._require(session, user, row.id) is row
    session.get.assert_not_awaited()


def test_simple_create_accepts_server_default_embedding() -> None:
    assert BaseCreate(name='知识库').embed_model_id is None


async def test_new_library_rejects_legacy_deny_and_expiry_rules() -> None:
    from zhiheng.modules.knowledge.contracts import BaseAcl

    user = principal()
    base = KnowledgeBase(id=uuid4(), owner_id=user.id, document_policy='library', acl_version=1)
    service = KnowledgeService(SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    service.require = AsyncMock(return_value=base)
    body = BaseAcl(expected_version=1, access='private', entries=[{'user_id': uuid4(), 'effect': 'deny'}])
    with pytest.raises(AppError) as error:
        await service.set_acl(SimpleNamespace(), user, base.id, body)
    assert error.value.code == 'invalid_library_sharing'


async def test_file_authority_is_not_library_authority_after_document_removed() -> None:
    user = principal()
    service = KnowledgeService(SimpleNamespace(), SimpleNamespace(), SimpleNamespace())
    # A retired library ownership record remains authoritative; generic file ACL must not revive it.
    service.require = AsyncMock(return_value=SimpleNamespace(owner_id=user.id, document_policy='library'))
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=SimpleNamespace(base_id=uuid4(), document_id=uuid4())),
        get=AsyncMock(return_value=SimpleNamespace(deleted_at=True)),
    )
    with pytest.raises(AppError):
        await service.authorize_library_file(session, user, uuid4())


async def test_chat_attachment_requires_actual_sent_reference_even_for_admin_owner() -> None:
    user = principal(admin=True)
    row = file_record(user.id)
    service = FileService(SimpleNamespace(), SimpleNamespace())
    service._require = AsyncMock(return_value=row)
    session = SimpleNamespace(scalar=AsyncMock(return_value=None))
    with pytest.raises(AppError) as error:
        await service.require_sent_attachment(session, user, row.id)
    assert error.value.code == 'attachment_not_sent'


async def test_private_dependencies_are_counts_and_confirmed_delete_is_allowed() -> None:
    from zhiheng.shared.references import ResourceReference

    user = principal(admin=True)
    foreign = ResourceReference('agent', uuid4(), 'secret', 'published', uuid4(), '/secret')
    service = KnowledgeService(
        SimpleNamespace(),
        SimpleNamespace(),
        SimpleNamespace(),
        base_references=AsyncMock(return_value={'items': [foreign], 'hidden_count': 2}),
    )
    service.require = AsyncMock(return_value=SimpleNamespace(deleted_at=None))
    summary = await service.references(SimpleNamespace(), user, uuid4())
    assert summary == {'items': [], 'hidden_count': 3, 'total': 3}
    await service.confirm_deletion(SimpleNamespace(), user, uuid4(), confirmed=True)
    with pytest.raises(AppError):
        await service.confirm_deletion(SimpleNamespace(), user, uuid4(), confirmed=False)
