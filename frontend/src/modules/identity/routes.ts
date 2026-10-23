import type { RouteRecordRaw } from 'vue-router'
export const identityRoutes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'login',
    component: () => import('./views/LoginView.vue'),
    meta: { public: true, title: '登录' }
  }
]
