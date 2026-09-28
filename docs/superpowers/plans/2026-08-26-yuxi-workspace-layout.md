# Yuxi 工作台式布局改造实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将智衡桌面端调整为接近 Yuxi 的安静工作台，并补齐模型中心菜单归组、模型停用后重新启用和模块内 API 说明页面。

**架构：** 保留现有后端 API 和页面路径，通过共享桌面壳层、平铺页面骨架和 AI 资源导航分组统一视觉。API 说明使用开发接入页面的 `view=docs` 查询视图，不新增菜单或公开接口。

**技术栈：** Vue 3、Vue Router、Pinia、Ant Design Vue、SCSS、FastAPI、SQLAlchemy、pytest、Node 契约测试、Playwright。

## 文件职责

- 后端模型状态机：`zhiheng-fastapi-backend/plugins/ai/service/model_catalog_service.py`、`dao/ai_model_catalog_dao.py`。
- 模型状态测试：`zhiheng-fastapi-backend/tests/plugins/ai/test_model_catalog_service.py`、`test_model_catalog_dao.py`。
- AI 导航：`zhiheng-fastapi-frontend/src/components/Workspace/workspaceTransforms.js`、`src/layout/components/PlatformSidebar.vue`。
- 模型界面：`zhiheng-fastapi-frontend/plugins/ai/views/model/components/PlatformModelsPanel.vue`、`ModelOnboardingDrawer.vue`、`plugins/ai/utils/modelEngine.js`。
- 桌面壳层：`zhiheng-fastapi-frontend/src/layout/index.vue`、`PlatformSidebar.vue`、`AdminSidebar.vue`、`PlatformTopbar.vue`。
- 新建账户区：`zhiheng-fastapi-frontend/src/layout/components/PlatformAccountMenu.vue`。
- 组件导出：`zhiheng-fastapi-frontend/src/layout/components/index.js`。
- 页面骨架：`zhiheng-fastapi-frontend/src/components/Workspace/WorkspaceToolbar.vue`、`src/layout/components/AiResourceShell.vue`、`src/assets/styles/yuxi-tokens*.css`。
- API 文档：`zhiheng-fastapi-frontend/plugins/ai/utils/apiDocs.js`、`views/application/components/ApplicationApiDocs.vue`、`views/application/index.vue`。
- 验收：`zhiheng-fastapi-frontend/tests/plugins/*.test.js`、`tests/e2e/platform_shell_check.py`。

除非步骤另有说明，后端命令在 `zhiheng-fastapi-backend` 目录执行，前端命令在 `zhiheng-fastapi-frontend` 目录执行，Git 命令在仓库工作树根目录执行。

---

### 任务 1：锁定模型生命周期重新启用

**目标：** 停用模型后可恢复为 `active`，且首次发布时间保持不变。

**文件：**
- 测试：`zhiheng-fastapi-backend/tests/plugins/ai/test_model_catalog_service.py`、`test_model_catalog_dao.py`
- 修改：`zhiheng-fastapi-backend/plugins/ai/service/model_catalog_service.py`、`dao/ai_model_catalog_dao.py`
- 前端测试：`zhiheng-fastapi-frontend/tests/plugins/modelEngineTransforms.test.js`
- 前端修改：`zhiheng-fastapi-frontend/plugins/ai/utils/modelEngine.js`、`views/model/components/PlatformModelsPanel.vue`

- [ ] **步骤 1：写后端状态测试**

```python
async def test_disabled_published_model_can_be_republished_without_resetting_published_at():
    service, _, _ = _service(runtime=FakeRuntime({'runtime-company-chat'}))
    model = await _create_model(service)
    await _create_binding(service, model.model_id)
    published = await service.publish_model(model.model_id, username='publisher', now=NOW)
    await service.disable_model(model.model_id, username='operator', now=LATER)
    enabled = await service.publish_model(model.model_id, username='publisher-2', now=LATEST)
    assert enabled.lifecycle_status == 'active'
    assert enabled.published_at == published.published_at
```

- [ ] **步骤 2：运行状态测试**

运行：`conda run -n dj pytest -q tests/plugins/ai/test_model_catalog_service.py -k republish`

若现有后端已通过，保留生产实现，只补测试；不得增加第二套 enable API。

- [ ] **步骤 3：锁定前端动作映射**

```js
assert.equal(getLifecycleAction('draft'), '发布')
assert.equal(getLifecycleAction('active'), '停用')
assert.equal(getLifecycleAction('disabled'), '重新启用')
```

`PlatformModelsPanel.vue` 对 `disabled` 显示“重新启用”，继续调用 `publishPlatformModel`；成功提示按状态显示“模型已发布”或“模型已重新启用”。

- [ ] **步骤 4：验证并提交**

运行：`conda run -n dj pytest -q tests/plugins/ai/test_model_catalog_service.py tests/plugins/ai/test_model_catalog_dao.py && npm run test:plugin`

提交：`git commit -m "fix: 支持停用模型重新启用"`

---

### 任务 2：将开发接入和用量记录归入模型中心导航

**目标：** 视觉层级为“模型中心 / 模型目录、开发接入、用量记录”，原路径保持不变。

**文件：**
- 测试：`zhiheng-fastapi-frontend/tests/plugins/workspaceTransforms.test.js`、`platformShellContract.test.js`
- 修改：`zhiheng-fastapi-frontend/src/components/Workspace/workspaceTransforms.js`、`src/layout/components/PlatformSidebar.vue`

- [ ] **步骤 1：写失败的分组测试**

```js
const groups = resolveAiResourceGroups(new Set(['*:*:*']))
assert.equal(groups[0].label, '模型中心')
assert.deepEqual(groups[0].children.map((item) => item.path), [
  '/ai/model', '/ai/application', '/ai/usage',
])
```

- [ ] **步骤 2：实现纯分组函数**

```js
export function resolveAiResourceGroups(permissions) {
  const children = resolveAiResourceTabs(permissions)
  return children.length ? [{ label: '模型中心', path: '/ai/model', children }] : []
}
```

保留 `resolveAiResourceTabs` 供 landing 和旧调用方使用。侧栏只调整展示层级，不修改 `plugin.yaml` 路由树。

- [ ] **步骤 3：覆盖权限组合**

测试仅有 `ai:application:list` 时分组只含开发接入；仅有 `ai:application:query` 时只含用量记录；通配权限包含三项。

- [ ] **步骤 4：验证并提交**

运行：`node tests/plugins/workspaceTransforms.test.js && npm run test:plugin`

提交：`git commit -m "refactor: 归组模型中心导航"`

---

### 任务 3：从模型界面收敛能力字段

**目标：** 能力 JSON 继续兼容历史数据，但不再占用列表列宽或要求人工勾选。

**文件：**
- 测试：`zhiheng-fastapi-frontend/tests/plugins/modelEngineTransforms.test.js`、`antdAiViewsContract.test.js`
- 修改：`zhiheng-fastapi-frontend/plugins/ai/views/model/components/PlatformModelsPanel.vue`、`ModelOnboardingDrawer.vue`、`plugins/ai/utils/modelEngine.js`

- [ ] **步骤 1：写失败的界面契约**

```js
assert.doesNotMatch(modelPanel, /title="能力"/)
assert.doesNotMatch(onboarding, /label="能力"/)
assert.match(onboarding, /capabilities:\s*defaultModelCapabilities/)
```

- [ ] **步骤 2：按类型生成兼容默认值**

```js
export function defaultModelCapabilities(type) {
  if (type === 'chat') return { streaming: true, vision: false, reasoning: false, tools: false }
  if (type === 'embedding') return { dimensionsOverride: false }
  return { returnDocuments: false }
}
```

移除能力勾选和列表能力列；提交 payload 仍包含 `capabilities`。保留 `normalizePlatformModel` 对历史能力字段的容错解析。

- [ ] **步骤 3：验证并提交**

运行：`node tests/plugins/modelEngineTransforms.test.js && node tests/plugins/antdAiViewsContract.test.js && npm run test:plugin`

提交：`git commit -m "refactor: 收敛模型能力字段交互"`

---

### 任务 4：重构桌面全局壳层

**目标：** 桌面去掉常驻顶部栏，把折叠、主题、通知和用户操作收进侧栏；移动端保留顶部栏。

**文件：**
- 测试：`zhiheng-fastapi-frontend/tests/plugins/platformShellContract.test.js`、`antdComponentsContract.test.js`
- 新建：`zhiheng-fastapi-frontend/src/layout/components/PlatformAccountMenu.vue`
- 修改：`zhiheng-fastapi-frontend/src/layout/index.vue`、`PlatformSidebar.vue`、`AdminSidebar.vue`、`PlatformTopbar.vue`、`src/layout/components/index.js`
- 样式：`zhiheng-fastapi-frontend/src/assets/styles/yuxi-tokens.css`、`yuxi-tokens.dark.css`

- [ ] **步骤 1：写桌面壳层契约**

```js
assert.match(layout, /<PlatformTopbar\s+v-if="isMobile"/)
assert.match(sidebar, /PlatformAccountMenu/)
assert.match(sidebar, /折叠侧边栏|展开侧边栏/)
```

- [ ] **步骤 2：拆出账户区**

`PlatformAccountMenu.vue` 复用现有主题、通知、头像、个人中心、锁屏、设置和退出逻辑；父组件只负责路由和设置抽屉事件。

- [ ] **步骤 3：调整桌面布局**

桌面侧栏宽度改为 `230px`，折叠宽度保持 `56px`，菜单项高度改为 `32px`。`PlatformTopbar` 仅在 `isMobile` 时渲染并继续负责打开抽屉。

- [ ] **步骤 4：验证并提交**

运行：`npm run test:plugin`

提交：`git commit -m "refactor: 调整 Yuxi 工作台桌面壳层"`

---

### 任务 5：统一平铺页面骨架

**目标：** 页面直接使用满高内容区和紧凑标题栏，去掉 AI 资源页外层大留白和嵌套卡片感。

**文件：**
- 测试：`zhiheng-fastapi-frontend/tests/plugins/subpageLayoutContract.test.js`
- 修改：`zhiheng-fastapi-frontend/src/components/Workspace/WorkspaceToolbar.vue`、`src/layout/components/AiResourceShell.vue`、`src/assets/styles/yuxi-tokens*.css`、`src/assets/styles/workspace.scss`
- 复核：`plugins/ai/views/chat/index.vue`、`views/model/index.vue`、`views/application/index.vue`、`views/usage/index.vue`

- [ ] **步骤 1：写失败的骨架契约**

```js
assert.match(resourceShell, /padding:\s*0/)
assert.doesNotMatch(resourceShell, /max-width:\s*var\(--workspace-content-max-width\)/)
assert.match(workspaceToolbar, /min-height:\s*56px/)
```

- [ ] **步骤 2：实现共享页面骨架**

`AiResourceShell` 改为满高、零外层卡片，内部页面自行滚动；`WorkspaceToolbar` 保留现有插槽，只调整为 `56px` 标题栏、底部分隔线和紧凑字号。

- [ ] **步骤 3：统一资源页滚动边界**

模型中心、开发接入和用量记录统一使用 `height: 100%`、`min-height: 0` 和局部滚动。对话页不套资源卡片，保持消息区滚动和底部输入区固定。

管理后台保留搜索表单、操作栏和数据表格，但 `workspace.scss` 移除页面级浮动卡片外观；不改变任何管理页面字段、权限和接口调用。

- [ ] **步骤 4：验证并提交**

运行：`node tests/plugins/subpageLayoutContract.test.js && npm run test:plugin`

提交：`git commit -m "refactor: 统一工作台页面骨架"`

---

### 任务 6：开发接入内嵌 API 说明

**目标：** 开发接入标题栏进入 `/ai/application?view=docs`，页面覆盖四个公开接口、Bearer 鉴权、流式调用和错误码。

**文件：**
- 测试：`zhiheng-fastapi-frontend/tests/plugins/applicationTransforms.test.js`、`antdAiViewsContract.test.js`
- 新建：`zhiheng-fastapi-frontend/plugins/ai/utils/apiDocs.js`、`views/application/components/ApplicationApiDocs.vue`
- 修改：`zhiheng-fastapi-frontend/plugins/ai/views/application/index.vue`

- [ ] **步骤 1：写失败的文档数据测试**

```js
assert.equal(buildPublicApiBase('https://example.com'), 'https://example.com/v1')
assert.equal(apiDocs.find((item) => item.path === '/v1/chat/completions').streaming, true)
assert.doesNotMatch(apiDocs.map((item) => item.example).join('\n'), /sk-zh_[a-z0-9_]+/i)
```

- [ ] **步骤 2：建立静态协议数据**

`apiDocs.js` 固定包含：

```js
export const apiDocs = [
  { id: 'models', method: 'GET', path: '/v1/models', title: '获取授权模型' },
  { id: 'chat', method: 'POST', path: '/v1/chat/completions', title: '对话生成', streaming: true },
  { id: 'embeddings', method: 'POST', path: '/v1/embeddings', title: '向量生成' },
  { id: 'rerank', method: 'POST', path: '/v1/rerank', title: '文本排序' },
]
```

每个条目补齐字段表、响应表、curl 和 Python 示例；密钥与模型只使用 `<DEVELOPER_API_KEY>`、`<MODEL_CODE>`。

请求示例固定采用以下最小结构：

```json
{"model":"<MODEL_CODE>","messages":[{"role":"user","content":"你好"}],"stream":true}
{"model":"<MODEL_CODE>","input":["第一段文本","第二段文本"]}
{"model":"<MODEL_CODE>","query":"需要排序的问题","documents":["候选一","候选二"],"top_n":2}
```

错误码章节从公开 API 的稳定错误结构整理，至少覆盖认证失败、模型未授权、配额受限、请求无效和上游失败；不复制运行时异常正文。

- [ ] **步骤 3：实现工作台文档视图**

`ApplicationApiDocs.vue` 使用左侧目录和右侧滚动正文；顶部提供返回、复制调用地址、Swagger、ReDoc。组件不得导入密钥列表 API，也不得读取 `issuedKey` 或 localStorage。

- [ ] **步骤 4：接入查询视图**

```js
function openApiDocs() {
  return router.push({ path: route.path, query: { ...route.query, view: 'docs' } })
}
function closeApiDocs() {
  const { view, ...query } = route.query
  return router.push({ path: route.path, query })
}
```

父页面在 `view=docs` 时只渲染文档，不加载应用、密钥、模型或用量；返回时保留原 `applicationId` 和 `tab`。

- [ ] **步骤 5：验证并提交**

运行：`node tests/plugins/applicationTransforms.test.js && node tests/plugins/antdAiViewsContract.test.js && npm run test:plugin`

提交：`git commit -m "feat: 增加开发接入 API 说明"`

---

### 任务 7：桌面截图和回归验收

**文件：**
- 修改：`zhiheng-fastapi-frontend/tests/e2e/platform_shell_check.py`

- [ ] **步骤 1：更新 Playwright 场景**

桌面场景依次验证：侧栏折叠、模型中心子项、开发接入 API 说明打开与返回、停用模型的“重新启用”、长中文截断、用量表格无页面溢出。移动端只保留既有顶部栏抽屉回归。

- [ ] **步骤 2：运行完整前端验证**

```bash
npm run test:plugin
NODE_OPTIONS=--max-old-space-size=4096 npm run build:docker
PYTHONPATH=tests/e2e python tests/e2e/platform_shell_check.py
```

预期：插件测试全部通过；Vite 构建退出码为 `0`；Playwright 两轮截图哈希一致，console 和 page error 均为空。

- [ ] **步骤 3：运行后端模型测试**

```bash
conda run -n dj pytest -q tests/plugins/ai/test_model_catalog_service.py tests/plugins/ai/test_model_catalog_dao.py tests/plugins/ai/test_ai_model_catalog_controller.py
```

预期：模型生命周期测试全部通过，重新启用保留首次发布时间。

- [ ] **步骤 4：提交验收变更**

运行：`git diff --check && git status --short --branch`

提交：`git commit -m "test: 完善 Yuxi 工作台桌面验收"`

---

### 任务 8：本地部署验证

- [ ] **步骤 1：保留回滚镜像并更新前端**

优先使用已经通过 `build:docker` 的 `dist`。完整 Docker 构建若重新下载未变化依赖，可基于当前 Nginx 镜像覆盖静态文件；必须先保留明确的回滚标签。

- [ ] **步骤 2：重启服务**

```bash
docker compose -p zhiheng-vue3-fastapi \
  --env-file /home/ja/project/ZhiHeng-Vue3-FastAPI/.env \
  -f docker-compose.pg.yml up -d --no-build --force-recreate zhiheng-frontend
docker restart zhiheng-backend-pg
docker ps --format '{{.Names}} {{.Status}}'
```

- [ ] **步骤 3：检查平台与文档入口**

```bash
curl --fail http://127.0.0.1:12580/
curl --fail -o /dev/null -w '%{http_code}\n' http://127.0.0.1:12580/docker-api/docs
curl --fail -o /dev/null -w '%{http_code}\n' http://127.0.0.1:12580/docker-api/redoc
```

预期：平台、Swagger、ReDoc 返回 `200`；三个模型中心子页面原路径均可直接访问。

---

## 依赖关系

1. 任务 1 必须先于任务 7，因为 Playwright 要验证“重新启用”。
2. 任务 2 必须先于任务 4，因为桌面侧栏直接渲染分组导航。
3. 任务 5 必须先于任务 6，因为 API 说明复用新的页面骨架。
4. 任务 1、2、3 可分别提交；任务 4、5、6 完成后统一进行任务 7、8。

## 最终验收

- 左侧导航只有“模型中心”一级分组，开发接入和用量记录位于其下。
- 模型能力字段不出现在列表和接入表单，但历史 API 与数据库兼容。
- 停用模型显示“重新启用”，操作后恢复 active。
- 开发接入可以进入 API 说明，并能返回原应用和标签状态。
- 桌面端无常驻顶部栏、无页面外层大卡片和 AI 装饰文案。
- 前端插件测试、后端模型测试、生产构建和 Playwright 桌面检查全部通过。
