import type { GraphData, GraphJob } from './api/graph.ts'

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
  const categories = [...new Set(graph?.nodes.map((node) => node.entity_type) ?? [])].map(
    (name) => ({ name })
  )
  return {
    nodes:
      graph?.nodes.map((node) => ({
        id: node.id,
        name: node.entity_name,
        category: categories.findIndex((item) => item.name === node.entity_type),
        symbolSize: 34,
        tooltipText: `${node.entity_name}\n${node.entity_type}\n${node.description}`
      })) ?? [],
    links:
      graph?.edges.map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        tooltipText: edge.description
      })) ?? [],
    categories,
    truncated: graph?.is_truncated ?? false
  }
}
export type ChartGraph = ReturnType<typeof chartGraph>
