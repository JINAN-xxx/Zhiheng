import type { ChatModel } from './api/index.ts'

export type ModelControlAction = 'enable' | 'disable' | 'delete'

export function modelTypeLabel(type: ChatModel['model_type']): string {
  return { chat: '对话', embedding: '向量', rerank: '重排序' }[type]
}
