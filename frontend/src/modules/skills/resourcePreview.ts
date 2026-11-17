import type { ResourcePreviewPage, ResourcePreviewQuery } from '../../shared/resourcePreview.ts'
import { listSkills } from './api/index.ts'

export async function skillResourcePreview(
  query: ResourcePreviewQuery,
  signal: AbortSignal
): Promise<ResourcePreviewPage> {
  const result = await listSkills(
    { keyword: query.keyword, pageNum: 1, pageSize: query.limit, status: undefined },
    signal
  )
  return {
    total: result.total,
    entries: result.rows.map((row) => ({
      id: row.skillId,
      name: row.skillName,
      description: row.description,
      path: '/skills'
    }))
  }
}
