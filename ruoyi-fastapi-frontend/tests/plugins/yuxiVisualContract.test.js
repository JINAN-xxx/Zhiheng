import assert from 'node:assert/strict'
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

const readWorkspace = (fileName) => readFileSync(`src/components/Workspace/${fileName}`, 'utf8')
const readGlobalStyle = (fileName) => readFileSync(`src/assets/styles/${fileName}`, 'utf8')
const assertNoLegacySurfaceBackground = (fileName, source) => {
  assert.doesNotMatch(source, /background(?:-color)?\s*:\s*var\(--gray-0\)/, `${fileName} must use semantic surfaces for backgrounds`)
}
const collectSourceFiles = (directory, extensions) => readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
  const fileName = join(directory, entry.name)
  if (entry.isSymbolicLink()) {
    throw new Error(`Refusing to scan symbolic link: ${fileName}`)
  }
  if (entry.isDirectory()) return collectSourceFiles(fileName, extensions)
  return entry.isFile() && extensions.some((extension) => fileName.endsWith(extension)) ? [fileName] : []
})
const legacyThemeColors = ['#2f7e78', '#226862', '#409eff', '#20b2aa', '#48d1cc', '#42b983', '#67c23a', '#e6a23c', '#f56c6c']
const legacyThemeColorPatterns = [
  ...legacyThemeColors.map((color) => [color, new RegExp(color, 'i')]),
  ['rgb(64, 158, 255)', /rgba?\(\s*64\s*[, ]\s*158\s*[, ]\s*255/i],
]
const findLegacyThemeColors = (source) => legacyThemeColorPatterns
  .filter(([, pattern]) => pattern.test(source))
  .map(([color]) => color)
const frontendSourceRoots = ['src', 'plugins']
const frontendSourceExtensions = ['.vue', '.js', '.ts', '.jsx', '.tsx', '.scss', '.css', '.less', '.sass']
const frontendStyleFiles = frontendSourceRoots
  .flatMap((directory) => collectSourceFiles(directory, frontendSourceExtensions))
  .sort()

const toolbar = readWorkspace('WorkspaceToolbar.vue')
const panel = readWorkspace('WorkspacePanel.vue')
const state = readWorkspace('WorkspaceState.vue')
const transportCrypto = readFileSync('src/views/monitor/transportCrypto/index.vue', 'utf8')
const coreSurfaceFiles = [
  ['src/components/Antd/AuthShell.vue', readFileSync('src/components/Antd/AuthShell.vue', 'utf8')],
  ['src/components/Workspace/WorkspacePanel.vue', panel],
  ['src/layout/components/PlatformSidebar.vue', readFileSync('src/layout/components/PlatformSidebar.vue', 'utf8')],
  ['src/layout/components/PlatformTopbar.vue', readFileSync('src/layout/components/PlatformTopbar.vue', 'utf8')],
  ['src/layout/components/RecentSessions.vue', readFileSync('src/layout/components/RecentSessions.vue', 'utf8')],
  ['src/layout/components/TagsView/index.vue', readFileSync('src/layout/components/TagsView/index.vue', 'utf8')],
]
const antdReset = readGlobalStyle('antd-reset.scss')
const aiViewFiles = collectSourceFiles('plugins/ai/views', ['.vue']).sort()
const aiContainerFiles = [
  'plugins/ai/views/application/index.vue',
  'plugins/ai/views/chat/index.vue',
  'plugins/ai/views/model/index.vue',
  'plugins/ai/views/model/components/CredentialForm.vue',
  'plugins/ai/views/model/components/CredentialsPanel.vue',
  'plugins/ai/views/model/components/DeploymentForm.vue',
  'plugins/ai/views/model/components/EngineDeploymentsPanel.vue',
  'plugins/ai/views/model/components/ModelOnboardingDrawer.vue',
  'plugins/ai/views/model/components/PlatformModelsPanel.vue',
  'plugins/ai/views/model/components/RuntimeStatusPanel.vue',
  'plugins/ai/views/usage/index.vue',
].map((fileName) => [fileName, readFileSync(fileName, 'utf8')])
const globalStyles = [
  ['ruoyi.scss', readGlobalStyle('ruoyi.scss')],
  ['sidebar.scss', readGlobalStyle('sidebar.scss')],
  ['index.scss', readGlobalStyle('index.scss')],
]

assert.deepEqual(frontendSourceRoots, ['src', 'plugins'], 'legacy theme scan must cover all frontend roots')
assert.deepEqual(frontendSourceExtensions, ['.vue', '.js', '.ts', '.jsx', '.tsx', '.scss', '.css', '.less', '.sass'], 'legacy theme scan must cover frontend source extensions')
assert.match(collectSourceFiles.toString(), /entry\.isSymbolicLink\(\)[\s\S]*?throw new Error/, 'source collector must reject symbolic links')

for (const [fileName, source] of coreSurfaceFiles) {
  assertNoLegacySurfaceBackground(fileName, source)
  assert.match(source, /background:\s*var\(--bg-container\)/, `${fileName} must include a container surface`)
}

assert.ok(aiViewFiles.length >= 13, 'AI view scan must include the current view set')
for (const fileName of [
  'plugins/ai/views/chat/components/AiMessage.vue',
  'plugins/ai/views/chat/components/ChatSessionPanel.vue',
]) {
  assert.ok(aiViewFiles.includes(fileName), `${fileName} must be included in the AI view scan`)
}
for (const fileName of aiViewFiles) {
  assertNoLegacySurfaceBackground(fileName, readFileSync(fileName, 'utf8'))
}
assert.throws(
  () => assertNoLegacySurfaceBackground('mutation.vue', '.surface { background: var(--gray-0); }'),
  /mutation\.vue must use semantic surfaces for backgrounds/,
)
assert.throws(
  () => assertNoLegacySurfaceBackground('mutation.vue', '.surface { background-color: var(--gray-0); }'),
  /mutation\.vue must use semantic surfaces for backgrounds/,
)

for (const [fileName, source] of aiContainerFiles) {
  assert.match(source, /background:\s*var\(--bg-container\)/, `${fileName} must include a container surface`)
}

assert.match(coreSurfaceFiles[0][1], /\.auth-shell__mark[\s\S]*?color:\s*var\(--gray-0\)/)
assert.match(coreSurfaceFiles[5][1], /\.contextmenu\s*\{[\s\S]*?background:\s*var\(--bg-elevated\)/)
assert.match(antdReset, /\.antd-app-root\s*\{[\s\S]*?background:\s*var\(--bg-page\)/)
assert.match(antdReset, /\.antd-global-loading__surface\s*\{[\s\S]*?background:\s*var\(--bg-elevated\)/)
for (const [fileName, source] of aiContainerFiles.filter(([fileName]) => /(?:CredentialsPanel|EngineDeploymentsPanel|model\/index)\.vue$/.test(fileName))) {
  assert.match(source, /ant-drawer-(?:content|header)[\s\S]*?background:\s*var\(--bg-elevated\)/, `${fileName} must elevate drawer surfaces`)
}

for (const [fileName, source] of globalStyles) {
  assert.doesNotMatch(source, /#2f7e78|#226862|#409eff|#20b2aa|#48d1cc|#42b983/i, `${fileName} must not contain a legacy theme color`)
}

const legacyThemeColorViolations = frontendStyleFiles.flatMap((fileName) => findLegacyThemeColors(readFileSync(fileName, 'utf8'))
  .map((color) => `${fileName}: ${color}`))
assert.deepEqual(
  legacyThemeColorViolations,
  [],
  `frontend source must not contain legacy theme colors:\n${legacyThemeColorViolations.join('\n')}`,
)
for (const color of legacyThemeColors) {
  assert.throws(
    () => assert.deepEqual(findLegacyThemeColors(`.mutation { color: ${color}; }`), [], 'legacy color must be rejected'),
    /legacy color must be rejected/,
    `${color} mutation must be rejected`,
  )
}
assert.throws(
  () => assert.deepEqual(findLegacyThemeColors('.mutation { background: rgba(64 158 255 / 30%); }'), [], 'legacy RGB color must be rejected'),
  /legacy RGB color must be rejected/,
)

const gitAttributes = existsSync('../.gitattributes') ? readFileSync('../.gitattributes', 'utf8') : ''
assert.match(
  gitAttributes,
  /^ruoyi-fastapi-frontend\/src\/views\/tool\/build\/index\.vue whitespace=cr-at-eol$/m,
  'tool/build/index.vue must declare its CRLF whitespace rule',
)

assert.match(transportCrypto, /function resolveThemeColor\(token, fallback\)[\s\S]*?getComputedStyle\(document\.documentElement\)\.getPropertyValue\(token\)\.trim\(\)/)
assert.match(transportCrypto, /import useSettingsStore from '@\/store\/modules\/settings'/)
assert.match(transportCrypto, /const settingsStore = useSettingsStore\(\)/)
assert.match(transportCrypto, /watch\(\(\) => settingsStore\.isDark, async \(\) => \{\s*await nextTick\(\)\s*renderFailureReasonChart\(\)\s*renderKidStatsChart\(\)\s*\}\)/)
for (const [token, fallback] of [
  ['--color-error-500', '#ff4d4f'],
  ['--color-warning-500', '#faad14'],
  ['--color-info-500', '#1890ff'],
  ['--color-success-500', '#52c41a'],
  ['--gray-400', '#979999'],
]) {
  assert.match(transportCrypto, new RegExp(`resolveThemeColor\\('${token}', '${fallback}'\\)`), `${token} must resolve to a Canvas-safe fallback`)
}

for (const source of [toolbar, panel, state]) {
  assert.doesNotMatch(source.toLowerCase(), /#2f7e78|#226862|#409eff/)
  assert.doesNotMatch(source, /box-shadow\s*:/)
}

assert.match(panel, /background:\s*var\(--bg-container\)/)
assert.match(panel, /border:\s*1px solid var\(--gray-150\)/)
assert.match(panel, /border-radius:\s*8px/)
assert.match(panel, /&__header[\s\S]*?border-bottom:\s*1px solid var\(--gray-150\)/)
assert.match(panel, /&__title[\s\S]*?color:\s*var\(--color-text\)/)
assert.match(panel, /&__title[\s\S]*?font-size:\s*16px/)
assert.match(panel, /&__title[\s\S]*?font-weight:\s*600/)
assert.match(panel, /&__subtitle[\s\S]*?color:\s*var\(--color-text-secondary\)/)
assert.match(panel, /&__body[\s\S]*?padding:\s*var\(--workspace-space-5\)/)
assert.match(panel, /&\.is-flush &__body[\s\S]*?padding:\s*0/)

assert.match(toolbar, /&__title[\s\S]*?font-size:\s*20px/)
assert.match(toolbar, /min-height:\s*56px/)
assert.match(toolbar, /border-bottom:\s*1px solid var\(--gray-150\)/)
assert.match(toolbar, /&__title[\s\S]*?font-weight:\s*600/)
assert.match(toolbar, /&__title[\s\S]*?color:\s*var\(--color-text\)/)
assert.match(toolbar, /&__subtitle[\s\S]*?color:\s*var\(--color-text-secondary\)/)
assert.match(toolbar, /&__subtitle[\s\S]*?font-size:\s*14px/)
assert.match(toolbar, /&__menu[\s\S]*?border:\s*1px solid var\(--gray-150\)/)
assert.match(toolbar, /&__menu[\s\S]*?&:hover[\s\S]*?(background|border-color|color):/)
assert.match(toolbar, /&__menu[\s\S]*?&:focus-visible[\s\S]*?outline:\s*2px solid var\(--main-color\)/)
assert.match(toolbar, /flex-wrap:\s*wrap/)
assert.match(toolbar, /&__actions[\s\S]*?width:\s*100%/)
assert.doesNotMatch(toolbar, /transform\s*:/)

assert.match(state, /<LoadingOutlined v-if="kind === 'loading'/)
assert.match(state, /<WarningOutlined v-else-if="kind === 'error' \|\| kind === 'stale'/)
assert.match(state, /<FileTextOutlined v-else/)
assert.match(state, /:class="`is-\$\{kind\}`"/)
assert.match(state, /kind === 'error' \|\| kind === 'stale'/)
assert.match(state, /@click="emit\('retry'\)"/)
assert.match(state, /background:\s*var\(--gray-10\)/)
assert.match(state, /color:\s*var\(--color-text\)/)
assert.match(state, /&\.is-error[\s\S]*?background:\s*var\(--color-error-50\)/)
assert.match(state, /&\.is-error[\s\S]*?color:\s*var\(--color-error-700\)/)
assert.match(state, /&\.is-stale[\s\S]*?background:\s*var\(--color-warning-50\)/)
assert.match(state, /&\.is-stale[\s\S]*?color:\s*var\(--color-warning-900\)/)
assert.match(state, /&\.is-warning[\s\S]*?background:\s*var\(--color-warning-50\)/)
assert.match(state, /&\.is-warning[\s\S]*?color:\s*var\(--color-warning-900\)/)
assert.match(state, /&\.is-forbidden[\s\S]*?background:\s*var\(--gray-100\)/)
assert.match(state, /&\.is-forbidden[\s\S]*?color:\s*var\(--gray-700\)/)
assert.match(state, /&\.is-loading[\s\S]*?color:\s*var\(--main-color\)/)
assert.match(state, /strong[\s\S]*?color:\s*var\(--color-text\)/)
assert.match(state, /p[\s\S]*?color:\s*var\(--color-text-secondary\)/)
