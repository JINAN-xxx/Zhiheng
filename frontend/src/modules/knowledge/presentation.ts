import type { KnowledgeFolder } from './api/index.ts'
export function formatTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { hour12: false })
}
export { formatFileSize as formatSize } from '../files/public.ts'
export const documentStatus = (status: string): string =>
  ({
    ready: '已索引',
    pending: '等待处理',
    processing: '处理中',
    failed: '失败',
    deleting: '正在删除'
  })[status] ?? status
export const jobStatus = (status: string): string =>
  ({
    pending: '等待处理',
    running: '处理中',
    waiting_model: '等待模型',
    publishing: '正在发布',
    completed: '已完成',
    partial: '部分完成',
    failed: '失败',
    cancel_requested: '正在取消',
    cancelled: '已取消',
    interrupted: '已中断'
  })[status] ?? status
export function folderTrail(
  folders: readonly KnowledgeFolder[],
  id: string | null
): KnowledgeFolder[] {
  const seen = new Set<string>(),
    result: KnowledgeFolder[] = []
  while (id && !seen.has(id)) {
    seen.add(id)
    const folder = folders.find((folder) => folder.id === id)
    if (!folder) break
    result.unshift(folder)
    id = folder.parent_id
  }
  return result
}
export function validChunks(size: number | null, overlap: number | null): boolean {
  return (
    size !== null &&
    overlap !== null &&
    Number.isInteger(size) &&
    Number.isInteger(overlap) &&
    size >= 100 &&
    size <= 4000 &&
    overlap >= 0 &&
    overlap <= 1000 &&
    overlap < size
  )
}
