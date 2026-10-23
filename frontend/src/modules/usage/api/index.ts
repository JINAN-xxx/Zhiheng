import { http } from '../../../shared/http.ts'
import { list, nullableNumber, number, object, string } from '../../../shared/validation.ts'
export interface UsageRecord {
  id: string
  run_id: string
  model_id: string
  source: string
  status: string
  input_tokens: number | null
  output_tokens: number | null
  charged_tokens: number | null
  created_at: string
}
export interface UsageSummary {
  limit_tokens: number
  used_tokens: number
  reserved_tokens: number
  records: UsageRecord[]
}
function parseRecord(value: unknown): UsageRecord {
  const data = object(value)
  return {
    id: string(data.id),
    run_id: string(data.run_id),
    model_id: string(data.model_id),
    source: string(data.source),
    status: string(data.status),
    input_tokens: nullableNumber(data.input_tokens),
    output_tokens: nullableNumber(data.output_tokens),
    charged_tokens: nullableNumber(data.charged_tokens),
    created_at: string(data.created_at)
  }
}
export const getUsage = (signal: AbortSignal) =>
  http.request('/usage', {
    signal,
    decode: (value): UsageSummary => {
      const data = object(value)
      return {
        limit_tokens: number(data.limit_tokens),
        used_tokens: number(data.used_tokens),
        reserved_tokens: number(data.reserved_tokens),
        records: list(parseRecord)(data.records)
      }
    }
  })
