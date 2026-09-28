import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const userStore = readFileSync('src/store/modules/user.js', 'utf8')
const permission = readFileSync('src/permission.js', 'utf8')
const request = readFileSync('src/utils/request.js', 'utf8')

assert.match(userStore, /clearSession\(\)/)
assert.match(userStore, /clearSession\(\)[\s\S]*?removeToken\(\)/)
assert.match(userStore, /logOut\(\)[\s\S]*?this\.clearSession\(\)[\s\S]*?bestEffortLogout\(/)

assert.match(permission, /catch \(err\)[\s\S]*?useUserStore\(\)\.clearSession\(\)/)
assert.doesNotMatch(permission, /catch \(err\)[\s\S]*?await useUserStore\(\)\.logOut\(\)/)
assert.match(permission, /path:\s*['"]\/login['"]/)

assert.match(request, /handleExpiredSession/)
assert.match(request, /useUserStore\(\)\.clearSession\(\)/)
assert.match(request, /location\.replace\(/)
assert.doesNotMatch(request, /登录状态已过期，您可以继续留在该页面/)

console.log('ok expired authentication recovers without a blank screen')
