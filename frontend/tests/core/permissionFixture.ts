const canonicalActions: Record<string, readonly string[]> = {
  'platform:overview': ['read'],
  'platform:chat': ['read', 'use', 'update', 'delete', 'share'],
  'platform:agents': ['read', 'use', 'create', 'update', 'delete', 'debug', 'publish'],
  'platform:work': ['read', 'use', 'create', 'update', 'delete'],
  'platform:models': ['read', 'config_read', 'create', 'update', 'delete', 'toggle', 'test'],
  'platform:knowledge': [
    'read',
    'query',
    'create',
    'update',
    'delete',
    'import',
    'rebuild',
    'share'
  ],
  'platform:knowledge_settings': ['read', 'update'],
  'platform:skills': ['read', 'use', 'create', 'update', 'delete', 'toggle'],
  'platform:developer': [
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
    'invoke'
  ],
  'platform:usage': ['read'],
  'ai:credentials': ['read', 'create', 'update', 'delete', 'toggle'],
  'ai:rates': ['read', 'create'],
  'ai:credits': ['read', 'adjust', 'configure', 'reconcile'],
  'ai:storage': ['read', 'configure', 'maintain', 'acl', 'transfer', 'delete'],
  'system:users': [
    'read',
    'create',
    'update',
    'delete',
    'toggle',
    'reset_password',
    'assign_roles'
  ],
  'system:roles': ['read', 'create', 'update', 'delete', 'toggle', 'grant'],
  'system:departments': ['read', 'create', 'update', 'delete', 'toggle'],
  'system:notice_settings': ['read', 'create', 'update', 'delete', 'toggle'],
  'system:notices': ['read', 'create', 'update', 'delete', 'publish'],
  'system:logs': ['read'],
  'system:sessions': ['read', 'revoke'],
  'system:monitor': ['read']
}

/** Add module prerequisites to explicitly named canonical test operations. */
export function grantedPermissions(...codes: string[]): string[] {
  const result = new Set<string>()
  for (const code of codes) {
    const separator = code.lastIndexOf(':')
    const module = code.slice(0, separator),
      action = code.slice(separator + 1)
    if (
      !canonicalActions[module] ||
      (action !== 'enabled' && !canonicalActions[module].includes(action))
    )
      throw new Error('Test grants require a canonical permission: ' + code)
    result.add(module + ':enabled')
    if (action !== 'enabled') result.add(module + ':read')
    result.add(code)
  }
  return [...result]
}
