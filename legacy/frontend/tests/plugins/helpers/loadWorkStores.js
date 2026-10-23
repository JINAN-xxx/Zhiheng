import { readFileSync } from 'node:fs'

const root = new URL('../../../', import.meta.url)
const sourceUrl = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
let sequence = 0

function moduleUrl(path, replacements) {
  const source = readFileSync(path, 'utf8').replace(/\bfrom\s+(['"])([^'"]+)\1/g, (_, quote, specifier) => {
    if (!specifier.startsWith('.') && !specifier.startsWith('@/')) {
      return `from ${JSON.stringify(import.meta.resolve(specifier))}`
    }
    const target = specifier.startsWith('@/')
      ? new URL(`src/${specifier.slice(2)}`, root)
      : new URL(specifier, path)
    if (!/\.[a-z]+$/i.test(target.pathname)) target.pathname += '.js'
    return `from ${JSON.stringify(replacements.get(target.href) || target.href)}`
  })
  return sourceUrl(source)
}

// Real ESM store definitions, re-exports, Pinia and lifecycle plugin run unchanged.
// Only network, browser storage and UI dependencies of the user store are replaced.
export async function loadWorkStores({ api = {}, auth = {} } = {}) {
  const key = `__workStoreTestHooks${++sequence}`
  let token = 'old-token'
  globalThis[key] = {
    api: {
      listWorkTasks: async () => ({ rows: [], total: 0, pageNum: 1, pageSize: 20 }),
      ...api,
    },
    auth: {
      getToken: () => token,
      setToken: value => { token = value },
      removeToken: () => { token = '' },
      login: async () => ({ token: 'new-token' }),
      logout: async () => {},
      getInfo: async () => ({}),
      ...auth,
    },
  }
  const replacements = new Map()
  const mock = (path, source) => replacements.set(new URL(path, root).href, sourceUrl(source))
  const hookSource = `const hooks = globalThis[${JSON.stringify(key)}];\n`
  const functions = (group, names) => hookSource + names.map(name => (
    `export const ${name} = (...args) => hooks.${group}.${name}(...args);`
  )).join('\n')
  mock('plugins/agent_extension/api/workTask.js', functions('api', [
    'addWorkTaskMember', 'cancelWorkTaskRun', 'createWorkTask', 'createWorkTaskRunStream', 'deleteWorkTask',
    'getWorkTask', 'listAvailableWorkAgents', 'listWorkTasks', 'removeWorkTaskMember', 'updateWorkTask',
    'updateWorkTaskMemberVersion',
  ]))
  mock('src/api/login.js', functions('auth', ['login', 'logout', 'getInfo']))
  mock('src/utils/auth.js', functions('auth', ['getToken', 'setToken', 'removeToken']))
  mock('src/router.js', 'export default { push() {} }')
  mock('src/plugins/cache.js', 'export default { session: { set() {} } }')
  mock('src/plugins/antdFeedback.js', 'export default { confirm: async () => {} }')
  mock('src/store/modules/lock.js', 'export default () => ({ unlockScreen() {} })')
  mock('src/utils/validate.js', 'export const isHttp = () => false; export const isEmpty = value => !value;')
  mock('src/assets/images/profile.jpg', 'export default "avatar"')
  try {
    const storePath = new URL('plugins/agent_extension/stores/workTask.js', root)
    replacements.set(storePath.href, moduleUrl(storePath, replacements))
    const publicPath = new URL('plugins/agent_extension/public/workTasks.js', root)
    const publicUrl = moduleUrl(publicPath, replacements)
    replacements.set(publicPath.href, publicUrl)
    return {
      work: await import(publicUrl),
      legacy: await import(moduleUrl(new URL('src/store/modules/workTask.js', root), replacements)),
      user: await import(moduleUrl(new URL('src/store/modules/user.js', root), replacements)),
    }
  } finally {
    delete globalThis[key]
  }
}
