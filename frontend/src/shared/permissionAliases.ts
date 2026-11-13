// Internal compatibility names only; these are never grantable role operations.
export const permissionAliases: Record<string, readonly string[]> = {
  'ai:credentials:write': [
    'ai:credentials:create',
    'ai:credentials:delete',
    'ai:credentials:toggle',
    'ai:credentials:update'
  ],
  'ai:rates:write': ['ai:rates:create'],
  'ai:credits:write': ['ai:credits:adjust', 'ai:credits:configure', 'ai:credits:reconcile'],
  'ai:storage:write': [
    'ai:storage:acl',
    'ai:storage:configure',
    'ai:storage:delete',
    'ai:storage:maintain',
    'ai:storage:parse',
    'ai:storage:transfer'
  ],
  'system:users:write': [
    'system:users:assign_roles',
    'system:users:create',
    'system:users:delete',
    'system:users:reset_password',
    'system:users:toggle',
    'system:users:update'
  ],
  'system:roles:write': [
    'system:roles:create',
    'system:roles:delete',
    'system:roles:grant',
    'system:roles:toggle',
    'system:roles:update'
  ],
  'system:departments:write': [
    'system:departments:create',
    'system:departments:delete',
    'system:departments:toggle',
    'system:departments:update'
  ],
  'system:configs:write': [
    'system:configs:create',
    'system:configs:delete',
    'system:configs:toggle',
    'system:configs:update'
  ],
  'system:notices:write': [
    'system:notices:create',
    'system:notices:delete',
    'system:notices:publish',
    'system:notices:update'
  ],
  'system:logs:write': [],
  'system:sessions:write': ['system:sessions:revoke'],
  'system:monitor:write': [],
  'platform:overview:access': [],
  'platform:chat:access': [
    'platform:chat:delete',
    'platform:chat:share',
    'platform:chat:update',
    'platform:chat:use'
  ],
  'platform:agents:access': ['platform:agents:use'],
  'platform:agents:manage': [
    'platform:agents:create',
    'platform:agents:debug',
    'platform:agents:delete',
    'platform:agents:update'
  ],
  'platform:work:access': ['platform:work:use'],
  'platform:work:manage': ['platform:work:create', 'platform:work:delete', 'platform:work:update'],
  'platform:models:access': [],
  'platform:knowledge:access': ['platform:knowledge:query'],
  'platform:knowledge:manage': [
    'platform:knowledge:create',
    'platform:knowledge:delete',
    'platform:knowledge:import',
    'platform:knowledge:rebuild',
    'platform:knowledge:update'
  ],
  'platform:skills:access': ['platform:skills:use'],
  'platform:skills:manage': [
    'platform:skills:create',
    'platform:skills:delete',
    'platform:skills:toggle',
    'platform:skills:update'
  ],
  'platform:developer:access': [],
  'platform:developer:manage': [
    'platform:developer:create',
    'platform:developer:delete',
    'platform:developer:grant',
    'platform:developer:key_create',
    'platform:developer:key_delete',
    'platform:developer:key_revoke',
    'platform:developer:key_rotate',
    'platform:developer:toggle',
    'platform:developer:update'
  ],
  'platform:usage:access': [],
  'ai:models:read': ['platform:models:config_read'],
  'ai:models:write': [
    'platform:models:create',
    'platform:models:update',
    'platform:models:delete',
    'platform:models:toggle'
  ],
  'ai:models:test': ['platform:models:test'],
  'ai:skills:write': []
}
