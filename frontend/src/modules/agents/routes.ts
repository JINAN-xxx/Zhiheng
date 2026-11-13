import type { RouteRecordRaw } from 'vue-router'
export const agentRoutes: RouteRecordRaw[] = [
  {
    path: '/agents/:id/chat',
    name: 'assistant-chat',
    component: () => import('./views/AssistantChatView.vue'),
    meta: { title: '助手对话', activeMenu: '/agents', reuseByParam: 'id' }
  },
  {
    path: '/agents',
    alias: '/agent_extension/agent',
    name: 'agents',
    component: () => import('./views/AgentsView.vue'),
    meta: { title: '智能助手', navigation: true }
  },
  {
    path: '/agents/new',
    name: 'agent-create',
    component: () => import('./views/AgentEditorView.vue'),
    meta: { title: '新建智能助手', navigation: false, activeMenu: '/agents' }
  },
  {
    path: '/agents/:id',
    name: 'agent-editor',
    component: () => import('./views/AgentEditorView.vue'),
    meta: { title: '智能助手配置', navigation: false, activeMenu: '/agents' }
  }
]
