import type { RouteRecordRaw } from 'vue-router'
export const chatRoutes: RouteRecordRaw[] = [
  {
    path: '/share/:token',
    name: 'shared-message',
    component: () => import('./views/SharedMessageView.vue'),
    meta: { title: '分享的回复', public: true }
  },
  {
    path: '/chat',
    alias: '/ai/chat',
    name: 'chat',
    component: () => import('./views/ChatView.vue'),
    meta: { title: '对话体验', navigation: true }
  }
]
