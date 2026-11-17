export interface PlatformIdentity {
  is_admin: boolean
  permissions?: readonly string[]
}
export const platformModules = [
  { id: 'overview', label: '工作台', path: '/overview' },
  { id: 'chat', label: '对话体验', path: '/chat' },
  { id: 'agents', label: '智能助手', path: '/agents' },
  { id: 'work', label: '工作空间', path: '/work' },
  { id: 'models', label: '模型目录', path: '/models' },
  { id: 'credentials', label: '凭据管理', path: '/credentials' },
  { id: 'knowledge', label: '知识库', path: '/knowledge' },
  { id: 'skills', label: '技能', path: '/skills' },
  { id: 'developer', label: '开发接入', path: '/developer' },
  { id: 'usage', label: '用量与调用', path: '/usage' }
] as const
export type PlatformModule = (typeof platformModules)[number]['id']
export function hasPermission(user: PlatformIdentity | null | undefined, code: string): boolean {
  if (!user) return false
  if (user.is_admin) return true
  const index = code.lastIndexOf(':'),
    module = code.slice(0, index),
    action = code.slice(index + 1)
  return (
    !!user.permissions?.includes(module + ':enabled') &&
    (action === 'enabled' || !!user.permissions?.includes(module + ':read')) &&
    !!user.permissions?.includes(code)
  )
}
export function hasCapability(
  user: PlatformIdentity | null | undefined,
  module: string,
  action: string
): boolean {
  return hasPermission(user, `${module === 'credentials' ? 'ai' : 'platform'}:${module}:${action}`)
}
export function hasModule(user: PlatformIdentity | null | undefined, module: string): boolean {
  return hasCapability(user, module, 'read')
}
const legacyModulePaths: readonly (readonly [string, string])[] = [
  ['/ai/chat/work', '/work'],
  ['/ai/chat', '/chat'],
  ['/ai/model', '/models'],
  ['/ai/application', '/developer'],
  ['/ai/usage', '/usage'],
  ['/agent_extension/agent', '/agents'],
  ['/agent_extension/skill', '/skills'],
  ['/knowledge/storage', '/admin/storage'],
  ['/files', '/admin/storage'],
  ['/system/file', '/admin/storage']
]
export function moduleForPath(path: string): PlatformModule | null {
  const alias = legacyModulePaths.find(
    ([legacy]) => path === legacy || path.startsWith(legacy + '/')
  )
  if (alias) path = alias[1] + path.slice(alias[0].length)
  return (
    platformModules.find((module) => path === module.path || path.startsWith(module.path + '/'))
      ?.id ?? null
  )
}
export function firstModulePath(user: PlatformIdentity | null | undefined): string {
  const module = platformModules.find((item) => hasModule(user, item.id))
  if (module) return module.path
  return user?.permissions?.some(
    (code) =>
      (code.startsWith('system:') || code.startsWith('ai:storage:')) &&
      code.endsWith(':read') &&
      hasPermission(user, code)
  )
    ? '/admin'
    : '/access'
}
