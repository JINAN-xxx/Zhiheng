const SECRET_FIELD_PATTERN = /(api[_-]?key|access[_-]?key|credential|auth|token|secret|password|passphrase|private[_-]?key|certificate|cert)/i

const ICON_ALIASES = Object.freeze({
  application: 'AppstoreOutlined',
  bell: 'BellOutlined',
  chat: 'MessageOutlined',
  dashboard: 'DashboardOutlined',
  database: 'DatabaseOutlined',
  edit: 'EditOutlined',
  file: 'FileOutlined',
  home: 'HomeOutlined',
  menu: 'MenuOutlined',
  model: 'RobotOutlined',
  monitor: 'FundProjectionScreenOutlined',
  search: 'SearchOutlined',
  setting: 'SettingOutlined',
  tool: 'ToolOutlined',
  user: 'UserOutlined',
})

export function resolveAsyncState({ loading = false, error = '', rows = [], configured = true } = {}) {
  if (!configured) return { kind: 'misconfigured' }
  if (loading && rows.length === 0) return { kind: 'loading' }
  if (error && rows.length === 0) return { kind: 'error' }
  if (error && rows.length > 0) return { kind: 'stale' }
  if (rows.length === 0) return { kind: 'empty' }
  return { kind: 'ready' }
}

export function canRunAction(userPermissions = [], requiredPermissions = [], blocked = false) {
  if (blocked) return false
  if (requiredPermissions.length === 0) return true
  if (userPermissions.includes('*:*:*')) return true
  return requiredPermissions.some((permission) => userPermissions.includes(permission))
}

export function sanitizeSecretDefault(fieldName, defaultValue) {
  return SECRET_FIELD_PATTERN.test(String(fieldName ?? '')) ? null : defaultValue
}

export function resolvePlatformIcon(iconName) {
  const normalized = String(iconName ?? '').trim().toLowerCase()
  return ICON_ALIASES[normalized] ?? 'QuestionOutlined'
}
