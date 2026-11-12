import { http } from '../../../shared/http.ts'
import { number, object, string } from '../../../shared/validation.ts'

export interface KnowledgeDefaults {
  default_embed_model_id: string | null
  default_rerank_model_id: string | null
  chunk_size: number
  chunk_overlap: number
  version: number
  updated_at: string
}
export type KnowledgeDefaultsInput = Omit<KnowledgeDefaults, 'version' | 'updated_at'> & {
  expected_version: number
}
function decode(value: unknown): KnowledgeDefaults {
  const data = object(value)
  return {
    default_embed_model_id:
      data.default_embed_model_id == null ? null : string(data.default_embed_model_id),
    default_rerank_model_id:
      data.default_rerank_model_id == null ? null : string(data.default_rerank_model_id),
    chunk_size: number(data.chunk_size),
    chunk_overlap: number(data.chunk_overlap),
    version: number(data.version),
    updated_at: string(data.updated_at)
  }
}
export const getKnowledgeDefaults = (signal: AbortSignal) =>
  http.request('/knowledge/settings', { signal, decode })
export const updateKnowledgeDefaults = (body: KnowledgeDefaultsInput, signal: AbortSignal) =>
  http.request('/knowledge/settings', { method: 'PATCH', body, signal, decode })
