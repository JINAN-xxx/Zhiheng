import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Generic, Literal, NoReturn, Protocol, TypeVar, cast

from sqlalchemy.ext.asyncio import AsyncSession

from plugins.ai.dao.ai_developer_api_dao import AiDeveloperApiDao, PublicModelRecord
from plugins.ai.domain.public_api_errors import PublicApiError
from plugins.ai.entity.vo.ai_openai_compat_vo import OpenAIModelListResponseModel
from plugins.ai.infrastructure.developer_key_authenticator import DeveloperApiIdentity

ResultT = TypeVar('ResultT')
FailureKind = Literal['MODEL_NOT_FOUND', 'UNAVAILABLE', 'CANCELLED']


class EntitlementDao(Protocol):
    async def list_public_models_for_key(self, db: AsyncSession, api_key_id: str) -> list[PublicModelRecord]: ...

    async def get_public_model_for_key(
        self,
        db: AsyncSession,
        api_key_id: str,
        model_code: str,
    ) -> PublicModelRecord | None: ...


@dataclass(frozen=True, slots=True)
class AuthorizedPublicModel:
    platform_model_id: str
    model_code: str


@dataclass(frozen=True, slots=True)
class _EntitlementOutcome(Generic[ResultT]):
    value: ResultT | None = None
    failure: FailureKind | None = None
    base_exception_type: type[BaseException] | None = None


def _raise_entitlement_failure(
    failure: FailureKind,
    base_exception_type: type[BaseException] | None,
) -> NoReturn:
    if failure == 'CANCELLED' and base_exception_type is not None:
        raise base_exception_type from None
    if failure == 'MODEL_NOT_FOUND':
        raise PublicApiError.model_not_found() from None
    raise PublicApiError.service_unavailable() from None


def _unwrap_outcome(outcome: _EntitlementOutcome[ResultT]) -> ResultT:
    if outcome.failure is not None:
        _raise_entitlement_failure(outcome.failure, outcome.base_exception_type)
    return cast('ResultT', outcome.value)


class ApiEntitlementService:
    """Resolve public model entitlements without exposing engine metadata."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        developer_api_dao: EntitlementDao = AiDeveloperApiDao,
    ) -> None:
        self._db = db
        self._dao = developer_api_dao
        if db.in_transaction():
            raise RuntimeError('Public API 授权服务需要独占的只读数据库会话')

    async def list_models(self, identity: DeveloperApiIdentity) -> OpenAIModelListResponseModel:
        return _unwrap_outcome(await self._list_models_sensitive(identity.key_id))

    async def resolve_model(self, identity: DeveloperApiIdentity, model_code: str) -> AuthorizedPublicModel:
        return _unwrap_outcome(await self._resolve_model_sensitive(identity.key_id, model_code))

    async def _list_models_sensitive(self, key_id: str) -> _EntitlementOutcome[OpenAIModelListResponseModel]:
        try:
            records = await self._dao.list_public_models_for_key(self._db, key_id)
            response = OpenAIModelListResponseModel.model_validate(
                {
                    'object': 'list',
                    'data': [
                        {
                            'id': record.model_code,
                            'object': 'model',
                            'created': self._unix_time(record.published_at),
                            'owned_by': 'zhiheng',
                        }
                        for record in records
                    ],
                }
            )
            await self._rollback_visible()
            return _EntitlementOutcome(value=response)
        except BaseException as error:
            await self._rollback_preserving_error()
            return self._failure_outcome(error)

    async def _resolve_model_sensitive(
        self,
        key_id: str,
        model_code: str,
    ) -> _EntitlementOutcome[AuthorizedPublicModel]:
        try:
            record = await self._dao.get_public_model_for_key(self._db, key_id, model_code)
            await self._rollback_visible()
            if record is None:
                return _EntitlementOutcome(failure='MODEL_NOT_FOUND')
            return _EntitlementOutcome(
                value=AuthorizedPublicModel(
                    platform_model_id=record.platform_model_id,
                    model_code=record.model_code,
                )
            )
        except BaseException as error:
            await self._rollback_preserving_error()
            return self._failure_outcome(error)

    @staticmethod
    def _failure_outcome(error: BaseException) -> _EntitlementOutcome[object]:
        if isinstance(error, asyncio.CancelledError):
            return _EntitlementOutcome(failure='CANCELLED', base_exception_type=type(error))
        return _EntitlementOutcome(failure='UNAVAILABLE')

    @staticmethod
    def _unix_time(value: datetime) -> int:
        normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return int(normalized.timestamp())

    async def _rollback_visible(self) -> None:
        if self._db.in_transaction():
            await self._db.rollback()

    async def _rollback_preserving_error(self) -> None:
        try:
            await self._rollback_visible()
        except BaseException:
            pass
