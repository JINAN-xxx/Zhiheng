# 模型快速接入简化实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将模型中心默认接入流程简化为 Provider、凭据、上游模型和平台名称三个用户步骤，同时保留现有资源编排、安全重试与高级管理能力。

**架构：** 在现有 `modelEngine.js` 中新增无副作用的快速接入命名和状态转换函数；`CredentialForm`、`DeploymentForm` 通过显式 `quick` 属性隐藏内部资源字段并保留原高级管理模式；`ModelOnboardingDrawer` 继续调用现有 API 和对账函数，但只呈现“连接信息、测试模型、确认接入”三段状态。模型中心默认只突出平台模型，高级标签由用户主动展开。

**技术栈：** Vue 3、Vite、Element Plus、现有 ZhiHeng 权限体系、Node 原生断言测试、Python Playwright、Docker Nginx。

---

## 文件与职责

- 修改：`zhiheng-fastapi-frontend/plugins/ai/utils/modelEngine.js`，增加快速接入资源命名和步骤文案纯函数。
- 修改：`zhiheng-fastapi-frontend/tests/plugins/modelEngineTransforms.test.js`，锁定安全命名、默认值和步骤映射。
- 修改：`zhiheng-fastapi-frontend/plugins/ai/views/model/components/CredentialForm.vue`，增加隐藏 Credential Name 与重复 Provider 字段的快速模式。
- 修改：`zhiheng-fastapi-frontend/plugins/ai/views/model/components/DeploymentForm.vue`，增加只展示模型、测试和折叠高级参数的快速模式。
- 修改：`zhiheng-fastapi-frontend/plugins/ai/views/model/components/ModelOnboardingDrawer.vue`，重排三步流程并复用现有失败保留、精确对账和发布逻辑。
- 修改：`zhiheng-fastapi-frontend/plugins/ai/views/model/index.vue`，默认突出平台模型，将底层资源标签收进高级管理。
- 创建：`zhiheng-fastapi-frontend/tests/plugins/modelOnboardingContract.test.js`，静态锁定快速模式、用户文案和高级管理入口。
- 创建：`docs/superpowers/verification/2026-08-19-model-onboarding-simplification.md`，记录构建、视口、网络调用和失败重试证据。

## 任务 1：建立快速接入纯函数契约

**文件：**
- 修改：`zhiheng-fastapi-frontend/plugins/ai/utils/modelEngine.js`
- 修改：`zhiheng-fastapi-frontend/tests/plugins/modelEngineTransforms.test.js`

- [ ] **步骤 1：编写失败测试**

在 `modelEngineTransforms.test.js` 中导入并断言：

```js
assert.deepEqual(
  buildQuickResourceIdentity({ provider: 'OpenAI', upstreamModel: 'openai/gpt-4o', suffix: 'a1b2c3d4' }),
  {
    credentialName: 'quick-openai-a1b2c3d4',
    deploymentAlias: 'quick-openai-gpt-4o-a1b2c3d4',
    modelCode: 'openai-gpt-4o',
    displayName: 'gpt-4o',
  },
)
assert.equal(getQuickOnboardingStep('credential').title, '连接信息')
assert.equal(getQuickOnboardingStep('deployment').title, '测试模型')
assert.equal(getQuickOnboardingStep('catalog').title, '确认接入')
assert.equal(getQuickOnboardingStep('published').title, '接入成功')
```

同时覆盖空值、中文、空格、斜杠、控制字符和超长模型名，要求资源标识只含 `[a-z0-9._-]`、不超过 100 字符且不为空。

- [ ] **步骤 2：运行测试验证失败**

运行：`cd zhiheng-fastapi-frontend && node tests/plugins/modelEngineTransforms.test.js`

预期：FAIL，提示 `buildQuickResourceIdentity` 或 `getQuickOnboardingStep` 未导出。

- [ ] **步骤 3：实现最少纯函数**

```js
const QUICK_STEP_META = Object.freeze({
  credential: Object.freeze({ index: 0, title: '连接信息' }),
  deployment: Object.freeze({ index: 1, title: '测试模型' }),
  catalog: Object.freeze({ index: 2, title: '确认接入' }),
  published: Object.freeze({ index: 3, title: '接入成功' }),
})

const safeResourcePart = (value, fallback) => {
  const normalized = asString(value).toLowerCase()
    .replace(/[\u0000-\u001f\u007f-\u009f]/g, '')
    .replace(/[^a-z0-9._-]+/g, '-')
    .replace(/^[._-]+|[._-]+$/g, '')
  return (normalized || fallback).slice(0, 64)
}

export function buildQuickResourceIdentity({ provider, upstreamModel, suffix }) {
  const providerPart = safeResourcePart(provider, 'provider')
  const modelPart = safeResourcePart(upstreamModel, 'model')
  const suffixPart = safeResourcePart(suffix, 'local').slice(0, 16)
  return {
    credentialName: `quick-${providerPart}-${suffixPart}`.slice(0, 100),
    deploymentAlias: `quick-${modelPart}-${suffixPart}`.slice(0, 100),
    modelCode: modelPart.slice(0, 100),
    displayName: asString(upstreamModel).split('/').filter(Boolean).at(-1) || 'AI 模型',
  }
}

export const getQuickOnboardingStep = (state) => QUICK_STEP_META[state] || QUICK_STEP_META.credential
```

- [ ] **步骤 4：运行测试并提交**

运行：`npm run test:plugin`

预期：全部插件测试通过。

```bash
git add plugins/ai/utils/modelEngine.js tests/plugins/modelEngineTransforms.test.js
git commit -m "feat: 建立模型快速接入状态契约"
```

## 任务 2：为凭据与部署表单增加快速模式

**文件：**
- 修改：`zhiheng-fastapi-frontend/plugins/ai/views/model/components/CredentialForm.vue`
- 修改：`zhiheng-fastapi-frontend/plugins/ai/views/model/components/DeploymentForm.vue`
- 创建：`zhiheng-fastapi-frontend/tests/plugins/modelOnboardingContract.test.js`

- [ ] **步骤 1：编写结构红灯**

创建测试并读取 SFC 源码：

```js
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const credential = readFileSync(new URL('../../plugins/ai/views/model/components/CredentialForm.vue', import.meta.url), 'utf8')
const deployment = readFileSync(new URL('../../plugins/ai/views/model/components/DeploymentForm.vue', import.meta.url), 'utf8')

assert.match(credential, /quick:\s*\{ type: Boolean, default: false \}/)
assert.match(credential, /v-if="!quick"[^>]*label="凭据名称"/)
assert.match(credential, /v-if="!quick"[^>]*label="Provider"/)
assert.match(deployment, /quick:\s*\{ type: Boolean, default: false \}/)
assert.match(deployment, /<el-collapse v-if="quick"/)
assert.match(deployment, />高级设置</)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`node tests/plugins/modelOnboardingContract.test.js`

预期：FAIL，两个表单均不存在 `quick` 属性。

- [ ] **步骤 3：实现 CredentialForm 快速模式**

新增 `quick: { type: Boolean, default: false }`。快速模式由父组件传入 `credentialName` 和 `provider`，隐藏这两个表单项，只渲染当前 Provider 动态字段；`submit()` 仍输出原有 `{ credentialName, provider, values }`。按钮文案改为“保存并继续”，不改变高级管理页面的默认模式。

- [ ] **步骤 4：实现 DeploymentForm 快速模式**

新增：

```js
quick: { type: Boolean, default: false },
initialAlias: { type: String, default: '' },
```

快速模式隐藏 Alias、Provider 和 Credential 表单项，将 timeout、streamTimeout、maxRetries 和 description 放入“高级设置”折叠面板；上游模型和连接测试始终可见。`payload()` 和测试请求继续输出现有 API 结构。

- [ ] **步骤 5：运行验证并提交**

运行：

```bash
npm run test:plugin
NODE_OPTIONS=--max-old-space-size=4096 npm run build:docker
```

预期：测试全部通过，Vite 构建退出 0。

```bash
git add plugins/ai/views/model/components/CredentialForm.vue \
  plugins/ai/views/model/components/DeploymentForm.vue \
  tests/plugins/modelOnboardingContract.test.js
git commit -m "feat: 简化模型接入表单"
```

## 任务 3：将接入抽屉重排为三步

**文件：**
- 修改：`zhiheng-fastapi-frontend/plugins/ai/views/model/components/ModelOnboardingDrawer.vue`
- 修改：`zhiheng-fastapi-frontend/tests/plugins/modelOnboardingContract.test.js`

- [ ] **步骤 1：扩展抽屉红灯**

```js
assert.match(drawer, /<el-step title="连接信息"/)
assert.match(drawer, /<el-step title="测试模型"/)
assert.match(drawer, /<el-step title="确认接入"/)
assert.doesNotMatch(drawer, /<el-step title="Provider 凭据"/)
assert.doesNotMatch(drawer, /<el-step title="引擎部署"/)
assert.match(drawer, />接入并发布</)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`node tests/plugins/modelOnboardingContract.test.js`

预期：FAIL，抽屉仍显示四段内部资源步骤。

- [ ] **步骤 3：实现连接信息步骤**

顶部只显示 Provider 选择。选择 Provider 后，有兼容凭据时可复用；新建时给 `CredentialForm` 传 `quick`、Provider 和当前抽屉生命周期固定的自动 Credential Name。Provider 变化时清空不兼容选择和测试结果。资源后缀在 `resetWorkflow()` 中使用 `crypto.randomUUID()` 生成一次，整个重试周期不变。

- [ ] **步骤 4：实现测试模型步骤**

给 `DeploymentForm` 传 `quick`、自动 Alias、Provider 和 Credential。连接成功前禁用继续；连接成功后沿用 `createDeployment()` 的创建前对账、响应校验和未知结果复核。部署成功时用上游模型初始化平台名称与 Model Code。

- [ ] **步骤 5：实现确认接入步骤**

默认只显示平台名称和可选 Model Code；模型类型与 Capabilities 放入高级设置。按钮文案为“接入并发布”，内部继续执行 Model、Binding CAS、Publish。失败只显示用户步骤和脱敏原因，保留 `resources` 与表单；成功页展示平台名称、编码和 Binding Alias。

- [ ] **步骤 6：运行验证并提交**

运行：`npm run test:plugin && NODE_OPTIONS=--max-old-space-size=4096 npm run build:docker`

预期：测试与构建通过，新产物包含三个新步骤标题且不包含旧步骤标题。

```bash
git add plugins/ai/views/model/components/ModelOnboardingDrawer.vue tests/plugins/modelOnboardingContract.test.js
git commit -m "feat: 发布三步模型快速接入"
```

## 任务 4：将底层资源标签收进高级管理

**文件：**
- 修改：`zhiheng-fastapi-frontend/plugins/ai/views/model/index.vue`
- 修改：`zhiheng-fastapi-frontend/tests/plugins/modelOnboardingContract.test.js`

- [ ] **步骤 1：编写高级管理红灯**

```js
assert.match(indexView, />高级管理</)
assert.match(indexView, /advancedOpen/)
assert.match(indexView, /v-if="advancedOpen && can\('ai:engine:deployment:list'\)"/)
assert.match(indexView, /v-if="can\('ai:model:list'\)" label="平台模型"/)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`node tests/plugins/modelOnboardingContract.test.js`

预期：FAIL，四个标签仍始终平铺。

- [ ] **步骤 3：实现渐进式高级入口**

新增 `advancedOpen`。平台模型标签始终按原权限展示；引擎部署、Provider 凭据和运行状态只在展开时渲染。工具栏增加带 `Setting` 图标的“高级管理/收起高级管理”按钮，仅在用户至少拥有一个高级标签权限时显示。收起时若当前位于高级标签，自动回到 `platform`；刷新和接入完成继续使用可选链。

- [ ] **步骤 4：运行验证并提交**

运行：`npm run test:plugin && NODE_OPTIONS=--max-old-space-size=4096 npm run build:docker`

预期：测试和构建通过。

```bash
git add plugins/ai/views/model/index.vue tests/plugins/modelOnboardingContract.test.js
git commit -m "feat: 收纳模型中心高级管理"
```

## 任务 5：真实浏览器验证与部署

**文件：**
- 创建：`docs/superpowers/verification/2026-08-19-model-onboarding-simplification.md`

- [ ] **步骤 1：启动验证服务**

运行：`npm run dev -- --host 127.0.0.1 --port 15175`

保持进程运行到浏览器验证结束。

- [ ] **步骤 2：Playwright 验证 API 与失败恢复**

Mock `/docker-api/ai/model-engine/**` 和 `/docker-api/ai/models/**`，验证新凭据完整链、已有凭据不重复创建、Connection 失败保留输入、Deployment 未知结果先对账、Catalog/Binding/Publish 失败保留前置资源、Management 未配置门禁和关闭后迟到响应不落地。所有请求计数必须精确，禁止 DELETE 补偿。

- [ ] **步骤 3：验证三个视口**

在 1440×900、390×844、320×720 截图并断言：默认突出平台模型；高级管理默认收起且可展开；步骤标题依次为连接信息、测试模型、确认接入；Secret 为空；高级设置折叠；无横向溢出；`consoleErrors=[]`、`pageErrors=[]`。

- [ ] **步骤 4：运行最终验证**

```bash
npm run test:plugin
NODE_OPTIONS=--max-old-space-size=4096 npm run build:docker
git diff --check
```

预期：插件测试通过，Vite 构建退出 0，差异检查无输出。

- [ ] **步骤 5：更新前端容器并复验**

确认新 `dist` 包含新版文案后，保留当前镜像回滚标签，将 `dist` 注入干净 Nginx 镜像，只替换 `zhiheng-frontend`。访问 `http://127.0.0.1:12580/` 重复截图与控制台检查，不重建后端、数据库、Redis 或 LiteLLM。

- [ ] **步骤 6：记录证据并提交**

验证文档记录 commit、命令、结果、镜像 ID 和截图路径，不记录 Token、Credential 或 Provider Secret。

```bash
git add docs/superpowers/verification/2026-08-19-model-onboarding-simplification.md
git commit -m "test: 验证模型快速接入流程"
```
