import { readFileSync } from 'node:fs'

const frontendRoot = new URL('../../../', import.meta.url)
const publicPath = new URL('plugins/ai/public/chatSessions.js', frontendRoot)
let loadSequence = 0

const sourceUrl = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`

// Load real ESM exports, including compatibility re-exports, with only the shared
// HTTP boundary mocked. Other helpers still execute their production modules.
function moduleUrl(path, replacements) {
  const source = readFileSync(path, 'utf8').replace(/\bfrom\s+(['"])([^'"]+)\1/g, (_, quote, specifier) => {
    const target = specifier.startsWith('@/')
      ? new URL(`src/${specifier.slice(2)}`, frontendRoot)
      : new URL(specifier, path)
    if (!target.pathname.endsWith('.js')) target.pathname += '.js'
    return `from ${JSON.stringify(replacements.get(target.href) || target.href)}`
  })
  return sourceUrl(source)
}

export async function loadChatApis(request) {
  const key = `__chatApiTestRequest${++loadSequence}`
  globalThis[key] = request
  try {
    const replacements = new Map([
      [new URL('src/utils/request.js', frontendRoot).href, sourceUrl(`export default globalThis[${JSON.stringify(key)}]`)],
    ])
    const publicUrl = moduleUrl(publicPath, replacements)
    replacements.set(publicPath.href, publicUrl)
    return {
      sessions: await import(publicUrl),
      chat: await import(moduleUrl(new URL('plugins/ai/api/chat.js', frontendRoot), replacements)),
      workspace: await import(moduleUrl(new URL('src/api/platformWorkspace.js', frontendRoot), replacements)),
    }
  } finally {
    delete globalThis[key]
  }
}
