import assert from 'node:assert/strict'

import {
  buildCreateWorkTaskPayload,
  buildRunWorkTaskPayload,
  createWorkTaskState,
  resolveWorkTaskCapabilities,
  validateWorkGoal,
  validateWorkMembers,
  validateWorkTitle,
} from '../../plugins/agent_extension/utils/workTask.js'

const ids = ['key-1', 'key-2', 'key-3', 'key-4']
const state = createWorkTaskState({ createId: () => ids.shift() })
const draft = {
  goal: '整理发布资料',
  agentIds: ['agent-1', 'agent-2'],
  activeAgentId: 'agent-1',
}

assert.deepEqual(buildCreateWorkTaskPayload({ ...draft, clientMessageId: 'create-1', ignored: true }), {
  goal: '整理发布资料',
  agentIds: ['agent-1', 'agent-2'],
  activeAgentId: 'agent-1',
  clientMessageId: 'create-1',
})
assert.deepEqual(buildRunWorkTaskPayload({
  agentId: 'agent-2', clientMessageId: 'run-1', message: '补充关键指标', modelId: 'forbidden',
}), {
  agentId: 'agent-2', clientMessageId: 'run-1', message: '补充关键指标',
})
assert.equal(validateWorkTitle('a'.repeat(100)).valid, true)
assert.equal(validateWorkTitle('a'.repeat(101)).valid, false)
assert.equal(validateWorkGoal('a'.repeat(32000)).valid, true)
assert.equal(validateWorkGoal(' ').valid, false)
assert.equal(validateWorkMembers(['agent-1']).valid, true)
assert.equal(validateWorkMembers(Array.from({ length: 11 }, (_, index) => `agent-${index}`)).valid, false)

assert.deepEqual(resolveWorkTaskCapabilities(new Set(['agent_extension:work:query'])), {
  visible: true, list: false, query: true, add: false, edit: false, remove: false,
  useAgent: false, canCreate: false, canRun: false, canManageMembers: false,
})
assert.equal(resolveWorkTaskCapabilities(new Set(['*:*:*'])).canManageMembers, true)
assert.equal(resolveWorkTaskCapabilities(new Set(['agent_extension:work:add', 'agent_extension:agent:use'])).canCreate, true)
assert.equal(resolveWorkTaskCapabilities(new Set(['agent_extension:work:add'])).canCreate, false)

const firstAttempt = state.prepareCreate(draft)
assert.equal(firstAttempt.clientMessageId, 'key-1')
assert.equal(state.prepareCreate({ ...draft }).clientMessageId, 'key-1', 'same create content reuses its key')
assert.equal(state.prepareCreate({ ...draft, goal: 'changed' }).clientMessageId, 'key-2', 'changed content rotates key')
state.recordCreateFailure(new Error('network'))
assert.equal(state.draft.goal, 'changed', 'create failures retain the draft')

const task = { taskId: 'task-1', currentRunId: null, status: 'active', version: 1, messages: [] }
state.recordCreated(task, { ...draft, clientMessageId: 'create-existing' })
assert.equal(state.currentTask.taskId, 'task-1')
assert.equal(state.shouldCreateTask(), false, 'run failure after creation must not recreate the task')
state.recordRunFailure(new Error('upstream'), { message: draft.goal, clientMessageId: 'create-existing' })
assert.equal(state.draft.goal, draft.goal)
assert.equal(state.prepareRunRetry({ kind: 'uncertain' }).clientMessageId, 'create-existing')
assert.equal(state.prepareRunRetry({ kind: 'failed' }).clientMessageId, 'key-3')

const oldDetail = state.beginDetailRequest('task-old')
const currentDetail = state.beginDetailRequest('task-new')
assert.equal(state.applyDetail(oldDetail, { taskId: 'task-old' }), false)
assert.equal(state.applyDetail(currentDetail, { taskId: 'task-new', currentRunId: null, status: 'active' }), true)
assert.equal(state.currentTask.taskId, 'task-new')

state.beginRun('run-local')
state.applyRunEvent({ event: 'meta', data: { taskId: 'task-new', runId: 'run-server', messageId: 'message-1' } })
const replay = state.applyRunEvent({ event: 'done', data: { taskId: 'task-new', runId: 'run-server', status: 'pending', replay: true } })
assert.equal(replay.observe, true)
assert.equal(state.isRunLocked(), true)
state.requestCancel()
assert.equal(state.runState, 'cancelling')
state.applyObservedDetail({ taskId: 'task-new', currentRunId: 'run-server', status: 'active' })
assert.equal(state.isRunLocked(), true, 'cancel response does not release the server claim')
state.applyObservedDetail({ taskId: 'task-new', currentRunId: null, status: 'active', version: 2 })
assert.equal(state.runState, 'cancelled')
assert.equal(state.isRunLocked(), false, 'only detail with a released claim unlocks execution')

const runGeneration = state.beginRun('run-next')
state.invalidateRun()
assert.equal(state.applyRunEvent({ event: 'content', data: { delta: 'late' } }, runGeneration), false)
