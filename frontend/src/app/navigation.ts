import { hasModule, platformModules } from '../shared/platformAccess.ts'
import type { PlatformIdentity, PlatformModule } from '../shared/platformAccess.ts'

/** Display grouping uses the same module paths, labels and permission policy as routing. */
export const navigationGroups = [
  { id: 'applications', title: '应用', modules: ['chat', 'agents', 'work'] },
  {
    id: 'resources',
    title: '资源',
    modules: ['models', 'credentials', 'knowledge', 'knowledge_settings', 'skills', 'mcp']
  },
  { id: 'development', title: '开发与用量', modules: ['developer', 'usage'] }
] satisfies { id: string; title: string; modules: PlatformModule[] }[]

export function primaryNavigation(user: PlatformIdentity | null) {
  return navigationGroups
    .map((group) => ({
      id: group.id,
      title: group.title,
      items: group.modules.flatMap((id) => {
        if (id === 'knowledge_settings' && hasModule(user, 'knowledge')) return []
        const module = platformModules.find((module) => module.id === id)!
        return hasModule(user, id) ? [module] : []
      })
    }))
    .filter((group) => group.items.length > 0)
}
export type NavigationGroup = ReturnType<typeof primaryNavigation>[number]
