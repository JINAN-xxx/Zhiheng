"""Operation-aware capability checks for platform modules."""

from zhiheng.shared.errors import AppError
from zhiheng.shared.permission_policy import PERMISSION_CODES, catalog, is_granted
from zhiheng.shared.principal import Principal

PLATFORM_PERMISSION_CODES = frozenset(code for code in PERMISSION_CODES if code.startswith('platform:'))
PLATFORM_MODULES = {
    row['resource']: row['label'] for row in catalog() if row['namespace'] == 'platform' and row['kind'] == 'module'
}


def platform_catalog() -> list[dict[str, str]]:
    return [row for row in catalog() if row['namespace'] == 'platform']


def has_capability(principal: Principal, module: str, action: str = 'read') -> bool:
    if principal.is_admin:
        return True
    defaults = {
        'chat': 'use',
        'agents': 'use',
        'work': 'use',
        'knowledge': 'query',
        'skills': 'use',
        'developer': 'read',
        'models': 'read',
        'usage': 'read',
        'overview': 'read',
    }
    if action == 'access':
        action = defaults.get(module, 'read')
    if action == 'use' and module == 'knowledge':
        action = 'query'
    if action == 'manage':
        return is_granted(principal.permissions, f'platform:{module}:manage')
    if action == 'write':
        action = 'update'
    return is_granted(principal.permissions, f'platform:{module}:{action}')


def require_capability(principal: Principal, module: str, action: str = 'read') -> None:
    if not has_capability(principal, module, action):
        raise AppError('forbidden', '当前账号没有此操作权限', 403)


def require_execution_capabilities(
    principal: Principal, kind: str, *, knowledge: bool = False, skills: bool = False
) -> None:
    module, action = {
        'chat': ('chat', 'use'),
        'chat_summary': ('chat', 'use'),
        'agent_chat': ('agents', 'use'),
        'agent_preview': ('agents', 'debug'),
        'work_turn': ('work', 'use'),
        'knowledge_query': ('knowledge', 'query'),
    }.get(kind, ('knowledge', 'import'))
    require_capability(principal, module, action)
    if knowledge:
        require_capability(principal, 'knowledge', 'query')
    if skills:
        require_capability(principal, 'skills', 'use')


def require_resource_operation(principal: Principal, operation: str) -> None:
    if operation.startswith('knowledge.'):
        suffix = operation[len('knowledge.') :]
        action = (
            'delete'
            if suffix.startswith('delete')
            else {
                'create': 'create',
                'folder.create': 'create',
                'patch': 'update',
                'folder.patch': 'update',
                'rechunk': 'update',
                'rebuild': 'rebuild',
                'query': 'query',
                'import': 'import',
            }.get(suffix, 'update')
        )
        require_capability(principal, 'knowledge', action)
    elif operation.startswith('skills.'):
        skill_action = {'delete': 'delete', 'status': 'toggle'}.get(operation[len('skills.') :])
        if skill_action:
            require_capability(principal, 'skills', skill_action)
        else:
            principal.require_any_permission('platform:skills:create', 'platform:skills:update')
