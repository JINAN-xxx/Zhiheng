import type { RouteRecordRaw } from 'vue-router'
export const chatRoutes: RouteRecordRaw[] = [
  {
    path: '/chat',
    alias: '/ai/chat',
    name: 'chat',
    component: () => import('./views/ChatView.vue'),
    meta: { title: '智能对话', navigation: true }
  }
]
