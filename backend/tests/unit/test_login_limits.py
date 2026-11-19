from unittest.mock import AsyncMock

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from zhiheng.infrastructure.login_limits import LoginRateLimiter
from zhiheng.shared.errors import AppError


async def test_login_limit_fails_closed_when_shared_counter_is_unavailable() -> None:
    redis = AsyncMock()
    redis.eval.side_effect = RedisConnectionError('private redis address')
    with pytest.raises(AppError) as error:
        await LoginRateLimiter(redis, 'test').consume('alice', '127.0.0.1')
    assert error.value.status_code == 503 and 'private redis address' not in error.value.message


async def test_shared_counter_keys_do_not_contain_usernames_or_network_addresses() -> None:
    redis = AsyncMock()
    redis.eval.return_value = 0
    await LoginRateLimiter(redis, 'test').consume('Alice@example.com', '192.0.2.123')
    arguments = str(redis.eval.call_args)
    assert 'Alice@example.com' not in arguments and '192.0.2.123' not in arguments


async def test_password_verifications_have_bounded_parallelism() -> None:
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import patch

    from zhiheng.modules.identity.public import IdentityService

    identity = IdentityService()
    session = SimpleNamespace(scalar=AsyncMock(return_value=None))
    started = 0
    maximum = 0

    async def verify(*args: object) -> None:
        nonlocal started, maximum
        started += 1
        maximum = max(maximum, started)
        await asyncio.sleep(0.01)
        started -= 1

    with patch('zhiheng.modules.identity.service.asyncio.to_thread', verify):
        await asyncio.gather(
            *(identity.login(session, username='missing', password='wrong') for _ in range(8)), return_exceptions=True
        )
    assert maximum == 2
