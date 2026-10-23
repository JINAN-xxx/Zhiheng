import type { RouteRecordRaw } from 'vue-router'
export const fileRoutes: RouteRecordRaw[] = [
  {
    path: '/files',
    alias: '/system/file',
    name: 'files',
    component: () => import('./views/FilesView.vue'),
    meta: { title: '文件管理', admin: true }
  }
]
