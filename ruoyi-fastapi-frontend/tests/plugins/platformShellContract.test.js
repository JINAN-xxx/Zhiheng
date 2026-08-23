import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const platformSidebar = readFileSync('src/layout/components/PlatformSidebar.vue', 'utf8')
const recentSessions = readFileSync('src/layout/components/RecentSessions.vue', 'utf8')
const adminSidebar = readFileSync('src/layout/components/AdminSidebar.vue', 'utf8')
const noticeDetail = readFileSync('src/layout/components/HeaderNotice/DetailView.vue', 'utf8')
const packageJson = JSON.parse(readFileSync('package.json', 'utf8'))
const layout = readFileSync('src/layout/index.vue', 'utf8')
const appStore = readFileSync('src/store/modules/app.js', 'utf8')
const componentExports = readFileSync('src/layout/components/index.js', 'utf8')
const topbar = readFileSync('src/layout/components/PlatformTopbar.vue', 'utf8')
const chatView = readFileSync('plugins/ai/views/chat/index.vue', 'utf8')
const appMain = readFileSync('src/layout/components/AppMain.vue', 'utf8')
const resourceShell = readFileSync('src/layout/components/AiResourceShell.vue', 'utf8')
const usageView = readFileSync('plugins/ai/views/usage/index.vue', 'utf8')
const settings = readFileSync('src/layout/components/Settings/index.vue', 'utf8')
const settingsStore = readFileSync('src/store/modules/settings.js', 'utf8')
const defaultSettings = readFileSync('src/settings.js', 'utf8')
const styles = readFileSync('src/assets/styles/workspace.scss', 'utf8')
const workspaceTransforms = readFileSync('src/components/Workspace/workspaceTransforms.js', 'utf8')
const shellFiles = [
  'src/layout/index.vue',
  'src/layout/components/PlatformSidebar.vue',
  'src/layout/components/PlatformTopbar.vue',
  'src/layout/components/AdminSidebar.vue',
  'src/layout/components/RecentSessions.vue',
  'src/layout/components/AiResourceShell.vue',
  'src/layout/components/AppMain.vue',
  'src/layout/components/HeaderNotice/index.vue',
  'src/layout/components/HeaderNotice/DetailView.vue',
  'src/layout/components/TagsView/index.vue',
  'src/layout/components/TagsView/ScrollPane.vue',
  'src/layout/components/Sidebar/SidebarItem.vue',
]

for (const fileName of shellFiles) {
  const source = readFileSync(fileName, 'utf8')
  assert.doesNotMatch(source, /element-plus|@element-plus\/icons-vue|<el-|--el-/)
}

assert.match(platformSidebar, /新建对话/)
assert.match(platformSidebar, /智衡平台/)
assert.match(platformSidebar, /智能对话/)
assert.match(workspaceTransforms, /模型中心/)
assert.match(workspaceTransforms, /开发接入/)
assert.match(workspaceTransforms, /用量记录/)
assert.match(platformSidebar, /管理后台/)
assert.match(platformSidebar, /resolveAiResourceTabs/)
assert.match(platformSidebar, /useWorkspaceStore/)
assert.doesNotMatch(platformSidebar, />AI 对话<|>AI 资源<|智衡 AI|Developer API/)

assert.match(recentSessions, /可能已过期/)
assert.match(recentSessions, /aria-label="搜索最近会话"/)
assert.match(recentSessions, /open-session/)
assert.match(recentSessions, /archive-session/)
assert.doesNotMatch(recentSessions, /listRecentSessions/)

assert.match(adminSidebar, /返回 AI 工作台/)
assert.match(adminSidebar, /filterAdminWorkspaceRoutes/)
assert.match(adminSidebar, /SidebarItem/)
assert.match(adminSidebar, /mode="inline"/)
assert.match(adminSidebar, /v-model:open-keys="openKeys"/)
assert.match(adminSidebar, /findMenuKeyChain/)
assert.match(adminSidebar, /@open-change="handleOpenChange"/)
assert.match(noticeDetail, /DOMPurify/)
assert.match(noticeDetail, /v-html="safeNoticeContent"/)
assert.ok(packageJson.dependencies.dompurify, '公告富文本清洗器必须声明为直接依赖')

assert.match(componentExports, /PlatformSidebar/)
assert.match(componentExports, /AdminSidebar/)

assert.match(layout, /PlatformSidebar/)
assert.match(layout, /AdminSidebar/)
assert.match(layout, /resolveWorkspaceMode/)
assert.match(layout, /mode === 'admin' && needTagsView/)
assert.doesNotMatch(layout, /<navbar/)

assert.match(topbar, /返回智能工作台/)
assert.match(topbar, /aria-label="打开主菜单"/)
assert.doesNotMatch(topbar, /源码地址|文档地址|布局大小/)
assert.doesNotMatch(topbar, /AI 工作台/)

assert.match(appStore, /mobileSidebarOpen/)
assert.match(appStore, /openMobileSidebar/)
assert.match(appStore, /closeMobileSidebar/)

assert.doesNotMatch(chatView, /class="chat-sessions"/)
assert.doesNotMatch(chatView, /mobile-session-drawer/)
assert.doesNotMatch(chatView, /ChatSessionPanel/)
assert.match(chatView, /route\.query\.sessionId/)
assert.match(chatView, /workspaceStore\.loadSessions/)
assert.match(chatView, /router\.replace/)

assert.match(appMain, /AiResourceShell/)
assert.doesNotMatch(resourceShell, /resolveAiResourceTabs/)
assert.doesNotMatch(resourceShell, /AI RESOURCE|AI 资源导航/)
assert.match(usageView, /listApplications/)
assert.match(usageView, /listApplicationUsage/)
assert.match(usageView, /数据可能已过期/)

assert.doesNotMatch(settings, /navType|顶部菜单|混合菜单|侧边栏主题|标签页样式/)
assert.match(settings, /深色模式/)
assert.match(settingsStore, /platformShell:\s*true/)
assert.doesNotMatch(settingsStore, /storageSetting\.navType/)
assert.match(defaultSettings, /tagsView:\s*true/)

assert.match(styles, /--platform-sidebar-width:\s*184px/)
assert.match(styles, /--platform-sidebar-collapsed-width:\s*56px/)
assert.match(styles, /--platform-topbar-height:\s*54px/)
assert.match(styles, /--platform-menu-item-height:\s*38px/)
assert.match(styles, /@media\s*\(max-width:\s*991px\)/)
assert.match(styles, /prefers-reduced-motion/)
assert.match(platformSidebar, /aria-label="新建对话"/)
assert.match(platformSidebar, /aria-current/)
assert.doesNotMatch(adminSidebar, /class="admin-sidebar sidebar-container"/)
assert.match(layout, /@after-open-change="handleMobileDrawerChange"/)
assert.match(layout, /focusMobileSidebar/)
assert.match(layout, /restoreMenuFocus/)
assert.match(layout, /a-drawer/)
assert.match(topbar, /defineExpose\(\{ focusMenu \}\)/)
assert.match(topbar, /a-dropdown/)
assert.match(readFileSync('src/layout/components/HeaderNotice/index.vue', 'utf8'), /a-popover/)
assert.match(readFileSync('src/layout/components/HeaderNotice/index.vue', 'utf8'), /a-list/)
assert.match(readFileSync('src/layout/components/TagsView/index.vue', 'utf8'), /a-dropdown/)
