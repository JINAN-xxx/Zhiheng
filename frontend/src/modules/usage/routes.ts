import type { RouteRecordRaw } from 'vue-router'
export const usageRoutes: RouteRecordRaw[] = [
  {
    path: '/usage',
    alias: '/ai/usage',
    name: 'usage',
    component: () => import('./views/UsageView.vue'),
    meta: { title: '用量与调用', navigation: true }
  }
]
