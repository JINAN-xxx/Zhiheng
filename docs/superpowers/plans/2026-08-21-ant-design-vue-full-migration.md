# 智衡 AI Ant Design Vue 全量迁移实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将智衡 AI 前端完整迁移到 Ant Design Vue，使预设稿成为实际交付界面，并在最终产物中彻底移除 Element Plus。

**架构：** 保留 Vue Router、Pinia、Axios、动态路由、权限码和后端契约，新增统一的 Ant Design Vue 主题入口与平台业务组件。迁移在独立分支中按域完成并逐步验证，双组件库只允许存在于内部迁移阶段；全部页面完成后删除 Element Plus，一次合并完整结果。

**技术栈：** Vue 3.5、Vite 6、Pinia 3、Vue Router 4、Ant Design Vue 4、Ant Design Icons、Axios、Python Playwright、Node contract tests

---

## 执行前约束

- 基线分支：`feat-litellm-deployment`，至少包含提交 `8254837`。
- 不在当前含 `ruoyi-fastapi-app` 未提交删除的工作区直接实施。
- 使用 `using-git-worktrees` 创建 `feat/antd-full-migration` 独立工作树。
- `ruoyi-fastapi-app` 不属于本计划，不删除、不恢复、不迁移。
- 不改变后端 API、数据库、权限码、SSE 协议或业务状态机。
- 每个任务完成后提交；未完成全量迁移前不得部署为用户验收版本。
- 视觉基线来自规格和现有 `/tmp/yuxi-*.png` 截图；执行者应把稳定基线复制到仓库测试快照目录，不依赖 `/tmp` 长期存在。

## 文件结构决策

### 新建设计系统

- `ruoyi-fastapi-frontend/src/design/antdTheme.js`：亮色、暗色和组件 Token。
- `ruoyi-fastapi-frontend/src/design/AntdRoot.vue`：`ConfigProvider`、语言和主题入口。
- `ruoyi-fastapi-frontend/src/design/useAntdTheme.js`：现有主题状态到 Ant Design 算法的映射。
- `ruoyi-fastapi-frontend/src/assets/styles/antd-reset.scss`：仅包含平台级 reset 和布局变量。
- `ruoyi-fastapi-frontend/src/components/Antd/WorkspacePage.vue`：页面标题、说明和工具条。
- `ruoyi-fastapi-frontend/src/components/Antd/AsyncState.vue`：加载、空、失败、过期和误配置状态。
- `ruoyi-fastapi-frontend/src/components/Antd/ResourceTable.vue`：资源列表与移动列策略。
- `ruoyi-fastapi-frontend/src/components/Antd/ResourceDrawer.vue`：创建、编辑和详情抽屉。
- `ruoyi-fastapi-frontend/src/components/Antd/PermissionAction.vue`：权限和运行时门禁。
- `ruoyi-fastapi-frontend/src/components/Antd/SecretField.vue`：秘密输入与一次性展示边界。
- `ruoyi-fastapi-frontend/src/components/Antd/index.js`：设计系统公开出口。

### 新建测试

- `ruoyi-fastapi-frontend/tests/plugins/antdThemeContract.test.js`
- `ruoyi-fastapi-frontend/tests/plugins/antdComponentsContract.test.js`
- `ruoyi-fastapi-frontend/tests/plugins/antdGlobalServicesContract.test.js`
- `ruoyi-fastapi-frontend/tests/plugins/antdAuthShellContract.test.js`
- `ruoyi-fastapi-frontend/tests/plugins/antdAiViewsContract.test.js`
- `ruoyi-fastapi-frontend/tests/plugins/antdAdminViewsContract.test.js`
- `ruoyi-fastapi-frontend/tests/plugins/elementPlusRemovalContract.test.js`
- `ruoyi-fastapi-frontend/tests/e2e/antd_full_migration_check.py`
- `ruoyi-fastapi-frontend/tests/e2e/baselines/`：固定视觉基线截图。

## 任务 1：建立独立迁移工作树和 Element Plus 清单门禁

**文件：**
- 创建：`ruoyi-fastapi-frontend/tests/plugins/elementPlusRemovalContract.test.js`
- 修改：`ruoyi-fastapi-frontend/tests/plugins/run-plugin-tests.js`

- [ ] **步骤 1：创建独立工作树**

运行：

```bash
git worktree add ../RuoYi-Vue3-FastAPI-antd -b feat/antd-full-migration feat-litellm-deployment
cd ../RuoYi-Vue3-FastAPI-antd
```

预期：新工作树干净，`git status --short` 无输出；原工作区的 App 删除不出现在新工作树。

- [ ] **步骤 2：编写当前 Element Plus 清单测试**

测试读取 `src` 与 `plugins/ai` 下的 `.vue/.js` 文件，匹配以下模式：

```js
const elementPatterns = [
  /from ['"]element-plus['"]/,
  /@element-plus\/icons-vue/,
  /<el-[a-z-]+/,
  /--el-[a-z-]+/,
]
```

第一阶段断言扫描器能发现 `src/main.js`、`src/plugins/modal.js` 和 `plugins/ai/views/chat/index.vue`，并输出确定性排序列表。最终任务会把断言改成零匹配。

- [ ] **步骤 3：运行测试确认清单有效**

运行：

```bash
cd ruoyi-fastapi-frontend
node tests/plugins/elementPlusRemovalContract.test.js
```

预期：PASS，并打印非零 Element Plus 文件计数；若计数为零，测试必须失败，说明扫描器失效。

- [ ] **步骤 4：接入插件测试入口**

在 `run-plugin-tests.js` 的确定性测试列表中加入 `elementPlusRemovalContract.test.js`，保证后续每次 `npm run test:plugin` 都执行清单门禁。

- [ ] **步骤 5：提交**

```bash
git add ruoyi-fastapi-frontend/tests/plugins/elementPlusRemovalContract.test.js ruoyi-fastapi-frontend/tests/plugins/run-plugin-tests.js
git commit -m "test: 建立 Element Plus 迁移清单"
```

## 任务 2：建立 Ant Design Vue 主题根节点

**文件：**
- 创建：`ruoyi-fastapi-frontend/src/design/antdTheme.js`
- 创建：`ruoyi-fastapi-frontend/src/design/AntdRoot.vue`
- 创建：`ruoyi-fastapi-frontend/src/design/useAntdTheme.js`
- 创建：`ruoyi-fastapi-frontend/src/assets/styles/antd-reset.scss`
- 创建：`ruoyi-fastapi-frontend/tests/plugins/antdThemeContract.test.js`
- 修改：`ruoyi-fastapi-frontend/src/App.vue`
- 修改：`ruoyi-fastapi-frontend/src/main.js`
- 修改：`ruoyi-fastapi-frontend/src/utils/theme.js`

- [ ] **步骤 1：编写主题契约红灯**

测试直接导入 `antdTheme.js`，断言：

```js
assert.equal(lightTheme.token.colorPrimary, '#3996AE')
assert.equal(lightTheme.token.borderRadius, 4)
assert.equal(lightTheme.token.borderRadiusLG, 8)
assert.equal(lightTheme.token.colorBgLayout, '#F5F7F7')
assert.equal(darkTheme.token.colorBgLayout, '#0C0D0D')
assert.equal(componentSize, 'middle')
```

同时静态断言 `AntdRoot.vue` 使用 `a-config-provider`、`zh_CN`、主题算法和弹层容器，不包含硬编码用户数据。

- [ ] **步骤 2：运行红灯**

运行：`node tests/plugins/antdThemeContract.test.js`

预期：FAIL，提示 `src/design/antdTheme.js` 不存在。

- [ ] **步骤 3：实现主题对象和根节点**

`antdTheme.js` 提供稳定接口：

```js
export const componentSize = 'middle'
export const lightTheme = {
  token: {
    colorPrimary: '#3996AE',
    colorBgLayout: '#F5F7F7',
    colorBgContainer: '#FFFFFF',
    colorText: '#151616',
    borderRadius: 4,
    borderRadiusLG: 8,
    controlHeight: 36,
  },
}
```

`AntdRoot.vue` 包裹 `<router-view />`，从现有 settings store 读取暗色状态，通过 `ConfigProvider` 切换 `theme.darkAlgorithm`。`utils/theme.js` 停止写 Element CSS variables，只负责设置平台主题属性和 Ant Design 算法状态。`main.js` 注册 Ant Design Vue，但此阶段暂时保留 Element Plus，以便后续逐页迁移。

- [ ] **步骤 4：运行主题测试和生产构建**

运行：

```bash
npm run test:plugin
NODE_OPTIONS=--max-old-space-size=4096 npm run build:docker
```

预期：测试通过；构建退出码 0；页面仍可启动。

- [ ] **步骤 5：提交**

```bash
git add ruoyi-fastapi-frontend/src/design ruoyi-fastapi-frontend/src/assets/styles/antd-reset.scss ruoyi-fastapi-frontend/src/App.vue ruoyi-fastapi-frontend/src/main.js ruoyi-fastapi-frontend/src/utils/theme.js ruoyi-fastapi-frontend/tests/plugins/antdThemeContract.test.js
git commit -m "feat: 建立 Ant Design Vue 主题入口"
```

## 任务 3：建立平台业务组件

**文件：**
- 创建：`ruoyi-fastapi-frontend/src/components/Antd/WorkspacePage.vue`
- 创建：`ruoyi-fastapi-frontend/src/components/Antd/AsyncState.vue`
- 创建：`ruoyi-fastapi-frontend/src/components/Antd/ResourceTable.vue`
- 创建：`ruoyi-fastapi-frontend/src/components/Antd/ResourceDrawer.vue`
- 创建：`ruoyi-fastapi-frontend/src/components/Antd/PermissionAction.vue`
- 创建：`ruoyi-fastapi-frontend/src/components/Antd/SecretField.vue`
- 创建：`ruoyi-fastapi-frontend/src/components/Antd/PlatformIcon.vue`
- 创建：`ruoyi-fastapi-frontend/src/components/Antd/index.js`
- 创建：`ruoyi-fastapi-frontend/tests/plugins/antdComponentsContract.test.js`

- [ ] **步骤 1：编写组件契约红灯**

静态与纯函数测试必须覆盖：

```js
assert.deepEqual(resolveAsyncState({ loading: true, rows: [] }), { kind: 'loading' })
assert.deepEqual(resolveAsyncState({ error: 'x', rows: [] }), { kind: 'error' })
assert.deepEqual(resolveAsyncState({ error: 'x', rows: [1] }), { kind: 'stale' })
assert.equal(canRunAction(['ai:model:edit'], ['ai:model:edit'], false), true)
assert.equal(canRunAction(['ai:model:edit'], ['ai:model:edit'], true), false)
assert.equal(sanitizeSecretDefault('api_key', 'secret'), null)
assert.equal(resolvePlatformIcon('unknown-icon'), 'QuestionOutlined')
```

静态断言组件只 import Ant Design Vue/Ant Design Icons，不 import Element Plus。

- [ ] **步骤 2：运行红灯**

运行：`node tests/plugins/antdComponentsContract.test.js`

预期：FAIL，提示组件或导出不存在。

- [ ] **步骤 3：实现最小组件接口**

`AsyncState.vue` 使用具名 slot：`loading`、`empty`、`error`、`stale`、`default`。`PermissionAction.vue` 同时控制渲染、disabled 和点击 handler。`SecretField.vue` 不接收秘密默认值，密码输入默认关闭浏览器自动填充。

`ResourceTable.vue` 的列配置使用：

```js
{
  key: 'provider',
  title: 'Provider',
  priority: 'secondary',
  mobileHidden: true,
}
```

移动端由列元数据决定隐藏，不通过 CSS 猜测列顺序。

`PlatformIcon.vue` 将现有动态菜单字符串映射为 Ant Design Icons。未知名称固定回退 `QuestionOutlined`，不执行动态组件名或任意 HTML。

- [ ] **步骤 4：运行测试和 Story Harness**

运行：

```bash
npm run test:plugin
npm run dev -- --host 127.0.0.1 --port 15175
```

使用 Playwright 打开测试路由或组件宿主页，确认亮暗主题、抽屉、错误态和 320px 无溢出。

- [ ] **步骤 5：提交**

```bash
git add ruoyi-fastapi-frontend/src/components/Antd ruoyi-fastapi-frontend/tests/plugins/antdComponentsContract.test.js
git commit -m "feat: 建立 Ant Design Vue 平台组件"
```

## 任务 4：迁移全局消息、确认、Loading 和下载服务

**文件：**
- 创建：`ruoyi-fastapi-frontend/src/plugins/antdFeedback.js`
- 创建：`ruoyi-fastapi-frontend/tests/plugins/antdGlobalServicesContract.test.js`
- 修改：`ruoyi-fastapi-frontend/src/plugins/modal.js`
- 修改：`ruoyi-fastapi-frontend/src/plugins/download.js`
- 修改：`ruoyi-fastapi-frontend/src/utils/request.js`
- 修改：`ruoyi-fastapi-frontend/src/permission.js`
- 修改：`ruoyi-fastapi-frontend/src/store/modules/user.js`
- 修改：`ruoyi-fastapi-frontend/src/plugins/index.js`

- [ ] **步骤 1：编写全局服务红灯**

测试锁定现有公开调用兼容：

```js
assert.equal(typeof feedback.msgSuccess, 'function')
assert.equal(typeof feedback.confirm, 'function')
assert.equal(typeof feedback.loading, 'function')
assert.equal(typeof feedback.closeLoading, 'function')
```

并扫描上述生产文件，断言不再 import `ElMessage`、`ElNotification`、`ElMessageBox` 或 `ElLoading`。

- [ ] **步骤 2：运行红灯**

运行：`node tests/plugins/antdGlobalServicesContract.test.js`

预期：FAIL，显示 Element Plus imports。

- [ ] **步骤 3：实现 Ant Design feedback 适配**

使用 Ant Design Vue `message`、`notification`、`Modal`、`Spin`。保留 `proxy.$modal.msgSuccess()` 等现有公开名称，避免同时修改全部业务调用。`confirm()` 必须返回 Promise，并将取消映射为 reject，保持现有 handler 语义。

请求层 401 重登录确认、下载 Loading、路由权限提示和退出确认全部改用适配器。

- [ ] **步骤 4：运行请求与权限回归**

运行：

```bash
npm run test:plugin
NODE_OPTIONS=--max-old-space-size=4096 npm run build:docker
```

预期：全绿；源码扫描中全局服务无 Element Plus import。

- [ ] **步骤 5：提交**

```bash
git add ruoyi-fastapi-frontend/src/plugins ruoyi-fastapi-frontend/src/utils/request.js ruoyi-fastapi-frontend/src/permission.js ruoyi-fastapi-frontend/src/store/modules/user.js ruoyi-fastapi-frontend/tests/plugins/antdGlobalServicesContract.test.js
git commit -m "refactor: 迁移全局反馈与请求交互"
```

## 任务 5：重做登录、注册、锁屏和错误页

**文件：**
- 创建：`ruoyi-fastapi-frontend/tests/plugins/antdAuthShellContract.test.js`
- 修改：`ruoyi-fastapi-frontend/src/views/login.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/register.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/lock.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/error/401.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/error/404.vue`

- [ ] **步骤 1：编写认证页面红灯**

静态测试断言登录页包含 `a-form`、`a-input`、`a-input-password`、`a-checkbox`、`a-button`，不包含 `<el-`；验证码、记住密码和重定向方法仍存在。CSS 不引用 `login-background.jpg`，认证区在 320px 下使用 `width: min(100%, 420px)`。

- [ ] **步骤 2：运行红灯**

运行：`node tests/plugins/antdAuthShellContract.test.js`

预期：FAIL，显示 `<el-form>` 和旧背景图引用。

- [ ] **步骤 3：实现认证视觉与交互**

登录页采用单一全宽背景表面和居中认证区，品牌位于左上或表单上方，不使用照片分屏。表单提交继续调用现有 user store；验证码刷新、Cookie 记忆和 redirect query 逻辑不变。

注册、锁屏和错误页复用同一认证/状态布局，禁止复制另一套 Token。

- [ ] **步骤 4：浏览器验证认证流程**

Playwright 以真实 `captchaImage` 接口打开 1440、390、320 三视口，断言：

```python
assert page.title() == "智衡 AI"
assert page.locator(".auth-shell").count() == 1
assert document_width <= viewport_width
assert console_errors == []
```

不提交真实账号、密码或验证码。

- [ ] **步骤 5：提交**

```bash
git add ruoyi-fastapi-frontend/src/views/login.vue ruoyi-fastapi-frontend/src/views/register.vue ruoyi-fastapi-frontend/src/views/lock.vue ruoyi-fastapi-frontend/src/views/error ruoyi-fastapi-frontend/tests/plugins/antdAuthShellContract.test.js
git commit -m "feat: 重做 Ant Design 认证界面"
```

## 任务 6：迁移全局应用外壳和导航

**文件：**
- 修改：`ruoyi-fastapi-frontend/src/layout/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/layout/components/PlatformSidebar.vue`
- 修改：`ruoyi-fastapi-frontend/src/layout/components/PlatformTopbar.vue`
- 修改：`ruoyi-fastapi-frontend/src/layout/components/AdminSidebar.vue`
- 修改：`ruoyi-fastapi-frontend/src/layout/components/RecentSessions.vue`
- 修改：`ruoyi-fastapi-frontend/src/layout/components/AiResourceShell.vue`
- 修改：`ruoyi-fastapi-frontend/src/layout/components/AppMain.vue`
- 修改：`ruoyi-fastapi-frontend/src/layout/components/HeaderNotice/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/layout/components/HeaderNotice/DetailView.vue`
- 修改：`ruoyi-fastapi-frontend/src/layout/components/TagsView/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/layout/components/TagsView/ScrollPane.vue`
- 修改：`ruoyi-fastapi-frontend/src/assets/styles/workspace.scss`
- 修改：`ruoyi-fastapi-frontend/tests/plugins/platformShellContract.test.js`

- [ ] **步骤 1：扩展外壳红灯**

测试断言所有目标 layout 文件无 `<el-`；移动侧栏使用 `a-drawer`；通知使用 `a-popover`/`a-list`；用户菜单使用 `a-dropdown`；当前路由使用 `aria-current="page"`；AI 模式不渲染旧面包屑和旧 TagsView。

- [ ] **步骤 2：运行红灯**

运行：`node tests/plugins/platformShellContract.test.js`

预期：FAIL，列出残余 Element Plus 标签。

- [ ] **步骤 3：实现统一外壳**

保留现有 route workspace resolver 和动态管理菜单数据。桌面侧栏宽 230px/56px，顶栏 48px；移动端使用 Ant Drawer，路由切换自动关闭并恢复菜单按钮焦点。

管理控制台仍显示动态菜单和紧凑标签页，但与 AI 模式共享 Token、顶栏和用户菜单。

- [ ] **步骤 4：运行导航 Playwright**

运行现有 `tests/e2e/platform_shell_check.py`，并更新定位器为 Ant Design DOM。验证 1440/1024/390/320 下 AI/管理往返、刷新、浏览器返回、抽屉和折叠。

- [ ] **步骤 5：提交**

```bash
git add ruoyi-fastapi-frontend/src/layout ruoyi-fastapi-frontend/src/assets/styles/workspace.scss ruoyi-fastapi-frontend/tests/plugins/platformShellContract.test.js ruoyi-fastapi-frontend/tests/e2e/platform_shell_check.py
git commit -m "feat: 迁移 Ant Design 全局应用外壳"
```

## 任务 7：迁移 AI 对话和会话历史

**文件：**
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/chat/index.vue`
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/chat/components/AiMessage.vue`
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/chat/components/ChatSessionPanel.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/Workspace/WorkspaceSidebar.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/Workspace/WorkspaceMoreMenu.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/Workspace/WorkspaceState.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/Workspace/WorkspaceToolbar.vue`
- 修改：`ruoyi-fastapi-frontend/tests/plugins/sseParser.test.js`
- 修改：`ruoyi-fastapi-frontend/tests/plugins/workspaceTransforms.test.js`
- 创建：`ruoyi-fastapi-frontend/tests/plugins/antdAiViewsContract.test.js`

- [ ] **步骤 1：编写对话迁移红灯**

测试断言聊天文件无 `<el-`，停止按钮在新请求开始时不能使用旧 `runId`：

```js
const state = beginSend({ runId: 'run-old', status: 'done' })
assert.equal(state.runId, null)
assert.equal(state.status, 'connecting')
```

会话选择和更多菜单必须是兄弟原生交互元素，不允许 button 嵌套。

- [ ] **步骤 2：运行红灯**

运行：

```bash
node tests/plugins/antdAiViewsContract.test.js
npm run test:plugin
```

预期：新增测试因 Element 标签失败；现有 SSE 测试保持通过。

- [ ] **步骤 3：实现连续消息流和底部输入区**

使用 Ant Select、Dropdown、Tooltip、Upload 和 Button。消息内容组件继续使用现有 Markdown/流式渲染器；用户与 AI 消息不嵌套装饰卡片。输入区稳定固定在工作区底部，发送、停止和附件按钮不改变布局尺寸。

保留请求代次、活动异常优先、SSE 完成/失败/取消语义和历史会话加载边界。

- [ ] **步骤 4：运行流式浏览器回归**

Mock SSE 覆盖 meta、delta、usage、done、503、无 meta 停止和历史加载；断言旧 run ID 不进入新 cancel body。真实后端覆盖至少一次普通流式回复。

- [ ] **步骤 5：提交**

```bash
git add ruoyi-fastapi-frontend/plugins/ai/views/chat ruoyi-fastapi-frontend/src/components/Workspace ruoyi-fastapi-frontend/tests/plugins/antdAiViewsContract.test.js ruoyi-fastapi-frontend/tests/plugins/sseParser.test.js ruoyi-fastapi-frontend/tests/plugins/workspaceTransforms.test.js
git commit -m "feat: 迁移 Ant Design AI 对话工作台"
```

## 任务 8：迁移模型中心与 Chat/Embedding/Rerank 接入

**文件：**
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/model/index.vue`
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/model/components/PlatformModelsPanel.vue`
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/model/components/EngineDeploymentsPanel.vue`
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/model/components/CredentialsPanel.vue`
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/model/components/RuntimeStatusPanel.vue`
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/model/components/ModelOnboardingDrawer.vue`
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/model/components/CredentialForm.vue`
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/model/components/DeploymentForm.vue`
- 修改：`ruoyi-fastapi-frontend/tests/plugins/modelEngineTransforms.test.js`
- 修改：`ruoyi-fastapi-frontend/tests/plugins/modelOnboardingContract.test.js`

- [ ] **步骤 1：编写模型页红灯**

扩展契约：所有模型组件无 `<el-`；能力筛选支持 `chat`、`embedding`、`rerank`；秘密字段使用 `SecretField`；配置缺失门禁在刷新 pending 期间保持；移动操作使用 Dropdown。

- [ ] **步骤 2：运行红灯**

运行：

```bash
node tests/plugins/modelEngineTransforms.test.js
node tests/plugins/modelOnboardingContract.test.js
node tests/plugins/antdAiViewsContract.test.js
```

预期：Ant 组件契约失败，原业务转换测试通过。

- [ ] **步骤 3：按资源视图迁移**

使用 `WorkspacePage`、`ResourceTable`、`ResourceDrawer`、`AsyncState` 和 `PermissionAction`。保留：

- Platform 分页末页回退。
- query 权限才加载详情。
- stale 数据保留。
- Management misconfigured 门禁。
- Provider display/canonical 双标识。
- 凭据秘密不回填。
- 部署精确 reconciliation。
- active Binding 才允许发布。
- Chat/Embedding/Rerank 能力与烟雾测试路径。

- [ ] **步骤 4：浏览器验证模型接入**

覆盖模型列表、能力筛选、凭据创建、部署、连接测试、Binding、发布、Embedding/Rerank 模型和 320px 操作菜单；console/page error 为空。

- [ ] **步骤 5：提交**

```bash
git add ruoyi-fastapi-frontend/plugins/ai/views/model ruoyi-fastapi-frontend/tests/plugins/modelEngineTransforms.test.js ruoyi-fastapi-frontend/tests/plugins/modelOnboardingContract.test.js
git commit -m "feat: 迁移 Ant Design 模型中心"
```

## 任务 9：迁移应用接入、Developer API Key 和用量

**文件：**
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/application/index.vue`
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/usage/index.vue`
- 修改：`ruoyi-fastapi-frontend/tests/plugins/applicationTransforms.test.js`
- 修改：`ruoyi-fastapi-frontend/tests/plugins/antdAiViewsContract.test.js`

- [ ] **步骤 1：编写应用页红灯**

断言应用与用量页面无 `<el-`；创建 Key 必须同时满足 `ai:application:key:add` 和 `ai:model:list`；`warningRatio`、`hardLimitRatio` 仍发送字符串；一次性 Key 关闭后从 DOM 和状态中清除。

- [ ] **步骤 2：运行红灯**

运行：

```bash
node tests/plugins/applicationTransforms.test.js
node tests/plugins/antdAiViewsContract.test.js
```

预期：Ant 迁移断言失败；安全转换测试保持通过。

- [ ] **步骤 3：实现统一应用资源视图**

应用列表使用 `ResourceTable`；Key、授权模型和用量使用同一详情上下文，不嵌套卡片。创建和轮换结果使用专用一次性秘密 Modal，关闭时同步清空完整 Key。

用量页保留应用选择、时间筛选、摘要和调用台账，缺少权限时不发请求。

- [ ] **步骤 4：运行 Developer API 浏览器回归**

覆盖应用创建、Key 创建/轮换/吊销、模型授权、缺权限、stale 刷新和用量查询；扫描 URL、Storage 和 console 不含完整 Key。

- [ ] **步骤 5：提交**

```bash
git add ruoyi-fastapi-frontend/plugins/ai/views/application ruoyi-fastapi-frontend/plugins/ai/views/usage ruoyi-fastapi-frontend/tests/plugins/applicationTransforms.test.js ruoyi-fastapi-frontend/tests/plugins/antdAiViewsContract.test.js
git commit -m "feat: 迁移 Ant Design 应用与用量页面"
```

## 任务 10：迁移高频管理页面

**文件：**
- 创建：`ruoyi-fastapi-frontend/tests/plugins/antdAdminViewsContract.test.js`
- 修改：`ruoyi-fastapi-frontend/src/views/system/user/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/user/view.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/user/authRole.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/user/profile/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/user/profile/userInfo.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/user/profile/resetPwd.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/user/profile/userAvatar.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/role/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/role/authUser.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/role/selectUser.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/menu/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/dept/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/post/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/config/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/dict/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/dict/data.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/dict/detail.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/notice/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/notice/ReadUsers.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/dashboard/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/dashboard/editable-link-group.vue`

- [ ] **步骤 1：编写高频管理页红灯**

测试扫描上述精确文件，断言没有 Element imports/tags/CSS variables；每个列表页面必须使用 `WorkspacePage` 或 `ResourceTable`；权限码字符串集合与迁移前快照一致。

- [ ] **步骤 2：运行红灯**

运行：`node tests/plugins/antdAdminViewsContract.test.js`

预期：FAIL，输出仍含 Element 的文件列表。

- [ ] **步骤 3：迁移查询、表格和编辑交互**

使用 Ant Form、Table、Tree、Drawer、Modal、Dropdown 和 Pagination。保留现有 API 函数、query 参数、selection keys、树转换和 `v-hasPermi` 权限码。

每迁移一个资源组立即运行构建，不对全部文件做无审查的全局替换。

- [ ] **步骤 4：浏览器验证代表页面**

验证用户、角色、菜单、字典和配置：查询、重置、分页、新增、编辑、删除确认、权限隐藏、树选择和 320px 更多菜单。

- [ ] **步骤 5：提交**

```bash
git add ruoyi-fastapi-frontend/src/views/system/user ruoyi-fastapi-frontend/src/views/system/role ruoyi-fastapi-frontend/src/views/system/menu ruoyi-fastapi-frontend/src/views/system/dept ruoyi-fastapi-frontend/src/views/system/post ruoyi-fastapi-frontend/src/views/system/config ruoyi-fastapi-frontend/src/views/system/dict ruoyi-fastapi-frontend/src/views/system/notice ruoyi-fastapi-frontend/tests/plugins/antdAdminViewsContract.test.js
git commit -m "feat: 迁移 Ant Design 高频管理页面"
```

## 任务 11：迁移文件、插件、审计和监控页面

**文件：**
- 修改：`ruoyi-fastapi-frontend/src/views/system/file/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/file/components/FileAclDrawer.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/file/components/FileAuditDrawer.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/file/components/FileDetailDialog.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/file/components/FileReconcileDrawer.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/file/components/FileReferenceDrawer.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/file/components/FileRetentionPolicyDrawer.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/file/components/FileRetentionReminderDrawer.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/file/components/FileSearchForm.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/file/components/FileStatistics.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/file/components/FileTable.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/file/components/FileTransferDialog.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/plugin/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/plugin/components/PluginConfigDialog.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/plugin/components/PluginDependencyDialog.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/plugin/components/PluginDetailDialog.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/plugin/components/PluginDiagnosticDialog.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/system/plugin/components/PluginPlanDialog.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/monitor/cache/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/monitor/cache/list.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/monitor/job/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/monitor/job/detail.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/monitor/job/log.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/monitor/logininfor/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/monitor/online/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/monitor/operlog/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/monitor/operlog/detail.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/monitor/server/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/monitor/transportCrypto/index.vue`
- 修改：`ruoyi-fastapi-frontend/tests/plugins/antdAdminViewsContract.test.js`

- [ ] **步骤 1：扩展管理页扫描红灯**

把上述精确文件加入 `antdAdminViewsContract.test.js`，断言无 Element 使用，文件和插件抽屉必须使用 `ResourceDrawer`，监控列表使用统一状态与分页。

- [ ] **步骤 2：运行红灯**

运行：`node tests/plugins/antdAdminViewsContract.test.js`

预期：FAIL，列出此任务尚未迁移的文件。

- [ ] **步骤 3：按域迁移**

文件域保留上传、ACL、引用、保留策略和审计状态；插件域保留依赖、配置、诊断和计划；监控域保留刷新、筛选、日志详情和安全操作确认。所有 API 和权限码不变。

- [ ] **步骤 4：运行代表性集成验证**

Playwright 覆盖文件搜索/详情、插件详情/配置、操作日志和在线用户。断言首次失败、stale、空态和权限隐藏可区分。

- [ ] **步骤 5：提交**

```bash
git add ruoyi-fastapi-frontend/src/views/system/file ruoyi-fastapi-frontend/src/views/system/plugin ruoyi-fastapi-frontend/src/views/monitor ruoyi-fastapi-frontend/tests/plugins/antdAdminViewsContract.test.js
git commit -m "feat: 迁移 Ant Design 文件插件与监控页面"
```

## 任务 12：迁移代码生成和剩余公共组件

**文件：**
- 修改：`ruoyi-fastapi-frontend/src/views/tool/build/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/tool/build/CodeTypeDialog.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/tool/build/DraggableItem.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/tool/build/IconsDialog.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/tool/build/RightPanel.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/tool/build/TreeNodeDialog.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/tool/gen/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/tool/gen/createTable.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/tool/gen/editTable.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/tool/gen/importTable.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/tool/gen/basicInfoForm.vue`
- 修改：`ruoyi-fastapi-frontend/src/views/tool/gen/genInfoForm.vue`
- 修改：`ruoyi-fastapi-frontend/src/utils/generator/config.js`
- 修改：`ruoyi-fastapi-frontend/src/utils/generator/html.js`
- 修改：`ruoyi-fastapi-frontend/src/components/BusinessFileUpload/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/Crontab/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/Crontab/result.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/Crontab/second.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/Crontab/min.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/Crontab/hour.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/Crontab/day.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/Crontab/month.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/Crontab/week.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/Crontab/year.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/DictTag/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/Editor/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/ExcelImportDialog/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/FileUpload/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/HeaderSearch/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/IconSelect/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/ImagePreview/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/ImageUpload/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/Pagination/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/RightToolbar/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/TreePanel/index.vue`
- 修改：`ruoyi-fastapi-frontend/tests/plugins/antdAdminViewsContract.test.js`

- [ ] **步骤 1：扩展剩余组件红灯**

扫描任务 1 清单中的剩余 Element 文件，按“代码生成页面”和“公共组件”两组断言。测试必须输出精确路径，防止漏迁移。

- [ ] **步骤 2：运行红灯**

运行：

```bash
node tests/plugins/antdAdminViewsContract.test.js
node tests/plugins/elementPlusRemovalContract.test.js
```

预期：管理契约失败；清单计数继续下降但仍非零。

- [ ] **步骤 3：迁移复杂公共控件**

上传组件映射 Ant Upload 的 `fileList` 与现有后端响应；Cron 和代码生成表单保持现有 schema；Editor 只替换外围表单与弹窗，不改变 Quill 数据格式；分页继续接收现有 `total/page/pageSize` props 并发出 `pagination` 事件。

- [ ] **步骤 4：运行管理与生成器回归**

验证生成器表单、Cron 结果、上传、图片预览、字典标签和分页。构建必须通过，无 Vue runtime warning。

- [ ] **步骤 5：提交**

```bash
git add ruoyi-fastapi-frontend/src/views/tool ruoyi-fastapi-frontend/src/components ruoyi-fastapi-frontend/tests/plugins/antdAdminViewsContract.test.js
git commit -m "feat: 迁移 Ant Design 工具与公共组件"
```

## 任务 13：移除旧布局兼容层和 Element Plus

**文件：**
- 修改：`ruoyi-fastapi-frontend/src/main.js`
- 修改：`ruoyi-fastapi-frontend/package.json`
- 修改：`ruoyi-fastapi-frontend/src/components/SvgIcon/index.vue`
- 修改：`ruoyi-fastapi-frontend/src/components/SvgIcon/svgicon.js`
- 删除：`ruoyi-fastapi-frontend/src/layout/components/Navbar.vue`
- 删除：`ruoyi-fastapi-frontend/src/layout/components/Sidebar/index.vue`
- 删除：`ruoyi-fastapi-frontend/src/layout/components/Sidebar/SidebarItem.vue`
- 删除：`ruoyi-fastapi-frontend/src/layout/components/Sidebar/Link.vue`
- 删除：`ruoyi-fastapi-frontend/src/layout/components/Sidebar/Logo.vue`
- 删除：`ruoyi-fastapi-frontend/src/layout/components/TopBar/index.vue`
- 删除：`ruoyi-fastapi-frontend/src/layout/components/TopNav/index.vue`
- 删除：`ruoyi-fastapi-frontend/src/layout/components/Settings/index.vue`
- 删除：`ruoyi-fastapi-frontend/src/components/Breadcrumb/index.vue`
- 删除：`ruoyi-fastapi-frontend/src/components/SizeSelect/index.vue`
- 修改：`ruoyi-fastapi-frontend/tests/plugins/elementPlusRemovalContract.test.js`

- [ ] **步骤 1：把清单测试改为最终零引用**

最终断言：

```js
assert.deepEqual(scanElementPlusReferences(projectRoot), [])
assert.equal(packageJson.dependencies['element-plus'], undefined)
assert.equal(packageJson.dependencies['@element-plus/icons-vue'], undefined)
```

生产构建产物扫描不得出现 `element-plus` 或 `el-button` 特征字符串。

- [ ] **步骤 2：运行红灯**

运行：`node tests/plugins/elementPlusRemovalContract.test.js`

预期：FAIL，列出最后的注册、依赖或旧组件引用。

- [ ] **步骤 3：删除 Element Plus 入口和死代码**

从 `main.js` 删除 Element Plus imports、CSS、locale、icons 和 `app.use(ElementPlus)`。从 `package.json` 删除两项依赖。`SvgIcon` 保留本地 SVG sprite 能力，并把 Element icon fallback 改为任务 3 的 `PlatformIcon` 映射。逐个运行 `rg` 确认上述旧布局文件无 import 后删除；任一文件仍有调用时先把调用迁移到 `PlatformShell` 对应组件，再执行删除。

- [ ] **步骤 4：重新安装并验证产物**

运行：

```bash
npm install --package-lock=false
npm run test:plugin
NODE_OPTIONS=--max-old-space-size=4096 npm run build:docker
rg -n "element-plus|@element-plus/icons-vue|<el-|--el-" src plugins/ai package.json
rg -a -n "element-plus|el-button" dist
```

预期：测试和构建通过；两个 `rg` 命令均无输出。`package.json` 不再含 Element Plus。

- [ ] **步骤 5：提交**

```bash
git add -A ruoyi-fastapi-frontend/src ruoyi-fastapi-frontend/plugins/ai ruoyi-fastapi-frontend/package.json ruoyi-fastapi-frontend/tests/plugins/elementPlusRemovalContract.test.js
git commit -m "refactor: 完成 Ant Design Vue 单栈切换"
```

## 任务 14：建立全页面视觉基线和真实联调验收

**文件：**
- 创建：`ruoyi-fastapi-frontend/tests/e2e/antd_full_migration_check.py`
- 创建：`ruoyi-fastapi-frontend/tests/e2e/baselines/login-1440.png`
- 创建：`ruoyi-fastapi-frontend/tests/e2e/baselines/chat-1440.png`
- 创建：`ruoyi-fastapi-frontend/tests/e2e/baselines/model-1440.png`
- 创建：`ruoyi-fastapi-frontend/tests/e2e/baselines/application-1440.png`
- 创建：`ruoyi-fastapi-frontend/tests/e2e/baselines/admin-user-1440.png`
- 创建：`ruoyi-fastapi-frontend/tests/e2e/baselines/chat-390.png`
- 创建：`ruoyi-fastapi-frontend/tests/e2e/baselines/model-320.png`
- 创建：`docs/superpowers/verification/2026-08-21-ant-design-vue-full-migration.md`

- [ ] **步骤 1：编写真实验收脚本**

脚本使用独立浏览器 context，收集 `console error`、`pageerror`、失败请求和截图。每个视口断言：

```python
assert page.evaluate("document.documentElement.scrollWidth") <= viewport_width
assert console_errors == []
assert page_errors == []
assert overlays_do_not_overlap(page)
```

Mock 场景负责权限矩阵和确定性视觉状态；真实后端场景负责登录、流式对话、模型列表、应用列表和用量接口。

- [ ] **步骤 2：运行视觉红灯并人工对比**

首次运行：

```bash
conda run -n dj python tests/e2e/antd_full_migration_check.py --update-candidates /tmp/antd-candidates
```

预期：生成候选截图。逐张与已确认预设稿对比；存在结构、间距、字体、控件形态或信息密度偏差时测试保持失败，不直接更新基线。

- [ ] **步骤 3：修正偏差并固化基线**

只在用户确认候选视觉后，把候选复制到 `tests/e2e/baselines`。像素比较允许字体渲染小阈值，但布局区域、组件数量和关键颜色使用结构断言，不用高阈值掩盖偏差。

- [ ] **步骤 4：执行完整验证矩阵**

运行：

```bash
cd ruoyi-fastapi-frontend
npm run test:plugin
NODE_OPTIONS=--max-old-space-size=4096 npm run build:docker
conda run -n dj python tests/e2e/antd_full_migration_check.py

cd ../ruoyi-fastapi-backend
conda run -n dj pytest tests/plugins/ai tests/plugins/sample_plugins/test_ai_plugin.py -q

cd ..
git diff --check
```

预期：全部退出码 0；浏览器无未白名单错误；后端 AI 回归通过。

- [ ] **步骤 5：编写验证记录并提交**

验证文档记录：提交范围、测试计数、构建模块数、四视口结果、真实后端接口、Element Plus 零引用证据、截图路径和非阻断第三方 warning。

```bash
git add ruoyi-fastapi-frontend/tests/e2e docs/superpowers/verification/2026-08-21-ant-design-vue-full-migration.md
git commit -m "test: 验证 Ant Design Vue 全量迁移"
```

## 任务 15：最终审查、整合和部署

**文件：**
- 修改：`docs/superpowers/verification/2026-08-21-ant-design-vue-full-migration.md`
- 不修改：`ruoyi-fastapi-app/`

- [ ] **步骤 1：执行规格审查**

逐条对照 `docs/superpowers/specs/2026-08-21-ant-design-vue-full-migration-design.md`，重点检查完整页面范围、Element Plus 零引用、真实业务流程、权限和视觉基线。

- [ ] **步骤 2：执行质量审查**

检查异步竞态、秘密数据、动态菜单、弹层焦点、移动端、暗色模式、构建体积和死代码。Critical/Important/Minor findings 必须在合并前清零。发现代码 finding 时暂停任务 15，把 finding 作为带红灯测试、最小修复、目标回归和独立提交的新任务追加到本计划末尾，再恢复最终验证；不得在审查步骤中直接做未记录修改。

- [ ] **步骤 3：新鲜运行完整验证**

不得复用任务 14 的历史输出。重新运行插件测试、生产构建、Playwright、后端 AI 回归、Element Plus 扫描和 `git diff --check`。

- [ ] **步骤 4：合并到目标分支**

在 `feat-litellm-deployment` 工作树确认只含用户原有未提交内容，不覆盖 `ruoyi-fastapi-app` 删除。使用非交互合并：

```bash
git merge --no-ff feat/antd-full-migration -m "feat: 完成 Ant Design Vue 全量迁移"
```

发生冲突时停止，逐文件确认所有权；不得使用 `git checkout --theirs` 或硬重置覆盖用户改动。

- [ ] **步骤 5：构建并替换前端运行产物**

生产部署必须使用本次 fresh build 产物或可复现 Docker build，不复用旧 `ruoyi-frontend:latest`。启动后验证实际运行页面的静态资源 hash 与新构建一致，并再次检查登录页和 AI 工作台截图。

- [ ] **步骤 6：提交交付报告**

报告必须包含：最终 merge commit、运行 URL、镜像/产物标识、测试结果、Element Plus 零引用证据、视觉截图和回滚提交边界。
