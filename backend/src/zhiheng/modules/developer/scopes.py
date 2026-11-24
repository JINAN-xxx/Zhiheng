"""Finite, issuance-time capabilities; model allowlists are an additional restriction."""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Literal

from zhiheng.shared.errors import AppError
from zhiheng.shared.platform_permissions import require_capability
from zhiheng.shared.principal import Principal

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


@dataclass(frozen=True)
class ScopeDefinition:
    scope: Scope
    label: str
    description: str
    requires_models: bool = False


SCOPE_DEFINITIONS = (
    ScopeDefinition('models:invoke', '模型调用', '调用允许列表中的模型', True),
    ScopeDefinition('agents:invoke', '智能体调用', '调用此密钥选定的智能体'),
    ScopeDefinition('files:read', '读取文件', '读取已授权文件的元数据与内容'),
    ScopeDefinition('files:write', '管理文件', '上传、回收与恢复已授权文件'),
    ScopeDefinition('knowledge:read', '读取知识库', '读取已授权知识库、目录与文档元数据'),
    ScopeDefinition('knowledge:write', '管理知识库', '按授权创建知识库、入库、重建或删除'),
    ScopeDefinition('knowledge:query', '知识检索', '检索已授权知识库中的片段'),
    ScopeDefinition('skills:read', '读取技能', '读取已授权个人技能与不可变修订'),
    ScopeDefinition('skills:write', '管理技能', '预检、确认、启停与删除个人技能'),
)
SCOPES: tuple[Scope, ...] = tuple(item.scope for item in SCOPE_DEFINITIONS)
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


def require_key_capabilities(principal: Principal, scopes: Sequence[str] = (), *, operation: str = 'invoke') -> None:
    """The same functional gate applies to catalog projection, issuance and use."""
    require_capability(principal, 'developer', operation)
    for scope in scopes:
        namespace, action = scope.split(':')
        if namespace not in {'knowledge', 'skills', 'agents'}:
            continue
        if action == 'write':
            operations = (
                ('create', 'update', 'delete', 'import', 'rebuild')
                if namespace == 'knowledge'
                else ('create', 'update', 'delete', 'toggle')
            )
            principal.require_any_permission(*(f'platform:{namespace}:{item}' for item in operations))
        else:
            require_capability(principal, namespace, 'use' if action == 'invoke' else action)


def scope_catalog(principal: Principal) -> dict[str, object]:
    definitions: list[dict[str, object]] = []
    for item in SCOPE_DEFINITIONS:
        reason = None
        try:
            require_key_capabilities(principal, (item.scope,), operation='key_create')
        except AppError:
            reason = f'当前账号没有签发“{item.label}”的权限'
        definitions.append({**asdict(item), 'grantable': reason is None, 'reason': reason})
    return {
        'scopes': SCOPES,
        'definitions': definitions,
        'caps': {'resource_rpm_min': 1, 'resource_rpm_max': RESOURCE_RPM_MAX},
    }
