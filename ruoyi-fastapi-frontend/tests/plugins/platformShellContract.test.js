import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const platformSidebar = readFileSync('src/layout/components/PlatformSidebar.vue', 'utf8')
const recentSessions = readFileSync('src/layout/components/RecentSessions.vue', 'utf8')
const adminSidebar = readFileSync('src/layout/components/AdminSidebar.vue', 'utf8')
const componentExports = readFileSync('src/layout/components/index.js', 'utf8')

assert.match(platformSidebar, /新建对话/)
assert.match(platformSidebar, /AI 对话/)
assert.match(platformSidebar, /AI 资源/)
assert.match(platformSidebar, /管理控制台/)
assert.match(platformSidebar, /resolveAiResourceTabs/)
assert.match(platformSidebar, /useWorkspaceStore/)
assert.doesNotMatch(platformSidebar, /Developer API/)

assert.match(recentSessions, /可能已过期/)
assert.match(recentSessions, /aria-label="搜索最近会话"/)
assert.match(recentSessions, /open-session/)
assert.match(recentSessions, /archive-session/)
assert.doesNotMatch(recentSessions, /listRecentSessions/)

assert.match(adminSidebar, /返回 AI 工作台/)
assert.match(adminSidebar, /filterAdminWorkspaceRoutes/)
assert.match(adminSidebar, /SidebarItem/)

assert.match(componentExports, /PlatformSidebar/)
assert.match(componentExports, /AdminSidebar/)
