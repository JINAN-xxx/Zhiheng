import type { FileStatus, StorageStatus } from './api/index.ts'
export const commonFileTypeOptions = [
  { label: '全部类型', value: '' },
  { label: 'PDF', value: 'application/pdf' },
  {
    label: 'Word（DOCX）',
    value: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
  },
  {
    label: 'Excel（XLSX）',
    value: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
  },
  { label: '文本', value: 'text/plain' },
  { label: 'Markdown', value: 'text/markdown' },
  { label: 'CSV', value: 'text/csv' },
  { label: 'JSON', value: 'application/json' },
  { label: 'HTML', value: 'text/html' },
  { label: 'PNG 图片', value: 'image/png' },
  { label: 'JPEG 图片', value: 'image/jpeg' },
  { label: 'GIF 图片', value: 'image/gif' },
  { label: 'WebP 图片', value: 'image/webp' },
  { label: 'SVG 图片', value: 'image/svg+xml' },
  { label: 'ZIP 压缩包', value: 'application/zip' }
]
export function fileTypeLabel(filename: string, mime: string): string {
  const extension = filename.match(/\.([a-z0-9]{1,16})$/i)?.[1]
  if (extension) return extension.toUpperCase()
  return (
    commonFileTypeOptions.find((option) => option.value !== '' && option.value === mime)?.label ??
    '文件'
  )
}
export function formatFileSize(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(2)} KB`
  if (size < 1024 * 1024 * 1024) return `${(size / 1024 / 1024).toFixed(2)} MB`
  return `${(size / 1024 / 1024 / 1024).toFixed(2)} GB`
}
export function formatTime(value: string | null | undefined): string {
  if (!value || Number.isNaN(new Date(value).getTime())) return '-'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false
  }).format(new Date(value))
}
export const accessTypeLabel = (value: string) => (value === 'public' ? '平台内公开' : '受保护文件')
export const statusLabel = (status: FileStatus) =>
  ({ active: '正常', deleted: '已删除', purging: '清理中', purged: '已清理' })[status]
export function expirationLabel(value: string | null): string {
  if (!value) return '永久有效'
  const remaining = new Date(value).getTime() - Date.now()
  if (remaining <= 0) return '已过期'
  if (remaining <= 7 * 86400000) return '即将过期'
  return formatTime(value).split(' ')[0] ?? value
}
export const expirationTagType = (value: string | null): string =>
  !value
    ? 'default'
    : new Date(value).getTime() <= Date.now()
      ? 'red'
      : isAclExpiring(value)
        ? 'orange'
        : 'green'
export const isAclExpiring = (value: string | null): boolean =>
  value !== null &&
  new Date(value).getTime() > Date.now() &&
  new Date(value).getTime() <= Date.now() + 7 * 86400000
export const storageStatusLabel = (value: StorageStatus) =>
  ({
    normal: '正常',
    missing: '文件缺失',
    quarantined: '已隔离',
    invalid: '异常',
    unknown: '尚未检查'
  })[value]
export const storageStatusTagType = (value: StorageStatus) =>
  ({ normal: 'green', missing: 'red', quarantined: 'orange', invalid: 'red', unknown: 'default' })[
    value
  ]
const actionLabels: Record<string, string> = {
  uploaded: '上传',
  updated: '元数据修改',
  download: '下载',
  acl_updated: '授权变更',
  transferred: '归属转移',
  deleted: '移入回收站',
  restored: '恢复',
  purged: '永久清理',
  reconcile: '存储对账',
  expiry_extended: '保留延期',
  expired_references_released: '到期处置',
  references_released: '解除业务引用',
  parsed: '原生解析'
}
export const actionLabel = (value: string): string => actionLabels[value] ?? value
export function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob),
    anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
export function localDateTime(value: string | null): string | undefined {
  if (!value) return undefined
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return undefined
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
}
export function isoDateTime(value: string | undefined): string | null {
  if (!value) return null
  const date = new Date(value.replace(' ', 'T'))
  if (Number.isNaN(date.getTime())) throw new Error('请输入有效日期')
  return date.toISOString()
}
