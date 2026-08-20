const NAV_PERMISSION_KEYS = Object.freeze({
  chat: 'canChat',
  model: 'canModel',
  api: 'canApi',
})

const AI_RESOURCE_TABS = Object.freeze([
  Object.freeze({ key: 'model', label: '模型', path: '/ai/model', permission: 'ai:model:list' }),
  Object.freeze({ key: 'application', label: '应用接入', path: '/ai/application', permission: 'ai:application:list' }),
  Object.freeze({ key: 'usage', label: '用量', path: '/ai/usage', permission: 'ai:application:list' }),
])

const LANDING_PATH_PRIORITY = Object.freeze([
  '/ai/chat',
  '/ai/model',
  '/ai/application',
  '/ai/usage',
])

export function visibleWorkspaceNav(access = {}) {
  return Object.entries(NAV_PERMISSION_KEYS)
    .filter(([, permissionKey]) => access[permissionKey] === true)
    .map(([key]) => key)
}

export function resolveWorkspaceState({ loading = false, rows = [], error = null } = {}) {
  if (loading) return { kind: 'loading' }
  if (error && rows.length) return { kind: 'stale', error }
  if (error) return { kind: 'error', error }
  return rows.length ? { kind: 'ready' } : { kind: 'empty' }
}

export function groupWorkspaceActions({ mobile = false, canEdit = false, canDelete = false } = {}) {
  if (mobile) return ['more']
  return [canEdit && 'edit', canDelete && 'delete'].filter(Boolean)
}

export function workspaceStateLabel(kind) {
  return {
    loading: '加载中',
    empty: '暂无数据',
    error: '加载失败',
    stale: '数据可能已过期',
    ready: '已就绪',
  }[kind] || '加载失败'
}

export function resolveWorkspaceMode(path = '') {
  return path === '/ai' || path.startsWith('/ai/') ? 'ai' : 'admin'
}

export function resolveAiResourceTabs(permissions = new Set()) {
  return AI_RESOURCE_TABS.filter(
    (item) => permissions.has('*:*:*') || permissions.has(item.permission),
  )
}

export function filterAdminWorkspaceRoutes(routes = []) {
  return routes.filter((route) => {
    const path = typeof route?.path === 'string' ? route.path : ''
    return path !== '/ai' && !path.startsWith('/ai/')
  })
}

export function resolveWorkspaceLandingPath(currentPath, accessiblePaths = []) {
  if (currentPath !== '/' && currentPath !== '/index') return currentPath

  const preferredPath = LANDING_PATH_PRIORITY.find((path) => accessiblePaths.includes(path))
  if (preferredPath) return preferredPath

  return accessiblePaths.find(
    (path) => path.startsWith('/') && path !== '/index' && resolveWorkspaceMode(path) === 'admin',
  ) || '/401'
}

export function collectWorkspaceRoutePaths(routes = [], parentPath = '') {
  return routes.flatMap((route) => {
    const routePath = typeof route?.path === 'string' ? route.path : ''
    const fullPath = routePath.startsWith('/')
      ? routePath
      : [parentPath.replace(/\/$/u, ''), routePath].filter(Boolean).join('/')
    const normalizedPath = fullPath ? `/${fullPath.replace(/^\/+/, '')}` : parentPath
    const current = routePath ? [normalizedPath] : []
    return current.concat(collectWorkspaceRoutePaths(route?.children, normalizedPath))
  })
}

export function resolvePaginationLayout(width, desktopLayout) {
  if (width < 600) return 'prev, pager, next'
  if (width < 900) return 'total, prev, pager, next'
  return desktopLayout
}
