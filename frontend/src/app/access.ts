import type { RouteLocationNormalized, RouteLocationRaw } from 'vue-router'
import type { User } from '../modules/identity/public.ts'
import { canAccessAdmin } from '../modules/admin/public.ts'
import {
  firstModulePath,
  hasCapability,
  hasModule,
  hasPermission,
  moduleForPath
} from '../shared/platformAccess.ts'

export function accessRedirect(
  to: Pick<RouteLocationNormalized, 'path' | 'name' | 'meta' | 'params'>,
  user: User
): RouteLocationRaw | null {
  if (to.meta.public) return null
  const module = moduleForPath(to.path)
  if (module && !to.meta.permission && !hasModule(user, module))
    return { path: '/access', query: { denied: module } }
  if (
    (to.name === 'agent-create' && !hasCapability(user, 'agents', 'create')) ||
    (to.name === 'agent-editor' && !hasCapability(user, 'agents', 'read')) ||
    (to.name === 'assistant-chat' && !hasCapability(user, 'agents', 'use'))
  )
    return { path: '/access', query: { denied: 'agents' } }
  if (
    module === 'knowledge' &&
    to.params.view === 'query' &&
    !hasCapability(user, 'knowledge', 'query')
  )
    return { path: '/access', query: { denied: 'knowledge' } }
  if (to.meta.admin && !user.is_admin) return firstModulePath(user)
  if (to.meta.console && !canAccessAdmin(user)) return firstModulePath(user)
  if (to.meta.permission && !hasPermission(user, to.meta.permission)) return '/admin'
  if (to.meta.anyPermissions && !to.meta.anyPermissions.some((code) => hasPermission(user, code)))
    return { path: '/access', query: { denied: module ?? to.path } }
  return null
}
