from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from zhiheng.api.console.chat import ChatRequest
from zhiheng.application.files.inputs import MAX_TOTAL_ATTACHMENT_BYTES, FileInputService
from zhiheng.modules.files.public import AttachmentSummary, PreparedAttachment
from zhiheng.shared.errors import AppError
from zhiheng.shared.principal import Principal


def inputs(
    monkeypatch: pytest.MonkeyPatch, sizes: list[int], *, image: bool = False
) -> tuple[FileInputService, Principal, list[UUID], AsyncMock]:
    principal = Principal(uuid4(), 'member', False)
    identifiers = [uuid4() for _ in sizes]
    facts = dict(zip(identifiers, sizes, strict=True))
    files = MagicMock()
    files.get = AsyncMock(side_effect=lambda _session, _principal, identifier: SimpleNamespace(size=facts[identifier]))
    service = FileInputService(MagicMock(), files, MagicMock(), MagicMock())
    prepare = AsyncMock(
        side_effect=lambda _principal, identifier: PreparedAttachment(
            summary=AttachmentSummary(
                file_id=identifier,
                original_filename='file.txt',
                mime='text/plain',
                size=facts[identifier],
                sha256='source',
            ),
            version=1,
            acl_version=1,
            text='doc',
            image_data='data:image/jpeg;base64,YQ==' if image else None,
        )
    )
    monkeypatch.setattr(service, 'prepare', prepare)
    return service, principal, identifiers, prepare


async def test_total_size_above_fifty_mib_is_rejected_before_any_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    mib = 1024 * 1024
    service, principal, identifiers, prepare = inputs(monkeypatch, [20 * mib, 20 * mib, 10 * mib + 1])
    with pytest.raises(AppError) as error:
        await service.prepare_many(principal, identifiers)
    assert error.value.code == 'attachments_too_large'
    prepare.assert_not_awaited()


async def test_exact_total_size_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    mib = 1024 * 1024
    service, principal, identifiers, _ = inputs(monkeypatch, [20 * mib, 20 * mib, 10 * mib])
    result = await service.prepare_many(principal, identifiers)
    assert sum(item.summary.size for item in result) == MAX_TOTAL_ATTACHMENT_BYTES


@pytest.mark.parametrize('image', [False, True])
async def test_small_attachments_have_no_document_image_or_request_count_cap(
    monkeypatch: pytest.MonkeyPatch, image: bool
) -> None:
    service, principal, identifiers, _ = inputs(monkeypatch, [1] * 16, image=image)
    request = ChatRequest(model_id=uuid4(), message='files', client_request_id=uuid4(), file_ids=identifiers)
    assert len(await service.prepare_many(principal, request.file_ids)) == 16


async def test_duplicate_attachments_are_still_rejected_before_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    service, principal, identifiers, prepare = inputs(monkeypatch, [1])
    with pytest.raises(AppError) as error:
        await service.prepare_many(principal, identifiers * 2)
    assert error.value.code == 'invalid_attachments'
    prepare.assert_not_awaited()
