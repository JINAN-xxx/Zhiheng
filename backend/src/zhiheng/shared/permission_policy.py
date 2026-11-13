"""Canonical module gates, operation permissions and explicit legacy-role conversion."""

from collections.abc import Iterable

# Namespace, resource, label, operations. Every operation depends on gate + read.
MODULE_DEFINITIONS = (
    ('platform', 'overview', '工作台', ('read',)),
    ('platform', 'chat', '对话体验', ('read', 'use', 'update', 'delete', 'share')),
    ('platform', 'agents', '智能助手', ('read', 'use', 'create', 'update', 'delete', 'debug', 'publish')),
    ('platform', 'work', '工作空间', ('read', 'use', 'create', 'update', 'delete')),
    ('platform', 'models', '模型目录', ('read', 'config_read', 'create', 'update', 'delete', 'toggle', 'test')),
    ('platform', 'knowledge', '知识库', ('read', 'query', 'create', 'update', 'delete', 'import', 'rebuild', 'share')),
    ('platform', 'skills', '技能', ('read', 'use', 'create', 'update', 'delete', 'toggle')),
    (
        'platform',
        'developer',
        '开发接入',
        (
            'read',
            'create',
            'update',
            'delete',
            'toggle',
            'key_create',
            'key_rotate',
            'key_revoke',
            'key_delete',
            'grant',
            'invoke',
        ),
    ),
    ('platform', 'usage', '用量与调用', ('read',)),
    ('ai', 'credentials', '凭据管理', ('read', 'create', 'update', 'delete', 'toggle')),
    ('ai', 'rates', '模型费率', ('read', 'create')),
    ('ai', 'credits', '积分与额度管理', ('read', 'adjust', 'configure', 'reconcile')),
    ('ai', 'storage', '存储管理', ('read', 'configure', 'maintain', 'acl', 'transfer', 'delete', 'parse')),
    ('system', 'users', '用户管理', ('read', 'create', 'update', 'delete', 'toggle', 'reset_password', 'assign_roles')),
    ('system', 'roles', '角色管理', ('read', 'create', 'update', 'delete', 'toggle', 'grant')),
    ('system', 'departments', '部门管理', ('read', 'create', 'update', 'delete', 'toggle')),
    ('system', 'configs', '系统参数', ('read', 'create', 'update', 'delete', 'toggle')),
    ('system', 'notices', '通知公告', ('read', 'create', 'update', 'delete', 'publish')),
    ('system', 'logs', '日志审计', ('read',)),
    ('system', 'sessions', '在线会话', ('read', 'revoke')),
    ('system', 'monitor', '运行监控', ('read',)),
)
OPERATIONS = {f'{namespace}:{resource}': actions for namespace, resource, _, actions in MODULE_DEFINITIONS}
PERMISSION_CODES = frozenset(
    f'{module}:{action}' for module, actions in OPERATIONS.items() for action in ('enabled', *actions)
)
ACTION_LABELS = {
    'read': '查看',
    'use': '使用',
    'query': '检索',
    'create': '新增',
    'update': '编辑',
    'delete': '删除',
    'import': '导入资料',
    'rebuild': '重建索引',
    'share': '共享管理',
    'debug': '草稿调试',
    'publish': '发布与撤回',
    'toggle': '启用与停用',
    'config_read': '查看配置',
    'test': '试用与连接测试',
    'key_create': '签发 Key',
    'key_rotate': '轮换 Key',
    'key_revoke': '撤销 Key',
    'key_delete': '删除 Key',
    'grant': '配置权限',
    'invoke': 'API 调用',
    'adjust': '发放与扣减',
    'configure': '配置',
    'reconcile': '用量核对',
    'maintain': '执行维护',
    'acl': '文件授权',
    'transfer': '文件转移',
    'parse': '文件解析',
    'reset_password': '重置密码',
    'assign_roles': '分配角色',
    'revoke': '强制下线',
}
DESCRIPTIONS = {
    'platform:knowledge': '仅操作本人拥有或明确获授权的知识库，新增、删除和共享独立授权。',
    'platform:models': '模型目录不包含凭据权限；模型配置和连接测试分别勾选。',
    'ai:credentials': '敏感权限，独立于模型目录；已保存密钥不明文回读。',
    'platform:developer': '仅管理本人应用与 Key；API 调用还受 Scope、模型和资源授权限制。',
    'platform:agents': '仅操作本人助手；使用、调试与发布独立授权。',
    'ai:credits': '涉及全平台积分、额度和用量，不等同于查看本人用量。',
}


def catalog() -> list[dict[str, str]]:
    result = []
    for namespace, resource, label, actions in MODULE_DEFINITIONS:
        module = f'{namespace}:{resource}'
        section = '平台功能' if namespace == 'platform' else '后台管理'
        common = {
            'module': module,
            'namespace': namespace,
            'resource': resource,
            'group': label,
            'section': section,
            'description': DESCRIPTIONS.get(module, ''),
        }
        result.append({**common, 'code': f'{module}:enabled', 'label': label, 'kind': 'module', 'action': 'enabled'})
        result.extend(
            {
                **common,
                'code': f'{module}:{action}',
                'label': ACTION_LABELS[action],
                'kind': 'action',
                'action': action,
                'parent': f'{module}:enabled',
                'description': '',
            }
            for action in actions
        )
    return result


def _bundle(module: str, actions: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({f'{module}:enabled', f'{module}:read', *(f'{module}:{action}' for action in actions)}))


LEGACY_PERMISSIONS: dict[str, tuple[str, ...]] = {}
for namespace, resource, _, actions in MODULE_DEFINITIONS:
    module = f'{namespace}:{resource}'
    # A historical read/publish/share/invoke grant gains only the gate and its read dependency.
    for action in actions:
        LEGACY_PERMISSIONS[f'{module}:{action}'] = _bundle(module, (action,))
    if namespace == 'system' or module in {'ai:credentials', 'ai:rates', 'ai:credits', 'ai:storage'}:
        LEGACY_PERMISSIONS[f'{module}:write'] = _bundle(module, (action for action in actions if action != 'read'))
LEGACY_PERMISSIONS.update(
    {
        'platform:overview:access': _bundle('platform:overview', ('read',)),
        'platform:chat:access': _bundle('platform:chat', ('read', 'use', 'update', 'delete', 'share')),
        'platform:agents:access': _bundle('platform:agents', ('read', 'use')),
        'platform:agents:manage': _bundle('platform:agents', ('create', 'update', 'delete', 'debug')),
        'platform:work:access': _bundle('platform:work', ('read', 'use')),
        'platform:work:manage': _bundle('platform:work', ('create', 'update', 'delete')),
        'platform:models:access': _bundle('platform:models', ('read',)),
        'platform:knowledge:access': _bundle('platform:knowledge', ('read', 'query')),
        'platform:knowledge:manage': _bundle('platform:knowledge', ('create', 'update', 'delete', 'import', 'rebuild')),
        'platform:skills:access': _bundle('platform:skills', ('read', 'use')),
        'platform:skills:manage': _bundle('platform:skills', ('create', 'update', 'delete', 'toggle')),
        'platform:developer:access': _bundle('platform:developer', ('read',)),
        'platform:developer:manage': _bundle(
            'platform:developer',
            ('create', 'update', 'delete', 'toggle', 'key_create', 'key_rotate', 'key_revoke', 'key_delete', 'grant'),
        ),
        'platform:usage:access': _bundle('platform:usage', ('read',)),
        'ai:models:read': _bundle('platform:models', ('config_read',)),
        'ai:models:write': _bundle('platform:models', ('config_read', 'create', 'update', 'delete', 'toggle')),
        'ai:models:test': _bundle('platform:models', ('test',)),
        'ai:skills:write': (),
    }
)
# Internal preconditions remain compatible with their old aggregate names. They never
# appear as grantable permissions; each HTTP operation and queued command checks its exact action.
INTERNAL_ALIASES = {
    key: tuple(code for code in values if not code.endswith((':enabled', ':read')))
    for key, values in LEGACY_PERMISSIONS.items()
    if key not in PERMISSION_CODES
}
INTERNAL_ALIASES.update(
    {
        'ai:models:read': ('platform:models:config_read',),
        'ai:models:write': (
            'platform:models:create',
            'platform:models:update',
            'platform:models:delete',
            'platform:models:toggle',
        ),
        'ai:models:test': ('platform:models:test',),
    }
)


def is_granted(permissions: Iterable[str], requested: str) -> bool:
    codes = permissions if isinstance(permissions, (set, frozenset)) else frozenset(permissions)
    if requested in PERMISSION_CODES:
        module, action = requested.rsplit(':', 1)
        return (
            f'{module}:enabled' in codes and (action == 'enabled' or f'{module}:read' in codes) and requested in codes
        )
    return any(is_granted(codes, code) for code in INTERNAL_ALIASES.get(requested, ()))


def effective_permissions(permissions: Iterable[str]) -> frozenset[str]:
    codes = frozenset(permissions)
    return frozenset(code for code in codes if code in PERMISSION_CODES and is_granted(codes, code))


def upgrade_legacy_permissions(permissions: Iterable[str]) -> list[str]:
    result: set[str] = set()
    for code in permissions:
        if code in LEGACY_PERMISSIONS:
            result.update(LEGACY_PERMISSIONS[code])
        elif code in PERMISSION_CODES:
            result.add(code)
        elif code.startswith(('system:posts:', 'system:menus:', 'system:dictionaries:')):
            continue
    return sorted(result)


def invalid_dependencies(permissions: Iterable[str]) -> bool:
    codes = frozenset(permissions)
    return any(
        code.rsplit(':', 1)[1] not in {'enabled', 'read'} and f'{code.rsplit(":", 1)[0]}:read' not in codes
        for code in codes
        if code in PERMISSION_CODES
    )
