import type { WorkExecutionState, WorkInput, WorkTask } from './api/index.ts'

export function pendingWorkInputs(task: Pick<WorkTask, 'inputs' | 'messages'>): WorkInput[] {
  const sent = new Set(task.messages.flatMap((message) => message.inputFileIds ?? []))
  return task.inputs.filter((file) => !sent.has(file.fileId))
}

export interface WorkDraft {
  goal: string
  modelId: string | null
  skillSlugs: string[]
}
export interface CreateIntent extends WorkDraft {
  clientRequestId: string
}
export interface WorkIntent {
  taskId: string
  message: string
  expectedVersion: number
  clientRequestId: string
  fileIds: string[]
  resume: boolean
}
const sameSet = (left: string[], right: string[]) =>
  JSON.stringify([...left].sort()) === JSON.stringify([...right].sort())
export function nextWorkIntent(
  previous: WorkIntent | null,
  input: Omit<WorkIntent, 'clientRequestId'>,
  createId: () => string
): WorkIntent {
  const message = input.message.trim()
  if (
    previous &&
    previous.taskId === input.taskId &&
    previous.message === message &&
    previous.resume === input.resume &&
    sameSet(previous.fileIds, input.fileIds)
  )
    return previous
  return { ...input, fileIds: [...input.fileIds], message, clientRequestId: createId() }
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
    previous.modelId === draft.modelId &&
    sameSet(previous.skillSlugs, draft.skillSlugs)
  )
    return previous
  return {
    goal,
    modelId: draft.modelId,
    skillSlugs: [...draft.skillSlugs],
    clientRequestId: createId()
  }
}
export function emptyWorkDraft(): WorkDraft {
  return { goal: '', modelId: null, skillSlugs: [] }
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
export const MAX_WORK_FILE_BYTES = 20 * 1024 * 1024
export const workExecutionLabel: Record<WorkExecutionState, string> = {
  idle: '等待指令',
  queued: '排队中',
  running: '正在执行',
  waiting_input: '待补充资料',
  paused: '已暂停',
  completed: '本轮已完成',
  failed: '执行未完成',
  cancelled: '已停止'
}
