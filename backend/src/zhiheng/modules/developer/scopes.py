"""Finite, issuance-time capabilities; model allowlists are an additional restriction."""

from typing import Literal

Scope = Literal[
    'models:invoke',
    'agents:invoke',
    'files:read',
    'files:write',
    'knowledge:read',
    'knowledge:write',
    'knowledge:query',
    'skills:read',
    'skills:write',
]
SCOPES: tuple[Scope, ...] = (
    'models:invoke',
    'agents:invoke',
    'files:read',
    'files:write',
    'knowledge:read',
    'knowledge:write',
    'knowledge:query',
    'skills:read',
    'skills:write',
)
INVOKE_SCOPES: tuple[Scope, ...] = ('models:invoke', 'agents:invoke')
RESOURCE_RPM_MAX = 120
ResourceKind = Literal['file', 'knowledge_base', 'skill']
ResourceAction = Literal['read', 'write', 'query', 'delete']


def resource_scope(kind: ResourceKind, action: ResourceAction) -> Scope:
    if kind == 'file':
        return 'files:read' if action == 'read' else 'files:write'
    if kind == 'skill':
        return 'skills:read' if action == 'read' else 'skills:write'
    if action == 'query':
        return 'knowledge:query'
    return 'knowledge:read' if action == 'read' else 'knowledge:write'


def validate_actions(kind: ResourceKind, actions: tuple[ResourceAction, ...]) -> None:
    if not actions or len(set(actions)) != len(actions) or (kind != 'knowledge_base' and 'query' in actions):
        raise ValueError('Invalid resource actions')
