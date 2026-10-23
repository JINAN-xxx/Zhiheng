import { readFileSync } from 'node:fs'

const root = new URL('../../../', import.meta.url)
const sourceUrl = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
let sequence = 0

// Keep the production API modules and parser; substitute only browser/network
// inputs and the build-time base URL so the actual exports run under Node.
export async function loadPlatformStreamApis(fetch) {
  const key = `__platformStreamTest${++sequence}`
  globalThis[key] = { fetch }
  const capture = `const hooks = globalThis[${JSON.stringify(key)}];\n`
  const replacements = new Map([
    [new URL('src/utils/auth.js', root).href, sourceUrl('export const getToken = () => "test-token";')],
    [new URL('src/utils/request.js', root).href, sourceUrl('export default () => { throw new Error("Unexpected shared request"); };')],
  ])
  function moduleUrl(path) {
    const source = readFileSync(path, 'utf8')
      .replaceAll('import.meta.env.VITE_APP_BASE_API', JSON.stringify('/test-api'))
      .replace(/\bfrom\s+(['"])([^'"]+)\1/g, (_, quote, specifier) => {
        const target = specifier.startsWith('@/')
          ? new URL(`src/${specifier.slice(2)}`, root)
          : new URL(specifier, path)
        if (!target.pathname.endsWith('.js')) target.pathname += '.js'
        return `from ${JSON.stringify(replacements.get(target.href) || target.href)}`
      })
    return sourceUrl(capture + 'const fetch = (...args) => hooks.fetch(...args);\n' + source)
  }
  try {
    const sessions = new URL('plugins/ai/public/chatSessions.js', root)
    replacements.set(sessions.href, moduleUrl(sessions))
    return {
      chat: await import(moduleUrl(new URL('plugins/ai/api/chat.js', root))),
      work: await import(moduleUrl(new URL('plugins/agent_extension/api/workTask.js', root))),
    }
  } finally {
    delete globalThis[key]
  }
}
