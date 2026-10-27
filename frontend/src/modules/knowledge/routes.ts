import type { RouteRecordRaw } from 'vue-router'
export const knowledgeRoutes: RouteRecordRaw[] = [
  {
    path: '/knowledge/:baseId?/:view?',
    name: 'knowledge',
    component: () => import('./views/KnowledgeView.vue'),
    meta: { title: '知识库', navigation: true }
  }
]
