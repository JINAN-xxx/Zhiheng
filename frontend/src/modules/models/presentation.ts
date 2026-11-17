import type { ChatModel } from './api/index.ts'

export type ModelControlAction = 'enable' | 'disable' | 'delete'

export function modelTypeLabel(type: ChatModel['model_type']): string {
  return { chat: '对话', embedding: '向量', rerank: '重排序' }[type]
}

export function modelCapacityLabel(model: ChatModel): string {
  const status =
    model.capacity_status ??
    (model.model_type !== 'chat'
      ? 'not_applicable'
      : model.context_window_tokens == null
        ? 'unknown'
        : model.capacity_note?.trim()
          ? 'documented'
          : 'unverified')
  return {
    unknown: '窗口未知',
    unverified: '未提供依据',
    documented: '已记录依据',
    not_applicable: '按输入接口限制'
  }[status]
}
export function modelUnavailableReason(model: ChatModel): string {
  const reason = model.availability?.reason
  return reason
    ? {
        model_disabled: '模型已停用',
        model_deleted: '模型已删除',
        connection_missing: '模型连接不存在',
        connection_disabled: '模型连接已停用',
        connection_deleted: '模型连接已删除'
      }[reason]
    : ''
}
export function modelConnectionIssue(model: ChatModel): string {
  return model.availability?.reason?.startsWith('connection_') ? modelUnavailableReason(model) : ''
}
