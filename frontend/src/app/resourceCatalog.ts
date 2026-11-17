import { modelResourcePreview } from '../modules/models/public.ts'
import { agentResourcePreview } from '../modules/agents/public.ts'
import { knowledgeResourcePreview } from '../modules/knowledge/public.ts'
import { skillResourcePreview } from '../modules/skills/public.ts'
import { hasModule, platformModules } from '../shared/platformAccess.ts'
import type { PlatformIdentity } from '../shared/platformAccess.ts'
import type {
  ResourcePreview,
  ResourcePreviewPage,
  ResourcePreviewQuery
} from '../shared/resourcePreview.ts'
import { errorMessage } from '../shared/http.ts'

export type ResourceKind = 'models' | 'agents' | 'knowledge' | 'skills'
export interface CatalogSection {
  kind: ResourceKind
  label: string
  path: string
  total: number | null
  entries: ResourcePreview[]
  error: string
}

const resources: {
  kind: ResourceKind
  label: string
  preview: (
    query: ResourcePreviewQuery,
    signal: AbortSignal,
    admin: boolean
  ) => Promise<ResourcePreviewPage>
}[] = [
  { kind: 'models', label: '模型', preview: modelResourcePreview },
  { kind: 'agents', label: '智能助手', preview: agentResourcePreview },
  { kind: 'knowledge', label: '知识库', preview: knowledgeResourcePreview },
  { kind: 'skills', label: '技能', preview: skillResourcePreview }
]

export async function loadResourceCatalog(
  user: PlatformIdentity | null,
  signal: AbortSignal,
  keyword = ''
): Promise<CatalogSection[]> {
  const query = { keyword: keyword.trim(), limit: 12 }
  const enabled = resources.filter((resource) => hasModule(user, resource.kind))
  const results = await Promise.allSettled(
    enabled.map((resource) => resource.preview(query, signal, user?.is_admin ?? false))
  )
  signal.throwIfAborted()
  return results.map((result, index) => {
    const resource = enabled[index]!
    return {
      kind: resource.kind,
      label: resource.label,
      path: platformModules.find((module) => module.id === resource.kind)!.path,
      ...(result.status === 'fulfilled'
        ? { ...result.value, error: '' }
        : { total: null, entries: [], error: errorMessage(result.reason) })
    }
  })
}
