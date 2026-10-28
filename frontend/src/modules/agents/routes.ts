import type { RouteRecordRaw } from 'vue-router'
export const agentRoutes: RouteRecordRaw[] = [
  {
    path: '/agents',
    alias: '/agent_extension/agent',
    name: 'agents',
    component: () => import('./views/AgentsView.vue'),
    meta: { title: '智能体', navigation: true }
  },
  {
    path: '/agents/new',
    name: 'agent-create',
    component: () => import('./views/AgentEditorView.vue'),
    meta: { title: '新建智能体', navigation: false, activeMenu: '/agents' }
  },
  {
    path: '/agents/:id',
    name: 'agent-editor',
    component: () => import('./views/AgentEditorView.vue'),
    meta: { title: '智能体配置', navigation: false, activeMenu: '/agents' }
  }
]
