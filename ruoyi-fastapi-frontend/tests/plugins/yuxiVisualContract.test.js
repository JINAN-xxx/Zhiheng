import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const readWorkspace = (fileName) => readFileSync(`src/components/Workspace/${fileName}`, 'utf8')
const readGlobalStyle = (fileName) => readFileSync(`src/assets/styles/${fileName}`, 'utf8')

const toolbar = readWorkspace('WorkspaceToolbar.vue')
const panel = readWorkspace('WorkspacePanel.vue')
const state = readWorkspace('WorkspaceState.vue')
const globalStyles = [
  ['ruoyi.scss', readGlobalStyle('ruoyi.scss')],
  ['sidebar.scss', readGlobalStyle('sidebar.scss')],
  ['index.scss', readGlobalStyle('index.scss')],
]

for (const [fileName, source] of globalStyles) {
  assert.doesNotMatch(source, /#2f7e78|#226862|#409eff|#20b2aa|#48d1cc|#42b983/i, `${fileName} must not contain a legacy theme color`)
}

for (const source of [toolbar, panel, state]) {
  assert.doesNotMatch(source.toLowerCase(), /#2f7e78|#226862|#409eff/)
  assert.doesNotMatch(source, /box-shadow\s*:/)
}

assert.match(panel, /background:\s*var\(--gray-0\)/)
assert.match(panel, /border:\s*1px solid var\(--gray-150\)/)
assert.match(panel, /border-radius:\s*8px/)
assert.match(panel, /&__header[\s\S]*?border-bottom:\s*1px solid var\(--gray-150\)/)
assert.match(panel, /&__title[\s\S]*?color:\s*var\(--color-text\)/)
assert.match(panel, /&__title[\s\S]*?font-size:\s*16px/)
assert.match(panel, /&__title[\s\S]*?font-weight:\s*600/)
assert.match(panel, /&__subtitle[\s\S]*?color:\s*var\(--color-text-secondary\)/)
assert.match(panel, /&__body[\s\S]*?padding:\s*var\(--workspace-space-5\)/)
assert.match(panel, /&\.is-flush &__body[\s\S]*?padding:\s*0/)

assert.match(toolbar, /&__title[\s\S]*?font-size:\s*22px/)
assert.match(toolbar, /&__title[\s\S]*?font-weight:\s*600/)
assert.match(toolbar, /&__title[\s\S]*?color:\s*var\(--color-text\)/)
assert.match(toolbar, /&__subtitle[\s\S]*?color:\s*var\(--color-text-secondary\)/)
assert.match(toolbar, /&__subtitle[\s\S]*?font-size:\s*13px/)
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
