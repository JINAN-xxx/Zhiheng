import { execFileSync } from 'node:child_process'
import assert from 'node:assert/strict'
import { existsSync, readFileSync, statSync } from 'node:fs'

const scriptPath = '../deploy.sh'
assert.ok(existsSync(scriptPath), '仓库根目录必须提供 deploy.sh')
const script = readFileSync(scriptPath, 'utf8')
const help = execFileSync(scriptPath, ['help'], { encoding: 'utf8' })
const mode = statSync(scriptPath).mode
const compose = readFileSync('../compose.yaml', 'utf8')
const legacyCompose = readFileSync('../docker-compose.pg.yml', 'utf8')
const runner = readFileSync('../deploy/platform.py', 'utf8')
const legacy = readFileSync('../deploy/legacy/manage.sh', 'utf8')
const mysqlCompose = readFileSync('../docker-compose.my.yml', 'utf8')
const initScript = readFileSync('../deploy/ruoyi/init-shared-postgres.sh', 'utf8')
const dockerfile = readFileSync('Dockerfile', 'utf8')

assert.ok(mode & 0o111, 'deploy.sh 必须具有可执行权限')
assert.match(runner, /compose\.yaml/)
assert.match(script, /deploy\/platform\.py/)
assert.match(legacy, /ruoyi-vue3-fastapi/)
assert.match(legacy, /init-shared-postgres\.sh/)
for (const command of ['init', 'start', 'restart', 'stop', 'status', 'logs', 'update', 'help']) {
  assert.ok(help.includes(command), `缺少 ${command} 命令`)
}
assert.match(script, /CDPATH=\s*cd\s+['\"]\$\(dirname ['\"]\$0['\"]\)['\"]/) 
assert.doesNotMatch(script, /docker\s+(?:system\s+prune|volume\s+rm|rm\s+-f)/)
assert.doesNotMatch(script, /compose\s+down\s+-v/)
assert.match(compose, /nginx:latest/)
assert.match(compose, /\.\/ruoyi-fastapi-frontend\/dist:\/usr\/share\/nginx\/html:ro/)
for (const source of [legacyCompose, mysqlCompose]) {
  assert.match(source, /\.\/data\/agent-skills:\/app\/data\/agent-skills/)
  assert.match(source, /AGENT_SKILL_DATA_DIR:\s*\/app\/data\/agent-skills/)
}
assert.match(initScript, /data\/agent-skills/)
assert.match(initScript, /mkdir -p/)
assert.doesNotMatch(initScript, /rm\s+-rf[^\n]*agent-skills/)
assert.doesNotMatch(dockerfile, /npm\s+install|npm\s+run\s+build:docker/)
