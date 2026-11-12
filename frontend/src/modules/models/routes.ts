import type { RouteRecordRaw } from 'vue-router'
export const modelRoutes: RouteRecordRaw[] = [
  {
    path: '/models',
    alias: '/ai/model',
    name: 'models',
    component: () => import('./views/ModelsView.vue'),
    meta: { title: '模型目录', navigation: true }
  }
]
