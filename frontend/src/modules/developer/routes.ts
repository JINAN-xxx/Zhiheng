import type { RouteRecordRaw } from 'vue-router'
export const developerRoutes: RouteRecordRaw[] = [
  {
    path: '/developer',
    alias: '/ai/application',
    name: 'developer',
    component: () => import('./views/ApplicationsView.vue'),
    meta: { title: '开发接入', navigation: true }
  }
]
