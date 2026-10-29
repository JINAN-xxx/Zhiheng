import type { CreditRateInput } from './api/types.ts'

const decimal = /^-?(0|[1-9]\d*)(\.\d{1,18})?$/
export function parsePoints(value: unknown): string {
  if (typeof value !== 'string' || !decimal.test(value))
    throw new Error('服务器返回的积分须为十进制字符串')
  return value
}
export function validPoints(value: string, positive = false): boolean {
  return /^(0|[1-9]\d*)(\.\d{1,18})?$/.test(value) && (!positive || /[1-9]/.test(value))
}
export function formatPoints(value: string): string {
  parsePoints(value)
  const [integer = '0', fraction = ''] = value.split('.')
  const trimmed = fraction.replace(/0+$/, '')
  return `${integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',')}${trimmed ? `.${trimmed}` : ''}`
}
export function creditAccess(user: { is_admin: boolean; permissions?: readonly string[] } | null) {
  const write = Boolean(user?.is_admin || user?.permissions?.includes('ai:credits:write'))
  return {
    read: write || Boolean(user?.permissions?.includes('ai:credits:read')),
    write,
    switchMode: Boolean(user?.is_admin)
  }
}
export function rateError(rate: CreditRateInput): string {
  const values = [rate.input_points, rate.output_points, rate.cache_points, rate.search_points]
  if (values.some((value) => !validPoints(value))) return '积分单价须为非负十进制数，最多 18 位小数'
  if (rate.is_free)
    return values.some((value) => /[1-9]/.test(value)) ? '免费模型的所有积分单价须为零' : ''
  const relevant = rate.billing_mode === 'search_unit' ? [rate.search_points] : values.slice(0, 3)
  return relevant.some((value) => /[1-9]/.test(value)) ? '' : '零价格必须明确标记为免费'
}
export const ledgerKind = (kind: string): string =>
  ({
    grant: '发放',
    reclaim: '回收',
    reserve: '预占',
    settle: '消费结算',
    release: '释放预占',
    reconcile: '人工核对',
    adjustment: '调整'
  })[kind] ?? kind
export const usageStatus = (status: string): string =>
  ({ reserved: '预占中', unknown: '待核对', settled: '已结算' })[status] ?? status
export const creditTime = (value: string): string =>
  new Date(value).toLocaleString('zh-CN', { hour12: false })
export function monthlyPointsLimit(value: string): string | null {
  const trimmed = value.trim()
  if (!trimmed) return null
  if (!validPoints(trimmed)) throw new Error('月积分上限须为非负十进制数，最多 18 位小数')
  return trimmed
}

export const creditSource = (source: string): string =>
  ({
    chat: '对话',
    connection_test: '连接测试',
    public: '开发接入',
    agent_preview: '智能体试聊',
    agent_invocation: '智能体 API',
    work_turn: '工作执行',
    knowledge: '知识处理',
    embedding: '向量处理',
    rerank: '重排序'
  })[source] ?? source
