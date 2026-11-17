import type { WorkbenchResource, ResourceKind, ResourceAction } from './api/index.ts'
export const kindLabels: Record<ResourceKind, string> = {
  model: '模型',
  agent: '智能助手',
  knowledge: '知识库',
  skill: '技能',
  application: '应用'
}
export const actionLabels: Record<ResourceAction, string> = {
  used: '使用了',
  created: '创建了',
  published: '启用了',
  indexed: '完成资料处理',
  uploaded: '上传了',
  failed: '资料处理未完成'
}
export function resourcePath(kind: ResourceKind, id: string): string {
  return {
    model: '/models',
    agent: `/agents/${encodeURIComponent(id)}`,
    knowledge: `/knowledge/${encodeURIComponent(id)}`,
    skill: '/skills?tab=mine',
    application: '/developer'
  }[kind]
}
export function statusLabel(value: string): string {
  return (
    (
      {
        available: '已启用',
        enabled: '已启用',
        disabled: '已停用',
        published: '已发布',
        draft: '草稿',
        processing: '处理中',
        ready: '索引可用',
        empty: '待入库',
        completed: '已完成',
        partial: '部分完成',
        failed: '处理失败',
        interrupted: '已中断',
        cancelled: '已取消'
      } as Record<string, string>
    )[value] ?? value
  )
}
export function isHealthy(value: string): boolean {
  return ['available', 'enabled', 'published', 'ready', 'completed'].includes(value)
}
export function successRate(successful: number, failed: number): string {
  const ended = successful + failed
  return ended ? `${((successful / ended) * 100).toFixed(1)}%` : '—'
}
export function callChange(today: number, yesterday: number): string {
  if (!yesterday) return today ? '昨日无调用' : '今日暂无调用'
  const change = ((today - yesterday) / yesterday) * 100
  return `较昨日 ${change > 0 ? '+' : ''}${change.toFixed(1)}%`
}
export function compactNumber(value: number): string {
  return new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 2 }).format(
    value
  )
}
export function pointText(value: string): string {
  const [whole = '0', raw = ''] = value.split('.')
  const fraction = raw.replace(/0+$/, '')
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  if (whole === '0' && fraction.length > 6 && !/[1-9]/.test(fraction.slice(0, 6)))
    return '<0.000001'
  return grouped + (fraction ? '.' + fraction.slice(0, 6) : '') + (fraction.length > 6 ? '…' : '')
}
export function activityText(item: WorkbenchResource): string {
  if (item.action === 'indexed') return `完成知识库资料处理 · ${item.name}`
  if (item.action === 'failed') return `知识库资料处理未完成 · ${item.name}`
  return `${actionLabels[item.action]}${kindLabels[item.kind]} · ${item.name}`
}
export function localTime(value: string): string {
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23'
  }).format(new Date(value))
}
