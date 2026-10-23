import { readFileSync } from 'node:fs'

const root = new URL('../../../', import.meta.url)
const dataUrl = text => `data:text/javascript;base64,${Buffer.from(text).toString('base64')}`
let sequence = 0

// Load actual ESM exports/re-exports. Only the shared HTTP request boundary is mocked.
export async function loadPublicClients(request) {
  const key = `__publicClientRequest${++sequence}`
  globalThis[key] = request
  const replacements = new Map([
    [new URL('src/utils/request.js', root).href, dataUrl(`export default globalThis[${JSON.stringify(key)}]`)],
  ])
  function moduleUrl(path) {
    if (replacements.has(path.href)) return replacements.get(path.href)
    const source = readFileSync(path, 'utf8').replace(/\bfrom\s+(['"])([^'"]+)\1/g, (_, quote, specifier) => {
      const target = specifier.startsWith('@/') ? new URL(`src/${specifier.slice(2)}`, root) : new URL(specifier, path)
      if (!target.pathname.endsWith('.js')) target.pathname += '.js'
      const url = replacements.get(target.href)
        || (/\/plugins\/[^/]+\/(api|public)\//.test(target.pathname) ? moduleUrl(target) : target.href)
      return `from ${JSON.stringify(url)}`
    })
    const url = dataUrl(source)
    replacements.set(path.href, url)
    return url
  }
  const load = path => import(moduleUrl(new URL(path, root)))
  try {
    return {
      skills: await load('plugins/agent_extension/public/skills.js'),
      resources: await load('plugins/ai/public/resourceSelection.js'),
      applications: await load('plugins/ai/public/applications.js'),
      keys: await load('plugins/ai/public/developerKeys.js'),
      legacySkills: await load('plugins/agent_extension/api/skill.js'),
      legacyModels: await load('plugins/ai/api/modelCatalog.js'),
      legacyKnowledge: await load('plugins/ai/api/knowledge.js'),
      legacyApplications: await load('plugins/ai/api/application.js'),
    }
  } finally {
    delete globalThis[key]
  }
}
