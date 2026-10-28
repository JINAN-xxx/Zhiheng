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
PERMISSION_CODES = frozenset(
    f'system:{area}:{action}'
    for area in AREAS
    for action in (('read',) if area in {'logs', 'monitor'} else ('read', 'write'))
)
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
            'group': area,
        }
        for area, label in AREAS.items()
        for action in (('read',) if area in {'logs', 'monitor'} else ('read', 'write'))
    ]
