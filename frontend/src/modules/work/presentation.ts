export interface WorkDraft {
  goal: string
  agentIds: string[]
  activeAgentId: string | null
}
export interface CreateIntent extends WorkDraft {
  clientRequestId: string
}
export interface WorkIntent {
  taskId: string
  agentId: string
  message: string
  expectedVersion: number
  clientRequestId: string
}
export function nextWorkIntent(
  previous: WorkIntent | null,
  input: Omit<WorkIntent, 'clientRequestId'>,
  createId: () => string
): WorkIntent {
  const message = input.message.trim()
  if (
    previous &&
    previous.taskId === input.taskId &&
    previous.agentId === input.agentId &&
    previous.message === message
  )
    return previous
  return { ...input, message, clientRequestId: createId() }
}
export function nextCreateIntent(
  previous: CreateIntent | null,
  draft: WorkDraft,
  createId: () => string
): CreateIntent {
  const goal = draft.goal.trim()
  if (
    previous &&
    previous.goal === goal &&
    previous.activeAgentId === draft.activeAgentId &&
    JSON.stringify([...previous.agentIds].sort()) === JSON.stringify([...draft.agentIds].sort())
  )
    return previous
  return {
    goal,
    agentIds: [...draft.agentIds],
    activeAgentId: draft.activeAgentId,
    clientRequestId: createId()
  }
}
function validText(value: string, maxLength: number, maxBytes = 65536): boolean {
  const trimmed = value.trim()
  return (
    trimmed.length > 0 &&
    [...trimmed].length <= maxLength &&
    !trimmed.includes('\u0000') &&
    !/[\uD800-\uDFFF]/u.test(trimmed) &&
    new TextEncoder().encode(trimmed).length <= maxBytes
  )
}
export const validateWorkGoal = (value: string) => ({ valid: validText(value, 32000) })
export const validateWorkMessage = validateWorkGoal
export const validateWorkTitle = (value: string) => ({ valid: validText(value, 100) })
export const validateWorkMembers = (ids: string[]) => ({
  valid: ids.length > 0 && ids.length <= 10 && new Set(ids).size === ids.length
})
