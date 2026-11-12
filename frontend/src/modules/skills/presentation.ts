export const skillStatusLabel = (status: string): string =>
  status === 'enabled' ? '已启用' : '已停用'
export function formatSkillBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
