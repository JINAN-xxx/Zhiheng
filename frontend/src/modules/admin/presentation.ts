export const formatAdminTime = (value?: string | null): string =>
  value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
export const activeLabel = (value: boolean): string => (value ? '启用' : '停用')

export const validSortOrder = (value: unknown): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 1000000
