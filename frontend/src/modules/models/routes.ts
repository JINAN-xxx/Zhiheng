import type { RouteRecordRaw } from 'vue-router'
export const modelRoutes: RouteRecordRaw[] = [
  {
    path: '/credentials',
    name: 'credentials',
    component: () => import('./views/ModelsView.vue'),
    meta: { title: '凭据管理', navigation: true }
  },
  {
    path: '/models',
    alias: '/ai/model',
    name: 'models',
    component: () => import('./views/ModelsView.vue'),
    meta: { title: '模型目录', navigation: true }
  }
]
