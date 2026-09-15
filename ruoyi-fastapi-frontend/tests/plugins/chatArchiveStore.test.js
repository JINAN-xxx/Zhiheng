import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import { createPinia, defineStore, setActivePinia } from 'pinia'
import { normalizeWorkspaceSessions, shouldApplyGeneration } from '../../src/components/Workspace/workspaceTransforms.js'

const source = readFileSync(new URL('../../src/store/modules/workspace.js', import.meta.url), 'utf8')
  .replace(/^import[\s\S]*?from ['"][^'"]+['"];?\s*$/gm, '')
  .replace('export default useWorkspaceStore', 'globalThis.useStore = useWorkspaceStore')

const row = (sessionId = 'session-1') => ({ sessionId, title: '保留的对话', updatedAt: '2026-09-15T10:00:00' })
const page = (rows, total = rows.length) => ({ code: 200, rows, total })
const deferred = () => {
  let resolve
  const promise = new Promise(done => { resolve = done })
  return { resolve, promise }
}
function actualStore(api = {}) {
  setActivePinia(createPinia())
  const scope = {
    defineStore, normalizeWorkspaceSessions, shouldApplyGeneration,
    listRecentSessions: async () => page([]),
    setRecentSessionStatus: async (sessionId, status) => ({ data: { ...row(sessionId), status } }),
    deleteRecentSession: async () => ({ code: 200 }),
    ...api,
  }
  vm.runInNewContext(source, scope, { filename: 'workspace.production-store.js' })
  return scope.useStore()
}

// Query the archive through the same production store, including server pagination.
{
  let query
  const store = actualStore({ listRecentSessions: async params => { query = params; return page([row()], 43) } })
  await store.loadSessions({ status: 'archived', pageNum: 2 })
  assert.equal(query?.status, 'archived')
  assert.equal(query.pageNum, 2)
  assert.equal(query.pageSize, 20)
  assert.equal(store.sessionStatus, 'archived')
  assert.equal(store.sessionsPageNum, 2)
  assert.equal(store.sessionsTotal, 43)
}

// Switching lists must not display rows or accept an older response from the other status.
{
  const oldRequest = deferred()
  const archiveRequest = deferred()
  let calls = 0
  const store = actualStore({ listRecentSessions: () => (++calls === 1 ? oldRequest.promise : archiveRequest.promise) })
  store.sessions = [row('old')]
  const active = store.loadSessions()
  const archived = store.loadSessions({ status: 'archived' })
  assert.equal(store.sessions.length, 0)
  archiveRequest.resolve(page([row('archived')]))
  await archived
  oldRequest.resolve(page([row('active')]))
  await active
  assert.equal(store.sessions[0].sessionId, 'archived')
  assert.equal(store.sessionStatus, 'archived')
}

// A query issued before archiving cannot resurrect the archived item after the mutation.
{
  const stale = deferred()
  let calls = 0
  const mutations = []
  const store = actualStore({
    listRecentSessions: () => (++calls === 1 ? stale.promise : Promise.resolve(page([]))),
    setRecentSessionStatus: async (id, status) => { mutations.push([id, status]); return { data: { ...row(id), status } } },
  })
  store.sessions = [row()]
  const pending = store.loadSessions()
  await store.setSessionStatus('session-1', 'archived')
  stale.resolve(page([row()]))
  await pending
  assert.deepEqual(mutations, [['session-1', 'archived']])
  assert.equal(store.sessions.length, 0)
  assert.equal(store.sessionChange.sessionId, 'session-1')
  assert.equal(store.sessionChange.status, 'archived')
  assert.equal(store.sessionStatusChanges['session-1'].status, 'archived')
}

// Restoring the last row on page two returns to the preceding archive page.
{
  const queries = []
  const store = actualStore({ listRecentSessions: async query => {
    queries.push({ ...query })
    return query.pageNum === 2 ? page([], 20) : page([row('remaining')], 20)
  } })
  store.sessionStatus = 'archived'
  store.sessionsPageNum = 2
  store.sessions = [row()]
  const restored = await store.setSessionStatus('session-1', 'active')
  assert.equal(restored.status, 'active')
  assert.deepEqual(queries.map(query => query.pageNum), [2, 1])
  assert.ok(queries.every(query => query.status === 'archived'))
  assert.equal(store.sessionsPageNum, 1)
  assert.equal(store.sessions[0].sessionId, 'remaining')
}

// Rejected mutations keep the visible row and do not broadcast a status change.
{
  const store = actualStore({ setRecentSessionStatus: async () => { throw new Error('会话正在生成') } })
  store.sessions = [row()]
  await assert.rejects(store.setSessionStatus('session-1', 'archived'), /正在生成/)
  assert.equal(store.sessions.length, 1)
  assert.equal(store.sessionChange, null)
  assert.equal(store.sessionMutatingIds.length, 0)
}

// Repeated clicks issue one request, while a failed refresh still leaves a successful archive hidden.
{
  const response = deferred()
  let calls = 0
  const store = actualStore({
    setRecentSessionStatus: () => { calls += 1; return response.promise },
    listRecentSessions: async () => { throw new Error('offline') },
  })
  store.sessions = [row()]
  const first = store.setSessionStatus('session-1', 'archived')
  assert.equal(await store.setSessionStatus('session-1', 'archived'), null)
  response.resolve({ data: { ...row(), status: 'archived' } })
  await first
  assert.equal(calls, 1)
  assert.equal(store.sessions.length, 0)
  assert.equal(store.sessionChange.status, 'archived')
  assert.ok(store.sessionsError)
}

console.log('ok chat archive store: filtering, paging, restore, ownership-safe UI updates and request races')
