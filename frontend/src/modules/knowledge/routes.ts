import type { RouteRecordRaw } from 'vue-router'
export const knowledgeRoutes: RouteRecordRaw[] = [
  {
    path: '/knowledge/join',
    name: 'knowledge-join',
    component: () => import('./views/KnowledgeJoinView.vue'),
    meta: { title: '知识库邀请', public: true }
  },
  {
    path: '/knowledge/settings',
    name: 'knowledge-settings',
    component: () => import('./views/KnowledgeDefaultsView.vue'),
    meta: { title: '知识库默认配置', permission: 'system:configs:read' }
  },
  {
    path: '/knowledge/:baseId?/:view?',
    name: 'knowledge',
    component: () => import('./views/KnowledgeView.vue'),
    meta: { title: '知识库', navigation: true, reuseByParam: 'baseId' }
  }
]
