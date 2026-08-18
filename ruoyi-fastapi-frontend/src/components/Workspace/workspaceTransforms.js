const NAV_PERMISSION_KEYS = Object.freeze({
  chat: 'canChat',
  model: 'canModel',
  api: 'canApi',
})

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

export function resolveWorkspaceLandingPath(currentPath, accessiblePaths = []) {
  if (currentPath === '/index' && accessiblePaths.includes('/ai/chat')) return '/ai/chat'
  return currentPath
}
