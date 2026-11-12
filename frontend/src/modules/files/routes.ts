import type { RouteRecordRaw } from 'vue-router'
import { hasAnyPermission, identity } from '../identity/public.ts'
export const fileRoutes: RouteRecordRaw[] = [
  {
    path: '/admin/storage',
    alias: ['/knowledge/storage', '/files', '/system/file'],
    name: 'files',
    component: () => import('./views/FilesView.vue'),
    meta: { title: '存储管理', activeMenu: '/admin' },
    beforeEnter: () =>
      hasAnyPermission(identity.value, 'ai:storage:read', 'ai:storage:write') || '/knowledge'
  }
]
