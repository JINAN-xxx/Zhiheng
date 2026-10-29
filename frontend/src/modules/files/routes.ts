import type { RouteRecordRaw } from 'vue-router'
import { hasAnyPermission, identity } from '../identity/public.ts'
export const fileRoutes: RouteRecordRaw[] = [
  {
    path: '/knowledge/storage',
    alias: ['/files', '/system/file'],
    name: 'files',
    component: () => import('./views/FilesView.vue'),
    meta: { title: '存储管理', activeMenu: '/knowledge' },
    beforeEnter: () =>
      hasAnyPermission(identity.value, 'ai:storage:read', 'ai:storage:write') || '/knowledge'
  }
]
