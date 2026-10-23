import type { RouteRecordRaw } from 'vue-router'
export const agentRoutes: RouteRecordRaw[] = [
  {
    path: '/agents',
    alias: '/agent_extension/agent',
    name: 'agents',
    component: () => import('./views/AgentsView.vue'),
    meta: { title: '智能体', navigation: true }
  }
]
