import type { RouteRecordRaw } from 'vue-router'
export const modelRoutes: RouteRecordRaw[] = [
  {
    path: '/models',
    name: 'models',
    component: () => import('./views/ModelsView.vue'),
    meta: { title: '模型管理', admin: true, navigation: true }
  }
]
