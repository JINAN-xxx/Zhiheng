"""Exact console operation policy. Module gates are enforced by every permission check."""

from fastapi import Request

from zhiheng.shared.errors import AppError
from zhiheng.shared.platform_permissions import require_capability
from zhiheng.shared.principal import Principal

ROUTE_CAPABILITIES: dict[str, dict[str, str]] = {
    'mcp': {
        'catalog': 'mcp:read',
        **dict.fromkeys(('create', 'update', 'sync'), 'mcp:configure'),
        **dict.fromkeys(('roles', 'grant'), 'mcp:grant'),
        'calls': 'mcp:audit',
        'publication_sources': 'mcp:publish',
    },
    'chat': {
        **dict.fromkeys(('conversations', 'messages', 'context_info', 'conversation_detail'), 'chat:read'),
        **dict.fromkeys(('create_conversation', 'create_run', 'regenerate_message'), 'chat:use'),
        'edit_conversation': 'chat:update',
        'delete_conversation': 'chat:delete',
    },
    'chat_files': {'upload': 'chat:use'},
    'assistant_conversations': {
        **dict.fromkeys(
            ('list_conversations', 'conversation_detail', 'download_attachment', 'submission_receipt'), 'agents:read'
        ),
        'upload_attachment': 'agents:use',
        **dict.fromkeys(('create_conversation', 'send_message'), 'agents:use'),
        'rename_conversation': 'agents:conversation_update',
        'delete_conversation': 'agents:conversation_delete',
    },
    'agents': {
        'list_agents': 'agents:read',
        'plaza_list': 'agents:read',
        'plaza_departments': 'agents:plaza_publish',
        'plaza_history': 'agents:read',
        'plaza_get': 'agents:read',
        'plaza_publish': 'agents:plaza_publish',
        'plaza_unpublish': 'agents:plaza_publish',
        'get': 'agents:read',
        'knowledge_policy': 'agents:read',
        'create': 'agents:create',
        'update': 'agents:update',
        'enable': 'agents:publish',
        'disable': 'agents:publish',
        'remove': 'agents:delete',
        'preview': 'agents:debug',
    },
    'work': {
        **dict.fromkeys(
            (
                'available',
                'tasks',
                'detail',
                'snapshot',
                'progress',
                'events',
                'cancel',
                'options',
                'input_download',
                'artifact_download',
                'artifact_preview',
            ),
            'work:read',
        ),
        'create': 'work:create',
        **dict.fromkeys(('update', 'add_member', 'upgrade_member', 'remove_member', 'detach_input'), 'work:update'),
        'remove': 'work:delete',
        'admit': 'work:use',
        'upload': 'work:use',
    },
    'skills': {
        'skills': 'skills:read',
        'get': 'skills:read',
        'content': 'skills:read',
        'platform_detail': 'skills:read',
        'list_references': 'skills:read',
        'remove': 'skills:delete',
        'publish_platform': 'skills:platform_publish',
        'withdraw_platform': 'skills:platform_publish',
    },
    'developer': {
        **dict.fromkeys(
            (
                'applications',
                'scope_catalog',
                'agent_candidates',
                'mcp_publications',
                'application',
                'keys',
                'key',
                'usage',
                'request_logs',
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
        'publish_mcp_tool': 'developer:grant',
        'withdraw_mcp_tool': 'developer:grant',
        'rotate': 'developer:key_rotate',
        'revoke': 'developer:key_revoke',
        'delete_key': 'developer:key_delete',
        'bind_resource': 'developer:grant',
        'unbind_resource': 'developer:grant',
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
                'document_visibility',
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
        'set_feedback': 'chat:feedback',
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
    if module == 'developer' and name == 'resource_quota':
        principal.require_admin()
        return True
    if module == 'admin_identity':
        _authorize_identity(request, principal, name)
        return True
    if module == 'admin_operations':
        if name == 'published_notices':
            return True
        area = 'logs' if name == 'logs' else ('notice_settings' if 'config' in name else 'notices')
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
        action = 'read' if request.method == 'GET' else 'update'
        require_capability(principal, 'knowledge_settings', action)
        return True
    return False


def authorize_console_route(request: Request, principal: Principal) -> None:  # noqa: PLR0911, PLR0912 - explicit policies for shared adapters
    module, name = route_identity(request)
    if module == 'overview':
        principal.require_permission('platform:overview:read')
        return
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
                'platform:knowledge_settings:read',
            )
        return  # Model and credential domain commands check their own exact operation.
    if module == 'agents' and name == 'available':
        principal.require_any_permission('platform:agents:read', 'platform:work:read')
        return
    if module == 'mcp' and name == 'servers':
        principal.require_any_permission('platform:mcp:configure', 'platform:mcp:grant', 'platform:mcp:audit')
        return
    if module == 'assistant_conversations' and name in {'rename_conversation', 'delete_conversation'}:
        require_capability(principal, 'agents', 'use')
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
        principal.require_permission('ai:credits:reconcile')
        return
    if module == 'billing':
        policy = {
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
        elif name in {'state', 'invocation_trace'}:
            principal.require_any_permission(
                'platform:usage:read',
                'platform:chat:read',
                'platform:agents:read',
                'platform:work:read',
                'ai:credits:read',
            )
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
