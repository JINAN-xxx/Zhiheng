const NAV_PERMISSION_KEYS = Object.freeze({
  chat: 'canChat',
  model: 'canModel',
  api: 'canApi',
})

const AI_RESOURCE_TABS = Object.freeze([
  Object.freeze({ key: 'model', label: '模型目录', path: '/ai/model', permission: 'ai:model:list' }),
  Object.freeze({ key: 'application', label: '开发接入', path: '/ai/application', permission: 'ai:application:list' }),
  Object.freeze({ key: 'usage', label: '用量记录', path: '/ai/usage', permission: 'ai:application:query' }),
])

const AGENT_EXTENSION_TABS = Object.freeze([
  Object.freeze({
    key: 'agent',
    label: '智能体管理',
    path: '/extensions/agent',
    permission: 'agent_extension:agent:list',
  }),
  Object.freeze({
    key: 'skill',
    label: '技能管理',
    path: '/extensions/skill',
    permission: 'agent_extension:skill:list',
  }),
])

const LANDING_PATH_PRIORITY = Object.freeze([
  '/ai/chat',
  '/ai/knowledge',
  '/ai/model',
  '/ai/application',
  '/ai/usage',
  '/extensions/agent',
  '/extensions/skill',
])

function isAiWorkspacePath(path = '') {
  return path === '/ai'
    || path.startsWith('/ai/')
    || path === '/extensions'
    || path.startsWith('/extensions/')
}

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
  return isAiWorkspacePath(path) ? 'ai' : 'admin'
}

export function resolveAiResourceTabs(permissions = new Set()) {
  return AI_RESOURCE_TABS.filter(
    (item) => permissions.has('*:*:*') || permissions.has(item.permission),
  )
}

export function resolveAiResourceGroups(permissions = new Set()) {
  const children = resolveAiResourceTabs(permissions)
  if (!children.length) return []

  return [{
    key: 'model-center',
    label: '模型中心',
    path: children[0].path,
    children,
  }]
}

export function resolveAgentExtensionGroups(permissions = new Set()) {
  const children = AGENT_EXTENSION_TABS.filter(
    (item) => permissions.has('*:*:*') || permissions.has(item.permission),
  )
  if (!children.length) return []
  return [{
    key: 'agent-extension',
    label: '智能体扩展',
    path: children[0].path,
    children,
  }]
}

export function isAiResourceGroupActive(group, currentPath = '') {
  return Boolean(getActiveAiResourceChildPath(group, currentPath))
}

/**
 * Resolve the deepest resource child that owns the current path.
 * Segment-aware matching prevents `/ai/application-old` from activating
 * the `/ai/application` menu item.
 */
export function getActiveAiResourceChildPath(group, currentPath = '') {
  if (!Array.isArray(group?.children) || typeof currentPath !== 'string') return null

  return group.children
    .filter((child) => {
      if (typeof child?.path !== 'string' || !child.path) return false
      const path = child.path.replace(/\/+$/u, '') || '/'
      return currentPath === path || currentPath.startsWith(`${path}/`)
    })
    .sort((left, right) => right.path.length - left.path.length)
    .at(0)?.path || null
}

export function resolveAuthorizedWorkspacePath(currentPath, accessiblePaths = []) {
  if (!isAiWorkspacePath(currentPath)) return currentPath

  const isAccessible = accessiblePaths.some((path) => (
    typeof path === 'string'
    && path.replace(/\/+$/u, '') !== '/ai'
    && (currentPath === path || currentPath.startsWith(`${path}/`))
  ))
  if (isAccessible) return currentPath

  return resolveWorkspaceLandingPath('/index', accessiblePaths)
}

export function filterAdminWorkspaceRoutes(routes = []) {
  return routes.filter((route) => {
    const path = typeof route?.path === 'string' ? route.path : ''
    return !isAiWorkspacePath(path)
  })
}

export function normalizeWorkspaceSessions(source) {
  const rows = Array.isArray(source)
    ? source
    : source?.rows || source?.data?.rows || source?.data?.items || source?.items || []

  return rows.filter(Boolean).map((session) => ({
    sessionId: session.sessionId ?? session.session_id ?? session.id ?? null,
    title: session.title || session.name || '新对话',
    updatedAt: session.updatedAt ?? session.updated_at ?? session.createdAt ?? session.created_at ?? null,
  })).filter((session) => session.sessionId)
}

export function shouldApplyGeneration(requestGeneration, activeGeneration) {
  return requestGeneration === activeGeneration
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

function isExternalWorkspacePath(path) {
  return /^(?:[a-z][a-z\d+.-]*:|\/\/)/iu.test(path)
}

export function collectWorkspaceLeafPaths(routes = [], parentPath = '') {
  if (!Array.isArray(routes)) return []

  return routes.flatMap((route) => {
    if (!route || route.hidden === true || route.meta?.hidden === true) return []

    const routePath = typeof route.path === 'string' ? route.path : ''
    if (isExternalWorkspacePath(routePath)) return []

    const fullPath = routePath.startsWith('/')
      ? routePath
      : [parentPath.replace(/\/$/u, ''), routePath].filter(Boolean).join('/')
    const normalizedPath = fullPath ? `/${fullPath.replace(/^\/+/, '')}` : parentPath
    const children = Array.isArray(route.children) ? route.children : []

    if (children.length) return collectWorkspaceLeafPaths(children, normalizedPath)
    return routePath && normalizedPath ? [normalizedPath] : []
  })
}

export function resolvePaginationLayout(width, desktopLayout) {
  if (width < 600) return 'prev, pager, next'
  if (width < 900) return 'total, prev, pager, next'
  return desktopLayout
}
