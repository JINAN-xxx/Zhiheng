"""The fixed permission directory and the internal pages wired to it."""

AREAS = {
    'users': '用户管理',
    'roles': '角色管理',
    'departments': '部门管理',
    'posts': '岗位管理',
    'menus': '菜单管理',
    'dictionaries': '字典管理',
    'configs': '系统参数',
    'notices': '通知公告',
    'logs': '日志审计',
    'sessions': '在线会话',
    'monitor': '运行监控',
}
SYSTEM_PERMISSION_CODES = frozenset(
    f'system:{area}:{action}'
    for area in AREAS
    for action in (('read',) if area in {'logs', 'monitor'} else ('read', 'write'))
)
AI_PERMISSIONS = {
    'ai:models:read': ('模型与凭据配置 · 查看', 'AI平台 · 模型与凭据'),
    'ai:models:write': ('模型与凭据配置 · 创建、编辑、启停、发布与删除', 'AI平台 · 模型与凭据'),
    'ai:models:test': ('模型连接 · 测试', 'AI平台 · 模型与凭据'),
    'ai:rates:read': ('模型费率 · 查看', 'AI平台 · 费率'),
    'ai:rates:write': ('模型费率 · 配置', 'AI平台 · 费率'),
    'ai:credits:read': ('用户积分与消费流水 · 查看', 'AI平台 · 积分'),
    'ai:credits:write': ('用户积分 · 发放、回收、暂停与用量核对', 'AI平台 · 积分'),
    'ai:skills:write': ('共享技能 · 导入、启停与删除', 'AI平台 · 共享技能'),
    'ai:storage:read': ('存储策略、对账与维护记录 · 查看', 'AI平台 · 存储治理'),
    'ai:storage:write': ('存储策略与维护任务 · 配置与执行', 'AI平台 · 存储治理'),
}
AI_PERMISSION_CODES = frozenset(AI_PERMISSIONS)
PERMISSION_CODES = SYSTEM_PERMISSION_CODES | AI_PERMISSION_CODES
ADMIN_PATHS = {
    '/admin/users': 'users',
    '/admin/roles': 'roles',
    '/admin/departments': 'departments',
    '/admin/posts': 'posts',
    '/admin/menus': 'menus',
    '/admin/dictionaries': 'dictionaries',
    '/admin/configs': 'configs',
    '/admin/notices': 'notices',
    '/admin/login-logs': 'logs',
    '/admin/operation-logs': 'logs',
    '/admin/sessions': 'sessions',
    '/admin/monitor': 'monitor',
}
ALLOWED_MENU_PATHS = frozenset(ADMIN_PATHS)


def permission_catalog() -> list[dict[str, str]]:
    return [
        {
            'code': f'system:{area}:{action}',
            'label': f'{label} · {"查看" if action == "read" else "维护"}',
            'group': f'系统管理 · {label}',
        }
        for area, label in AREAS.items()
        for action in (('read',) if area in {'logs', 'monitor'} else ('read', 'write'))
    ] + [{'code': code, 'label': label, 'group': group} for code, (label, group) in AI_PERMISSIONS.items()]
