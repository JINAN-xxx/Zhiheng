import type { RouteRecordRaw } from 'vue-router'
export const workRoutes: RouteRecordRaw[] = [
  {
    path: '/work',
    alias: '/ai/chat/work',
    name: 'work',
    component: () => import('./views/WorkView.vue'),
    meta: { title: '工作' }
  }
]
