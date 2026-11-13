import type { RouteRecordRaw } from 'vue-router'
import { fileRoutes } from '../files/public.ts'
export const adminRoutes: RouteRecordRaw[] = [
  {
    path: '/admin',
    name: 'admin',
    component: () => import('./components/AdminLayout.vue'),
    meta: { title: '后台管理', console: true },
    children: [
      {
        path: 'applications',
        name: 'admin-applications',
        component: () => import('./views/ApplicationsView.vue'),
        meta: { title: '应用治理', admin: true }
      },
      ...fileRoutes.map((route) => ({ ...route, path: route.path.replace(/^\/admin\//, '') })),
      {
        path: 'users',
        name: 'admin-users',
        component: () => import('./views/UsersView.vue'),
        meta: { title: '用户管理' }
      },
      {
        path: 'roles',
        name: 'admin-roles',
        component: () => import('./views/RolesView.vue'),
        meta: { title: '角色权限' }
      },
      {
        path: 'departments',
        name: 'admin-departments',
        component: () => import('./views/DepartmentsView.vue'),
        meta: { title: '部门管理' }
      },
      {
        path: 'posts',
        redirect: '/admin'
      },
      {
        path: 'menus',
        redirect: '/admin'
      },
      {
        path: 'dictionaries',
        redirect: '/admin'
      },
      {
        path: 'configs',
        name: 'admin-configs',
        component: () => import('./views/ConfigsView.vue'),
        meta: { title: '公告设置' }
      },
      {
        path: 'knowledge-settings',
        name: 'admin-knowledge-settings',
        redirect: '/knowledge/settings',
        meta: { title: '知识服务', permission: 'system:configs:read' }
      },
      {
        path: 'notices',
        name: 'admin-notices',
        component: () => import('./views/NoticesView.vue'),
        meta: { title: '通知公告' }
      },
      {
        path: 'login-logs',
        name: 'admin-login-logs',
        component: () => import('./views/LogsView.vue'),
        props: { kind: 'login' },
        meta: { title: '登录日志' }
      },
      {
        path: 'operation-logs',
        name: 'admin-operation-logs',
        component: () => import('./views/LogsView.vue'),
        props: { kind: 'operation' },
        meta: { title: '操作日志' }
      },
      {
        path: 'sessions',
        name: 'admin-sessions',
        component: () => import('./views/SessionsView.vue'),
        meta: { title: '在线会话' }
      },
      {
        path: 'monitor',
        name: 'admin-monitor',
        component: () => import('./views/MonitorView.vue'),
        meta: { title: '系统状态' }
      }
    ]
  }
]
