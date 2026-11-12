"""Upload and body contracts are declared on actual routes, not URL patterns."""

from dataclasses import dataclass
from typing import cast

from fastapi.routing import APIRoute, iter_route_contexts
from starlette.routing import Match
from starlette.types import Scope

JSON_LIMIT = 1024 * 1024
FILE_LIMIT = 20 * 1024 * 1024
MULTIPART_LIMIT = FILE_LIMIT + 64 * 1024


@dataclass(frozen=True)
class TransportPolicy:
    media_type: str | None
    max_bytes: int


POLICIES = {
    'json': TransportPolicy('application/json', JSON_LIMIT),
    'binary_file': TransportPolicy('application/octet-stream', FILE_LIMIT),
    'binary_skill': TransportPolicy('application/octet-stream', 10 * 1024 * 1024),
    'multipart_file': TransportPolicy('multipart/form-data', MULTIPART_LIMIT),
    'bodyless': TransportPolicy(None, 0),
}


def transport_policy(scope: Scope) -> TransportPolicy:
    for context in iter_route_contexts(scope['app'].router.routes):
        route = context.original_route
        if isinstance(route, APIRoute) and context.matches(scope)[0] == Match.FULL:
            name = (route.openapi_extra or {}).get('x-transport-policy', 'json')
            return POLICIES[cast('str', name)]
    return POLICIES['json']
