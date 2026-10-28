import type { RouteRecordRaw } from 'vue-router'
export const chatRoutes: RouteRecordRaw[] = [
  {
    path: '/chat',
    alias: '/ai/chat',
    name: 'chat',
    component: () => import('./views/ChatView.vue'),
    meta: { title: '对话体验', navigation: true }
  }
]
