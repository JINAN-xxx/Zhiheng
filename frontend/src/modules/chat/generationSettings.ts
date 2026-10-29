import { number, object, string } from '../../shared/validation.ts'

export interface ChatGenerationSettings {
  system_prompt: string
  temperature: number | null
  max_output_tokens: number | null
}
export interface ChatGenerationConfig extends ChatGenerationSettings {
  model_id: string
}
export interface ChatGenerationRequest {
  readonly system_prompt?: string
  readonly temperature?: number | null
  readonly max_output_tokens?: number | null
}
export function defaultGenerationSettings(): ChatGenerationSettings {
  return { system_prompt: '', temperature: null, max_output_tokens: null }
}
export function generationSettingsError(
  settings: ChatGenerationSettings,
  maxOutputTokens = 131072
): string {
  if (
    typeof settings.system_prompt !== 'string' ||
    settings.system_prompt.includes('\0') ||
    [...settings.system_prompt].some((char) => {
      const point = char.codePointAt(0)!
      return point >= 0xd800 && point <= 0xdfff
    })
  )
    return '系统提示词包含无效字符'
  if ([...settings.system_prompt].length > 32000) return '系统提示词不能超过 32000 个字符'
  if (
    settings.temperature !== null &&
    (typeof settings.temperature !== 'number' ||
      !Number.isFinite(settings.temperature) ||
      settings.temperature < 0 ||
      settings.temperature > 2)
  )
    return '温度须为 0–2 的有限数值，留空使用模型默认值'
  const limit = Math.min(131072, maxOutputTokens)
  if (
    settings.max_output_tokens !== null &&
    (!Number.isInteger(settings.max_output_tokens) ||
      settings.max_output_tokens < 1 ||
      settings.max_output_tokens > limit)
  )
    return `输出长度须为 1–${limit} 的整数，留空使用模型默认值`
  return ''
}
export function generationRequest(settings: ChatGenerationRequest): ChatGenerationRequest {
  return {
    ...(settings.system_prompt ? { system_prompt: settings.system_prompt } : {}),
    ...(settings.temperature != null ? { temperature: settings.temperature } : {}),
    ...(settings.max_output_tokens != null ? { max_output_tokens: settings.max_output_tokens } : {})
  }
}
export function parseGenerationConfig(value: unknown): ChatGenerationConfig {
  const data = object(value)
  const settings: ChatGenerationSettings = {
    system_prompt: string(data.system_prompt),
    temperature: data.temperature == null ? null : number(data.temperature),
    max_output_tokens: data.max_output_tokens == null ? null : number(data.max_output_tokens)
  }
  const error = generationSettingsError(settings)
  if (error) throw new Error(error)
  return { model_id: string(data.model_id), ...settings }
}
export function generationSettingsFromDraft(draft: {
  system_prompt: string
  temperature: string
  max_output_tokens: string
}): ChatGenerationSettings {
  return {
    system_prompt: draft.system_prompt,
    temperature: draft.temperature.trim() ? Number(draft.temperature) : null,
    max_output_tokens: draft.max_output_tokens.trim() ? Number(draft.max_output_tokens) : null
  }
}
