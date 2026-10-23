import asyncio
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, NoReturn, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from plugins.ai.dao.ai_developer_api_dao import AiDeveloperApiDao
from plugins.ai.domain.developer_api_errors import DeveloperApiError
from plugins.ai.domain.resource_id import new_resource_id
from plugins.ai.entity.do.ai_developer_api_do import (
    AiApiKey,
    AiApiKeyModelGrant,
    AiApiQuotaPolicy,
    AiApplication,
)
from plugins.ai.entity.vo.ai_developer_api_vo import (
    DeveloperKeyCreatedModel,
    DeveloperKeyCreateModel,
    DeveloperKeyRotateModel,
    DeveloperKeySummaryModel,
    ModelGrantModel,
    QuotaPolicyModel,
)
from plugins.ai.infrastructure.developer_key_codec import (
    DeveloperKeyCodec,
    DeveloperKeyCodecError,
    IssuedDeveloperKey,
)

FailureKind = Literal[
    'APPLICATION_NOT_FOUND',
    'APPLICATION_DISABLED',
    'API_KEY_NOT_FOUND',
    'API_KEY_INACTIVE',
    'MODEL_GRANT_INVALID',
    'DEVELOPER_API_MISCONFIGURED',
    'CANCELLED',
    'OPERATION_FAILED',
]


class DeveloperKeyOperationError(RuntimeError):
    """不保留数据库正文或Key材料的固定持久化错误。"""

    __slots__ = ()

    def __init__(self) -> None:
        Exception.__init__(self)

    def __str__(self) -> str:
        return 'Developer API Key 操作失败'


@dataclass(frozen=True, slots=True)
class DeveloperApiActor:
    user_id: int
    username: str
    is_admin: bool


@dataclass(frozen=True, slots=True)
class _SensitiveWriteOutcome:
    response: DeveloperKeyCreatedModel | None = None
    failure: FailureKind | None = None
    base_exception_type: type[BaseException] | None = None


class DeveloperApiDao(Protocol):
    async def get_application_by_id(self, db: AsyncSession, application_id: str) -> AiApplication | None: ...

    async def get_application_for_update(self, db: AsyncSession, application_id: str) -> AiApplication | None: ...

    async def find_active_model_ids(self, db: AsyncSession, model_ids: Collection[str]) -> set[str]: ...

    async def create_key(self, db: AsyncSession, api_key: AiApiKey) -> AiApiKey: ...

    async def get_key_by_id(
        self,
        db: AsyncSession,
        application_id: str,
        api_key_id: str,
    ) -> AiApiKey | None: ...

    async def get_key_for_update(
        self,
        db: AsyncSession,
        application_id: str,
        api_key_id: str,
    ) -> AiApiKey | None: ...

    async def list_keys_by_application(self, db: AsyncSession, application_id: str) -> list[AiApiKey]: ...

    async def transition_key_status(
        self,
        db: AsyncSession,
        application_id: str,
        api_key_id: str,
        *,
        expected_statuses: Collection[str],
        values: dict[str, Any],
    ) -> int: ...

    async def replace_grants(
        self,
        db: AsyncSession,
        api_key_id: str,
        grants: Sequence[AiApiKeyModelGrant],
    ) -> int: ...

    async def list_grants(self, db: AsyncSession, api_key_id: str) -> list[AiApiKeyModelGrant]: ...

    async def create_quota(self, db: AsyncSession, quota: AiApiQuotaPolicy) -> AiApiQuotaPolicy: ...

    async def get_quota_by_key(self, db: AsyncSession, api_key_id: str) -> AiApiQuotaPolicy | None: ...


class DeveloperKeyService:
    """管理Developer API Key、模型授权与配额的原子生命周期。"""

    def __init__(
        self,
        db: AsyncSession,
        codec: DeveloperKeyCodec,
        *,
        developer_api_dao: DeveloperApiDao = AiDeveloperApiDao,
    ) -> None:
        self._db = db
        self._codec = codec
        self._dao = developer_api_dao

    async def list_keys(
        self,
        application_id: str,
        *,
        actor: DeveloperApiActor,
    ) -> list[DeveloperKeySummaryModel]:
        application = await self._dao.get_application_by_id(self._db, application_id)
        self._require_application_access(application, actor)
        keys = await self._dao.list_keys_by_application(self._db, application_id)
        return [await self._summary(key) for key in keys]

    async def create_key(
        self,
        application_id: str,
        payload: DeveloperKeyCreateModel,
        *,
        actor: DeveloperApiActor,
        now: datetime,
    ) -> DeveloperKeyCreatedModel:
        outcome = await self._create_key_sensitive(application_id, payload, actor=actor, now=now)
        return self._unwrap_sensitive_outcome(outcome)

    async def rotate_key(
        self,
        application_id: str,
        key_id: str,
        payload: DeveloperKeyRotateModel,
        *,
        actor: DeveloperApiActor,
        now: datetime,
    ) -> DeveloperKeyCreatedModel:
        outcome = await self._rotate_key_sensitive(
            application_id,
            key_id,
            payload,
            actor=actor,
            now=now,
        )
        return self._unwrap_sensitive_outcome(outcome)

    async def revoke_key(
        self,
        application_id: str,
        key_id: str,
        *,
        actor: DeveloperApiActor,
        now: datetime,
    ) -> DeveloperKeySummaryModel:
        try:
            application = await self._dao.get_application_for_update(self._db, application_id)
            self._require_application_access(application, actor)
            key = await self._dao.get_key_for_update(self._db, application_id, key_id)
            if key is None:
                raise DeveloperApiError.api_key_not_found()
            if key.status == 'revoked':
                response = await self._summary(key)
                await self._db.rollback()
                return response
            values = {
                'status': 'revoked',
                'retire_at': None,
                'revoked_at': self._database_time(now),
                'updated_by': actor.username,
                'updated_at': self._database_time(now),
            }
            changed = await self._dao.transition_key_status(
                self._db,
                application_id,
                key_id,
                expected_statuses={'active', 'retiring', 'expired'},
                values=values,
            )
            if changed != 1:
                raise DeveloperApiError.api_key_inactive()
            snapshot = self._key_snapshot(key)
            response = await self._summary_from_snapshot({**snapshot, **values})
            await self._db.commit()
            return response
        except BaseException:
            await self._rollback_preserving_error()
            raise

    async def _create_key_sensitive(
        self,
        application_id: str,
        payload: DeveloperKeyCreateModel,
        *,
        actor: DeveloperApiActor,
        now: datetime,
    ) -> _SensitiveWriteOutcome:
        key_id = new_resource_id('key')
        try:
            issued = self._codec.issue(key_id)
        except DeveloperKeyCodecError:
            return _SensitiveWriteOutcome(failure='DEVELOPER_API_MISCONFIGURED')
        try:
            application = await self._dao.get_application_for_update(self._db, application_id)
            self._require_active_application(application, actor)
            requested_models = set(payload.model_ids)
            active_models = await self._dao.find_active_model_ids(self._db, requested_models)
            if active_models != requested_models:
                raise DeveloperApiError.model_grant_invalid()
            response = await self._persist_new_key(
                application_id,
                payload,
                issued,
                actor=actor,
                now=now,
            )
            await self._db.commit()
            return _SensitiveWriteOutcome(response=response)
        except BaseException as error:
            failure = await self._classify_sensitive_failure(error)
            return failure

    async def _rotate_key_sensitive(
        self,
        application_id: str,
        key_id: str,
        payload: DeveloperKeyRotateModel,
        *,
        actor: DeveloperApiActor,
        now: datetime,
    ) -> _SensitiveWriteOutcome:
        new_key_id = new_resource_id('key')
        try:
            issued = self._codec.issue(new_key_id)
        except DeveloperKeyCodecError:
            return _SensitiveWriteOutcome(failure='DEVELOPER_API_MISCONFIGURED')
        try:
            application = await self._dao.get_application_for_update(self._db, application_id)
            self._require_active_application(application, actor)
            current = await self._dao.get_key_for_update(self._db, application_id, key_id)
            if current is None:
                raise DeveloperApiError.api_key_not_found()
            if current.status != 'active' or self._is_expired(current, now):
                raise DeveloperApiError.api_key_inactive()
            grants = await self._dao.list_grants(self._db, key_id)
            quota = await self._dao.get_quota_by_key(self._db, key_id)
            if quota is None:
                raise DeveloperKeyOperationError
            transition_values = self._rotation_transition(payload, actor=actor, now=now)
            changed = await self._dao.transition_key_status(
                self._db,
                application_id,
                key_id,
                expected_statuses={'active'},
                values=transition_values,
            )
            if changed != 1:
                raise DeveloperApiError.api_key_inactive()
            response = await self._persist_rotated_key(
                application_id,
                current,
                grants,
                quota,
                issued,
                actor=actor,
                now=now,
            )
            await self._db.commit()
            return _SensitiveWriteOutcome(response=response)
        except BaseException as error:
            failure = await self._classify_sensitive_failure(error)
            return failure

    async def _persist_new_key(
        self,
        application_id: str,
        payload: DeveloperKeyCreateModel,
        issued: IssuedDeveloperKey,
        *,
        actor: DeveloperApiActor,
        now: datetime,
    ) -> DeveloperKeyCreatedModel:
        database_now = self._database_time(now)
        key = AiApiKey(
            api_key_id=issued.key_id,
            application_id=application_id,
            key_name=payload.key_name,
            key_prefix=issued.prefix,
            secret_digest=issued.digest,
            digest_version=issued.digest_version,
            fingerprint=issued.fingerprint,
            status='active',
            expires_at=self._database_time(payload.expires_at) if payload.expires_at is not None else None,
            retire_at=None,
            last_used_at=None,
            revoked_at=None,
            created_by=actor.username,
            created_at=database_now,
            updated_by=actor.username,
            updated_at=database_now,
        )
        grants = [
            self._new_grant(issued.key_id, model_id, actor=actor, now=database_now) for model_id in payload.model_ids
        ]
        quota = self._new_quota(issued.key_id, payload.quota.model_dump(), actor=actor, now=database_now)
        await self._dao.create_key(self._db, key)
        await self._dao.replace_grants(self._db, issued.key_id, grants)
        await self._dao.create_quota(self._db, quota)
        return self._created_response(key, grants, quota, issued.complete_key)

    async def _persist_rotated_key(
        self,
        application_id: str,
        current: AiApiKey,
        grants: Sequence[AiApiKeyModelGrant],
        quota: AiApiQuotaPolicy,
        issued: IssuedDeveloperKey,
        *,
        actor: DeveloperApiActor,
        now: datetime,
    ) -> DeveloperKeyCreatedModel:
        database_now = self._database_time(now)
        key = AiApiKey(
            api_key_id=issued.key_id,
            application_id=application_id,
            key_name=current.key_name,
            key_prefix=issued.prefix,
            secret_digest=issued.digest,
            digest_version=issued.digest_version,
            fingerprint=issued.fingerprint,
            status='active',
            expires_at=current.expires_at,
            retire_at=None,
            last_used_at=None,
            revoked_at=None,
            created_by=actor.username,
            created_at=database_now,
            updated_by=actor.username,
            updated_at=database_now,
        )
        copied_grants = [
            AiApiKeyModelGrant(
                grant_id=new_resource_id('grt'),
                api_key_id=issued.key_id,
                platform_model_id=grant.platform_model_id,
                status=grant.status,
                created_by=actor.username,
                created_at=database_now,
                updated_by=actor.username,
                updated_at=database_now,
            )
            for grant in grants
        ]
        copied_quota = self._new_quota(
            issued.key_id,
            {
                'rpm_limit': quota.rpm_limit,
                'tpm_limit': quota.tpm_limit,
                'concurrency_limit': quota.concurrency_limit,
                'monthly_budget': quota.monthly_budget,
                'warning_ratio': quota.warning_ratio,
                'hard_limit_ratio': quota.hard_limit_ratio,
                'currency': quota.currency,
            },
            actor=actor,
            now=database_now,
        )
        await self._dao.create_key(self._db, key)
        await self._dao.replace_grants(self._db, issued.key_id, copied_grants)
        await self._dao.create_quota(self._db, copied_quota)
        return self._created_response(key, copied_grants, copied_quota, issued.complete_key)

    def _new_grant(
        self,
        key_id: str,
        model_id: str,
        *,
        actor: DeveloperApiActor,
        now: datetime,
    ) -> AiApiKeyModelGrant:
        return AiApiKeyModelGrant(
            grant_id=new_resource_id('grt'),
            api_key_id=key_id,
            platform_model_id=model_id,
            status='active',
            created_by=actor.username,
            created_at=now,
            updated_by=actor.username,
            updated_at=now,
        )

    def _new_quota(
        self,
        key_id: str,
        values: dict[str, Any],
        *,
        actor: DeveloperApiActor,
        now: datetime,
    ) -> AiApiQuotaPolicy:
        return AiApiQuotaPolicy(
            quota_id=new_resource_id('qta'),
            api_key_id=key_id,
            **values,
            version=1,
            created_by=actor.username,
            created_at=now,
            updated_by=actor.username,
            updated_at=now,
        )

    async def _summary(self, key: AiApiKey) -> DeveloperKeySummaryModel:
        return await self._summary_from_snapshot(self._key_snapshot(key))

    async def _summary_from_snapshot(self, snapshot: dict[str, Any]) -> DeveloperKeySummaryModel:
        key_id = snapshot['key_id']
        grants = await self._dao.list_grants(self._db, key_id)
        quota = await self._dao.get_quota_by_key(self._db, key_id)
        if quota is None:
            raise DeveloperKeyOperationError
        return DeveloperKeySummaryModel.model_validate(
            {
                **snapshot,
                'masked_key': self._codec.mask(snapshot['key_prefix'], snapshot['fingerprint']),
                'grants': [self._grant_response(grant) for grant in grants],
                'quota': QuotaPolicyModel.model_validate(quota),
            }
        )

    def _created_response(
        self,
        key: AiApiKey,
        grants: Sequence[AiApiKeyModelGrant],
        quota: AiApiQuotaPolicy,
        complete_key: str,
    ) -> DeveloperKeyCreatedModel:
        snapshot = self._key_snapshot(key)
        return DeveloperKeyCreatedModel.model_validate(
            {
                **snapshot,
                'masked_key': self._codec.mask(key.key_prefix, key.fingerprint),
                'grants': [self._grant_response(grant) for grant in grants],
                'quota': QuotaPolicyModel.model_validate(quota),
                'api_key': complete_key,
            }
        )

    @staticmethod
    def _key_snapshot(key: AiApiKey) -> dict[str, Any]:
        return {
            'key_id': key.api_key_id,
            'application_id': key.application_id,
            'key_name': key.key_name,
            'key_prefix': key.key_prefix,
            'fingerprint': key.fingerprint,
            'status': key.status,
            'expires_at': key.expires_at,
            'retire_at': key.retire_at,
            'last_used_at': key.last_used_at,
            'created_at': key.created_at,
        }

    @staticmethod
    def _grant_response(grant: AiApiKeyModelGrant) -> ModelGrantModel:
        return ModelGrantModel.model_validate(
            {
                'grant_id': grant.grant_id,
                'model_id': grant.platform_model_id,
                'status': grant.status,
            }
        )

    @staticmethod
    def _rotation_transition(
        payload: DeveloperKeyRotateModel,
        *,
        actor: DeveloperApiActor,
        now: datetime,
    ) -> dict[str, Any]:
        database_now = DeveloperKeyService._database_time(now)
        if payload.grace_period_seconds == 0:
            return {
                'status': 'revoked',
                'retire_at': None,
                'revoked_at': database_now,
                'updated_by': actor.username,
                'updated_at': database_now,
            }
        return {
            'status': 'retiring',
            'retire_at': database_now + timedelta(seconds=payload.grace_period_seconds),
            'revoked_at': None,
            'updated_by': actor.username,
            'updated_at': database_now,
        }

    @staticmethod
    def _is_expired(key: AiApiKey, now: datetime) -> bool:
        if key.expires_at is None:
            return False
        return key.expires_at <= DeveloperKeyService._database_time(now)

    @staticmethod
    def _require_application_access(application: AiApplication | None, actor: DeveloperApiActor) -> None:
        if application is None or (not actor.is_admin and application.owner_user_id != actor.user_id):
            raise DeveloperApiError.application_not_found()

    @classmethod
    def _require_active_application(cls, application: AiApplication | None, actor: DeveloperApiActor) -> None:
        cls._require_application_access(application, actor)
        if application.status != 'active':
            raise DeveloperApiError.application_disabled()

    async def _classify_sensitive_failure(self, error: BaseException) -> _SensitiveWriteOutcome:
        rollback_base_exception = await self._rollback_sensitive()
        if rollback_base_exception is asyncio.CancelledError or isinstance(error, asyncio.CancelledError):
            return _SensitiveWriteOutcome(failure='CANCELLED')
        if rollback_base_exception is not None:
            return _SensitiveWriteOutcome(
                failure='OPERATION_FAILED',
                base_exception_type=rollback_base_exception,
            )
        if isinstance(error, DeveloperApiError):
            return _SensitiveWriteOutcome(failure=error.code)  # type: ignore[arg-type]
        if isinstance(error, Exception):
            return _SensitiveWriteOutcome(failure='OPERATION_FAILED')
        return _SensitiveWriteOutcome(failure='OPERATION_FAILED', base_exception_type=type(error))

    async def _rollback_sensitive(self) -> type[BaseException] | None:
        try:
            await self._db.rollback()
        except asyncio.CancelledError:
            return asyncio.CancelledError
        except Exception:
            pass
        except BaseException as error:
            return type(error)
        return None

    @staticmethod
    def _unwrap_sensitive_outcome(outcome: _SensitiveWriteOutcome) -> DeveloperKeyCreatedModel:
        if outcome.response is not None:
            return outcome.response
        if outcome.failure is None:
            raise DeveloperKeyOperationError from None
        return DeveloperKeyService._raise_sensitive_failure(outcome.failure, outcome.base_exception_type)

    @staticmethod
    def _raise_sensitive_failure(
        failure: FailureKind,
        base_exception_type: type[BaseException] | None,
    ) -> NoReturn:
        factories = {
            'APPLICATION_NOT_FOUND': DeveloperApiError.application_not_found,
            'APPLICATION_DISABLED': DeveloperApiError.application_disabled,
            'API_KEY_NOT_FOUND': DeveloperApiError.api_key_not_found,
            'API_KEY_INACTIVE': DeveloperApiError.api_key_inactive,
            'MODEL_GRANT_INVALID': DeveloperApiError.model_grant_invalid,
            'DEVELOPER_API_MISCONFIGURED': DeveloperApiError.configuration_error,
        }
        factory = factories.get(failure)
        if factory is not None:
            raise factory() from None
        if failure == 'CANCELLED':
            raise asyncio.CancelledError from None
        if base_exception_type is not None:
            raise base_exception_type() from None
        raise DeveloperKeyOperationError from None

    async def _rollback_preserving_error(self) -> None:
        try:
            await self._db.rollback()
        except Exception:
            pass

    @staticmethod
    def _database_time(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
