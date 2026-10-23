import assert from 'node:assert/strict'
import { loadChatApis } from './helpers/loadChatApis.js'

const requests = []
let pending
const { sessions, chat, workspace } = await loadChatApis(config => {
  requests.push(config)
  return pending
})

// The two existing entry points must expose the same functions, so a caller's
// old import cannot silently retain a second endpoint or response contract.
assert.equal(chat.listChatSession, sessions.listChatSessions)
assert.equal(chat.getChatSession, sessions.getChatSession)
assert.equal(chat.delChatSession, sessions.deleteChatSession)
assert.equal(workspace.listRecentSessions, sessions.listChatSessions)
assert.equal(workspace.renameRecentSession, sessions.renameChatSession)
assert.equal(workspace.setRecentSessionStatus, sessions.setChatSessionStatus)
assert.equal(workspace.deleteRecentSession, sessions.deleteChatSession)

const id = '会话/one ?#%'
const path = '/ai/chat/session/%E4%BC%9A%E8%AF%9D%2Fone%20%3F%23%25'
const query = Object.freeze({ status: 'archived', pageNum: 3, pageSize: 17 })
const row = { sessionId: id, title: '历史会话', status: 'archived' }
const cases = [
  {
    name: 'archived list pagination',
    call: () => sessions.listChatSessions(query),
    config: { url: '/ai/chat/session/list', method: 'get', params: query },
    response: { code: 200, rows: [row], total: 58 },
  },
  {
    name: 'active list pagination through the workspace entry',
    call: () => workspace.listRecentSessions({ status: 'active', pageNum: 1, pageSize: 20 }),
    config: { url: '/ai/chat/session/list', method: 'get', params: { status: 'active', pageNum: 1, pageSize: 20 } },
    response: { code: 200, rows: [], total: 0 },
  },
  {
    name: 'list without options through the chat entry',
    call: () => chat.listChatSession(),
    config: { url: '/ai/chat/session/list', method: 'get', params: {} },
    response: { code: 200, rows: [row], total: 1 },
  },
  {
    name: 'detail with an encoded session ID',
    call: () => chat.getChatSession(id),
    config: { url: path, method: 'get' },
    response: { code: 200, data: { session: row, messages: [{ role: 'user', content: '原始记录' }] } },
  },
  {
    name: 'rename preserves title text and encodes the ID',
    call: () => workspace.renameRecentSession(id, ' 新标题 / 中文 '),
    config: { url: path, method: 'patch', data: { title: ' 新标题 / 中文 ' } },
    response: { code: 200, data: { ...row, title: ' 新标题 / 中文 ' } },
  },
  ...['archived', 'active'].map(status => ({
    name: `set status to ${status} with an encoded ID`,
    call: () => workspace.setRecentSessionStatus(id, status),
    config: { url: `${path}/status`, method: 'patch', data: { status } },
    response: { code: 200, data: { ...row, status } },
  })),
  {
    name: 'delete with an encoded session ID',
    call: () => chat.delChatSession(id),
    config: { url: path, method: 'delete' },
    response: { code: 200, msg: '删除成功' },
  },
]

for (const { name, call, config, response } of cases) {
  const before = requests.length
  pending = Promise.resolve(response)
  const result = call()
  assert.equal(result, pending, `${name} must return the shared request promise`)
  assert.equal(requests.length, before + 1, `${name} must send one request`)
  assert.deepEqual(requests.at(-1), config, name)
  if (config.params === query) assert.equal(requests.at(-1).params, query, 'Forward pagination options unchanged')
  assert.equal(await result, response, `${name} must preserve the complete server response`)

  for (const failure of [new Error('会话正在生成'), { code: 'FORBIDDEN', detail: '无权访问会话' }, 'error']) {
    pending = Promise.reject(failure)
    await assert.rejects(call(), error => error === failure, `${name} must preserve the shared request rejection`)
  }
}

// String conversion must also retain a valid zero ID for every path operation.
for (const [call, url] of [
  [() => sessions.getChatSession(0), '/ai/chat/session/0'],
  [() => sessions.renameChatSession(0, 'zero'), '/ai/chat/session/0'],
  [() => sessions.setChatSessionStatus(0, 'active'), '/ai/chat/session/0/status'],
  [() => sessions.deleteChatSession(0), '/ai/chat/session/0'],
]) {
  pending = Promise.resolve({ code: 200 })
  await call()
  assert.equal(requests.at(-1).url, url)
}

console.log('ok chat session API: shared exports, pagination, detail, rename, archive/restore, delete, ID encoding and response/error passthrough')
