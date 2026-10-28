export interface AdminIdentity {
  is_admin: boolean
  permissions?: readonly string[]
}
export type AdminArea =
  | 'users'
  | 'roles'
  | 'departments'
  | 'posts'
  | 'menus'
  | 'dictionaries'
  | 'configs'
  | 'notices'
  | 'logs'
  | 'sessions'
  | 'monitor'
export interface NavigationOverride {
  path: string
  name: string
  sort_order: number
  is_visible: boolean
}
export interface AdminNavigation {
  path: string
  label: string
  area: AdminArea
  order: number
}
export const adminNavigation: AdminNavigation[] = [
  { path: '/admin/users', label: '用户管理', area: 'users', order: 10 },
  { path: '/admin/roles', label: '角色权限', area: 'roles', order: 20 },
  { path: '/admin/departments', label: '部门管理', area: 'departments', order: 30 },
  { path: '/admin/posts', label: '岗位管理', area: 'posts', order: 40 },
  { path: '/admin/menus', label: '菜单管理', area: 'menus', order: 50 },
  { path: '/admin/dictionaries', label: '字典管理', area: 'dictionaries', order: 60 },
  { path: '/admin/configs', label: '参数管理', area: 'configs', order: 70 },
  { path: '/admin/notices', label: '通知公告', area: 'notices', order: 80 },
  { path: '/admin/login-logs', label: '登录日志', area: 'logs', order: 90 },
  { path: '/admin/operation-logs', label: '操作日志', area: 'logs', order: 100 },
  { path: '/admin/sessions', label: '在线会话', area: 'sessions', order: 110 },
  { path: '/admin/monitor', label: '系统状态', area: 'monitor', order: 120 }
]
export function hasAdminPermission(
  user: AdminIdentity | null,
  area: AdminArea,
  action: 'read' | 'write' = 'read'
): boolean {
  return Boolean(user && (user.is_admin || user.permissions?.includes(`system:${area}:${action}`)))
}
export function canAccessAdmin(user: AdminIdentity | null): boolean {
  return Boolean(
    user &&
      (user.is_admin ||
        adminNavigation.some(
          ({ area }) => hasAdminPermission(user, area) || hasAdminPermission(user, area, 'write')
        ))
  )
}
export function visibleAdminNavigation(
  user: AdminIdentity | null,
  menus?: NavigationOverride[]
): AdminNavigation[] {
  return adminNavigation
    .filter((entry) => hasAdminPermission(user, entry.area))
    .flatMap((entry) => {
      const override = menus?.find((menu) => menu.path === entry.path)
      if (menus && (!override || !override.is_visible)) return []
      return [
        {
          ...entry,
          label: override?.name || entry.label,
          order: override?.sort_order ?? entry.order
        }
      ]
    })
    .sort((a, b) => a.order - b.order || a.path.localeCompare(b.path))
}
