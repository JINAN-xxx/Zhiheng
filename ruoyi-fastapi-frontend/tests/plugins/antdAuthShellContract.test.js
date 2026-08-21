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
}

const login = readFileSync('src/views/login.vue', 'utf8')
for (const component of ['a-form', 'a-input', 'a-input-password', 'a-checkbox', 'a-button']) {
  assert.match(login, new RegExp(`<${component}`))
}
assert.match(login, /getCodeImg/)
assert.match(login, /rememberMe/)
assert.match(login, /Cookies\.set/)
assert.match(login, /Cookies\.remove/)
assert.match(login, /redirect/)
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
