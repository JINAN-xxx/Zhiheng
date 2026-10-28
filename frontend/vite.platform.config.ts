import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import type { Connect, Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'
const platformFallback: Connect.NextHandleFunction = (request, _response, next) => {
  const pathname = request.url?.split('?')[0]
  if (
    request.headers.accept?.includes('text/html') &&
    pathname &&
    !pathname.startsWith('/api') &&
    !/^\/(v1|docs|redoc)(\/|$)/.test(pathname) &&
    !pathname.split('/').at(-1)?.includes('.')
  )
    request.url = '/platform.html'
  next()
}
const apiProxy = Object.fromEntries(
  ['/api', '/v1', '/docs', '/redoc', '/openapi.json'].map((path) => [
    path,
    { target: process.env.PLATFORM_API_TARGET ?? 'http://127.0.0.1:19090', changeOrigin: true }
  ])
)
const entryPlugin: Plugin = {
  name: 'platform-entry',
  configureServer(server) {
    server.middlewares.use(platformFallback)
  },
  configurePreviewServer(server) {
    server.middlewares.use(platformFallback)
  }
}
export default defineConfig({
  plugins: [entryPlugin, vue()],
  appType: 'spa',
  ...(process.env.PLATFORM_VITE_CACHE_DIR ? { cacheDir: process.env.PLATFORM_VITE_CACHE_DIR } : {}),
  server: {
    ...(process.env.PLATFORM_HMR_PORT
      ? { hmr: { clientPort: Number(process.env.PLATFORM_HMR_PORT) } }
      : {}),
    proxy: apiProxy
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: { input: fileURLToPath(new URL('./platform.html', import.meta.url)) }
  },
  preview: {
    proxy: apiProxy
  }
})
