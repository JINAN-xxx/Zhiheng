import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const readComponent = (name) => readFileSync(
  new URL(`../../plugins/ai/views/model/components/${name}`, import.meta.url),
  'utf8',
)

const credential = readComponent('CredentialForm.vue')
const deployment = readComponent('DeploymentForm.vue')
const drawer = readComponent('ModelOnboardingDrawer.vue')
const platformPanel = readComponent('PlatformModelsPanel.vue')
const indexView = readFileSync(new URL('../../plugins/ai/views/model/index.vue', import.meta.url), 'utf8')
const catalogApi = readFileSync(new URL('../../plugins/ai/api/modelCatalog.js', import.meta.url), 'utf8')

assert.match(credential, /quick:\s*\{ type: Boolean, default: false \}/)
assert.match(credential, /<a-form-item v-if="!quick" label="凭据名称"/)
assert.match(credential, /<a-form-item v-if="!quick" label="Provider"/)
assert.match(credential, /SecretField/)
assert.match(credential, /quick \? '保存并继续'/)

assert.match(deployment, /quick:\s*\{ type: Boolean, default: false \}/)
assert.match(deployment, /initialAlias:\s*\{ type: String, default: '' \}/)
assert.match(deployment, /<a-form-item v-if="!quick" label="路由别名"/)
assert.match(deployment, /<a-form-item v-if="!quick" label="Provider"/)
assert.match(deployment, /<a-form-item v-if="!quick" label="Provider 凭据"/)
assert.match(deployment, /<a-collapse v-if="quick"/)
assert.match(deployment, /header="高级设置"/)

assert.match(drawer, /const workflowSteps = \[\s*\{ title: '连接信息' \},\s*\{ title: '测试模型' \},\s*\{ title: '确认接入' \}/)
assert.doesNotMatch(drawer, /title: 'Provider 凭据'/)
assert.doesNotMatch(drawer, /title: '引擎部署'/)
assert.match(drawer, /<a-steps/)
assert.match(drawer, />接入并发布</)
assert.match(drawer, /buildQuickResourceIdentity/)

assert.match(indexView, /高级管理/)
assert.match(indexView, /advancedOpen/)
assert.match(indexView, /SettingOutlined/)
assert.match(indexView, /v-if="can\('ai:model:list'\)" key="platform" tab="平台模型"/)
assert.match(indexView, /v-if="advancedOpen && can\('ai:engine:deployment:list'\)" key="deployments"/)
assert.match(indexView, /v-if="advancedOpen && can\('ai:engine:credential:list'\)" key="credentials"/)
assert.match(indexView, /v-if="advancedOpen && can\('ai:runtime:query'\)" key="status"/)

assert.match(catalogApi, /url: '\/ai\/models', method: 'post', data, headers: \{ repeatSubmit: false \}/)
assert.match(catalogApi, /modelPath\(modelId, '\/binding'\).*headers: \{ repeatSubmit: false \}/s)
assert.match(catalogApi, /modelPath\(modelId, '\/publish'\).*headers: \{ repeatSubmit: false \}/s)
assert.match(platformPanel, /listModelRateCards/)
assert.match(platformPanel, /createModelRateCard/)
assert.match(platformPanel, /modelType === 'rerank' \? 'search_unit' : 'token'/)
assert.match(platformPanel, />费率配置</)
