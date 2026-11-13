import { createRouter, createWebHistory } from 'vue-router'
import { chatRoutes } from '../modules/chat/routes.ts'
import { identity, restoreIdentity, loginDestination } from '../modules/identity/public.ts'
import { identityRoutes } from '../modules/identity/routes.ts'
import { modelRoutes } from '../modules/models/routes.ts'
import { usageRoutes } from '../modules/usage/routes.ts'
import { developerRoutes } from '../modules/developer/public.ts'
import { skillRoutes } from '../modules/skills/public.ts'
import { agentRoutes } from '../modules/agents/public.ts'
import { workRoutes } from '../modules/work/public.ts'
import { knowledgeRoutes } from '../modules/knowledge/public.ts'
import { adminRoutes, canAccessAdmin } from '../modules/admin/public.ts'
import {
  moduleForPath,
  hasModule,
  hasCapability,
  firstModulePath
} from '../shared/platformAccess.ts'
import { errorMessage } from '../shared/http.ts'
declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    public?: boolean
    admin?: boolean
    navigation?: boolean
    activeMenu?: string
    console?: boolean
    permission?: string
    reuseByParam?: string
  }
}
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/overview',
      name: 'overview',
      component: () => import('./views/OverviewView.vue'),
      meta: { title: '工作台' }
    },
    ...identityRoutes,
    ...chatRoutes,
    ...modelRoutes,
    ...usageRoutes,
    ...developerRoutes,
    ...skillRoutes,
    ...agentRoutes,
    ...workRoutes,
    ...knowledgeRoutes,
    ...adminRoutes,
    {
      path: '/access',
      name: 'access',
      component: () => import('./views/NoAccessView.vue'),
      meta: { title: '功能访问' }
    },
    { path: '/', redirect: () => firstModulePath(identity.value) },
    { path: '/:pathMatch(.*)*', redirect: () => firstModulePath(identity.value) }
  ]
})
router.beforeEach(async (to) => {
  if (to.meta.public)
    return to.name === 'login' && identity.value ? loginDestination(to.query.redirect) : true
  let user
  try {
    user = await restoreIdentity()
  } catch (error) {
    return { path: '/login', query: { reason: errorMessage(error) } }
  }
  if (!user) return '/login'
  const module = moduleForPath(to.path)
  if (module && !to.meta.permission && !hasModule(user, module))
    return { path: '/access', query: { denied: module } }
  if (
    (to.name === 'agent-create' && !hasCapability(user, 'agents', 'create')) ||
    (to.name === 'agent-editor' && !hasCapability(user, 'agents', 'read')) ||
    (to.name === 'assistant-chat' && !hasCapability(user, 'agents'))
  )
    return { path: '/access', query: { denied: 'agents' } }
  if (module === 'knowledge' && to.params.view === 'query' && !hasCapability(user, 'knowledge'))
    return { path: '/access', query: { denied: 'knowledge' } }
  if (to.meta.admin && !user.is_admin) return firstModulePath(user)
  if (to.meta.console && !canAccessAdmin(user)) return firstModulePath(user)
  if (to.meta.permission && !user.is_admin && !user.permissions?.includes(to.meta.permission))
    return '/admin'
  return true
})
router.afterEach((to) => {
  document.title = `${to.meta.title ?? '工作空间'} · 智衡`
})
