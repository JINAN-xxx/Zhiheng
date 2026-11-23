"""Shared login admission limits before expensive password verification."""

import hashlib
from ipaddress import ip_address
from typing import TYPE_CHECKING, cast

from redis.asyncio import Redis
from redis.exceptions import RedisError

from zhiheng.shared.errors import AppError

if TYPE_CHECKING:
    from collections.abc import Awaitable

ACCOUNT_ATTEMPTS = 10
SOURCE_ATTEMPTS = 60
WINDOW_SECONDS = 60
CLIENT_IP_HEADER = 'x-forwarded-for'
CONSUME = """
local retry = 0
for i, key in ipairs(KEYS) do
    local count = tonumber(redis.call('GET', key) or '0')
    if count >= tonumber(ARGV[i]) then
        retry = math.max(retry, redis.call('TTL', key), 1)
    end
end
if retry > 0 then return retry end
for _, key in ipairs(KEYS) do
    local count = redis.call('INCR', key)
    if count == 1 then redis.call('EXPIRE', key, ARGV[3]) end
end
return 0
"""


class LoginRateLimiter:
    def __init__(self, redis: Redis, namespace: str) -> None:
        self.redis = redis
        self.namespace = namespace + ':login'

    def _key(self, kind: str, value: str) -> str:
        digest = hashlib.sha256(value.encode()).hexdigest()
        return f'{self.namespace}:{kind}:{digest}'

    async def consume(self, username: str, source: str) -> None:
        # Source is ASGI's trusted peer address; never trust arbitrary forwarded headers.
        keys = (self._key('account', username.casefold()), self._key('source', source))
        try:
            result = await cast(
                'Awaitable[int]',
                self.redis.eval(
                    CONSUME, len(keys), *keys, str(ACCOUNT_ATTEMPTS), str(SOURCE_ATTEMPTS), str(WINDOW_SECONDS)
                ),
            )
            retry = int(result)
        except RedisError as error:
            raise AppError('login_unavailable', '登录服务暂时不可用，请稍后重试', 503) from error
        if retry:
            raise AppError('login_rate_limited', '登录尝试过于频繁，请稍后重试', 429, details={'retry_after': retry})


def login_source(peer: str, forwarded: list[str], trusted_proxies: frozenset[str]) -> str:
    if peer not in trusted_proxies or len(forwarded) != 1 or '%' in forwarded[0]:
        return peer
    try:
        return str(ip_address(forwarded[0].strip()))
    except ValueError:
        return peer
