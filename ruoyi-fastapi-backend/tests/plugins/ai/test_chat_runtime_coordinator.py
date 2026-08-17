import asyncio
import inspect
from collections.abc import Callable
from typing import Any

import pytest

from plugins.ai.service.chat_runtime_coordinator import (
    CANCEL_TTL_SECONDS,
    LOCK_TTL_SECONDS,
    ChatCoordinationError,
    ChatRuntimeCoordinator,
)

SESSION_ID = 'ses_123e4567e89b42d3a456426614174000'
RUN_ID = 'run_987e4567e89b42d3a456426614174111'
LOCK_KEY = f'ai:chat:lock:{SESSION_ID}'
CANCEL_KEY = f'ai:chat:cancel:{RUN_ID}'
PRIVATE_MARKER = 'redis://:PRIVATE_PASSWORD@internal/0'
HTTP_CONFLICT = 409
HTTP_SERVICE_UNAVAILABLE = 503


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}
        self.set_calls: list[tuple[str, str, bool, int]] = []
        self.eval_calls: list[tuple[str, int, str, str]] = []
        self.get_calls: list[str] = []
        self.fail_operation: str | None = None

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int) -> bool:
        self._raise_if_failed('set')
        self.set_calls.append((key, value, nx, ex))
        if nx and key in self.values:
            return False
        self.values[key] = value
        self.expirations[key] = ex
        return True

    async def get(self, key: str) -> str | None:
        self._raise_if_failed('get')
        self.get_calls.append(key)
        return self.values.get(key)

    async def eval(self, script: str, key_count: int, key: str, token: str) -> int:
        self._raise_if_failed('eval')
        self.eval_calls.append((script, key_count, key, token))
        if self.values.get(key) != token:
            return 0
        del self.values[key]
        self.expirations.pop(key, None)
        return 1

    def _raise_if_failed(self, operation: str) -> None:
        if self.fail_operation == operation:
            raise RuntimeError(PRIVATE_MARKER)


class TokenFactory:
    def __init__(self) -> None:
        self._next = 0

    def __call__(self) -> str:
        self._next += 1
        return f'private-lock-token-{self._next}'


def _coordinator(
    redis: FakeRedis,
    token_factory: Callable[[], str] | None = None,
) -> ChatRuntimeCoordinator:
    return ChatRuntimeCoordinator(redis, token_factory=token_factory or TokenFactory())


def _production_traceback_locals(error: BaseException) -> str:
    values: list[dict[str, Any]] = []
    trace = error.__traceback__
    while trace is not None:
        filename = trace.tb_frame.f_code.co_filename.replace('\\', '/')
        if '/plugins/ai/' in filename:
            values.append(dict(trace.tb_frame.f_locals))
        trace = trace.tb_next
    return repr(values)


def _assert_unavailable(error: ChatCoordinationError) -> None:
    assert error.status_code == HTTP_SERVICE_UNAVAILABLE
    assert error.public_dict() == {
        'code': 'CHAT_COORDINATION_UNAVAILABLE',
        'message': '对话协调服务暂不可用',
    }
    assert error.args == ()
    assert error.__cause__ is None
    assert error.__context__ is None
    assert PRIVATE_MARKER not in repr(error)
    assert PRIVATE_MARKER not in _production_traceback_locals(error)


@pytest.mark.asyncio
async def test_session_lock_uses_fixed_key_nx_ttl_unique_token_and_compare_delete() -> None:
    redis = FakeRedis()
    tokens = TokenFactory()
    coordinator = _coordinator(redis, tokens)

    async with coordinator.acquire_session(SESSION_ID):
        first_token = redis.values[LOCK_KEY]
        assert redis.set_calls == [(LOCK_KEY, first_token, True, LOCK_TTL_SECONDS)]

    assert LOCK_KEY not in redis.values
    script, key_count, key, released_token = redis.eval_calls[0]
    assert key_count == 1
    assert key == LOCK_KEY
    assert released_token == first_token
    assert "redis.call('get'" in script
    assert "redis.call('del'" in script

    async with coordinator.acquire_session(SESSION_ID):
        second_token = redis.values[LOCK_KEY]
        assert second_token != first_token


@pytest.mark.asyncio
async def test_session_lock_does_not_delete_a_lock_owned_by_another_worker() -> None:
    redis = FakeRedis()
    coordinator = _coordinator(redis)

    async with coordinator.acquire_session(SESSION_ID):
        redis.values[LOCK_KEY] = 'another-worker-token'

    assert redis.values[LOCK_KEY] == 'another-worker-token'


@pytest.mark.asyncio
async def test_concurrent_session_lock_returns_stable_busy_error_without_release() -> None:
    redis = FakeRedis()
    coordinator = _coordinator(redis)

    async with coordinator.acquire_session(SESSION_ID):
        with pytest.raises(ChatCoordinationError) as raised:
            async with coordinator.acquire_session(SESSION_ID):
                pytest.fail('busy session must not enter the context')

    assert raised.value.status_code == HTTP_CONFLICT
    assert raised.value.public_dict() == {
        'code': 'CHAT_SESSION_BUSY',
        'message': '当前会话正在生成，请稍后重试',
    }
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert len(redis.eval_calls) == 1


def test_coordination_errors_are_fixed_and_immutable() -> None:
    error = ChatCoordinationError.session_busy()

    with pytest.raises(TypeError):
        ChatCoordinationError(0)
    with pytest.raises(AttributeError):
        error.args = ('forged redis detail',)
    with pytest.raises(AttributeError):
        error._kind_index = 1  # type: ignore[misc]

    assert error.args == ()
    assert error.public_dict() == {
        'code': 'CHAT_SESSION_BUSY',
        'message': '当前会话正在生成，请稍后重试',
    }


@pytest.mark.asyncio
async def test_session_lock_releases_after_body_error_and_preserves_original_error() -> None:
    redis = FakeRedis()

    with pytest.raises(ValueError, match='body failed') as raised:
        async with _coordinator(redis).acquire_session(SESSION_ID):
            raise ValueError('body failed')

    assert LOCK_KEY not in redis.values
    assert len(redis.eval_calls) == 1
    assert raised.value.__context__ is None


@pytest.mark.asyncio
async def test_session_lock_releases_after_cancellation_and_preserves_cancellation() -> None:
    redis = FakeRedis()

    with pytest.raises(asyncio.CancelledError) as raised:
        async with _coordinator(redis).acquire_session(SESSION_ID):
            raise asyncio.CancelledError

    assert LOCK_KEY not in redis.values
    assert len(redis.eval_calls) == 1
    assert raised.value.__context__ is None


@pytest.mark.asyncio
async def test_release_failure_is_visible_after_normal_body() -> None:
    redis = FakeRedis()
    coordinator = _coordinator(redis)

    with pytest.raises(ChatCoordinationError) as raised:
        async with coordinator.acquire_session(SESSION_ID):
            redis.fail_operation = 'eval'

    _assert_unavailable(raised.value)


@pytest.mark.asyncio
async def test_release_failure_does_not_replace_active_error_or_cancellation() -> None:
    for active_error in (ValueError('body failed'), asyncio.CancelledError()):
        redis = FakeRedis()
        coordinator = _coordinator(redis)

        with pytest.raises(type(active_error)) as raised:
            async with coordinator.acquire_session(SESSION_ID):
                redis.fail_operation = 'eval'
                raise active_error

        assert raised.value is active_error
        assert raised.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ('set', 'get'))
async def test_redis_faults_are_fail_closed_and_sanitized(operation: str) -> None:
    redis = FakeRedis()
    redis.fail_operation = operation
    coordinator = _coordinator(redis)

    with pytest.raises(ChatCoordinationError) as raised:
        if operation == 'set':
            async with coordinator.acquire_session(SESSION_ID):
                pytest.fail('failed lock must not enter the context')
        else:
            await coordinator.is_cancelled(RUN_ID)

    _assert_unavailable(raised.value)


@pytest.mark.asyncio
async def test_redis_cancellation_is_not_mapped_to_unavailable() -> None:
    class CancelledRedis(FakeRedis):
        async def get(self, key: str) -> str | None:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await _coordinator(CancelledRedis()).is_cancelled(RUN_ID)


@pytest.mark.asyncio
async def test_cancel_marker_uses_fixed_key_and_ttl() -> None:
    redis = FakeRedis()
    coordinator = _coordinator(redis)

    await coordinator.request_cancel(RUN_ID)

    assert redis.set_calls == [(CANCEL_KEY, '1', False, CANCEL_TTL_SECONDS)]
    assert redis.values[CANCEL_KEY] == '1'
    assert await coordinator.is_cancelled(RUN_ID) is True
    redis.values.pop(CANCEL_KEY)
    assert await coordinator.is_cancelled(RUN_ID) is False


@pytest.mark.asyncio
async def test_cancel_write_failure_is_stable_and_sanitized() -> None:
    redis = FakeRedis()
    redis.fail_operation = 'set'

    with pytest.raises(ChatCoordinationError) as raised:
        await _coordinator(redis).request_cancel(RUN_ID)

    _assert_unavailable(raised.value)


@pytest.mark.parametrize(
    ('method_name', 'resource_id'),
    (
        ('acquire_session', RUN_ID),
        ('request_cancel', SESSION_ID),
        ('is_cancelled', SESSION_ID),
    ),
)
def test_resource_ids_are_validated_before_redis_access(method_name: str, resource_id: str) -> None:
    redis = FakeRedis()
    coordinator = _coordinator(redis)

    with pytest.raises(ValueError):
        result = getattr(coordinator, method_name)(resource_id)
        if inspect.isawaitable(result):
            asyncio.run(result)

    assert redis.set_calls == []
    assert redis.get_calls == []
    assert redis.eval_calls == []
