import asyncio
from datetime import datetime, timezone

import pytest

from plugins.ai.dao.ai_developer_api_dao import PublicModelRecord
from plugins.ai.domain.public_api_errors import PublicApiError
from plugins.ai.infrastructure.developer_key_authenticator import DeveloperApiIdentity
from plugins.ai.service.api_entitlement_service import ApiEntitlementService, AuthorizedPublicModel

APPLICATION_ID = 'app_00000000000040008000000000000001'
KEY_ID = 'key_00000000000040008000000000000001'
MODEL_ID = 'mdl_00000000000040008000000000000001'
MODEL_CODE = 'deepseek-chat'
PUBLISHED_AT = datetime(2026, 8, 18, 12, 0)
EXPECTED_CREATED = int(PUBLISHED_AT.replace(tzinfo=timezone.utc).timestamp())


class FakeSession:
    def __init__(self, *, active: bool = False) -> None:
        self.active = active
        self.rollback_count = 0
        self.rollback_error: BaseException | None = None

    def in_transaction(self) -> bool:
        return self.active

    async def rollback(self) -> None:
        self.rollback_count += 1
        if self.rollback_error is not None:
            raise self.rollback_error
        self.active = False


class FakeDao:
    def __init__(self, records: list[PublicModelRecord]) -> None:
        self.records = records
        self.error: BaseException | None = None
        self.calls: list[tuple[str, str, str | None]] = []

    async def list_public_models_for_key(self, db: FakeSession, key_id: str) -> list[PublicModelRecord]:
        self.calls.append(('list', key_id, None))
        db.active = True
        if self.error is not None:
            raise self.error
        return self.records

    async def get_public_model_for_key(
        self,
        db: FakeSession,
        key_id: str,
        model_code: str,
    ) -> PublicModelRecord | None:
        self.calls.append(('resolve', key_id, model_code))
        db.active = True
        if self.error is not None:
            raise self.error
        return next((record for record in self.records if record.model_code == model_code), None)


def identity() -> DeveloperApiIdentity:
    return DeveloperApiIdentity(application_id=APPLICATION_ID, key_id=KEY_ID)


def model_record() -> PublicModelRecord:
    return PublicModelRecord(platform_model_id=MODEL_ID, model_code=MODEL_CODE, published_at=PUBLISHED_AT)


@pytest.mark.asyncio
async def test_list_models_returns_openai_public_fields_and_closes_transaction() -> None:
    session = FakeSession()
    dao = FakeDao([model_record()])
    service = ApiEntitlementService(session, developer_api_dao=dao)

    response = await service.list_models(identity())

    assert response.model_dump() == {
        'object': 'list',
        'data': [{'id': MODEL_CODE, 'object': 'model', 'created': EXPECTED_CREATED, 'owned_by': 'zhiheng'}],
    }
    assert dao.calls == [('list', KEY_ID, None)]
    assert session.rollback_count == 1
    assert session.in_transaction() is False


@pytest.mark.asyncio
async def test_resolve_model_returns_safe_platform_snapshot_and_closes_transaction() -> None:
    session = FakeSession()
    service = ApiEntitlementService(session, developer_api_dao=FakeDao([model_record()]))

    resolved = await service.resolve_model(identity(), MODEL_CODE)

    assert resolved == AuthorizedPublicModel(platform_model_id=MODEL_ID, model_code=MODEL_CODE)
    assert 'binding' not in repr(resolved)
    assert session.rollback_count == 1


@pytest.mark.asyncio
async def test_missing_or_ungranted_model_returns_fixed_not_found_after_cleanup() -> None:
    session = FakeSession()
    service = ApiEntitlementService(session, developer_api_dao=FakeDao([]))

    with pytest.raises(PublicApiError) as raised:
        await service.resolve_model(identity(), MODEL_CODE)

    assert raised.value.code == 'model_not_found'
    assert raised.value.__cause__ is None
    assert session.rollback_count == 1
    assert session.in_transaction() is False


@pytest.mark.asyncio
async def test_dao_error_is_sanitized_after_transaction_cleanup() -> None:
    session = FakeSession()
    dao = FakeDao([])
    dao.error = RuntimeError('PRIVATE SQL application/key/model ids')
    service = ApiEntitlementService(session, developer_api_dao=dao)

    with pytest.raises(PublicApiError) as raised:
        await service.list_models(identity())

    assert raised.value.code == 'service_unavailable'
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert 'PRIVATE SQL' not in repr(raised.value)
    assert session.rollback_count == 1


@pytest.mark.asyncio
async def test_cancelled_query_is_not_replaced_by_rollback_error() -> None:
    session = FakeSession()
    session.rollback_error = RuntimeError('rollback failed')
    dao = FakeDao([])
    dao.error = asyncio.CancelledError()
    service = ApiEntitlementService(session, developer_api_dao=dao)

    with pytest.raises(asyncio.CancelledError) as raised:
        await service.list_models(identity())

    assert raised.value.__context__ is None
    assert session.rollback_count == 1


def test_service_rejects_caller_owned_active_transaction_without_rollback() -> None:
    session = FakeSession(active=True)

    with pytest.raises(RuntimeError, match='独占'):
        ApiEntitlementService(session, developer_api_dao=FakeDao([]))

    assert session.rollback_count == 0
