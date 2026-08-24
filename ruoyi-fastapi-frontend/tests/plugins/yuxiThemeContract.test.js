import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const light = readFileSync('src/assets/styles/yuxi-tokens.css', 'utf8')
const dark = readFileSync('src/assets/styles/yuxi-tokens.dark.css', 'utf8')
const workspace = readFileSync('src/assets/styles/workspace.scss', 'utf8')
const variables = readFileSync('src/assets/styles/variables.module.scss', 'utf8')
const antdReset = readFileSync('src/assets/styles/antd-reset.scss', 'utf8')
const platformSidebar = readFileSync('src/layout/components/PlatformSidebar.vue', 'utf8')
const recentSessions = readFileSync('src/layout/components/RecentSessions.vue', 'utf8')
const sidebar = readFileSync('src/layout/components/Sidebar/index.vue', 'utf8')
const notice = readFileSync('THIRD_PARTY_NOTICES.md', 'utf8')
const main = readFileSync('src/main.js', 'utf8')

for (const [token, color] of Object.entries({
  '--main-700': '#046a82',
  '--main-500': '#3996ae',
  '--main-50': '#e1f6fb',
  '--gray-50': '#f5f7f7',
  '--gray-150': '#eef0f0',
  '--gray-1000': '#151616',
})) {
  assert.match(light.toLowerCase(), new RegExp(`${token}:\\s*${color}`))
}

assert.match(light.toLowerCase(), /--color-warning-900:\s*#ad6800/)

assert.match(dark.toLowerCase(), /--main-color:\s*#4a9fb8/)
assert.match(dark.toLowerCase(), /--gray-0:\s*#030303/)
assert.match(dark.toLowerCase(), /--bg-sider:\s*#141414/)

for (const [token, color] of Object.entries({
  '--main-1000': '#e1f6fb',
  '--main-900': '#c4eaf5',
  '--main-800': '#a3d8e8',
  '--main-700': '#82c3d6',
  '--main-600': '#5faec2',
  '--main-500': '#4a9fb8',
  '--main-400': '#3996ae',
  '--main-300': '#24839a',
  '--main-200': '#046a82',
  '--main-100': '#035065',
  '--main-50': '#023944',
  '--main-30': '#010d14',
  '--main-5': '#000405',
  '--gray-1000': '#f5f7f7',
  '--gray-900': '#eff2f2',
  '--gray-800': '#eef0f0',
  '--gray-700': '#e4e6e6',
  '--gray-600': '#d7d9d9',
  '--gray-500': '#bdbfbf',
  '--gray-400': '#979999',
  '--gray-300': '#697070',
  '--gray-200': '#4c4d4d',
  '--gray-150': '#323333',
  '--gray-100': '#1e1f1f',
  '--gray-50': '#151616',
  '--gray-25': '#0c0d0d',
  '--gray-10': '#080808',
  '--gray-0': '#030303',
  '--color-success-50': '#162312',
  '--color-success-500': '#52c41a',
  '--color-success-700': '#73d13d',
  '--color-error-50': '#2a1215',
  '--color-error-500': '#ff4d4f',
  '--color-error-700': '#ff7875',
  '--color-warning-50': '#2b2111',
  '--color-warning-500': '#faad14',
  '--color-warning-700': '#ffc53d',
  '--color-warning-900': '#ffd666',
  '--color-info-50': '#111b26',
  '--color-info-500': '#3d8fff',
  '--color-info-700': '#40a9ff',
})) {
  assert.match(dark.toLowerCase(), new RegExp(`${token}:\\s*${color}`))
}

const lightImport = main.indexOf("import '@/assets/styles/yuxi-tokens.css'")
const darkImport = main.indexOf("import '@/assets/styles/yuxi-tokens.dark.css'")
const workspaceImport = main.indexOf("import '@/assets/styles/workspace.scss'")
assert.ok(lightImport >= 0)
assert.ok(darkImport > lightImport)
assert.ok(workspaceImport > darkImport)

assert.match(notice, /Copyright \(c\) 2025 Yuxi Project Contributors/)
assert.match(notice, /MIT License/)

for (const [alias, token] of Object.entries({
  '--workspace-main': '--main-color',
  '--workspace-main-deep': '--main-800',
  '--workspace-main-soft': '--main-50',
  '--workspace-bg': '--gray-50',
  '--workspace-surface': '--gray-0',
  '--workspace-surface-muted': '--gray-10',
  '--workspace-border': '--gray-150',
  '--workspace-border-strong': '--gray-200',
  '--workspace-text': '--color-text',
  '--workspace-text-secondary': '--color-text-secondary',
  '--workspace-text-muted': '--color-text-tertiary',
  '--workspace-sidebar-bg': '--bg-sider',
  '--workspace-sidebar-surface': '--gray-50',
  '--workspace-sidebar-border': '--gray-150',
  '--workspace-sidebar-text': '--color-text',
  '--workspace-sidebar-muted': '--color-text-secondary',
  '--el-color-primary': '--main-color',
  '--el-bg-color-page': '--gray-50',
  '--el-bg-color': '--gray-0',
  '--el-border-color': '--gray-150',
})) {
  assert.match(workspace, new RegExp(`${alias}:\\s*var\\(${token}\\)`))
}

for (const color of ['#2f7e78', '#226862', '#172a2e', '#20383c', '#101d20', '#192c30']) {
  assert.doesNotMatch(workspace.toLowerCase(), new RegExp(color))
}

assert.match(variables, /\$menuActiveText:\s*#046a82/)
assert.match(variables, /\$--color-primary:\s*#046a82/)
assert.doesNotMatch(variables.toLowerCase(), /#409eff/)
assert.doesNotMatch(antdReset.toLowerCase(), /#2f7e78|#226862|#172a2e|#20383c|#101d20|#192c30/)
assert.match(workspace, /--workspace-shadow-soft:\s*none/)

assert.match(platformSidebar, /\.platform-brand[^}]*color:\s*var\(--color-text\)/)
assert.match(platformSidebar, /\.platform-new-chat[^}]*color:\s*var\(--color-text\)/)
assert.match(platformSidebar, /\.platform-nav-link:hover,[\s\S]*?\.platform-admin-entry:hover[^}]*color:\s*var\(--color-text\)/)
assert.match(platformSidebar, /\.platform-nav-link\.router-link-active[^}]*background:\s*var\(--main-50\);\s*color:\s*var\(--main-700\)/)
assert.doesNotMatch(platformSidebar, /workspace-sidebar-text/)
assert.doesNotMatch(platformSidebar, /color:\s*var\(--[^)]+\);\s*color:\s*var\(--[^)]+\)/)
assert.doesNotMatch(platformSidebar, /color:\s*#fff/i)

assert.match(recentSessions, /\.recent-session\.active\s*\{[^}]*background:\s*var\(--main-50\);\s*color:\s*var\(--main-700\)/)
assert.match(recentSessions, /\.recent-session\.active \.recent-session__open small\s*\{[^}]*color:\s*var\(--color-text-secondary\)/)
assert.doesNotMatch(recentSessions, /\.recent-session\.active[^}]*background:\s*var\(--workspace-main\)/)
assert.doesNotMatch(recentSessions, /\.recent-session\.active[^}]*color:\s*#fff/i)

assert.match(variables, /:root\s*\{[\s\S]*--menu-active-text:\s*var\(--main-700\)/)
assert.match(variables, /html\.dark\s*\{[\s\S]*--menu-active-text:\s*var\(--main-700\)/)
assert.match(variables, /\$menuText:\s*#d7d9d9/)
assert.match(sidebar, /color:\s*var\(--menu-active-text\)/)
assert.doesNotMatch(sidebar.toLowerCase(), /#409eff/)
