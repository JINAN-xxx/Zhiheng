import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, NoReturn, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from plugins.ai.dao.ai_developer_api_dao import AiDeveloperApiDao, DeveloperKeyAuthRecord
from plugins.ai.domain.public_api_errors import PublicApiError
from plugins.ai.infrastructure.developer_key_codec import DeveloperKeyCodec

FailureKind = Literal['INVALID', 'UNAVAILABLE', 'CANCELLED']
AUTHORIZATION_PART_COUNT = 2


class AuthenticationDao(Protocol):
    async def get_key_auth_record(self, db: AsyncSession, key_prefix: str) -> DeveloperKeyAuthRecord | None: ...


@dataclass(frozen=True, slots=True)
class DeveloperApiIdentity:
    application_id: str
    key_id: str


@dataclass(frozen=True, slots=True)
class _AuthenticationOutcome:
    identity: DeveloperApiIdentity | None = None
    failure: FailureKind | None = None
    base_exception_type: type[BaseException] | None = None


def _raise_failure(failure: FailureKind, base_exception_type: type[BaseException] | None) -> NoReturn:
    if failure == 'CANCELLED' and base_exception_type is not None:
        raise base_exception_type
    if failure == 'UNAVAILABLE':
        raise PublicApiError.service_unavailable() from None
    raise PublicApiError.invalid_api_key() from None


def require_authenticated_identity(outcome: _AuthenticationOutcome) -> DeveloperApiIdentity:
    if outcome.identity is not None:
        return outcome.identity
    _raise_failure(outcome.failure or 'UNAVAILABLE', outcome.base_exception_type)


class DeveloperKeyAuthenticator:
    def __init__(
        self,
        db: AsyncSession,
        codec: DeveloperKeyCodec,
        *,
        configured: bool,
        developer_api_dao: AuthenticationDao = AiDeveloperApiDao,
    ) -> None:
        self._db = db
        self._codec = codec
        self._configured = configured
        self._dao = developer_api_dao

    async def authenticate(self, authorization: str | None, *, now: datetime) -> _AuthenticationOutcome:
        if not self._configured:
            return _AuthenticationOutcome(failure='UNAVAILABLE')
        complete_key = self._extract_bearer(authorization)
        identified = self._codec.identify(complete_key)
        if identified is None or complete_key is None:
            return _AuthenticationOutcome(failure='INVALID')
        _key_id, prefix = identified
        try:
            record = await self._dao.get_key_auth_record(self._db, prefix)
            valid = self._record_is_valid(record, complete_key, now)
            identity = (
                DeveloperApiIdentity(application_id=record.application_id, key_id=record.key_id)
                if valid and record is not None
                else None
            )
        except BaseException as error:
            failure = 'CANCELLED' if isinstance(error, asyncio.CancelledError) else 'UNAVAILABLE'
            base_type = type(error) if failure == 'CANCELLED' else None
            await self._rollback_preserving_error()
            return _AuthenticationOutcome(failure=failure, base_exception_type=base_type)
        await self._rollback_visible()
        if identity is None:
            return _AuthenticationOutcome(failure='INVALID')
        return _AuthenticationOutcome(identity=identity)

    @staticmethod
    def _extract_bearer(authorization: str | None) -> str | None:
        if not isinstance(authorization, str):
            return None
        parts = authorization.split(' ')
        if len(parts) != AUTHORIZATION_PART_COUNT or parts[0].lower() != 'bearer' or not parts[1]:
            return None
        return parts[1]

    def _record_is_valid(self, record: DeveloperKeyAuthRecord | None, complete_key: str, now: datetime) -> bool:
        if record is None or record.application_status != 'active':
            return False
        normalized_now = self._utc(now)
        if record.expires_at is not None and self._utc(record.expires_at) <= normalized_now:
            return False
        if record.key_status == 'retiring':
            if record.retire_at is None or self._utc(record.retire_at) <= normalized_now:
                return False
        elif record.key_status != 'active':
            return False
        return self._codec.verify(complete_key, version=record.digest_version, expected_digest=record.secret_digest)

    @staticmethod
    def _utc(value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    async def _rollback_visible(self) -> None:
        if self._db.in_transaction():
            await self._db.rollback()

    async def _rollback_preserving_error(self) -> None:
        try:
            await self._rollback_visible()
        except BaseException:
            pass
