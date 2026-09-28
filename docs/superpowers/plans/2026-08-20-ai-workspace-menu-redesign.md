# AI 工作台菜单与全局布局改造实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将传统 ZhiHeng 全局菜单重构为 Yuxi 风格的 AI 工作台优先外壳，把 Developer API 归入 AI 资源，同时保留管理控制台、动态菜单和 RBAC。

**架构：** 使用路由驱动的单一 `PlatformShell`，按当前路径在 AI 与管理模式间切换。AI 模式使用固定高频导航、最近会话和 AI 资源页面级标签；管理模式继续消费现有动态路由树。所有模式、默认入口、菜单过滤和权限组合由纯函数计算，业务 URL 与后端接口保持兼容。

**技术栈：** Vue 3、Vue Router、Pinia、Element Plus、SCSS、Node 原生断言测试、Vite、Python Playwright 验收。

---

## 实施前约束

当前 `feat-litellm-deployment` 工作区包含未提交的 Embedding/Rerank、费率界面和既有 `zhiheng-fastapi-app` 删除项。本计划必须在当前工作区执行，但遵守以下隔离规则：

- 每个任务开始前运行 `git status --short`。
- 只编辑任务列出的文件。
- 每次提交前运行 `git diff --cached --name-status`，确认没有 Backend 文件、`zhiheng-fastapi-app` 删除项、`.superpowers/` 或架构汇报文档进入暂存区。
- 对已经有其他未提交修改的 `plugins/ai/plugin.yaml` 和 `tests/plugins/sample_plugins/test_ai_plugin.py`，使用精确补丁暂存本任务 hunk；不得直接 `git add` 这两个完整文件。
- 不回滚、不格式化、不重写本计划范围外的用户改动。

## 文件结构

### 新建

- `zhiheng-fastapi-frontend/src/layout/workspaceRouteResolver.js`：路由模式、默认入口、AI 标签和管理菜单纯函数。
- `zhiheng-fastapi-frontend/src/api/platformWorkspace.js`：全局最近会话只读/归档 API。
- `zhiheng-fastapi-frontend/src/store/modules/workspace.js`：最近会话、请求代次和过期状态。
- `zhiheng-fastapi-frontend/src/layout/components/PlatformSidebar.vue`：AI 模式主侧栏。
- `zhiheng-fastapi-frontend/src/layout/components/RecentSessions.vue`：会话搜索、切换和归档。
- `zhiheng-fastapi-frontend/src/layout/components/AdminSidebar.vue`：管理模式动态菜单。
- `zhiheng-fastapi-frontend/src/layout/components/PlatformTopbar.vue`：精简顶栏和模式操作。
- `zhiheng-fastapi-frontend/src/layout/components/AiResourceShell.vue`：AI 资源标题和页面级标签。
- `zhiheng-fastapi-frontend/plugins/ai/views/usage/index.vue`：按应用查看用量台账。
- `zhiheng-fastapi-frontend/tests/plugins/platformShellTransforms.test.js`：纯函数行为测试。
- `zhiheng-fastapi-frontend/tests/plugins/platformShellContract.test.js`：组件、可访问性和源文件契约。
- `zhiheng-fastapi-frontend/tests/e2e/platform_shell_check.py`：四视口浏览器验收脚本。

### 修改

- `zhiheng-fastapi-frontend/src/layout/index.vue`：装配单一平台外壳。
- `zhiheng-fastapi-frontend/src/layout/components/AppMain.vue`：AI 资源容器和模式化内容高度。
- `zhiheng-fastapi-frontend/src/layout/components/index.js`：导出新布局组件。
- `zhiheng-fastapi-frontend/src/store/modules/app.js`：桌面折叠与移动抽屉分离。
- `zhiheng-fastapi-frontend/src/store/modules/settings.js`：停止旧 navType 影响布局。
- `zhiheng-fastapi-frontend/src/layout/components/Settings/index.vue`：移除旧导航布局选项。
- `zhiheng-fastapi-frontend/src/permission.js`：使用新的默认入口解析。
- `zhiheng-fastapi-frontend/plugins/ai/views/chat/index.vue`：移除重复会话侧栏并同步路由会话 ID。
- `zhiheng-fastapi-frontend/plugins/ai/views/model/index.vue`：适配 AI 资源容器。
- `zhiheng-fastapi-frontend/plugins/ai/views/application/index.vue`：适配 AI 资源容器。
- `zhiheng-fastapi-frontend/src/assets/styles/workspace.scss`：全局外壳、侧栏、顶栏和响应式变量。
- `zhiheng-fastapi-backend/plugins/ai/plugin.yaml`：注册 `/ai/usage` 隐藏业务路由声明。
- `zhiheng-fastapi-backend/tests/plugins/sample_plugins/test_ai_plugin.py`：锁定用量页面声明。

## 任务 1：路由模式与菜单解析纯函数

**文件：**

- 创建：`zhiheng-fastapi-frontend/src/layout/workspaceRouteResolver.js`
- 创建：`zhiheng-fastapi-frontend/tests/plugins/platformShellTransforms.test.js`
- 修改：`zhiheng-fastapi-frontend/src/permission.js`

- [ ] **步骤 1：编写失败的模式、权限和默认入口测试**

```js
import assert from 'node:assert/strict'
import {
  filterAdminWorkspaceRoutes,
  resolveAiResourceTabs,
  resolvePlatformLandingPath,
  resolveWorkspaceMode,
} from '../../src/layout/workspaceRouteResolver.js'

const permissions = new Set(['ai:chat:list', 'ai:model:list', 'ai:application:list'])

assert.equal(resolveWorkspaceMode('/ai/chat'), 'ai')
assert.equal(resolveWorkspaceMode('/ai/model'), 'ai')
assert.equal(resolveWorkspaceMode('/system/user'), 'admin')
assert.equal(resolvePlatformLandingPath('/index', permissions, ['/system/user']), '/ai/chat')
assert.equal(
  resolvePlatformLandingPath('/index', new Set(['ai:model:list']), ['/system/user']),
  '/ai/model',
)
assert.deepEqual(resolveAiResourceTabs(permissions).map((item) => item.key), ['model', 'application', 'usage'])
assert.deepEqual(
  filterAdminWorkspaceRoutes([
    { path: '/ai', children: [{ path: 'chat' }] },
    { path: '/system', children: [{ path: 'user' }] },
  ]),
  [{ path: '/system', children: [{ path: 'user' }] }],
)
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd zhiheng-fastapi-frontend
node tests/plugins/platformShellTransforms.test.js
```

预期：FAIL，提示 `workspaceRouteResolver.js` 不存在。

- [ ] **步骤 3：实现纯函数**

```js
const AI_PREFIX = '/ai'

export function resolveWorkspaceMode(path) {
  return path === AI_PREFIX || path.startsWith(`${AI_PREFIX}/`) ? 'ai' : 'admin'
}

export function resolveAiResourceTabs(permissions) {
  const definitions = [
    { key: 'model', label: '模型', path: '/ai/model', permission: 'ai:model:list' },
    { key: 'application', label: '应用接入', path: '/ai/application', permission: 'ai:application:list' },
    { key: 'usage', label: '用量', path: '/ai/usage', permission: 'ai:application:list' },
  ]
  return definitions.filter((item) => permissions.has('*:*:*') || permissions.has(item.permission))
}

export function resolvePlatformLandingPath(path, permissions, adminPaths) {
  if (path !== '/' && path !== '/index') return path
  if (permissions.has('*:*:*') || permissions.has('ai:chat:list')) return '/ai/chat'
  const firstResource = resolveAiResourceTabs(permissions)[0]
  return firstResource?.path || adminPaths[0] || '/401'
}

export function filterAdminWorkspaceRoutes(routes) {
  return routes.filter((route) => route.path !== '/ai' && !route.path.startsWith('/ai/'))
}
```

在 `permission.js` 中用 `resolvePlatformLandingPath` 替换仅处理 `/ai/chat` 的旧入口逻辑，并传入 `useUserStore().permissions` 与可访问管理路径。

- [ ] **步骤 4：运行纯函数和现有插件测试**

```bash
cd zhiheng-fastapi-frontend
node tests/plugins/platformShellTransforms.test.js
npm run test:plugin
```

预期：新增测试 PASS；现有插件测试全部 PASS。

- [ ] **步骤 5：提交**

```bash
git add zhiheng-fastapi-frontend/src/layout/workspaceRouteResolver.js \
  zhiheng-fastapi-frontend/tests/plugins/platformShellTransforms.test.js \
  zhiheng-fastapi-frontend/src/permission.js
git diff --cached --name-status
git commit -m "feat: 建立工作台路由模式解析"
```

## 任务 2：最近会话状态与请求隔离

**文件：**

- 创建：`zhiheng-fastapi-frontend/src/api/platformWorkspace.js`
- 创建：`zhiheng-fastapi-frontend/src/store/modules/workspace.js`
- 修改：`zhiheng-fastapi-frontend/src/layout/workspaceRouteResolver.js`
- 修改：`zhiheng-fastapi-frontend/tests/plugins/platformShellTransforms.test.js`

- [ ] **步骤 1：编写失败的会话归一化和迟到响应测试**

```js
import { normalizeWorkspaceSessions, shouldApplyGeneration } from '../../src/layout/workspaceRouteResolver.js'

assert.deepEqual(normalizeWorkspaceSessions({ rows: [{ sessionId: 's-1', title: '模型排查' }] }), [
  { sessionId: 's-1', title: '模型排查', updatedAt: null },
])
assert.equal(shouldApplyGeneration(2, 2), true)
assert.equal(shouldApplyGeneration(1, 2), false)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`node tests/plugins/platformShellTransforms.test.js`

预期：FAIL，提示新导出不存在。

- [ ] **步骤 3：实现 API 与 Pinia Store**

`platformWorkspace.js`：

```js
import request from '@/utils/request'

export const listRecentSessions = () => request({ url: '/ai/chat/session/list', method: 'get' })
export const archiveRecentSession = (sessionId) => request({
  url: `/ai/chat/session/${encodeURIComponent(sessionId)}`,
  method: 'delete',
})
```

`workspace.js` 必须包含以下状态与代次检查：

```js
state: () => ({
  sessions: [],
  sessionsLoading: false,
  sessionsError: '',
  sessionsLastSuccessAt: null,
  sessionGeneration: 0,
  mobileDrawerOpen: false,
}),
```

`loadSessions()` 在开始时递增 generation；只有当前 generation 可以写入 rows、error、loading。刷新失败且已有 rows 时保留 rows，只写 `sessionsError`。

- [ ] **步骤 4：验证请求代次和插件测试**

运行：

```bash
node tests/plugins/platformShellTransforms.test.js
npm run test:plugin
```

预期：全部 PASS。

- [ ] **步骤 5：提交**

```bash
git add zhiheng-fastapi-frontend/src/api/platformWorkspace.js \
  zhiheng-fastapi-frontend/src/store/modules/workspace.js \
  zhiheng-fastapi-frontend/src/layout/workspaceRouteResolver.js \
  zhiheng-fastapi-frontend/tests/plugins/platformShellTransforms.test.js
git commit -m "feat: 建立全局最近会话状态"
```

## 任务 3：平台侧栏与管理侧栏

**文件：**

- 创建：`zhiheng-fastapi-frontend/src/layout/components/PlatformSidebar.vue`
- 创建：`zhiheng-fastapi-frontend/src/layout/components/RecentSessions.vue`
- 创建：`zhiheng-fastapi-frontend/src/layout/components/AdminSidebar.vue`
- 创建：`zhiheng-fastapi-frontend/tests/plugins/platformShellContract.test.js`
- 修改：`zhiheng-fastapi-frontend/src/layout/components/index.js`

- [ ] **步骤 1：编写失败的组件契约测试**

```js
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const platformSidebar = readFileSync('src/layout/components/PlatformSidebar.vue', 'utf8')
const recentSessions = readFileSync('src/layout/components/RecentSessions.vue', 'utf8')
const adminSidebar = readFileSync('src/layout/components/AdminSidebar.vue', 'utf8')

assert.match(platformSidebar, /新建对话/)
assert.match(platformSidebar, /AI 资源/)
assert.match(platformSidebar, /管理控制台/)
assert.match(recentSessions, /可能已过期/)
assert.match(recentSessions, /aria-label="搜索最近会话"/)
assert.match(adminSidebar, /返回 AI 工作台/)
assert.doesNotMatch(platformSidebar, /Developer API/)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`node tests/plugins/platformShellContract.test.js`

预期：FAIL，组件文件不存在。

- [ ] **步骤 3：实现三个侧栏组件**

`PlatformSidebar` 使用原生 `router-link` 和 `button`：

```vue
<router-link class="platform-brand" to="/ai/chat" aria-label="返回 AI 工作台">...</router-link>
<button class="new-chat" type="button" @click="newChat">...</button>
<router-link v-if="canChat" to="/ai/chat" aria-label="AI 对话">...</router-link>
<router-link v-if="resourceTabs.length" :to="resourceTabs[0].path" aria-label="AI 资源">...</router-link>
<RecentSessions v-if="!collapsed" ... />
<button type="button" @click="$emit('open-admin')">管理控制台</button>
```

`RecentSessions` 负责搜索、当前项、归档菜单和 stale 文案。会话选择发出 `open-session`；组件内不得直接请求网络。

`AdminSidebar` 复用现有 `SidebarItem` 渲染经过 `filterAdminWorkspaceRoutes` 的路由树，并在顶部提供返回按钮。

- [ ] **步骤 4：运行契约与插件测试**

```bash
node tests/plugins/platformShellContract.test.js
npm run test:plugin
```

预期：全部 PASS。

- [ ] **步骤 5：提交**

```bash
git add zhiheng-fastapi-frontend/src/layout/components/PlatformSidebar.vue \
  zhiheng-fastapi-frontend/src/layout/components/RecentSessions.vue \
  zhiheng-fastapi-frontend/src/layout/components/AdminSidebar.vue \
  zhiheng-fastapi-frontend/src/layout/components/index.js \
  zhiheng-fastapi-frontend/tests/plugins/platformShellContract.test.js
git commit -m "feat: 重构平台全局侧栏"
```

## 任务 4：单一 App Shell 与精简顶栏

**文件：**

- 创建：`zhiheng-fastapi-frontend/src/layout/components/PlatformTopbar.vue`
- 修改：`zhiheng-fastapi-frontend/src/layout/index.vue`
- 修改：`zhiheng-fastapi-frontend/src/layout/components/AppMain.vue`
- 修改：`zhiheng-fastapi-frontend/src/store/modules/app.js`
- 修改：`zhiheng-fastapi-frontend/tests/plugins/platformShellContract.test.js`

- [ ] **步骤 1：扩展失败契约测试**

```js
const layout = readFileSync('src/layout/index.vue', 'utf8')
const topbar = readFileSync('src/layout/components/PlatformTopbar.vue', 'utf8')

assert.match(layout, /PlatformSidebar/)
assert.match(layout, /AdminSidebar/)
assert.match(layout, /resolveWorkspaceMode/)
assert.match(topbar, /返回 AI 工作台/)
assert.match(topbar, /aria-label="打开主菜单"/)
assert.doesNotMatch(topbar, /源码地址|文档地址|布局大小/)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`node tests/plugins/platformShellContract.test.js`

预期：FAIL，顶栏和 Layout 尚未接入。

- [ ] **步骤 3：实现 `PlatformShell` 装配**

`layout/index.vue` 的核心结构固定为：

```vue
<div class="platform-shell" :class="[`is-${mode}`, { 'is-collapsed': collapsed }]">
  <div v-if="mobileDrawerOpen" class="platform-scrim" @click="closeMobileDrawer" />
  <PlatformSidebar v-if="mode === 'ai'" ... />
  <AdminSidebar v-else ... />
  <section class="platform-main">
    <PlatformTopbar :mode="mode" @toggle-sidebar="toggleSidebar" />
    <TagsView v-if="mode === 'admin' && needTagsView" />
    <AppMain />
  </section>
</div>
```

`app.js` 增加独立的 `mobileSidebarOpen`；桌面 `sidebar.opened` 继续持久化，移动开关不写 Cookie。路由变化时关闭移动抽屉。

- [ ] **步骤 4：运行测试与开发构建**

```bash
npm run test:plugin
NODE_OPTIONS=--max-old-space-size=4096 npm run build:docker
```

预期：测试 PASS；Vite 构建 exit 0。

- [ ] **步骤 5：提交**

```bash
git add zhiheng-fastapi-frontend/src/layout/components/PlatformTopbar.vue \
  zhiheng-fastapi-frontend/src/layout/index.vue \
  zhiheng-fastapi-frontend/src/layout/components/AppMain.vue \
  zhiheng-fastapi-frontend/src/store/modules/app.js \
  zhiheng-fastapi-frontend/tests/plugins/platformShellContract.test.js
git commit -m "feat: 建立 AI 工作台全局外壳"
```

## 任务 5：聊天页面接入全局会话侧栏

**文件：**

- 修改：`zhiheng-fastapi-frontend/plugins/ai/views/chat/index.vue`
- 修改：`zhiheng-fastapi-frontend/tests/plugins/platformShellContract.test.js`
- 修改：`zhiheng-fastapi-frontend/tests/plugins/workspaceTransforms.test.js`

- [ ] **步骤 1：编写失败的会话路由契约**

```js
const chatView = readFileSync('plugins/ai/views/chat/index.vue', 'utf8')

assert.doesNotMatch(chatView, /class="chat-sessions"/)
assert.doesNotMatch(chatView, /mobile-session-drawer/)
assert.match(chatView, /route\.query\.sessionId/)
assert.match(chatView, /workspaceStore\.loadSessions/)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`node tests/plugins/platformShellContract.test.js`

预期：FAIL，聊天页面仍渲染局部会话侧栏。

- [ ] **步骤 3：移除重复侧栏并同步路由**

规则：

- `/ai/chat` 表示新会话。
- `/ai/chat?sessionId=<id>` 表示打开已有会话。
- 监听 `route.query.sessionId`，变化时加载对应会话。
- 收到 SSE meta 中的新 `sessionId` 后执行 `router.replace({ path: '/ai/chat', query: { sessionId } })`。
- 发送结束和归档完成后调用 `workspaceStore.loadSessions()`。
- 不再渲染局部 `ChatSessionPanel` 和移动会话抽屉。

- [ ] **步骤 4：验证对话回归**

```bash
npm run test:plugin
NODE_OPTIONS=--max-old-space-size=4096 npm run build:docker
```

预期：SSE、路由和构建测试全部 PASS。

- [ ] **步骤 5：提交**

```bash
git add zhiheng-fastapi-frontend/plugins/ai/views/chat/index.vue \
  zhiheng-fastapi-frontend/tests/plugins/platformShellContract.test.js \
  zhiheng-fastapi-frontend/tests/plugins/workspaceTransforms.test.js
git commit -m "feat: 统一全局与对话会话导航"
```

## 任务 6：AI 资源容器与用量页面

**文件：**

- 创建：`zhiheng-fastapi-frontend/src/layout/components/AiResourceShell.vue`
- 创建：`zhiheng-fastapi-frontend/plugins/ai/views/usage/index.vue`
- 修改：`zhiheng-fastapi-frontend/src/layout/components/AppMain.vue`
- 修改：`zhiheng-fastapi-frontend/plugins/ai/views/model/index.vue`
- 修改：`zhiheng-fastapi-frontend/plugins/ai/views/application/index.vue`
- 修改：`zhiheng-fastapi-backend/plugins/ai/plugin.yaml`
- 修改：`zhiheng-fastapi-backend/tests/plugins/sample_plugins/test_ai_plugin.py`
- 修改：`zhiheng-fastapi-frontend/tests/plugins/platformShellContract.test.js`

- [ ] **步骤 1：编写失败的标签和清单测试**

```js
const resourceShell = readFileSync('src/layout/components/AiResourceShell.vue', 'utf8')
const usageView = readFileSync('plugins/ai/views/usage/index.vue', 'utf8')

assert.match(resourceShell, /resolveAiResourceTabs/)
assert.match(resourceShell, /aria-label="AI 资源导航"/)
assert.match(usageView, /listApplications/)
assert.match(usageView, /listApplicationUsage/)
```

后端清单测试增加：

```python
assert plugin.manifest.frontend.menus[0].children[3].component == 'plugin/ai/usage/index'
```

- [ ] **步骤 2：运行测试验证失败**

```bash
cd zhiheng-fastapi-frontend && node tests/plugins/platformShellContract.test.js
cd ../zhiheng-fastapi-backend && conda run -n dj pytest tests/plugins/sample_plugins/test_ai_plugin.py -q
```

预期：前端文件不存在，插件清单未声明用量页面。

- [ ] **步骤 3：实现资源容器和用量页面**

`AiResourceShell` 使用 `resolveAiResourceTabs(userStore.permissions)`，标签使用 `router-link`，当前标签使用 `aria-current="page"`。

`usage/index.vue`：

```js
const applications = ref([])
const selectedApplicationId = ref('')
const rows = ref([])
const loading = ref(false)
const error = ref('')
const lastSuccessAt = ref(null)
```

先调用 `listApplications({ pageNum: 1, pageSize: 100 })`；选择应用后调用 `listApplicationUsage(applicationId, query)`。刷新失败且已有 rows 时保留 rows 并显示 stale 状态。

在 `plugin.yaml` 的 AI 菜单下追加：

```yaml
- name: 用量
  path: usage
  component: plugin/ai/usage/index
  routeName: AiUsage
  perms: ai:application:list
  type: C
  orderNum: 4
  icon: chart
```

提交时只暂存该 YAML hunk，不暂存文件内已有的其他未提交变更。

- [ ] **步骤 4：运行前后端清单和构建测试**

```bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/sample_plugins/test_ai_plugin.py -q
cd ../zhiheng-fastapi-frontend
npm run test:plugin
NODE_OPTIONS=--max-old-space-size=4096 npm run build:docker
```

预期：全部 PASS。

- [ ] **步骤 5：精确暂存并提交**

先暂存不冲突文件：

```bash
git add zhiheng-fastapi-frontend/src/layout/components/AiResourceShell.vue \
  zhiheng-fastapi-frontend/plugins/ai/views/usage/index.vue \
  zhiheng-fastapi-frontend/src/layout/components/AppMain.vue \
  zhiheng-fastapi-frontend/plugins/ai/views/model/index.vue \
  zhiheng-fastapi-frontend/plugins/ai/views/application/index.vue \
  zhiheng-fastapi-frontend/tests/plugins/platformShellContract.test.js
```

使用 `git diff -U3 -- zhiheng-fastapi-backend/plugins/ai/plugin.yaml zhiheng-fastapi-backend/tests/plugins/sample_plugins/test_ai_plugin.py` 检查 hunk，并分别通过只包含“用量菜单”和对应断言的补丁执行 `git apply --cached <patch-file>`。随后：

```bash
git diff --cached --name-status
git diff --cached --check
git commit -m "feat: 归并模型与开发者资源入口"
```

## 任务 7：管理模式标签页与旧布局设置收敛

**文件：**

- 修改：`zhiheng-fastapi-frontend/src/store/modules/settings.js`
- 修改：`zhiheng-fastapi-frontend/src/layout/components/Settings/index.vue`
- 修改：`zhiheng-fastapi-frontend/src/layout/components/PlatformTopbar.vue`
- 修改：`zhiheng-fastapi-frontend/tests/plugins/platformShellContract.test.js`

- [ ] **步骤 1：编写失败的旧导航设置移除测试**

```js
const settings = readFileSync('src/layout/components/Settings/index.vue', 'utf8')
const settingsStore = readFileSync('src/store/modules/settings.js', 'utf8')

assert.doesNotMatch(settings, /navType|顶部菜单|混合菜单/)
assert.match(settingsStore, /platformShell: true/)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`node tests/plugins/platformShellContract.test.js`

预期：FAIL，旧 navType 设置仍存在。

- [ ] **步骤 3：收敛设置和管理标签**

- 从设置抽屉移除 navType、侧栏主题和旧标签样式选项。
- 保留主题、界面密度、固定顶部、动态标题和暗色模式。
- `PlatformTopbar` 仅在 `mode === 'admin'` 时渲染 `TagsView` 相关操作。
- 旧 localStorage 中的 navType 不得改变 `PlatformShell`。

- [ ] **步骤 4：验证**

运行：

```bash
npm run test:plugin
NODE_OPTIONS=--max-old-space-size=4096 npm run build:docker
```

预期：全部 PASS。

- [ ] **步骤 5：提交**

```bash
git add zhiheng-fastapi-frontend/src/store/modules/settings.js \
  zhiheng-fastapi-frontend/src/layout/components/Settings/index.vue \
  zhiheng-fastapi-frontend/src/layout/components/PlatformTopbar.vue \
  zhiheng-fastapi-frontend/tests/plugins/platformShellContract.test.js
git commit -m "fix: 收敛旧版导航布局设置"
```

## 任务 8：视觉变量、响应式和可访问性

**文件：**

- 修改：`zhiheng-fastapi-frontend/src/assets/styles/workspace.scss`
- 修改：`zhiheng-fastapi-frontend/src/layout/index.vue`
- 修改：`zhiheng-fastapi-frontend/src/layout/components/PlatformSidebar.vue`
- 修改：`zhiheng-fastapi-frontend/src/layout/components/AdminSidebar.vue`
- 修改：`zhiheng-fastapi-frontend/src/layout/components/PlatformTopbar.vue`
- 修改：`zhiheng-fastapi-frontend/src/layout/components/AiResourceShell.vue`
- 修改：`zhiheng-fastapi-frontend/tests/plugins/platformShellContract.test.js`

- [ ] **步骤 1：扩展失败的尺寸和可访问性契约**

```js
const styles = readFileSync('src/assets/styles/workspace.scss', 'utf8')

assert.match(styles, /--platform-sidebar-width:\s*230px/)
assert.match(styles, /--platform-sidebar-collapsed-width:\s*56px/)
assert.match(styles, /@media[^}]*max-width:\s*991px/s)
assert.match(platformSidebar, /aria-current/)
assert.match(platformSidebar, /aria-label="新建对话"/)
```

- [ ] **步骤 2：运行测试验证失败**

运行：`node tests/plugins/platformShellContract.test.js`

预期：FAIL，变量和语义未完整接入。

- [ ] **步骤 3：实现视觉与响应式规则**

新增变量：

```scss
:root {
  --platform-sidebar-width: 230px;
  --platform-sidebar-collapsed-width: 56px;
  --platform-topbar-height: 48px;
  --platform-menu-item-height: 32px;
}
```

要求：

- 侧栏和主内容使用稳定 flex-basis，不通过动态内容改变宽度。
- 激活菜单使用 `--workspace-main-soft` 与 `--workspace-main-deep`。
- 图标按钮固定 `32px × 32px`。
- `max-width: 991px` 时侧栏绝对定位为抽屉，主内容宽度 `100%`。
- 支持 `prefers-reduced-motion`。
- 抽屉开启后焦点进入侧栏；Escape 关闭并返回菜单按钮。
- 所有图标按钮提供 tooltip、title 或可访问名称。

- [ ] **步骤 4：运行静态与构建测试**

```bash
npm run test:plugin
NODE_OPTIONS=--max-old-space-size=4096 npm run build:docker
git diff --check
```

预期：全部通过。

- [ ] **步骤 5：提交**

```bash
git add zhiheng-fastapi-frontend/src/assets/styles/workspace.scss \
  zhiheng-fastapi-frontend/src/layout/index.vue \
  zhiheng-fastapi-frontend/src/layout/components/PlatformSidebar.vue \
  zhiheng-fastapi-frontend/src/layout/components/AdminSidebar.vue \
  zhiheng-fastapi-frontend/src/layout/components/PlatformTopbar.vue \
  zhiheng-fastapi-frontend/src/layout/components/AiResourceShell.vue \
  zhiheng-fastapi-frontend/tests/plugins/platformShellContract.test.js
git commit -m "style: 统一 AI 工作台全局布局"
```

## 任务 9：浏览器验收与完整回归

**文件：**

- 创建：`zhiheng-fastapi-frontend/tests/e2e/platform_shell_check.py`
- 可能修改：仅前面任务已列出的文件，用于修复验收发现的问题。

- [ ] **步骤 1：编写 Playwright 验收脚本**

脚本必须使用 mock route 覆盖登录信息、动态路由、会话、模型、应用和用量接口，并断言：

```python
assert await page.locator('.platform-sidebar').count() == 1
assert await page.get_by_text('Developer API', exact=True).count() == 0
assert await page.get_by_role('link', name='AI 资源').count() == 1
assert await page.get_by_role('link', name='应用接入').count() == 1
assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
assert console_errors == []
assert page_errors == []
```

依次覆盖：

- `1440 × 900` AI 模式展开/收起。
- `1024 × 768` 管理控制台往返和标签页。
- `390 × 844` 移动抽屉、会话选择和自动关闭。
- `320 × 720` AI 资源标签、更多菜单和无横向溢出。
- `/index`、直接 `/ai/model`、刷新、浏览器返回。
- `chat-only`、`model-only`、`application-only` 和无 AI 权限组合。
- 会话刷新失败保留 stale 数据。

- [ ] **步骤 2：运行 Playwright，确认若存在缺陷则先失败**

```bash
cd zhiheng-fastapi-frontend
conda run -n dj python tests/e2e/platform_shell_check.py
```

预期：首次运行如发现布局或行为缺陷，输出精确断言失败；不得忽略 Console 或 page error。

- [ ] **步骤 3：最小修复验收问题并重复运行**

每次只修一个已复现问题，直到脚本 exit 0。

- [ ] **步骤 4：运行完整前端与插件清单回归**

```bash
cd zhiheng-fastapi-frontend
npm run test:plugin
NODE_OPTIONS=--max-old-space-size=4096 npm run build:docker
cd ../zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/sample_plugins/test_ai_plugin.py -q
cd ..
git diff --check
```

预期：插件测试全部 PASS；Vite 构建 exit 0；后端清单测试 PASS；diff check 无输出。

- [ ] **步骤 5：提交验收脚本和最终修复**

```bash
git add zhiheng-fastapi-frontend/tests/e2e/platform_shell_check.py
# 仅追加暂存验收实际修改过的本计划文件
git diff --cached --name-status
git commit -m "test: 验证 AI 工作台全局导航"
```

## 最终检查清单

- [ ] `git status --short` 中所有本计划文件均已提交。
- [ ] 原有 Backend 未提交功能和 `zhiheng-fastapi-app` 删除项仍保持原状态，未被本计划提交。
- [ ] `/index` 根据权限进入 AI 对话、AI 资源或管理页面。
- [ ] Developer API 不再作为独立主菜单出现。
- [ ] AI 资源包含模型、应用接入和用量三个权限隔离标签。
- [ ] AI 与管理模式使用同一外壳并可稳定往返。
- [ ] AI 模式无传统面包屑和多标签页。
- [ ] 最近会话在 AI 模式中常驻，刷新失败保留旧数据。
- [ ] 四个视口无横向溢出、重叠或不可访问操作。
- [ ] 前端插件测试、生产构建、插件结构检查和 diff check 全部通过。
