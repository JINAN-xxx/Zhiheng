import type { DeveloperKey } from './api/index.ts'
import type { ChatModel } from '../models/public.ts'
export const applicationStatusLabel = (status: string, platformEnabled = true) =>
  !platformEnabled ? '平台停用' : status === 'active' ? '已启用' : '已停用'
export const developerKeyStatusLabel = (status: string) =>
  ({ active: '有效', retiring: '轮换宽限期', revoked: '已撤销', expired: '已过期' })[status] ??
  status
export function aggregateGrantedModels(keys: DeveloperKey[], models: ChatModel[]) {
  const catalog = new Map(models.map((model) => [model.id, model]))
  const grantedIds = [...new Set(keys.flatMap((key) => key.grants.map((grant) => grant.modelId)))]
  return grantedIds.map((id) => {
    const model = catalog.get(id)
    const authorized = keys.filter((key) => key.grants.some((grant) => grant.modelId === id))
    return {
      modelId: id,
      modelName: model?.name ?? '当前不可用的模型',
      modelCode: id,
      available: model?.enabled === true,
      keyCount: authorized.length,
      activeKeyCount: authorized.filter(
        (key) =>
          (key.status === 'active' || key.status === 'retiring') &&
          (key.expiresAt === null || Date.parse(key.expiresAt) > Date.now())
      ).length
    }
  })
}
