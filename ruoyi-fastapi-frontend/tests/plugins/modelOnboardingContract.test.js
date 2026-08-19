import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const readComponent = (name) => readFileSync(
  new URL(`../../plugins/ai/views/model/components/${name}`, import.meta.url),
  'utf8',
)

const credential = readComponent('CredentialForm.vue')
const deployment = readComponent('DeploymentForm.vue')
const drawer = readComponent('ModelOnboardingDrawer.vue')

assert.match(credential, /quick:\s*\{ type: Boolean, default: false \}/)
assert.match(credential, /<el-form-item v-if="!quick" label="凭据名称"/)
assert.match(credential, /<el-form-item v-if="!quick" label="Provider"/)
assert.match(credential, /quick \? '保存并继续'/)

assert.match(deployment, /quick:\s*\{ type: Boolean, default: false \}/)
assert.match(deployment, /initialAlias:\s*\{ type: String, default: '' \}/)
assert.match(deployment, /<el-form-item v-if="!quick" label="路由别名"/)
assert.match(deployment, /<el-form-item v-if="!quick" label="Provider"/)
assert.match(deployment, /<el-form-item v-if="!quick" label="Provider 凭据"/)
assert.match(deployment, /<el-collapse v-if="quick"/)
assert.match(deployment, />高级设置</)

assert.match(drawer, /<el-step title="连接信息"/)
assert.match(drawer, /<el-step title="测试模型"/)
assert.match(drawer, /<el-step title="确认接入"/)
assert.doesNotMatch(drawer, /<el-step title="Provider 凭据"/)
assert.doesNotMatch(drawer, /<el-step title="引擎部署"/)
assert.match(drawer, />接入并发布</)
assert.match(drawer, /buildQuickResourceIdentity/)
