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
assert.match(credential, /<a-form-item v-if="!quick" label="服务商"/)
assert.match(credential, /SecretField/)
assert.match(credential, /quick \? '保存并继续'/)

assert.match(deployment, /quick:\s*\{ type: Boolean, default: false \}/)
assert.match(deployment, /initialAlias:\s*\{ type: String, default: '' \}/)
assert.doesNotMatch(deployment, /<a-form-item v-if="!quick" label="路由别名"/)
assert.match(deployment, /<a-form-item v-if="!quick" label="服务商"/)
assert.match(deployment, /<a-form-item v-if="!quick" label="服务商凭据"/)
assert.match(deployment, /<a-collapse v-if="quick"/)
assert.match(deployment, /header="高级设置"/)

assert.doesNotMatch(drawer, /<a-steps|workflowSteps/)
assert.match(drawer, /模型信息/)
assert.match(drawer, /连接配置/)
assert.ok(drawer.indexOf('label="模型类型"') < drawer.indexOf('连接配置'))
assert.doesNotMatch(drawer, /label="模型代码"|label="路由别名"/)
assert.match(drawer, /buildQuickResourceIdentity/)
assert.match(drawer, /modelType: config.modelType/)
assert.match(drawer, /credentialFormRef.value\?\.clearSecrets/)
assert.match(credential, /embedded:/)
assert.match(credential, /defineExpose\(\{ getPayload, clearSecrets \}\)/)

assert.doesNotMatch(indexView, /高级管理|advancedOpen|SettingOutlined/)
assert.match(indexView, /v-if="can\('ai:engine:credential:list'\)" key="credentials" tab="凭据管理"/)
assert.doesNotMatch(indexView, /key="deployments"|key="status"/)
assert.match(indexView, /<PlatformModelsPanel ref="platformPanel"/)

assert.match(catalogApi, /url: '\/ai\/models', method: 'post', data, headers: \{ repeatSubmit: false \}/)
assert.match(catalogApi, /modelPath\(modelId, '\/binding'\).*headers: \{ repeatSubmit: false \}/s)
assert.match(catalogApi, /modelPath\(modelId, '\/publish'\).*headers: \{ repeatSubmit: false \}/s)
assert.match(platformPanel, /listModelRateCards/)
assert.match(platformPanel, /createModelRateCard/)
assert.match(platformPanel, /modelType === 'rerank' \? 'search_unit' : 'token'/)
assert.match(platformPanel, />费率配置</)
