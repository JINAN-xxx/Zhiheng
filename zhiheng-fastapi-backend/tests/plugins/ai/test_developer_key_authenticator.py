import traceback
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import SecretStr

from plugins.ai.dao.ai_developer_api_dao import DeveloperKeyAuthRecord
from plugins.ai.domain.public_api_errors import PublicApiError
from plugins.ai.infrastructure.developer_key_authenticator import (
    DeveloperKeyAuthenticator,
    require_authenticated_identity,
)
from plugins.ai.infrastructure.developer_key_codec import DeveloperKeyCodec

NOW = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
KEY_ID = 'key_00000000000040008000000000000001'
APPLICATION_ID = 'app_00000000000040008000000000000001'
PEPPER = SecretStr('p' * 32)


class FakeSession:
    def __init__(self) -> None:
        self.transaction_active = False
        self.rollback_count = 0

    def in_transaction(self) -> bool:
        return self.transaction_active

    async def rollback(self) -> None:
        self.rollback_count += 1
        self.transaction_active = False


class FakeDao:
    def __init__(self, record: DeveloperKeyAuthRecord | None) -> None:
        self.record = record
        self.calls = 0
        self.error: BaseException | None = None

    async def get_key_auth_record(self, db: FakeSession, _prefix: str) -> DeveloperKeyAuthRecord | None:
        self.calls += 1
        db.transaction_active = True
        if self.error is not None:
            raise self.error
        return self.record


def issued_key() -> tuple[DeveloperKeyCodec, str, DeveloperKeyAuthRecord]:
    codec = DeveloperKeyCodec(current_version='v1', peppers={'v1': PEPPER}, token_bytes=lambda _size: b'x' * 32)
    issued = codec.issue(KEY_ID)
    record = DeveloperKeyAuthRecord(
        key_id=KEY_ID,
        application_id=APPLICATION_ID,
        key_prefix=issued.prefix,
        secret_digest=issued.digest,
        digest_version='v1',
        key_status='active',
        expires_at=None,
        retire_at=None,
        application_status='active',
    )
    return codec, issued.complete_key, record


@pytest.mark.asyncio
async def test_authenticator_returns_safe_identity_and_ends_read_transaction() -> None:
    codec, complete_key, record = issued_key()
    session = FakeSession()
    dao = FakeDao(record)
    authenticator = DeveloperKeyAuthenticator(session, codec, configured=True, developer_api_dao=dao)

    identity = require_authenticated_identity(await authenticator.authenticate(f'Bearer {complete_key}', now=NOW))

    assert identity.application_id == APPLICATION_ID
    assert identity.key_id == KEY_ID
    assert complete_key not in repr(identity)
    assert session.rollback_count == 1
    assert session.in_transaction() is False


@pytest.mark.asyncio
async def test_unconfigured_authenticator_fails_before_database_access() -> None:
    codec, complete_key, record = issued_key()
    dao = FakeDao(record)
    authenticator = DeveloperKeyAuthenticator(FakeSession(), codec, configured=False, developer_api_dao=dao)

    with pytest.raises(PublicApiError) as raised:
        require_authenticated_identity(await authenticator.authenticate(f'Bearer {complete_key}', now=NOW))

    assert raised.value.code == 'service_unavailable'
    assert dao.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('authorization', (None, '', 'Basic abc', 'Bearer', 'Bearer a b', 'Bearer invalid'))
async def test_invalid_authorization_is_fixed_and_never_queries_database(authorization: str | None) -> None:
    codec, _, record = issued_key()
    dao = FakeDao(record)
    authenticator = DeveloperKeyAuthenticator(FakeSession(), codec, configured=True, developer_api_dao=dao)

    with pytest.raises(PublicApiError) as raised:
        require_authenticated_identity(await authenticator.authenticate(authorization, now=NOW))

    assert raised.value.code == 'invalid_api_key'
    assert dao.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('key_status', 'application_status', 'expires_at', 'retire_at'),
    (
        ('revoked', 'active', None, None),
        ('expired', 'active', None, None),
        ('active', 'disabled', None, None),
        ('active', 'active', NOW - timedelta(seconds=1), None),
        ('retiring', 'active', None, NOW - timedelta(seconds=1)),
    ),
)
async def test_inactive_key_or_application_is_rejected(
    key_status: str,
    application_status: str,
    expires_at: datetime | None,
    retire_at: datetime | None,
) -> None:
    codec, complete_key, record = issued_key()
    changed = DeveloperKeyAuthRecord(
        **{
            **{field: getattr(record, field) for field in record.__slots__},
            'key_status': key_status,
            'application_status': application_status,
            'expires_at': expires_at,
            'retire_at': retire_at,
        }
    )
    authenticator = DeveloperKeyAuthenticator(FakeSession(), codec, configured=True, developer_api_dao=FakeDao(changed))

    with pytest.raises(PublicApiError) as raised:
        require_authenticated_identity(await authenticator.authenticate(f'Bearer {complete_key}', now=NOW))
    assert raised.value.code == 'invalid_api_key'


@pytest.mark.asyncio
async def test_authentication_failure_traceback_does_not_retain_complete_key_or_database_error() -> None:
    codec, complete_key, record = issued_key()
    dao = FakeDao(record)
    dao.error = RuntimeError(f'PRIVATE-DB {complete_key}')
    authenticator = DeveloperKeyAuthenticator(FakeSession(), codec, configured=True, developer_api_dao=dao)

    with pytest.raises(PublicApiError) as raised:
        require_authenticated_identity(await authenticator.authenticate(f'Bearer {complete_key}', now=NOW))

    error = raised.value
    serialized = repr((error.args, vars(error), str(error), repr(error), error.__cause__, error.__context__))
    frames = []
    current = error.__traceback__
    while current is not None:
        filename = current.tb_frame.f_code.co_filename
        if '/plugins/ai/' in filename and '/tests/' not in filename:
            frames.append(repr(current.tb_frame.f_locals))
        current = current.tb_next
    assert error.code == 'service_unavailable'
    assert complete_key not in serialized
    assert complete_key not in ''.join(frames)
    assert 'PRIVATE-DB' not in ''.join(frames)
    assert complete_key not in ''.join(traceback.format_exception(type(error), error, error.__traceback__))
