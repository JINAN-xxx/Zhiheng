import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { defineConfig } from 'vite'
import type { Connect, Plugin } from 'vite'
import platform from '../../vite.platform.config.ts'

const schemaDirectory = process.env.PLATFORM_SCHEMA_FIXTURES
if (!schemaDirectory) throw new Error('Set PLATFORM_SCHEMA_FIXTURES to the exported public schemas')
const schemaRoot = resolve(schemaDirectory)
const schemaNames = new Set(['resources-v1.json', 'inference-v1.json'])
const fixtures: Connect.NextHandleFunction = (request, response, next) => {
  const path = request.url?.split('?')[0] ?? '/'
  if (/^\/(api|v1)(\/|$)/.test(path)) {
    response.writeHead(404)
    response.end()
    return
  }
  if (!path.startsWith('/openapi/')) return next()
  const name = path.slice('/openapi/'.length)
  if (schemaNames.has(name)) {
    try {
      const content = readFileSync(resolve(schemaRoot, name))
      response.writeHead(200, {
        'Content-Type': 'application/json; charset=utf-8',
        'Cache-Control': 'no-store'
      })
      response.end(request.method === 'HEAD' ? undefined : content)
      return
    } catch {
      // A missing fixture must remain a visible HTTP failure.
    }
  }
  response.writeHead(404)
  response.end()
}
const fixturePlugin: Plugin = {
  name: 'browser-fixture-boundaries',
  configureServer(server) {
    server.middlewares.use(fixtures)
  },
  configurePreviewServer(server) {
    server.middlewares.use(fixtures)
  }
}
export default defineConfig({
  ...platform,
  plugins: [...(platform.plugins ?? []), fixturePlugin],
  server: { ...platform.server, proxy: {} },
  preview: { ...platform.preview, proxy: {} }
})
