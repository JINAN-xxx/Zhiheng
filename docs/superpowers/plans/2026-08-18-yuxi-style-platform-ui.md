# 智衡 AI 统一 Yuxi 风格界面实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在保留 Element Plus、业务接口、权限码和路由契约的前提下，将登录页、全局壳、AI 对话、模型中心、Developer API 和系统管理统一为 Yuxi 风格的青绿色 AI 工作台。

**架构：** 先建立语义化 CSS 变量和小型 Workspace 展示组件，再让全局布局承载 AI 主导航与管理控制台入口；业务页面只接入外壳和状态组件，不把请求、权限或数据状态塞进展示组件。每个页面批次都保留现有 API 和状态机，并用插件测试、生产构建和 Playwright 视口检查收口。

**技术栈：** Vue 3、Vite、Element Plus、SCSS、Pinia、现有 ZhiHeng 路由/权限体系、Node 插件测试脚本、Python Playwright。

---

## 文件与职责

第一阶段新增平台样式与展示组件：

- 创建：`frontend/src/assets/styles/workspace.scss`，定义 Yuxi 风格语义变量、Element Plus 覆盖、表面、状态、滚动条和响应式尺寸。
- 创建：`frontend/src/components/Workspace/workspaceTransforms.js`，提供导航、状态和移动操作分组等无副作用纯函数。
- 创建：`frontend/src/components/Workspace/WorkspaceShell.vue`，提供 AI 工作台主内容与移动端抽屉槽位。
- 创建：`frontend/src/components/Workspace/WorkspaceSidebar.vue`，负责主导航、最近会话、管理控制台入口和折叠状态展示。
- 创建：`frontend/src/components/Workspace/WorkspaceToolbar.vue`，负责页面标题、状态、筛选与操作插槽。
- 创建：`frontend/src/components/Workspace/WorkspacePanel.vue`，负责轻边框面板和统一内边距。
- 创建：`frontend/src/components/Workspace/WorkspaceStatus.vue`，负责语义状态图标、文字和颜色。
- 创建：`frontend/src/components/Workspace/WorkspaceState.vue`，负责 loading、empty、首次 error 和 stale error。
- 创建：`frontend/src/components/Workspace/WorkspaceMoreMenu.vue`，负责移动端行操作。
- 创建：`frontend/tests/plugins/workspaceTransforms.test.js`，测试导航可见性、状态文案和响应式 action 分组等纯函数。

第二阶段修改全局框架：

- 修改：`frontend/src/main.js`，全局引入 `workspace.scss`。
- 修改：`frontend/src/assets/styles/variables.module.scss`，将 Element 主题和布局变量映射到 Workspace 变量。
- 修改：`frontend/src/layout/index.vue`，接入 AI 工作台壳，同时保留管理路由壳和锁屏。
- 修改：`frontend/src/layout/components/Sidebar/index.vue`、`Sidebar/Logo.vue`、`Navbar.vue`、`TopNav/index.vue`，接入窄侧栏、智衡品牌和管理控制台入口。
- 修改：`frontend/src/router/index.js`，确认登录后默认重定向到 AI 对话，不改变已有 route name。
- 修改：`frontend/src/permission.js`，动态路由注册完成后，仅在用户拥有 `ai:chat:list` 时将 `/index` 重定向到 `/ai/chat`。

第三阶段修改入口与业务页：

- 修改：`frontend/src/views/login.vue`、`src/views/register.vue`，重做品牌布局和表单表面。
- 修改：`frontend/plugins/ai/views/chat/index.vue`、`plugins/ai/views/chat/components/AiMessage.vue`，接入会话侧栏、工具栏、连续消息流和工作台输入区。
- 修改：`frontend/plugins/ai/views/model/index.vue` 及其 `components/` 下七个面板/表单，统一资源页壳、状态和移动操作。
- 创建：`frontend/plugins/ai/api/application.js`，封装应用、Key、轮换和撤销请求。
- 创建：`frontend/plugins/ai/utils/application.js`，提供应用状态、Key 状态、分页和秘密字段的安全转换。
- 创建：`frontend/plugins/ai/views/application/index.vue`，展示开发者应用、Key 列表、授权模型和一次性 Key 创建结果。
- 修改：`backend/plugins/ai/plugin.yaml`，在已有 AI 管理菜单下新增 `application` 前端菜单，权限使用已有 `ai:application:*` 代码。
- 系统管理页面的具体文件在任务 6 中逐项列出，任务 6 不使用通配路径。

## 任务 1：建立视觉变量和纯函数契约

**文件：**
- 创建：`frontend/src/assets/styles/workspace.scss`
- 修改：`frontend/src/main.js`
- 修改：`frontend/src/assets/styles/variables.module.scss`
- 创建：`frontend/tests/plugins/workspaceTransforms.test.js`

- [ ] **步骤 1：编写失败测试**

在 `workspaceTransforms.test.js` 中写入以下契约，使用 Node 原生 `assert`，不引入新的测试框架：

```js
import assert from 'node:assert/strict'
import { groupWorkspaceActions, resolveWorkspaceState, visibleWorkspaceNav } from '../../src/components/Workspace/workspaceTransforms.js'

assert.deepEqual(visibleWorkspaceNav({ canChat: true, canModel: false, canApi: true }), ['chat', 'api'])
assert.equal(resolveWorkspaceState({ loading: false, rows: [], error: null }).kind, 'empty')
assert.equal(resolveWorkspaceState({ loading: false, rows: [{ id: 1 }], error: new Error('timeout') }).kind, 'stale')
assert.deepEqual(groupWorkspaceActions({ mobile: true, canEdit: true, canDelete: true }), ['more'])
```

- [ ] **步骤 2：运行测试确认失败**

运行：`node tests/plugins/workspaceTransforms.test.js`

预期：FAIL，报错 `Cannot find module .../workspaceTransforms.js`。

- [ ] **步骤 3：实现最少契约和样式变量**

创建纯函数文件并让变量集中在 `:root` / `html.dark`：

```js
export function visibleWorkspaceNav(access) {
  return ['chat', 'model', 'api'].filter((key) => access[{ chat: 'canChat', model: 'canModel', api: 'canApi' }[key]])
}

export function resolveWorkspaceState({ loading, rows, error }) {
  if (loading) return { kind: 'loading' }
  if (error && rows.length) return { kind: 'stale', error }
  if (error) return { kind: 'error', error }
  return rows.length ? { kind: 'ready' } : { kind: 'empty' }
}

export function groupWorkspaceActions({ mobile, canEdit, canDelete }) {
  if (mobile) return ['more']
  return [canEdit && 'edit', canDelete && 'delete'].filter(Boolean)
}
```

变量至少包含 `--workspace-main`、`--workspace-bg`、`--workspace-surface`、`--workspace-border`、`--workspace-text`、`--workspace-muted`、`--workspace-success`、`--workspace-warning` 和 `--workspace-danger`，并映射 `--el-color-primary` 与 `--el-border-color`。

- [ ] **步骤 4：运行测试确认通过**

运行：`node tests/plugins/workspaceTransforms.test.js && npm run test:plugin`

预期：纯函数测试通过，现有插件测试全部通过。

- [ ] **步骤 5：Commit**

```bash
git add src/assets/styles/workspace.scss src/main.js src/assets/styles/variables.module.scss src/components/Workspace/workspaceTransforms.js tests/plugins/workspaceTransforms.test.js
git commit -m "feat: 建立智衡工作台视觉基础"
```

## 任务 2：实现 Workspace 展示组件

**文件：**
- 创建：`src/components/Workspace/WorkspaceShell.vue`
- 创建：`src/components/Workspace/WorkspaceSidebar.vue`
- 创建：`src/components/Workspace/WorkspaceToolbar.vue`
- 创建：`src/components/Workspace/WorkspacePanel.vue`
- 创建：`src/components/Workspace/WorkspaceStatus.vue`
- 创建：`src/components/Workspace/WorkspaceState.vue`
- 创建：`src/components/Workspace/WorkspaceMoreMenu.vue`

- [ ] **步骤 1：先补组件契约测试**

扩展 `workspaceTransforms.test.js`，断言桌面/移动 action 分组、stale 数据保留和管理入口可见性；测试只依赖纯函数，不把 Vue 实例作为业务状态来源。

- [ ] **步骤 2：运行测试确认失败**

运行：`node tests/plugins/workspaceTransforms.test.js`

预期：新增契约因组件状态辅助函数不存在而失败。

- [ ] **步骤 3：实现展示组件**

组件只接收 props、emit 事件和 slots：

```vue
<WorkspaceShell :sidebar-collapsed="collapsed" @toggle-sidebar="collapsed = !collapsed">
  <template #sidebar><WorkspaceSidebar :items="items" /></template>
  <template #toolbar><WorkspaceToolbar title="AI 对话" /></template>
  <RouterView />
</WorkspaceShell>
```

`WorkspaceState` 必须分别支持 `loading`、`empty`、`error` 和 `stale`；`WorkspaceMoreMenu` 在宽屏展示 inline actions，在 `max-width: 767px` 下只显示一个带 tooltip 的更多按钮。

- [ ] **步骤 4：运行验证**

运行：`node tests/plugins/workspaceTransforms.test.js && npm run test:plugin && NODE_OPTIONS=--max-old-space-size=4096 npm run build:docker`

预期：测试通过，Vite 成功构建，仅允许已有第三方 PURE 注释 warning。

- [ ] **步骤 5：Commit**

```bash
git add src/components/Workspace tests/plugins/workspaceTransforms.test.js
git commit -m "feat: 增加智衡工作台基础组件"
```

## 任务 3：接入全局壳和登录入口

**文件：**
- 修改：`src/layout/index.vue`
- 修改：`src/layout/components/Sidebar/index.vue`
- 修改：`src/layout/components/Sidebar/Logo.vue`
- 修改：`src/layout/components/Navbar.vue`
- 修改：`src/layout/components/TopNav/index.vue`
- 修改：`src/router/index.js`
- 修改：`src/permission.js`
- 修改：`src/views/login.vue`
- 修改：`src/views/register.vue`

- [ ] **步骤 1：写路由和导航契约测试**

在 `workspaceTransforms.test.js` 增加：未登录仍只进入 login；已登录且拥有 `ai:chat:list` 时 `/index` 解析为 `/ai/chat`；无 AI 权限时仍为 `/index`；无 API 权限时不显示 Developer API；管理控制台入口不绕过动态权限树。

- [ ] **步骤 2：运行测试确认失败**

运行：`node tests/plugins/workspaceTransforms.test.js`

预期：默认目标和导航过滤断言失败。

- [ ] **步骤 3：实现壳层**

保留 `useAppStore`、`usePermissionStore`、锁屏和动态路由；将视觉层替换为窄侧栏、管理分组和移动抽屉。品牌文案读取 `import.meta.env.VITE_APP_TITLE` 或已有系统配置，默认值为“智衡 AI”。登录/注册只改模板和 scoped 样式，不修改 `userStore.login`、验证码和重定向逻辑。

- [ ] **步骤 4：验证入口**

运行：`node tests/plugins/workspaceTransforms.test.js && npm run test:plugin && NODE_OPTIONS=--max-old-space-size=4096 npm run build:docker`

使用 Python Playwright 在 1440px、390px 访问 login 和登录后 mock 页面，断言 `document.documentElement.scrollWidth <= innerWidth`、侧栏折叠按钮可访问且无 console/page error。

- [ ] **步骤 5：Commit**

```bash
git add src/layout src/router/index.js src/views/login.vue src/views/register.vue tests/plugins/workspaceTransforms.test.js
git commit -m "feat: 重构智衡全局工作台壳"
```

## 任务 4：重做 AI 对话工作台

**文件：**
- 修改：`plugins/ai/views/chat/index.vue`
- 修改：`plugins/ai/views/chat/components/AiMessage.vue`
- 修改：`plugins/ai/api/chat.js`（仅在接口字段兼容需要时）

- [ ] **步骤 1：写对话视觉回归契约**

增加测试场景：空会话、历史会话、普通消息、流式消息、思考过程、停止、失败重试、图片队列和偏好弹窗；纯函数测试断言 run 状态文案与消息状态映射。

- [ ] **步骤 2：运行基线验证**

运行：`npm run test:plugin`

预期：现有插件测试通过，浏览器截图记录当前对话页作为基线。

- [ ] **步骤 3：接入工作台布局**

使用 `WorkspaceShell`/`WorkspaceToolbar`/`WorkspaceState`，保留 `createChatStream` 的 SSE 消费和 `AbortController`。消息区不得用嵌套卡片；输入区固定在内容底部，附件、发送、停止使用图标按钮和 tooltip；历史加载失败时保留当前消息，不清空旧数据。

- [ ] **步骤 4：浏览器验证**

使用 mock API 的 Python Playwright 脚本覆盖桌面 1440px、移动 390px/320px：断言能创建会话、打开历史、流式内容逐步出现、停止按钮生效、失败提示可见、输入区不溢出、console/page errors 为空。

- [ ] **步骤 5：Commit**

```bash
git add plugins/ai/views/chat plugins/ai/api/chat.js tests/plugins/workspaceTransforms.test.js
git commit -m "feat: 重构智衡 AI 对话工作台"
```

## 任务 5：统一模型中心与 Developer API

**文件：**
- 修改：`plugins/ai/views/model/index.vue`
- 修改：`plugins/ai/views/model/components/PlatformModelsPanel.vue`
- 修改：`plugins/ai/views/model/components/EngineDeploymentsPanel.vue`
- 修改：`plugins/ai/views/model/components/CredentialsPanel.vue`
- 修改：`plugins/ai/views/model/components/RuntimeStatusPanel.vue`
- 修改：`plugins/ai/views/model/components/ModelOnboardingDrawer.vue`
- 修改：`plugins/ai/views/model/components/CredentialForm.vue`
- 修改：`plugins/ai/views/model/components/DeploymentForm.vue`
- 创建：`plugins/ai/api/application.js`
- 创建：`plugins/ai/utils/application.js`
- 创建：`plugins/ai/views/application/index.vue`
- 修改：`backend/plugins/ai/plugin.yaml`

- [ ] **步骤 1：写资源页回归契约**

扩展测试覆盖四标签顺序、权限过滤、首次 error、stale 保留、misconfigured 门禁、secret 不回填、active/disabled/missing/unknown binding、移动端更多菜单、分页末页回退和 Developer Key 一次性展示。

- [ ] **步骤 2：运行测试确认新增边界失败**

运行：`node tests/plugins/workspaceTransforms.test.js && npm run test:plugin`

预期：新增状态和 action 断言先失败，现有模型中心插件测试继续通过。

- [ ] **步骤 3：接入统一资源组件**

页面保留既有请求代次、权限守卫、前后对账、expectedVersion、秘密字段过滤和无补偿语义。只替换表面结构、工具栏、状态显示、抽屉和移动操作；禁止在展示组件中直接发 API 请求。Developer API 应用页使用 `application.js` 的固定路径：`/ai/applications`、`/ai/applications/{applicationId}`、`/ai/applications/{applicationId}/keys`、`/ai/applications/{applicationId}/keys/{keyId}/rotate` 和 `/ai/applications/{applicationId}/keys/{keyId}/revoke`。

- [ ] **步骤 4：浏览器验证**

使用 mock API 验证 full-permission 与 list-only 用户：无权限不发详情请求；配置缺失禁用写操作；刷新失败保留旧数据；秘密 input 为空；320px/390px 使用更多菜单且无横向溢出。

- [ ] **步骤 5：Commit**

```bash
git add plugins/ai/views plugins/ai/api plugins/ai/utils tests/plugins/workspaceTransforms.test.js
git commit -m "feat: 统一模型与开发者 API 工作台"
```

## 任务 6：统一管理控制台页面

**文件：**
- 修改：`src/layout/components/TagsView/index.vue`
- 修改：`src/layout/components/AppMain.vue`
- 修改：`src/components/Pagination/index.vue`
- 修改：`src/views/system/user/index.vue`
- 修改：`src/views/system/role/index.vue`
- 修改：`src/views/system/menu/index.vue`
- 修改：`src/views/monitor/server/index.vue`
- 修改：`src/views/monitor/job/index.vue`
- 修改：`src/views/monitor/cache/index.vue`
- 修改：`src/views/tool/gen/index.vue`

- [ ] **步骤 1：记录管理页面基线**

使用路由扫描列出用户、角色、菜单、监控、系统工具、代码生成页面，记录动态菜单、权限指令、标签页、锁屏和暗色状态。

- [ ] **步骤 2：建立管理页面回归断言**

在插件测试中增加纯函数断言：权限隐藏动作、表格分页参数、空状态/错误状态分类和 dark 主题变量不改变。

- [ ] **步骤 3：统一页面表面**

查询区使用 `WorkspaceToolbar`，表格和详情使用 `WorkspacePanel`，分页使用统一尺寸；不改变任何 API 请求、权限码、表单字段和后端响应转换。

- [ ] **步骤 4：验证管理页面**

运行 `npm run test:plugin` 和生产构建，并用 Playwright 访问至少用户列表、监控列表、系统工具三类页面，断言动态权限和暗色切换保持可用。

- [ ] **步骤 5：Commit**

```bash
git add src/layout src/components/Pagination src/views tests/plugins
git commit -m "feat: 统一智衡管理控制台视觉"
```

## 任务 7：全局视觉回归与交付

**文件：**
- 修改：本计划前六个任务中仍存在视觉偏差的文件
- 创建：`docs/superpowers/verification/2026-08-18-yuxi-style-platform-ui.md`，记录截图、命令和已知风险

- [ ] **步骤 1：运行全量前端验证**

运行：

```bash
npm run test:plugin
NODE_OPTIONS=--max-old-space-size=4096 npm run build:docker
git diff --check
```

预期：插件测试通过，构建 exit 0，diff check 无输出。

- [ ] **步骤 2：运行关键浏览器验证**

使用 Python Playwright 验证 login、AI 对话、模型中心、Developer API、管理控制台在 1440px、768px、390px 和 320px 下无横向溢出、console error 或 page error；截图保存到 `/tmp`，不把构建产物或临时脚本提交到仓库。

- [ ] **步骤 3：检查视觉和安全边界**

扫描生产前端确认没有 `v-html`、console 泄露、Key 常量、敏感字段回填、紫色渐变和孤立旧色值；确认所有图标按钮有 aria-label 或 tooltip，状态有文字语义。

- [ ] **步骤 4：编写验证记录**

在验证文档中记录实际测试数字、构建 warning、浏览器视口和未解决的非阻断风险；禁止写入未验证的“通过”结论。

- [ ] **步骤 5：Commit**

```bash
git add docs/superpowers/verification
git commit -m "test: 完成智衡统一界面视觉回归"
```

## 计划自检

- 视觉变量、暗色、圆角、间距、状态、图标、响应式分别由任务 1、2、7 覆盖。
- 登录/注册、全局壳、AI 默认入口由任务 3 覆盖。
- AI 普通/流式、历史、停止、失败、附件和偏好由任务 4 覆盖。
- 模型中心和 Developer API 的权限、秘密、状态、分页和移动操作由任务 5 覆盖。
- 系统管理、标签页、锁屏、动态菜单和暗色由任务 6 覆盖。
- 所有任务都保留 Element Plus、既有 API、权限码、路由和业务状态机。
- 未使用 TODO、TBD、待定或未定义的方法名；浏览器脚本和截图仅用于本地验证，不成为生产依赖。
