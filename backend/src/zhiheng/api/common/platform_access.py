"""Exact console operation policy. Module gates are enforced by every permission check."""

from fastapi import Request

from zhiheng.shared.errors import AppError
from zhiheng.shared.platform_permissions import require_capability
from zhiheng.shared.principal import Principal

ROUTE_CAPABILITIES: dict[str, dict[str, str]] = {
    'chat': {
        **dict.fromkeys(('conversations', 'messages', 'context_info', 'conversation_detail'), 'chat:read'),
        **dict.fromkeys(('create_conversation', 'create_run', 'regenerate_message', 'retry_context'), 'chat:use'),
        'edit_conversation': 'chat:update',
        'delete_conversation': 'chat:delete',
    },
    'chat_files': {'upload': 'chat:use'},
    'assistant_conversations': {
        **dict.fromkeys(('list_conversations', 'conversation_detail'), 'agents:read'),
        **dict.fromkeys(
            ('create_conversation', 'send_message', 'rename_conversation', 'delete_conversation'), 'agents:use'
        ),
    },
    'agents': {
        'list_agents': 'agents:read',
        'get': 'agents:read',
        'knowledge_policy': 'agents:read',
        'create': 'agents:create',
        'update': 'agents:update',
        'publish': 'agents:publish',
        'unpublish': 'agents:publish',
        'remove': 'agents:delete',
        'preview': 'agents:debug',
        'api_access': 'agents:read',
    },
    'work': {
        **dict.fromkeys(('available', 'tasks', 'detail', 'snapshot', 'cancel', 'events'), 'work:read'),
        'create': 'work:create',
        **dict.fromkeys(('update', 'add_member', 'upgrade_member', 'remove_member'), 'work:update'),
        'remove': 'work:delete',
        'admit': 'work:use',
    },
    'skills': {'skills': 'skills:read', 'get': 'skills:read', 'content': 'skills:read', 'remove': 'skills:delete'},
    'developer': {
        **dict.fromkeys(
            (
                'applications',
                'scope_catalog',
                'application',
                'keys',
                'key',
                'usage',
                'resource_access_info',
                'resource_candidates',
                'resource_grants',
            ),
            'developer:read',
        ),
        'create': 'developer:create',
        'update': 'developer:update',
        'enable': 'developer:toggle',
        'disable': 'developer:toggle',
        'delete': 'developer:delete',
        'issue': 'developer:key_create',
        'rotate': 'developer:key_rotate',
        'revoke': 'developer:key_revoke',
        'delete_key': 'developer:key_delete',
        'bind_resource': 'developer:grant',
        'unbind_resource': 'developer:grant',
        'resource_quota': 'developer:grant',
    },
    'knowledge': {
        **dict.fromkeys(
            (
                'capabilities',
                'bases',
                'get_base',
                'acl',
                'folders',
                'documents',
                'get_job',
                'jobs',
                'references',
                'migration_preview',
                'pending_uploads',
                'upload_status',
            ),
            'knowledge:read',
        ),
        **dict.fromkeys(('create', 'create_folder'), 'knowledge:create'),
        **dict.fromkeys(
            ('patch_base', 'configure', 'rename_folder', 'rechunk', 'migration_confirm'), 'knowledge:update'
        ),
        **dict.fromkeys(('upload', 'import_documents', 'add_attachment', 'cancel_job'), 'knowledge:import'),
        **dict.fromkeys(('delete_document', 'delete_folder', 'delete_base', 'remove_upload'), 'knowledge:delete'),
        'rebuild': 'knowledge:rebuild',
        'query': 'knowledge:query',
    },
    'knowledge_sharing': {
        **dict.fromkeys(('preview', 'accept'), 'knowledge:read'),
        **dict.fromkeys(
            ('create', 'invitations', 'revoke', 'invitation_link', 'members', 'remove', 'cleanup_legacy'),
            'knowledge:share',
        ),
    },
    'knowledge_graph': {
        **dict.fromkeys(('config', 'jobs', 'job', 'cancel', 'labels', 'subgraph'), 'knowledge:read'),
        'patch_config': 'knowledge:update',
        'build': 'knowledge:rebuild',
        'preview': 'knowledge:rebuild',
        'query': 'knowledge:query',
    },
    'message_actions': {
        'set_feedback': 'chat:read',
        'conversation_feedback': 'chat:read',
        **dict.fromkeys(('share_preview', 'create_share', 'list_shares', 'revoke_share'), 'chat:share'),
    },
}


def route_identity(request: Request) -> tuple[str, str]:
    endpoint = getattr(request.scope.get('route'), 'endpoint', None)
    return getattr(endpoint, '__module__', '').rsplit('.', 1)[-1], getattr(endpoint, '__name__', '')


def _authorize_identity(request: Request, principal: Principal, name: str) -> None:
    if name in {'permissions', 'user_options', 'effective_permissions'}:
        if name == 'permissions':
            if not principal.is_admin and not any(
                code.startswith('system:') and code.endswith(':read') for code in principal.permissions
            ):
                raise AppError('forbidden', '需要后台管理权限', 403)
        else:
            principal.require_permission('system:users:read')
        return
    if name in {'sessions', 'revoke_session', 'reset_password'}:
        principal.require_permission(
            {
                'sessions': 'system:sessions:read',
                'revoke_session': 'system:sessions:revoke',
                'reset_password': 'system:users:reset_password',
            }[name]
        )
        return
    area = str(request.path_params.get('kind') or request.url.path.split('/')[3])
    action = {
        'list_records': 'read',
        'get_record': 'read',
        'create_record': 'create',
        'delete_record': 'delete',
        'remove_record': 'delete',
    }.get(name)
    if action:
        principal.require_permission(f'system:{area}:{action}')
    elif name == 'update_record':
        principal.require_any_permission(
            *(f'system:{area}:{action}' for action in ('update', 'toggle', 'assign_roles', 'grant'))
        )
    return


def _authorize_administration(request: Request, principal: Principal, module: str, name: str) -> bool:
    if module == 'admin_identity':
        _authorize_identity(request, principal, name)
        return True
    if module == 'admin_operations':
        if name == 'published_notices':
            return True
        area = 'logs' if name == 'logs' else ('configs' if 'config' in name else 'notices')
        action = (
            'read'
            if request.method == 'GET'
            else 'create'
            if request.method == 'POST'
            else 'delete'
            if request.method == 'DELETE'
            else None
        )
        if action:
            principal.require_permission(f'system:{area}:{action}')
        else:
            principal.require_any_permission(f'system:{area}:update', f'system:{area}:toggle', f'system:{area}:publish')
        return True
    if module == 'knowledge_settings':
        principal.require_permission('system:configs:read' if request.method == 'GET' else 'system:configs:update')
        return True
    return False


def authorize_console_route(request: Request, principal: Principal) -> None:  # noqa: PLR0911, PLR0912 - explicit policies for shared adapters
    module, name = route_identity(request)
    if _authorize_administration(request, principal, module, name):
        return
    if module == 'models':
        if name == 'models':
            principal.require_any_permission(
                'platform:models:read',
                'platform:chat:use',
                'platform:agents:read',
                'platform:work:read',
                'platform:knowledge:read',
                'platform:developer:read',
                'system:configs:read',
            )
        return  # Model and credential domain commands check their own exact operation.
    if module == 'agents' and name == 'api_access':
        require_capability(principal, 'agents', 'read')
        require_capability(principal, 'developer', 'read')
        return
    if module == 'agents' and name == 'available':
        principal.require_any_permission('platform:agents:read', 'platform:work:read')
        return
    if module == 'skills' and name in {'status', 'prepare', 'confirm'}:
        require_capability(principal, 'skills', 'read')
        if name != 'status':
            principal.require_any_permission('platform:skills:create', 'platform:skills:update')
        return  # Package target and status type decide create/update/toggle/use in the domain.
    if module == 'skills' and name == 'available':
        require_capability(principal, 'skills', 'use')
        return
    if module == 'knowledge' and name in {'available', 'preview', 'download'}:
        require_capability(principal, 'knowledge', 'read')
        return
    if module == 'knowledge' and name in {'preparation', 'cancel_preparation'}:
        return
    if module == 'usage' and name == 'usage':
        principal.require_any_permission('platform:usage:read', 'ai:credits:read')
        return
    if module == 'usage':
        principal.require_permission('ai:credits:configure' if name == 'quota' else 'ai:credits:reconcile')
        return
    if module == 'billing':
        policy = {
            'state': 'ai:credits:read',
            'set_state': 'ai:credits:configure',
            'accounts': 'ai:credits:read',
            'adjust': 'ai:credits:adjust',
            'account_state': 'ai:credits:configure',
            'account_ledger': 'ai:credits:read',
            'account_usage': 'ai:credits:read',
            'rates': 'ai:rates:read',
            'create_rate': 'ai:rates:create',
            'reconcile': 'ai:credits:reconcile',
        }
        if name in policy:
            principal.require_permission(policy[name])
        elif name in {'me', 'my_ledger', 'my_usage'}:
            principal.require_any_permission(
                'platform:usage:read', 'platform:chat:read', 'platform:agents:read', 'platform:work:read'
            )
        return
    if module == 'files':
        if request.method in {'GET', 'HEAD'} or name == 'read_reminder':
            principal.require_any_permission(
                'platform:chat:read', 'platform:knowledge:read', 'platform:developer:read', 'ai:storage:read'
            )
        elif name == 'upload':
            principal.require_any_permission(
                'platform:knowledge:import', 'platform:developer:grant', 'ai:storage:maintain'
            )
        else:
            action = {
                'update_policy': 'configure',
                'create_policy': 'configure',
                'patch_policy': 'configure',
                'delete_policy': 'configure',
                'update_acl': 'acl',
                'transfer': 'transfer',
                'remove': 'delete',
                'purge': 'delete',
                'dispose': 'delete',
                'restore': 'maintain',
                'extend': 'maintain',
                'parse': 'parse',
            }.get(name, 'maintain')
            principal.require_permission('ai:storage:' + action)
        return
    route_policy = ROUTE_CAPABILITIES.get(module)
    if route_policy is None:
        return
    code = route_policy.get(name)
    if code is None:
        raise AppError('platform_policy_missing', '该操作尚未配置权限', 403)
    area, action = code.split(':')
    require_capability(principal, area, action)
