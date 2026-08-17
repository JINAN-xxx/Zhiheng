from collections.abc import Callable
from dataclasses import dataclass
from secrets import token_urlsafe
from types import MappingProxyType
from typing import Any, ClassVar, NoReturn

from plugins.ai.domain.resource_id import validate_resource_id

LOCK_TTL_SECONDS = 900
CANCEL_TTL_SECONDS = 60

_LOCK_KEY_PREFIX = 'ai:chat:lock:'
_CANCEL_KEY_PREFIX = 'ai:chat:cancel:'
_CANCEL_VALUE = '1'
_RELEASE_LOCK_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
""".strip()
_ERROR_CONTRACTS = (
    (409, 'CHAT_SESSION_BUSY', '当前会话正在生成，请稍后重试'),
    (503, 'CHAT_COORDINATION_UNAVAILABLE', '对话协调服务暂不可用'),
)
_ERROR_VIEW = MappingProxyType(
    {
        'session_busy': _ERROR_CONTRACTS[0],
        'unavailable': _ERROR_CONTRACTS[1],
    }
)
_ERROR_CONSTRUCTION_TOKEN = object()


def _error_contract(kind_index: object) -> tuple[int, str, str]:
    if type(kind_index) is not int or not 0 <= kind_index < len(_ERROR_CONTRACTS):
        raise TypeError('无效的对话协调错误分类')
    return _ERROR_CONTRACTS[kind_index]


def _trusted_error_index(error: 'ChatCoordinationError') -> int:
    if type(error) is not ChatCoordinationError:
        raise TypeError('无效的对话协调错误实例')
    try:
        kind_index = object.__getattribute__(error, '_kind_index')
        args = object.__getattribute__(error, 'args')
    except AttributeError:
        raise TypeError('无效的对话协调错误实例') from None
    _error_contract(kind_index)
    if args != ():
        raise TypeError('无效的对话协调错误实例')
    return kind_index


def _new_coordination_error(
    kind_index: int,
    token: object = _ERROR_CONSTRUCTION_TOKEN,
) -> 'ChatCoordinationError':
    return ChatCoordinationError(kind_index, token)


def _restore_coordination_error(kind_index: int) -> 'ChatCoordinationError':
    return _new_coordination_error(kind_index)


class ChatCoordinationError(Exception):
    """对外固定、不可变且不携带 Redis 细节的协调错误。"""

    __slots__ = ('_kind_index',)
    _ERRORS: ClassVar = _ERROR_VIEW

    def __init_subclass__(cls, **kwargs: object) -> NoReturn:
        raise TypeError('ChatCoordinationError 不允许继承')

    def __init__(self, kind_index: int, token: object | None = None) -> None:
        if token is not _ERROR_CONSTRUCTION_TOKEN:
            raise TypeError('ChatCoordinationError 只能通过固定工厂创建')
        _error_contract(kind_index)
        object.__setattr__(self, '_kind_index', kind_index)
        object.__setattr__(self, 'args', ())

    def __setattr__(self, name: str, value: object) -> NoReturn:
        raise AttributeError('ChatCoordinationError 实例不可修改')

    def __delattr__(self, name: str) -> NoReturn:
        raise AttributeError('ChatCoordinationError 实例不可修改')

    @property
    def status_code(self) -> int:
        return _error_contract(_trusted_error_index(self))[0]

    @property
    def code(self) -> str:
        return _error_contract(_trusted_error_index(self))[1]

    @property
    def public_message(self) -> str:
        return _error_contract(_trusted_error_index(self))[2]

    def __str__(self) -> str:
        return self.public_message

    def __repr__(self) -> str:
        return f'ChatCoordinationError(code={self.code!r}, message={self.public_message!r})'

    def __reduce__(self) -> tuple[Callable[[int], 'ChatCoordinationError'], tuple[int]]:
        return _restore_coordination_error, (_trusted_error_index(self),)

    def public_dict(self) -> dict[str, str]:
        return {'code': self.code, 'message': self.public_message}

    @staticmethod
    def session_busy() -> 'ChatCoordinationError':
        return _new_coordination_error(0)

    @staticmethod
    def unavailable() -> 'ChatCoordinationError':
        return _new_coordination_error(1)


@dataclass(frozen=True, slots=True)
class _RedisOutcome:
    failed: bool
    value: object | None = None


async def _redis_set(
    redis: Any,
    key: str,
    value: str,
    *,
    nx: bool,
    ttl: int,
) -> _RedisOutcome:
    try:
        result = await redis.set(key, value, nx=nx, ex=ttl)
    except Exception:
        return _RedisOutcome(failed=True)
    return _RedisOutcome(failed=False, value=result)


async def _redis_get(redis: Any, key: str) -> _RedisOutcome:
    try:
        result = await redis.get(key)
    except Exception:
        return _RedisOutcome(failed=True)
    return _RedisOutcome(failed=False, value=result)


async def _redis_release(redis: Any, key: str, token: str) -> _RedisOutcome:
    try:
        result = await redis.eval(_RELEASE_LOCK_SCRIPT, 1, key, token)
    except Exception:
        return _RedisOutcome(failed=True)
    return _RedisOutcome(failed=False, value=result)


def _raise_unavailable() -> NoReturn:
    raise ChatCoordinationError.unavailable()


class _SessionLock:
    def __init__(self, redis: Any, key: str, token: str) -> None:
        self._redis = redis
        self._key = key
        self._token = token
        self._acquired = False

    async def __aenter__(self) -> None:
        outcome = await _redis_set(
            self._redis,
            self._key,
            self._token,
            nx=True,
            ttl=LOCK_TTL_SECONDS,
        )
        if outcome.failed:
            _raise_unavailable()
        if outcome.value is not True:
            raise ChatCoordinationError.session_busy()
        self._acquired = True

    async def __aexit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object | None,
    ) -> bool:
        if not self._acquired:
            return False
        self._acquired = False
        try:
            outcome = await _redis_release(self._redis, self._key, self._token)
        except BaseException:
            if exception_type is not None:
                return False
            raise
        if outcome.failed and exception_type is None:
            _raise_unavailable()
        return False


class ChatRuntimeCoordinator:
    """使用平台共享 Redis 客户端协调 Session 执行和 Run 取消。"""

    def __init__(
        self,
        redis: Any,
        *,
        token_factory: Callable[[], str] = token_urlsafe,
    ) -> None:
        self._redis = redis
        self._token_factory = token_factory

    def acquire_session(self, session_id: str) -> _SessionLock:
        validate_resource_id(session_id, 'ses')
        token = self._token_factory()
        if not isinstance(token, str) or not token:
            raise TypeError('锁 token 必须是非空字符串')
        return _SessionLock(self._redis, f'{_LOCK_KEY_PREFIX}{session_id}', token)

    async def request_cancel(self, run_id: str) -> None:
        validate_resource_id(run_id, 'run')
        outcome = await _redis_set(
            self._redis,
            f'{_CANCEL_KEY_PREFIX}{run_id}',
            _CANCEL_VALUE,
            nx=False,
            ttl=CANCEL_TTL_SECONDS,
        )
        if outcome.failed:
            _raise_unavailable()

    async def is_cancelled(self, run_id: str) -> bool:
        validate_resource_id(run_id, 'run')
        outcome = await _redis_get(self._redis, f'{_CANCEL_KEY_PREFIX}{run_id}')
        if outcome.failed:
            _raise_unavailable()
        return outcome.value is not None
