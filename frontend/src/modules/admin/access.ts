export interface AdminIdentity {
  is_admin: boolean
  permissions?: readonly string[]
}
export type AdminArea =
  | 'users'
  | 'applications'
  | 'roles'
  | 'departments'
  | 'configs'
  | 'notices'
  | 'logs'
  | 'sessions'
  | 'monitor'
  | 'storage'
export interface AdminNavigation {
  path: string
  label: string
  area: AdminArea
}
export const adminNavigation: AdminNavigation[] = [
  { path: '/admin/users', label: '用户管理', area: 'users' },
  { path: '/admin/applications', label: '应用治理', area: 'applications' },
  { path: '/admin/roles', label: '角色权限', area: 'roles' },
  { path: '/admin/departments', label: '部门管理', area: 'departments' },
  { path: '/admin/configs', label: '参数管理', area: 'configs' },
  { path: '/admin/storage', label: '存储管理', area: 'storage' },
  { path: '/admin/notices', label: '通知公告', area: 'notices' },
  { path: '/admin/login-logs', label: '登录日志', area: 'logs' },
  { path: '/admin/operation-logs', label: '操作日志', area: 'logs' },
  { path: '/admin/sessions', label: '在线会话', area: 'sessions' },
  { path: '/admin/monitor', label: '系统状态', area: 'monitor' }
]
export function adminPermissionCode(area: AdminArea, action: 'read' | 'write' = 'read'): string {
  return `${area === 'storage' ? 'ai' : 'system'}:${area}:${action}`
}
export function hasAdminPermission(
  user: AdminIdentity | null,
  area: AdminArea,
  action: 'read' | 'write' = 'read'
): boolean {
  if (area === 'applications') return Boolean(user?.is_admin)
  if (area === 'storage')
    return Boolean(
      user &&
        (user.is_admin ||
          user.permissions?.includes(adminPermissionCode(area, action)) ||
          (action === 'read' && user.permissions?.includes('ai:storage:write')))
    )
  return Boolean(
    user && (user.is_admin || user.permissions?.includes(adminPermissionCode(area, action)))
  )
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
export function visibleAdminNavigation(user: AdminIdentity | null): AdminNavigation[] {
  return adminNavigation.filter((entry) => hasAdminPermission(user, entry.area))
}

export interface AdminNavigationGroup {
  id: string
  label: string
  entries: AdminNavigation[]
  path: string
  tabs: boolean
}
export const adminGroups = [
  {
    id: 'users',
    label: '用户管理',
    paths: ['/admin/users', '/admin/departments', '/admin/applications'],
    tabs: true
  },
  { id: 'roles', label: '角色权限', paths: ['/admin/roles'], tabs: false },
  { id: 'notices', label: '通知公告', paths: ['/admin/notices', '/admin/configs'], tabs: false },
  { id: 'storage', label: '存储管理', paths: ['/admin/storage'], tabs: false },
  {
    id: 'operations',
    label: '系统运维',
    paths: ['/admin/monitor', '/admin/sessions', '/admin/login-logs', '/admin/operation-logs'],
    tabs: true
  }
] as const

export function visibleAdminGroups(user: AdminIdentity | null): AdminNavigationGroup[] {
  const visible = visibleAdminNavigation(user)
  return adminGroups.flatMap((group) => {
    const entries = group.paths.flatMap((path) => {
      const entry = visible.find((item) => item.path === path)
      return entry ? [entry] : []
    })
    if (!entries.length) return []
    return [
      {
        id: group.id,
        label: group.label,
        entries,
        path: entries[0]!.path,
        tabs: group.tabs
      }
    ]
  })
}

export function adminEntryLabel(entry: AdminNavigation): string {
  const labels: Record<string, string> = {
    '/admin/users': '用户',
    '/admin/departments': '部门'
  }
  return labels[entry.path] ?? entry.label
}

export function isActiveAdminPermission(code: string): boolean {
  return !/^system:(menus|dictionaries|posts):/.test(code)
}
