"""Chinese audit descriptions over immutable operation and resource identifiers."""

RESOURCE_LABELS = {
    'user': '用户',
    'users': '用户',
    'role': '角色',
    'roles': '角色',
    'identity_role': '角色',
    'department': '部门',
    'departments': '部门',
    'config': '系统参数',
    'configs': '系统参数',
    'notice': '通知公告',
    'notices': '通知公告',
    'session': '登录会话',
    'sessions': '登录会话',
    'application': 'API 应用',
    'applications': 'API 应用',
    'agent': '智能助手',
    'skill': '技能',
    'knowledge_defaults': '知识库默认设置',
    'parsing_settings': '文档解析设置',
    'billing': '积分管理',
    'run': '模型调用',
    'post': '岗位',
    'posts': '岗位',
    'menu': '菜单',
    'menus': '菜单',
    'dictionary': '字典',
    'dictionaries': '字典',
}
CRUD_RESOURCES = (
    'user',
    'users',
    'role',
    'roles',
    'department',
    'departments',
    'config',
    'configs',
    'notice',
    'notices',
    'post',
    'posts',
    'menu',
    'menus',
    'dictionary',
    'dictionaries',
)
ACTION_LABELS = {
    f'{prefix}{resource}.{action}': verb + RESOURCE_LABELS[resource]
    for prefix in ('', 'admin.')
    for resource in CRUD_RESOURCES
    for action, verb in (('create', '新增'), ('update', '修改'), ('delete', '删除'))
}
ACTION_LABELS.update(
    {
        'login': '用户登录',
        'profile.update': '修改个人资料',
        'profile.change_password': '修改个人密码',
        'users.reset_password': '重置用户密码',
        'sessions.revoke': '强制下线会话',
        'applications.platform_state': '调整 API 应用状态',
        'applications.resource_quota': '调整 API 应用资源配额',
        'agent.plaza.publish': '发布广场助手',
        'agent.plaza.withdraw': '下架广场助手',
        'skill.platform.publish': '发布平台技能',
        'skill.platform.withdraw': '下架平台技能',
        'knowledge.defaults.update': '修改知识库默认设置',
        'knowledge.parsing.update': '修改文档解析设置',
        'usage.reconcile': '核对模型用量',
        'billing.grant': '发放积分',
        'billing.reclaim': '回收积分',
        'billing.resume': '恢复积分账户',
        'billing.pause': '暂停积分账户',
        'billing.enable': '开启积分计费',
        'billing.disable': '关闭积分计费',
        'billing.rate_create': '新增计费规则',
        'billing.reconcile': '核对积分用量',
        'billing.usage_reset': '重置用量与消费记录',
        'identity.roles.restrict_credit_management': '调整角色积分管理权限',
    }
)


def action_label(action: str) -> str:
    return ACTION_LABELS.get(action, action)


def resource_label(resource: str) -> str:
    return RESOURCE_LABELS.get(resource, resource)


def matching_log_codes(search: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    value = search.strip().casefold()
    if not value:
        return (), ()
    return (
        tuple(code for code, label in ACTION_LABELS.items() if value in label.casefold()),
        tuple(code for code, label in RESOURCE_LABELS.items() if value in label.casefold()),
    )
