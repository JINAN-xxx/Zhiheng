import type { GraphData, GraphJob } from './api/graph.ts'

const entityTypeLabels = new Map([
  ['person', '人物'],
  ['creature', '生物'],
  ['organization', '组织'],
  ['location', '地点'],
  ['event', '事件'],
  ['concept', '概念'],
  ['method', '方法'],
  ['content', '内容'],
  ['data', '数据'],
  ['artifact', '人造物'],
  ['naturalobject', '自然物'],
  ['other', '其他'],
  ['unknown', '未分类']
])

export function graphIsActive(job: GraphJob | null): boolean {
  return !!job && !['completed', 'failed', 'cancelled', 'interrupted'].includes(job.status)
}
export function graphStatusLabel(job: GraphJob | null, preview = false): string {
  if (!job) return preview ? '图谱试构建' : '图谱待构建'
  return {
    pending: '图谱构建排队中',
    running: '正在构建图谱',
    waiting_model: '等待模型结果',
    draining: '正在停止图谱任务',
    completed: preview ? '试构建完成' : '图谱构建完成',
    failed: preview ? '试构建失败' : '图谱构建失败',
    cancelled: '图谱任务已取消',
    interrupted: '图谱构建已中断'
  }[job.status]
}
export function graphProgress(job: GraphJob | null): number | undefined {
  if (!job?.total_documents || job.processed_documents === null) return undefined
  return Math.min(100, Math.round((job.processed_documents / job.total_documents) * 100))
}
export function chartGraph(graph: GraphData | null) {
  const categoryIndices = new Map<string, number>()
  const degree = new Map<string, number>()
  for (const { source, target } of graph?.edges ?? []) {
    degree.set(source, (degree.get(source) ?? 0) + 1)
    degree.set(target, (degree.get(target) ?? 0) + 1)
  }
  const nodes = (graph?.nodes ?? []).map((node) => {
    const rawType = node.entity_type.trim()
    const typeKey = rawType.toLowerCase().replace(/[\s_-]+/g, '')
    const label = entityTypeLabels.get(typeKey) ?? (rawType || '未分类')
    let category = categoryIndices.get(label)
    if (category === undefined) {
      category = categoryIndices.size
      categoryIndices.set(label, category)
    }
    return {
      id: node.id,
      name: node.entity_name,
      category,
      symbolSize: Math.min(38, 16 + 5 * Math.sqrt(degree.get(node.id) ?? 0)),
      description: node.description,
      tooltipText: `${node.entity_name}\n${label}\n${node.description}`
    }
  })
  return {
    nodes,
    links:
      graph?.edges.map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        tooltipText: edge.description
      })) ?? [],
    categories: [...categoryIndices.keys()].map((name) => ({ name })),
    truncated: graph?.is_truncated ?? false
  }
}
export type ChartGraph = ReturnType<typeof chartGraph>
