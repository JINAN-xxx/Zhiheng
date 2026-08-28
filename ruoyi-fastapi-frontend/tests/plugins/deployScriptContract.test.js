import assert from 'node:assert/strict'
import { existsSync, readFileSync, statSync } from 'node:fs'

const scriptPath = '../deploy.sh'
assert.ok(existsSync(scriptPath), '仓库根目录必须提供 deploy.sh')
const script = readFileSync(scriptPath, 'utf8')
const mode = statSync(scriptPath).mode

assert.ok(mode & 0o111, 'deploy.sh 必须具有可执行权限')
assert.match(script, /docker-compose\.pg\.yml/)
assert.match(script, /ruoyi-vue3-fastapi/)
assert.match(script, /init-shared-postgres\.sh/)
for (const command of ['init', 'start', 'restart', 'stop', 'status', 'logs', 'update', 'help']) {
  assert.match(script, new RegExp(`\\./deploy\\.sh ${command}`), `缺少 ${command} 命令`)
}
assert.match(script, /CDPATH=\s*cd\s+['\"]\$\(dirname ['\"]\$0['\"]\)['\"]/) 
assert.doesNotMatch(script, /docker\s+(?:system\s+prune|volume\s+rm|rm\s+-f)/)
assert.doesNotMatch(script, /compose\s+down\s+-v/)
