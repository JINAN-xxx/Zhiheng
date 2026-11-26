import type { RouteRecordRaw } from 'vue-router'
import { MCP_ADMIN_PERMISSIONS } from './permissions.ts'

export const mcpRoutes: RouteRecordRaw[] = [
  {
    path: '/mcp',
    name: 'mcp-catalog',
    component: () => import('./views/McpCatalog.vue'),
    meta: { title: 'MCP' }
  },
  {
    path: '/mcp/servers',
    name: 'mcp-servers',
    component: () => import('./views/McpServers.vue'),
    meta: { title: 'MCP 服务管理', anyPermissions: MCP_ADMIN_PERMISSIONS }
  }
]
