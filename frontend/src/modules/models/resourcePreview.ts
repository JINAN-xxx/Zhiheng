import type { ResourcePreviewPage, ResourcePreviewQuery } from '../../shared/resourcePreview.ts'
import { getModels } from './api/index.ts'

export async function modelResourcePreview(
  query: ResourcePreviewQuery,
  signal: AbortSignal,
  includeDisabled = false
): Promise<ResourcePreviewPage> {
  const rows = (await getModels(signal, includeDisabled)).filter((row) =>
    row.name.toLocaleLowerCase().includes(query.keyword.toLocaleLowerCase())
  )
  return {
    total: rows.length,
    entries: rows.slice(0, query.limit).map((row) => ({
      id: row.id,
      name: row.name,
      description: row.upstream_model,
      path: '/models'
    }))
  }
}
