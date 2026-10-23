import { createRouter, createWebHistory } from 'vue-router'
import { chatRoutes } from '../modules/chat/routes.ts'
import { identity, restoreIdentity } from '../modules/identity/public.ts'
import { identityRoutes } from '../modules/identity/routes.ts'
import { modelRoutes } from '../modules/models/routes.ts'
import { usageRoutes } from '../modules/usage/routes.ts'
import { errorMessage } from '../shared/http.ts'
declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    public?: boolean
    admin?: boolean
    navigation?: boolean
  }
}
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    ...identityRoutes,
    ...chatRoutes,
    ...modelRoutes,
    ...usageRoutes,
    { path: '/', redirect: '/chat' },
    { path: '/:pathMatch(.*)*', redirect: '/chat' }
  ]
})
router.beforeEach(async (to) => {
  if (to.meta.public) return identity.value ? '/chat' : true
  let user
  try {
    user = await restoreIdentity()
  } catch (error) {
    return { path: '/login', query: { reason: errorMessage(error) } }
  }
  if (!user) return '/login'
  if (to.meta.admin && !user.is_admin) return '/chat'
  return true
})
router.afterEach((to) => {
  document.title = `${to.meta.title ?? '工作空间'} · 智衡`
})
