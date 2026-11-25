"""Canonical module gates and independently granted operation permissions."""

from collections.abc import Iterable

# Namespace, resource, label, operations. Every operation depends on gate + read.
MODULE_DEFINITIONS = (
    ('platform', 'overview', '工作台', ('read', 'platform_stats')),
    ('platform', 'chat', '对话体验', ('read', 'use', 'update', 'delete', 'share')),
    ('platform', 'agents', '智能助手', ('read', 'use', 'create', 'update', 'delete', 'debug', 'publish', 'plaza_publish')),
    ('platform', 'work', '工作空间', ('read', 'use', 'create', 'update', 'delete', 'execute_code')),
    ('platform', 'models', '模型目录', ('read', 'config_read', 'create', 'update', 'delete', 'toggle', 'test')),
    ('platform', 'knowledge', '知识库', ('read', 'query', 'create', 'update', 'delete', 'import', 'rebuild', 'share')),
    ('platform', 'knowledge_settings', '知识库默认配置', ('read', 'update')),
    ('platform', 'skills', '技能', ('read', 'use', 'create', 'update', 'delete', 'toggle', 'platform_publish')),
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
    ('ai', 'storage', '存储管理', ('read', 'configure', 'maintain', 'acl', 'transfer', 'delete')),
    ('system', 'users', '用户管理', ('read', 'create', 'update', 'delete', 'toggle', 'reset_password', 'assign_roles')),
    ('system', 'roles', '角色管理', ('read', 'create', 'update', 'delete', 'toggle', 'grant')),
    ('system', 'departments', '部门管理', ('read', 'create', 'update', 'delete', 'toggle')),
    ('system', 'notices', '通知公告', ('read', 'create', 'update', 'delete', 'publish')),
    ('system', 'notice_settings', '公告展示设置', ('read', 'create', 'update', 'delete', 'toggle')),
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
    'execute_code': '文件处理与代码执行',
    'platform_stats': '全平台统计',
    'use': '使用',
    'query': '检索',
    'create': '新增',
    'update': '编辑',
    'delete': '删除',
    'import': '导入资料',
    'rebuild': '重建索引',
    'share': '共享管理',
    'debug': '草稿调试',
    'publish': '启用与停用',
    'plaza_publish': '广场发布',
    'platform_publish': '平台发布',
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
    'reset_password': '重置密码',
    'assign_roles': '分配角色',
    'revoke': '强制下线',
}
OPERATION_LABELS = {'platform:agents:publish': '启停助手'}
DESCRIPTIONS = {
    'platform:knowledge': '仅操作本人拥有或明确获授权的知识库，新增、删除和共享独立授权。',
    'platform:knowledge_settings': '管理平台的默认模型、切片和文档解析设置。',
    'platform:models': '模型目录不包含凭据权限；模型配置和连接测试分别勾选。',
    'ai:credentials': '敏感权限，独立于模型目录；已保存密钥不明文回读。',
    'platform:developer': '仅管理本人应用与 Key；API 调用还受 Scope、模型和资源授权限制。',
    'platform:agents': '使用、调试、启停和广场发布独立授权。管理本人助手，超级管理员可紧急下架广场助手。',
    'ai:rates': '费率入口在模型目录中，进入页面还需「模型目录 → 查看」。',
    'ai:credits': '用户积分入口在用量与调用中，进入页面还需「用量与调用 → 查看」。',
    'system:notice_settings': '管理通知公告的展示开关。新增和启停用于初始化或恢复设置，删除通过管理接口执行。',
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
                'label': OPERATION_LABELS.get(f'{module}:{action}', ACTION_LABELS[action]),
                'kind': 'action',
                'action': action,
                'parent': f'{module}:enabled',
                'description': '',
            }
            for action in actions
        )
    return result


def is_granted(permissions: Iterable[str], requested: str) -> bool:
    codes = permissions if isinstance(permissions, (set, frozenset)) else frozenset(permissions)
    if requested in PERMISSION_CODES:
        module, action = requested.rsplit(':', 1)
        return (
            f'{module}:enabled' in codes and (action == 'enabled' or f'{module}:read' in codes) and requested in codes
        )
    return False


def effective_permissions(permissions: Iterable[str]) -> frozenset[str]:
    codes = frozenset(permissions)
    return frozenset(code for code in codes if code in PERMISSION_CODES and is_granted(codes, code))


def invalid_dependencies(permissions: Iterable[str]) -> bool:
    codes = frozenset(permissions)
    return any(
        code.rsplit(':', 1)[1] not in {'enabled', 'read'} and f'{code.rsplit(":", 1)[0]}:read' not in codes
        for code in codes
        if code in PERMISSION_CODES
    )
