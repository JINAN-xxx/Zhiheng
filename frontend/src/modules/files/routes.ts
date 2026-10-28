import type { RouteRecordRaw } from 'vue-router'
export const fileRoutes: RouteRecordRaw[] = [
  {
    path: '/knowledge/storage',
    alias: ['/files', '/system/file'],
    name: 'files',
    component: () => import('./views/FilesView.vue'),
    meta: { title: '存储管理', admin: true, activeMenu: '/knowledge' }
  }
]
