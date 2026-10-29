export interface CreditState {
  enabled: boolean
  version: number
}
export interface CreditSummary extends CreditState {
  user_id: string
  allocated_points: string
  reclaimed_points: string
  used_points: string
  reserved_points: string
  available_points: string
}
export interface CreditAccount extends CreditSummary {
  username: string
  display_name: string
  is_active: boolean
}
export interface CreditLedgerEntry {
  id: string
  user_id: string
  operator_id: string | null
  kind: string
  amount: string
  available_delta: string
  reserved_delta: string
  available_after: string
  reserved_after: string
  run_id: string | null
  reason: string
  created_at: string
}
export interface CreditUsage {
  run_id: string
  user_id: string
  model_id: string
  source: string
  billing_mode: 'token' | 'search_unit'
  input_tokens: number | null
  output_tokens: number | null
  cache_tokens: number | null
  search_units: number | null
  reasoning_tokens: number | null
  status: 'reserved' | 'unknown' | 'settled'
  version: number
  reserved_points: string
  charged_points: string | null
  rate_version: number
  created_at: string
}
export interface CreditPage<T> {
  items: T[]
  total: number
  page: number
  page_size: number
}
export interface CreditRateInput {
  billing_mode: 'token' | 'search_unit'
  is_free: boolean
  input_points: string
  output_points: string
  cache_points: string
  search_points: string
}
export interface CreditRate extends CreditRateInput {
  id: string
  model_id: string
  version: number
  created_at: string
}
export interface NoteMutation {
  reason?: string
  client_request_id: string
}
export interface AuditedMutation extends NoteMutation {
  reason: string
}
export interface CreditRateCreate extends CreditRateInput, NoteMutation {}
export interface CreditStateMutation extends NoteMutation {
  enabled: boolean
  expected_version: number
}
export interface StateMutation extends AuditedMutation {
  enabled: boolean
  expected_version: number
}
export interface CreditAdjustment extends AuditedMutation {
  kind: 'grant' | 'reclaim'
  points: string
}
export interface CreditReconciliation extends AuditedMutation {
  expected_version: number
  input_tokens: number | null
  output_tokens: number | null
  cache_tokens: number | null
  search_units: number | null
  rejected_before_execution: boolean
}
