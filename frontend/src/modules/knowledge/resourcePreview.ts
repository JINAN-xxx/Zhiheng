import type { ResourcePreviewPage, ResourcePreviewQuery } from '../../shared/resourcePreview.ts'
import { listBases } from './api/index.ts'

export async function knowledgeResourcePreview(
  query: ResourcePreviewQuery,
  signal: AbortSignal
): Promise<ResourcePreviewPage> {
  const rows = (await listBases(signal))
    .filter((row) => row.availability === 'available')
    .filter((row) => row.name.toLocaleLowerCase().includes(query.keyword.toLocaleLowerCase()))
  return {
    total: rows.length,
    entries: rows.slice(0, query.limit).map((row) => ({
      id: row.id,
      name: row.name,
      description: row.description,
      path: `/knowledge/${encodeURIComponent(row.id)}`
    }))
  }
}
