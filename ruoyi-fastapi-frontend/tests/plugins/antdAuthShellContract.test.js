import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const authPages = [
  'src/views/login.vue',
  'src/views/register.vue',
  'src/views/lock.vue',
  'src/views/error/401.vue',
  'src/views/error/404.vue',
]

for (const fileName of authPages) {
  const source = readFileSync(fileName, 'utf8')
  assert.doesNotMatch(source, /element-plus|@element-plus\/icons-vue|<el-|--el-/)
  assert.match(source, /auth-shell/)
  assert.doesNotMatch(source, /#(?:2f7e78|226862|409eff)/i)
  assert.doesNotMatch(source, /AI WORKSPACE|eyebrow\s*=/)
}

const authShell = readFileSync('src/components/Antd/AuthShell.vue', 'utf8')
assert.doesNotMatch(authShell, /auth-shell::before|workspace-shadow-float|box-shadow\s*:/)
assert.match(authShell, /background:\s*var\(--gray-50\)/)
assert.match(authShell, /background:\s*var\(--bg-container\)/)
assert.match(authShell, /border:\s*1px solid var\(--gray-150\)/)
assert.match(authShell, /border-radius:\s*8px/)
assert.match(authShell, /color:\s*var\(--color-text\)/)
assert.match(authShell, /color:\s*var\(--color-text-secondary\)/)
assert.match(authShell, /auth-shell__brand/)
assert.match(authShell, /box-sizing:\s*border-box/)
assert.match(authShell, /min-height:\s*100vh/)
assert.match(authShell, /display:\s*flex/)
assert.match(authShell, /flex-direction:\s*column/)
assert.match(authShell, /justify-content:\s*center/)
assert.match(authShell, /align-items:\s*center/)

const login = readFileSync('src/views/login.vue', 'utf8')
const router = readFileSync('src/router/index.js', 'utf8')
for (const component of ['a-form', 'a-input', 'a-input-password', 'a-checkbox', 'a-button']) {
  assert.match(login, new RegExp(`<${component}`))
}
assert.match(login, /getCodeImg/)
assert.match(login, /rememberMe/)
assert.match(login, /Cookies\.set/)
assert.match(login, /Cookies\.remove/)
assert.match(login, /redirect/)
assert.match(login, /const defaultLandingPath\s*=\s*['"]\/ai\/chat['"]/)
assert.match(login, /router\.push\(\{\s*path:\s*redirect\.value\s*\|\|\s*defaultLandingPath/s)
assert.match(router, /path:\s*['"]['"][\s\S]*?redirect:\s*['"]\/ai\/chat['"]/)
assert.doesNotMatch(router, /path:\s*['"]['"][\s\S]*?redirect:\s*['"]\/index['"]/)
assert.match(login, /width:\s*min\(100%,\s*420px\)/)
assert.doesNotMatch(login, /login-background\.jpg/)

const register = readFileSync('src/views/register.vue', 'utf8')
assert.match(register, /a-input-password/)
assert.match(register, /getCodeImg/)
assert.match(register, /registerPwdValidator/)
assert.doesNotMatch(register, /dangerouslyUseHTMLString|v-html/)

const lock = readFileSync('src/views/lock.vue', 'utf8')
assert.match(lock, /a-input-password/)
assert.match(lock, /a-button/)
assert.match(lock, /unlockScreen/)

for (const fileName of ['src/views/error/401.vue', 'src/views/error/404.vue']) {
  const source = readFileSync(fileName, 'utf8')
  assert.match(source, /a-result/)
  assert.match(source, /a-button/)
}
