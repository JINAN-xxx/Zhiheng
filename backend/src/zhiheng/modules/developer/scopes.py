"""Finite, issuance-time capabilities; model allowlists are an additional restriction."""

from dataclasses import asdict, dataclass
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


@dataclass(frozen=True)
class ScopeDefinition:
    scope: Scope
    label: str
    description: str
    requires_models: bool = False


SCOPE_DEFINITIONS = (
    ScopeDefinition('models:invoke', '模型调用', '调用允许列表中的模型', True),
    ScopeDefinition('agents:invoke', '智能体调用', '调用当前应用发布的智能体', True),
    ScopeDefinition('files:read', '读取文件', '读取已授权文件的元数据与内容'),
    ScopeDefinition('files:write', '管理文件', '上传、回收与恢复已授权文件'),
    ScopeDefinition('knowledge:read', '读取知识库', '读取已授权知识库、目录与文档元数据'),
    ScopeDefinition('knowledge:write', '管理知识库', '创建、入库、重建与删除；模型步骤另需模型允许列表'),
    ScopeDefinition('knowledge:query', '知识检索', '检索片段；模型步骤另需模型允许列表，不自动开放原文下载'),
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


def scope_catalog() -> dict[str, object]:
    return {
        'scopes': SCOPES,
        'definitions': [asdict(item) for item in SCOPE_DEFINITIONS],
        'caps': {'resource_rpm_min': 1, 'resource_rpm_max': RESOURCE_RPM_MAX},
    }
