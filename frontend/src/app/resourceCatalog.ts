import { getModels } from '../modules/models/public.ts'
import { listAgents } from '../modules/agents/public.ts'
import { listBases } from '../modules/knowledge/public.ts'
import { listSkills } from '../modules/skills/public.ts'
import { errorMessage } from '../shared/http.ts'

export type ResourceKind = 'models' | 'agents' | 'knowledge' | 'skills'
export interface CatalogEntry {
  id: string
  name: string
  description: string
  path: string
  kind: ResourceKind
}
export interface CatalogSection {
  kind: ResourceKind
  label: string
  path: string
  total: number | null
  entries: CatalogEntry[]
  error: string
}

export async function loadResourceCatalog(
  signal: AbortSignal,
  keyword = '',
  admin = false
): Promise<CatalogSection[]> {
  const query = { keyword: keyword.trim(), pageNum: 1, pageSize: 12 }
  const definitions: { kind: ResourceKind; label: string; path: string }[] = [
    { kind: 'models', label: '模型', path: admin ? '/models' : '/chat' },
    { kind: 'agents', label: '智能体', path: '/agents' },
    { kind: 'knowledge', label: '知识库', path: '/knowledge' },
    { kind: 'skills', label: '技能', path: '/skills' }
  ]
  const requests = [
    getModels(signal, admin).then((rows) => {
      const filtered = rows.filter((row) =>
        row.name.toLocaleLowerCase().includes(query.keyword.toLocaleLowerCase())
      )
      return {
        total: filtered.length,
        entries: filtered.map((row) => ({
          id: row.id,
          name: row.name,
          description: row.upstream_model,
          path: admin ? '/models' : '/chat',
          kind: 'models' as const
        }))
      }
    }),
    listAgents(query, signal).then((result) => ({
      total: result.total,
      entries: result.rows.map((row) => ({
        id: row.agentId,
        name: row.name,
        description: row.description,
        path: `/agents/${encodeURIComponent(row.agentId)}`,
        kind: 'agents' as const
      }))
    })),
    listBases(signal).then((rows) => {
      const filtered = rows.filter((row) =>
        row.name.toLocaleLowerCase().includes(query.keyword.toLocaleLowerCase())
      )
      return {
        total: filtered.length,
        entries: filtered.slice(0, 12).map((row) => ({
          id: row.id,
          name: row.name,
          description: row.description,
          path: `/knowledge/${encodeURIComponent(row.id)}`,
          kind: 'knowledge' as const
        }))
      }
    }),
    listSkills({ ...query, status: undefined }, signal).then((result) => ({
      total: result.total,
      entries: result.rows.map((row) => ({
        id: row.skillId,
        name: row.skillName,
        description: row.description,
        path: '/skills',
        kind: 'skills' as const
      }))
    }))
  ]
  const results = await Promise.allSettled(requests)
  return results.map((result, index) => ({
    ...definitions[index]!,
    ...(result.status === 'fulfilled'
      ? { ...result.value, error: '' }
      : { total: null, entries: [], error: errorMessage(result.reason) })
  }))
}
