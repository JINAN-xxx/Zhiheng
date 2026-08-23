# 智衡平台前端布局统一实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 把当前 AI 前端改造成平衡密度的统一工作台，并将面向用户的混合语言文案收敛为中文。

**架构：** 保留现有 Vue 路由、Pinia 状态和接口调用，仅重构全局工作台骨架、工作区视觉组件和四个 AI 页面。全局导航由 `PlatformSidebar` 统一承担，页面使用单层标题和扁平数据表面，移动端沿用现有抽屉并增加页面级重排。

**技术栈：** Vue 3、Ant Design Vue 4、SCSS、Node.js 合约测试、Playwright、Vite。

---

## 文件职责

- `src/assets/styles/workspace.scss`：全局工作台颜色、尺寸、内容边距和响应式变量。
- `src/layout/components/PlatformSidebar.vue`：AI 全局导航、最近会话和管理后台入口。
- `src/layout/components/PlatformTopbar.vue`：单层当前位置、主题、消息和用户操作。
- `src/layout/components/AiResourceShell.vue`：移除重复资源总标题和二级总导航。
- `src/components/Workspace/WorkspaceToolbar.vue`：单层页面标题、中文说明和页面主操作。
- `plugins/ai/views/chat/index.vue`：对话工作区标题、空状态和输入区布局。
- `plugins/ai/views/model/index.vue`：模型中心标题、页签和数据区布局。
- `plugins/ai/views/model/components/*.vue`：模型表头、类型、操作和表单中文化。
- `plugins/ai/views/application/index.vue`：开发接入主从布局、访问密钥与调用概览。
- `plugins/ai/views/usage/index.vue`：用量记录筛选、表头、类型和状态中文化。
- `tests/plugins/platformShellContract.test.js`：全局骨架、尺寸和中文导航契约。
- `tests/plugins/antdAiViewsContract.test.js`：AI 页面布局与中文文案契约。
- `tests/e2e/platform_shell_check.py`：真实浏览器桌面与移动端骨架检查。
- `tests/e2e/antd_chat_check.py`：真实流式对话回归检查。

### 任务一：建立中文工作台骨架契约

**文件：**
- 修改：`ruoyi-fastapi-frontend/tests/plugins/platformShellContract.test.js`
- 修改：`ruoyi-fastapi-frontend/tests/plugins/antdAiViewsContract.test.js`

- [ ] **步骤 1：编写失败的全局骨架测试**

在 `platformShellContract.test.js` 中把旧断言替换为：

```js
assert.match(platformSidebar, /智衡平台/)
assert.match(platformSidebar, /智能对话/)
assert.match(platformSidebar, /模型中心/)
assert.match(platformSidebar, /开发接入/)
assert.match(platformSidebar, /用量记录/)
assert.doesNotMatch(platformSidebar, />AI 对话<|>AI 资源<|智衡 AI/)
assert.doesNotMatch(topbar, /AI 工作台/)
assert.match(styles, /--platform-sidebar-width:\s*184px/)
assert.match(styles, /--platform-topbar-height:\s*54px/)
```

- [ ] **步骤 2：编写失败的 AI 页面中文文案测试**

在 `antdAiViewsContract.test.js` 中增加：

```js
assert.doesNotMatch(modelSources, /Model Service|Provider 凭据|Model ID|Model Code|Base Model|Capability/)
assert.doesNotMatch(applicationView, /Developer API|API Key|Embedding|Rerank/)
assert.doesNotMatch(usageView, /Usage Ledger|Chat|Embedding|Rerank|Token|Search Unit/)
assert.match(applicationView, /开发接入/)
assert.match(applicationView, /访问密钥/)
assert.match(usageView, /用量记录/)
```

其中 `modelSources` 为 `modelFiles.map(...).join('\n')`。

- [ ] **步骤 3：运行测试验证失败**

运行：

```bash
node tests/plugins/platformShellContract.test.js
node tests/plugins/antdAiViewsContract.test.js
```

预期：旧导航文案、旧尺寸和英文页面文案导致断言失败。

- [ ] **步骤 4：提交测试基线**

```bash
git add ruoyi-fastapi-frontend/tests/plugins/platformShellContract.test.js ruoyi-fastapi-frontend/tests/plugins/antdAiViewsContract.test.js
git commit -m "test: 建立中文工作台布局契约"
```

### 任务二：实现统一全局骨架

**文件：**
- 修改：`ruoyi-fastapi-frontend/src/assets/styles/workspace.scss`
- 修改：`ruoyi-fastapi-frontend/src/layout/components/PlatformSidebar.vue`
- 修改：`ruoyi-fastapi-frontend/src/layout/components/PlatformTopbar.vue`
- 修改：`ruoyi-fastapi-frontend/src/layout/components/RecentSessions.vue`
- 修改：`ruoyi-fastapi-frontend/src/layout/components/AiResourceShell.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/Workspace/WorkspaceToolbar.vue`

- [ ] **步骤 1：更新主题尺寸和色彩变量**

在 `workspace.scss` 中设置：

```scss
--workspace-main: #2f7e78;
--workspace-main-deep: #226862;
--workspace-main-soft: #e5f3f1;
--workspace-bg: #f5f7f8;
--platform-sidebar-width: 184px;
--platform-sidebar-collapsed-width: 56px;
--platform-topbar-height: 54px;
--platform-menu-item-height: 38px;
```

为 AI 侧栏增加深青灰表面变量，并保持管理后台模式原有可读性。

- [ ] **步骤 2：重写 AI 侧栏导航结构**

`PlatformSidebar.vue` 使用 `resolveAiResourceTabs()` 权限结果生成四个中文入口：

```js
const navItems = computed(() => [
  canChat.value && { path: '/ai/chat', label: '智能对话', icon: MessageOutlined },
  ...resourceTabs.value.map((tab) => ({
    ...tab,
    label: ({ '/ai/model': '模型中心', '/ai/application': '开发接入', '/ai/usage': '用量记录' })[tab.path] || tab.label,
  })),
].filter(Boolean))
```

品牌改为“智衡平台”，管理入口改为“管理后台”。保留权限过滤、最近会话、归档和折叠能力。

- [ ] **步骤 3：把顶栏收敛为单层标题**

`PlatformTopbar.vue` 删除“AI 工作台”副标题和 AI 模式下重复的管理后台文字按钮。顶栏左侧只显示当前路由中文标题，右侧保留主题、消息和用户菜单；管理后台模式仍保留返回 AI 工作台入口。

- [ ] **步骤 4：移除资源总壳重复层级**

`AiResourceShell.vue` 不再渲染“AI 资源”总标题、英文眉题和模型/应用/用量总页签，只负责为资源页面提供统一内容容器。页面切换完全由全局侧栏负责。

- [ ] **步骤 5：调整最近会话和工具栏密度**

最近会话去掉厚重输入框边界和冗余日期格式；`WorkspaceToolbar` 保留 `title` 与 `subtitle` 属性兼容现有调用，但所有本次页面只传中文副标题。标题区域允许移动端换行，主操作不截断。

- [ ] **步骤 6：运行骨架测试验证通过**

运行：

```bash
node tests/plugins/platformShellContract.test.js
```

预期：输出 `ok platform shell contract`。

- [ ] **步骤 7：提交全局骨架**

```bash
git add ruoyi-fastapi-frontend/src/assets/styles/workspace.scss ruoyi-fastapi-frontend/src/layout/components/PlatformSidebar.vue ruoyi-fastapi-frontend/src/layout/components/PlatformTopbar.vue ruoyi-fastapi-frontend/src/layout/components/RecentSessions.vue ruoyi-fastapi-frontend/src/layout/components/AiResourceShell.vue ruoyi-fastapi-frontend/src/components/Workspace/WorkspaceToolbar.vue
git commit -m "feat: 统一中文工作台骨架"
```

### 任务三：改造智能对话和模型中心

**文件：**
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/chat/index.vue`
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/chat/components/AiMessage.vue`
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/model/index.vue`
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/model/components/PlatformModelsPanel.vue`
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/model/components/EngineDeploymentsPanel.vue`
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/model/components/CredentialsPanel.vue`
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/model/components/RuntimeStatusPanel.vue`
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/model/components/ModelOnboardingDrawer.vue`
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/model/components/CredentialForm.vue`
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/model/components/DeploymentForm.vue`

- [ ] **步骤 1：改造对话工作区**

对话页保留既有发送、停止、图片和偏好逻辑，只调整标题、空状态、历史区和输入区尺寸。标题为“智能对话”，模型选择位于右侧；用户与助手标识改为“你”和“智能助手”。

- [ ] **步骤 2：改造模型中心壳层**

模型中心标题说明改为“管理模型绑定、发布状态和计费规则”；高级页签直接常驻显示，不再用“高级管理”按钮控制显隐。页签统一为“平台模型”“引擎部署”“凭据管理”“运行状态”。

- [ ] **步骤 3：中文化模型表格和表单**

统一替换面向用户的标签：

```js
const modelTypeMeta = (type) => ({
  chat: { label: '对话', type: 'primary' },
  embedding: { label: '向量', type: 'success' },
  rerank: { label: '排序', type: 'warning' },
}[type] || { label: type || '-', type: 'info' })
```

表头使用“模型”“模型代码”“类型”“能力”“绑定状态”“发布状态”“更新时间”“操作”；“Provider”显示为“服务商”，“Base Model”显示为“上游模型”，“Model ID”显示为“模型编号”。

- [ ] **步骤 4：实现移动端信息行**

宽度小于 768 像素时隐藏次要列，以模型名称、类型、绑定状态和发布状态为固定顺序；页签允许横向滚动，页面禁止横向滚动。

- [ ] **步骤 5：运行 AI 页面契约测试**

运行：

```bash
node tests/plugins/antdAiViewsContract.test.js
node tests/plugins/modelEngineTransforms.test.js
node tests/plugins/modelOnboardingContract.test.js
node tests/plugins/sseParser.test.js
```

预期：四组测试全部输出 `ok`。

- [ ] **步骤 6：提交对话与模型中心**

```bash
git add ruoyi-fastapi-frontend/plugins/ai/views/chat ruoyi-fastapi-frontend/plugins/ai/views/model
git commit -m "feat: 重做对话与模型中心布局"
```

### 任务四：改造开发接入和用量记录

**文件：**
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/application/index.vue`
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/usage/index.vue`
- 修改：`ruoyi-fastapi-frontend/plugins/ai/utils/application.js`

- [ ] **步骤 1：重排开发接入主从区域**

页面标题改为“开发接入”，左侧应用列表固定 280 至 320 像素，右侧为应用详情与访问密钥。用量不再作为第三张独立嵌套卡片，而是在详情底部使用紧凑调用记录区。

- [ ] **步骤 2：中文化访问密钥交互**

把“Developer API”“API Key”“Key”统一为“开发接入”“访问密钥”“密钥”；RPM、TPM 在说明中分别显示为“每分钟请求数”“每分钟令牌数”，技术值和密钥内容保持原值。

- [ ] **步骤 3：中文化用量记录**

标题使用“用量记录”，调用类型使用“对话”“向量”“排序”，表头使用“令牌”“检索单元”“费用”。日期通过：

```js
new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', hour12: false,
}).format(new Date(value))
```

- [ ] **步骤 4：保留错误与权限行为**

保持现有请求代次、卸载取消、权限守卫和密钥清理逻辑；空状态与错误状态只改中文文案和布局，不改变分支条件。

- [ ] **步骤 5：运行应用契约测试**

运行：

```bash
node tests/plugins/antdAiViewsContract.test.js
node tests/plugins/applicationTransforms.test.js
```

预期：两组测试全部输出 `ok`。

- [ ] **步骤 6：提交开发接入与用量记录**

```bash
git add ruoyi-fastapi-frontend/plugins/ai/views/application/index.vue ruoyi-fastapi-frontend/plugins/ai/views/usage/index.vue ruoyi-fastapi-frontend/plugins/ai/utils/application.js
git commit -m "feat: 重做开发接入与用量记录布局"
```

### 任务五：完整验证、构建与部署

**文件：**
- 修改：`ruoyi-fastapi-frontend/tests/e2e/platform_shell_check.py`
- 修改：`ruoyi-fastapi-frontend/tests/e2e/antd_chat_check.py`
- 生成但不提交：桌面端与移动端截图。

- [ ] **步骤 1：更新真实浏览器断言**

断言全局导航包含“智能对话”“模型中心”“开发接入”“用量记录”，页面不存在“AI RESOURCE”“Model Service”“Developer API”“Usage Ledger”。移动端验证菜单抽屉可打开并访问四个入口。

- [ ] **步骤 2：运行全量插件测试**

运行：

```bash
npm run test:plugin
```

预期：全部插件测试组通过，无失败。

- [ ] **步骤 3：运行生产构建**

运行：

```bash
NODE_OPTIONS=--max-old-space-size=4096 npm run build:docker
```

预期：Vite 构建退出码为 0。

- [ ] **步骤 4：构建可回滚前端镜像**

先给当前镜像添加回滚标签，再将构建产物制作成新镜像并更新 `ruoyi-frontend:latest`。只重建 `ruoyi-frontend` 容器，不重启数据库、Redis、LiteLLM 和后端。

- [ ] **步骤 5：执行真实浏览器验收**

运行桌面端 1440×900、移动端 390×844 的登录与核心页面脚本，并验证：

```text
document.documentElement.scrollWidth <= window.innerWidth
页面无控制台错误
核心接口无四百或五百响应
流式对话返回二百且助手回复非空
```

- [ ] **步骤 6：人工检查截图**

检查侧栏、标题、表格、输入区、页签和移动抽屉无重叠、截断或异常留白。

- [ ] **步骤 7：提交验收脚本**

```bash
git add ruoyi-fastapi-frontend/tests/e2e/platform_shell_check.py ruoyi-fastapi-frontend/tests/e2e/antd_chat_check.py
git commit -m "test: 验证统一工作台响应式布局"
```
