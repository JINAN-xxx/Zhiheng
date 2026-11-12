import type { ChatModel, ModelTrialPayload } from './api/index.ts'

export interface TrialIntent extends ModelTrialPayload {
  client_request_id: string
}
export interface TrialProgress {
  ownerId: string
  modelId: string
  intent: TrialIntent
  runId: string | null
}
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
export function trialPayload(
  prompt: string,
  documents: string,
  type: ChatModel['model_type']
): ModelTrialPayload {
  const value = prompt.trim()
  if (!value || [...value].length > 4000 || value.includes('\0'))
    throw new Error('请输入 1 至 4000 个字符的试用内容')
  const rows =
    type === 'rerank'
      ? documents
          .split(/\r?\n/)
          .map((row) => row.trim())
          .filter(Boolean)
      : []
  if (type === 'rerank' && (!rows.length || rows.length > 8))
    throw new Error('请提供 1 至 8 份候选资料，每行一份')
  if (rows.some((row) => [...row].length > 1000 || row.includes('\0')))
    throw new Error('每份候选资料最多 1000 个字符')
  return { prompt: value, documents: rows }
}
export function parseTrialProgress(
  raw: string | null,
  ownerId: string,
  modelId: string
): TrialProgress | null {
  if (!raw) return null
  try {
    const data: unknown = JSON.parse(raw)
    if (
      !data ||
      typeof data !== 'object' ||
      !('ownerId' in data) ||
      data.ownerId !== ownerId ||
      !('modelId' in data) ||
      data.modelId !== modelId ||
      !('intent' in data) ||
      !data.intent ||
      typeof data.intent !== 'object' ||
      !('runId' in data) ||
      (data.runId !== null && (typeof data.runId !== 'string' || !uuid.test(data.runId)))
    )
      return null
    const intent = data.intent
    if (
      !('client_request_id' in intent) ||
      typeof intent.client_request_id !== 'string' ||
      !uuid.test(intent.client_request_id) ||
      !('prompt' in intent) ||
      typeof intent.prompt !== 'string' ||
      !intent.prompt.trim() ||
      [...intent.prompt].length > 4000 ||
      intent.prompt.includes('\0') ||
      !('documents' in intent) ||
      !Array.isArray(intent.documents) ||
      intent.documents.length > 8 ||
      intent.documents.some(
        (row) =>
          typeof row !== 'string' || !row.trim() || [...row].length > 1000 || row.includes('\0')
      )
    )
      return null
    return {
      ownerId,
      modelId,
      runId: data.runId,
      intent: {
        client_request_id: intent.client_request_id,
        prompt: intent.prompt,
        documents: intent.documents as string[]
      }
    }
  } catch {
    return null
  }
}
