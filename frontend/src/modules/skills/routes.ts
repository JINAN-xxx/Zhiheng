import type { RouteRecordRaw } from 'vue-router'
export const skillRoutes: RouteRecordRaw[] = [
  {
    path: '/skills',
    alias: '/agent_extension/skill',
    name: 'skills',
    component: () => import('./views/SkillsView.vue'),
    meta: { title: '技能管理', navigation: true }
  }
]
