import type { ResourcePreviewPage, ResourcePreviewQuery } from '../../shared/resourcePreview.ts'
import { listAgents } from './api/index.ts'

export async function agentResourcePreview(
  query: ResourcePreviewQuery,
  signal: AbortSignal
): Promise<ResourcePreviewPage> {
  const result = await listAgents(
    { keyword: query.keyword, pageNum: 1, pageSize: query.limit },
    signal
  )
  return {
    total: result.total,
    entries: result.rows.map((row) => ({
      id: row.agentId,
      name: row.name,
      description: row.description,
      path: `/agents/${encodeURIComponent(row.agentId)}`
    }))
  }
}
