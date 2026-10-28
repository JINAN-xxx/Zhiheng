import type { RouteRecordRaw } from 'vue-router'
export const adminRoutes: RouteRecordRaw[] = [
  {
    path: '/admin',
    name: 'admin',
    component: () => import('./components/AdminLayout.vue'),
    meta: { title: '后台管理', console: true },
    children: [
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
        name: 'admin-posts',
        component: () => import('./views/PostsView.vue'),
        meta: { title: '岗位管理' }
      },
      {
        path: 'menus',
        name: 'admin-menus',
        component: () => import('./views/MenusView.vue'),
        meta: { title: '菜单管理' }
      },
      {
        path: 'dictionaries',
        name: 'admin-dictionaries',
        component: () => import('./views/DictionariesView.vue'),
        meta: { title: '字典管理' }
      },
      {
        path: 'configs',
        name: 'admin-configs',
        component: () => import('./views/ConfigsView.vue'),
        meta: { title: '参数管理' }
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
