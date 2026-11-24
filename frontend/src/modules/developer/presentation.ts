import type { DeveloperKey, ResourceAction, ResourceKind } from './api/index.ts'
import type { ChatModel } from '../models/public.ts'
export const applicationStatusLabel = (status: string, platformEnabled = true) =>
  !platformEnabled ? '平台停用' : status === 'active' ? '已启用' : '已停用'
export const developerKeyStatusLabel = (status: string) =>
  ({ active: '有效', retiring: '轮换宽限期', revoked: '已撤销', expired: '已过期' })[status] ??
  status
export const resourceKindLabels: Record<ResourceKind, string> = {
  file: '文件',
  knowledge_base: '知识库',
  skill: '个人技能'
}
export const resourceActionLabels: Record<ResourceAction, string> = {
  read: '读取',
  write: '写入',
  query: '检索',
  delete: '删除'
}
export const resourceCreationLabels: Record<ResourceKind, string> = {
  file: '上传新文件',
  knowledge_base: '创建新知识库',
  skill: '导入新技能'
}
export function aggregateGrantedModels(keys: DeveloperKey[], models: ChatModel[]) {
  const catalog = new Map(models.map((model) => [model.id, model]))
  const directKeys = keys.filter((key) => key.scopes.includes('models:invoke'))
  const grantedIds = [
    ...new Set(directKeys.flatMap((key) => key.grants.map((grant) => grant.modelId)))
  ]
  return grantedIds.map((id) => {
    const model = catalog.get(id)
    const authorized = directKeys.filter((key) => key.grants.some((grant) => grant.modelId === id))
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
