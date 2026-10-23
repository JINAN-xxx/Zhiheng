# 智衡平台 Yuxi 视觉层移植实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将智衡平台的主题、全局壳和核心页面完整迁移到 Yuxi 青蓝与冷灰视觉体系，同时保留现有品牌、业务接口、路由、权限和状态机。

**架构：** 新增 Yuxi 浅色与暗色 Token 作为唯一颜色来源，通过 Ant Design Vue 主题和 Element Plus 兼容变量向现有组件分发。`--workspace-*` 暂时保留为语义别名，核心页面逐步改用统一表面、列表、表单和状态规范；每个阶段使用契约测试和 Playwright 截图验证。

**技术栈：** Vue 3、Vite 6、Ant Design Vue 4、Element Plus 兼容层、SCSS/CSS Variables、Node.js 契约测试、Python Playwright

---

## 文件结构

### 新建文件

- `frontend/src/assets/styles/yuxi-tokens.css`：Yuxi 浅色 Token 唯一来源。
- `frontend/src/assets/styles/yuxi-tokens.dark.css`：Yuxi 暗色 Token 覆盖。
- `frontend/THIRD_PARTY_NOTICES.md`：记录 Yuxi 来源、版权和 MIT License。
- `frontend/tests/plugins/yuxiThemeContract.test.js`：验证 Token、许可、主题入口和禁止的旧主色。
- `frontend/tests/plugins/yuxiVisualContract.test.js`：验证全局壳与核心页面只使用统一语义颜色和表面规则。

### 主题与全局样式

- `frontend/src/main.js`：按浅色、暗色、兼容层顺序加载主题。
- `frontend/src/design/antdTheme.js`：将 Ant Design Vue Token 固定映射到 Yuxi 色值。
- `frontend/src/design/useAntdTheme.js`：移除任意用户主色覆盖。
- `frontend/src/assets/styles/workspace.scss`：改为 Yuxi Token 兼容别名和布局规则。
- `frontend/src/assets/styles/antd-reset.scss`：统一 Ant 浮层、控件和反馈表面。
- `frontend/src/assets/styles/variables.module.scss`：旧 SCSS 变量映射到 Yuxi 色值。
- `frontend/src/assets/styles/zhiheng.scss`：清理会覆盖核心页面的旧蓝色、绿色和重阴影。
- `frontend/src/assets/styles/sidebar.scss`：移除旧 `#409EFF` 侧栏强调色。
- `frontend/src/utils/theme.js`：停止运行时写入任意 `--workspace-main`。
- `frontend/src/store/modules/settings.js`：固定 Yuxi 主色，仅持久化明暗和布局设置。
- `frontend/src/layout/components/Settings/index.vue`：删除主题颜色选择器，保留深色与密度控制。

### 全局壳与公共组件

- `frontend/src/layout/components/PlatformSidebar.vue`：浅色 AI 侧栏。
- `frontend/src/layout/components/AdminSidebar.vue`：统一管理侧栏。
- `frontend/src/layout/components/RecentSessions.vue`：浅色会话列表和选中态。
- `frontend/src/layout/components/PlatformTopbar.vue`：白色顶栏和中性工具按钮。
- `frontend/src/layout/components/TagsView/index.vue`：移除动态任意主题色。
- `frontend/src/layout/components/AiResourceShell.vue`：使用 Yuxi 响应式页面间距。
- `frontend/src/components/Workspace/WorkspaceToolbar.vue`：标题、说明和操作层级。
- `frontend/src/components/Workspace/WorkspacePanel.vue`：8px 轻边框表面。
- `frontend/src/components/Workspace/WorkspaceState.vue`：统一加载、空、错误和过期状态。
- `frontend/src/components/Antd/AuthShell.vue`：Yuxi 风格认证表面。

### 核心页面

- `frontend/plugins/ai/views/chat/index.vue`
- `frontend/plugins/ai/views/chat/components/AiMessage.vue`
- `frontend/plugins/ai/views/model/index.vue`
- `frontend/plugins/ai/views/model/components/PlatformModelsPanel.vue`
- `frontend/plugins/ai/views/model/components/EngineDeploymentsPanel.vue`
- `frontend/plugins/ai/views/model/components/CredentialsPanel.vue`
- `frontend/plugins/ai/views/model/components/RuntimeStatusPanel.vue`
- `frontend/plugins/ai/views/model/components/ModelOnboardingDrawer.vue`
- `frontend/plugins/ai/views/model/components/CredentialForm.vue`
- `frontend/plugins/ai/views/model/components/DeploymentForm.vue`
- `frontend/plugins/ai/views/application/index.vue`
- `frontend/plugins/ai/views/usage/index.vue`
- `frontend/src/views/login.vue`
- `frontend/src/views/register.vue`
- `frontend/src/views/lock.vue`
- `frontend/src/views/error/401.vue`
- `frontend/src/views/error/404.vue`

### 测试与验收

- `frontend/tests/plugins/antdThemeContract.test.js`
- `frontend/tests/plugins/platformShellContract.test.js`
- `frontend/tests/plugins/antdComponentsContract.test.js`
- `frontend/tests/plugins/antdAiViewsContract.test.js`
- `frontend/tests/plugins/antdAuthShellContract.test.js`
- `frontend/tests/plugins/elementPlusRemovalContract.test.js`
- `frontend/tests/e2e/platform_shell_check.py`
- `frontend/tests/e2e/antd_chat_check.py`

## 实施约束

- 所有实现遵循红灯、绿灯、重构顺序；没有先失败的测试，不修改生产代码。
- 不改变 API 路径、请求参数、响应字段、SSE、路由、权限码和 Pinia 业务状态。
- 不新增 Yuxi 的品牌、Logo、插画或业务能力。
- 核心页面不新增 Element Plus 引用；低频旧页面只通过兼容 Token 收敛视觉。
- 每个任务完成后执行目标测试和 `git diff --check`，然后独立提交。

### 任务 1：建立 Yuxi Token 与许可边界

**文件：**
- 创建：`frontend/src/assets/styles/yuxi-tokens.css`
- 创建：`frontend/src/assets/styles/yuxi-tokens.dark.css`
- 创建：`frontend/THIRD_PARTY_NOTICES.md`
- 创建：`frontend/tests/plugins/yuxiThemeContract.test.js`
- 修改：`frontend/src/main.js`

- [ ] **步骤 1：编写失败的 Token 与许可测试**

```js
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const light = readFileSync('src/assets/styles/yuxi-tokens.css', 'utf8')
const dark = readFileSync('src/assets/styles/yuxi-tokens.dark.css', 'utf8')
const notice = readFileSync('THIRD_PARTY_NOTICES.md', 'utf8')
const main = readFileSync('src/main.js', 'utf8')

for (const [token, color] of Object.entries({
  '--main-700': '#046a82',
  '--main-500': '#3996ae',
  '--main-50': '#e1f6fb',
  '--gray-50': '#f5f7f7',
  '--gray-150': '#eef0f0',
  '--gray-1000': '#151616',
})) {
  assert.match(light.toLowerCase(), new RegExp(`${token}:\\s*${color}`))
}
assert.match(dark.toLowerCase(), /--main-color:\s*#4a9fb8/)
assert.match(dark.toLowerCase(), /--gray-0:\s*#030303/)
assert.match(dark.toLowerCase(), /--bg-sider:\s*#141414/)
assert.match(notice, /Copyright \(c\) 2025 Yuxi Project Contributors/)
assert.match(notice, /MIT License/)
assert.match(main, /yuxi-tokens\.css/)
assert.match(main, /yuxi-tokens\.dark\.css/)
```

- [ ] **步骤 2：运行测试并确认正确失败**

运行：`cd frontend && node tests/plugins/yuxiThemeContract.test.js`

预期：FAIL，提示 `yuxi-tokens.css` 不存在。

- [ ] **步骤 3：添加完整核心 Token、暗色覆盖和许可文本**

`yuxi-tokens.css` 写入以下完整主色、中性色和语义入口：

```css
:root {
  --main-1000: #01151f;
  --main-900: #023944;
  --main-800: #035065;
  --main-700: #046a82;
  --main-600: #24839a;
  --main-500: #3996ae;
  --main-400: #5faec2;
  --main-300: #82c3d6;
  --main-200: #a3d8e8;
  --main-100: #c4eaf5;
  --main-50: #e1f6fb;
  --main-30: #f7fbfd;
  --main-5: #fcfefe;
  --main-color: var(--main-700);
  --gray-1000: #151616;
  --gray-900: #1e1f1f;
  --gray-800: #323333;
  --gray-700: #4c4d4d;
  --gray-600: #697070;
  --gray-500: #979999;
  --gray-400: #bdbfbf;
  --gray-300: #d7d9d9;
  --gray-200: #e4e6e6;
  --gray-150: #eef0f0;
  --gray-100: #eff2f2;
  --gray-50: #f5f7f7;
  --gray-25: #f8fafa;
  --gray-10: #fbfcfc;
  --gray-0: #ffffff;
  --color-success-50: #e6f7e6;
  --color-success-500: #52c41a;
  --color-success-700: #389e0d;
  --color-error-50: #fff2f0;
  --color-error-500: #ff4d4f;
  --color-error-700: #cf1322;
  --color-warning-50: #fffbe6;
  --color-warning-500: #faad14;
  --color-warning-700: #d48806;
  --color-info-50: #e6f7ff;
  --color-info-500: #1890ff;
  --color-info-700: #096dd9;
  --color-text: var(--gray-1000);
  --color-text-secondary: var(--gray-600);
  --color-text-tertiary: var(--gray-500);
  --bg-sider: var(--main-5);
  --page-padding: 22px;
}
```

`yuxi-tokens.dark.css` 使用 `html.dark` 覆盖：

```css
html.dark {
  --main-color: #4a9fb8;
  --main-50: #023944;
  --gray-150: #323333;
  --gray-50: #151616;
  --gray-10: #080808;
  --gray-0: #030303;
  --bg-sider: #141414;
  --color-text: #ffffff;
  --color-text-secondary: rgb(255 255 255 / 65%);
  --color-text-tertiary: rgb(255 255 255 / 45%);
}
```

`THIRD_PARTY_NOTICES.md` 写明来源 URL、版权行和完整 MIT License。`main.js` 在 `workspace.scss` 前导入两份 Token 文件。

- [ ] **步骤 4：运行目标测试和完整插件测试**

运行：`cd frontend && node tests/plugins/yuxiThemeContract.test.js && npm run test:plugin`

预期：新增测试 PASS；现有插件测试全部 PASS。

- [ ] **步骤 5：提交 Token 基线**

```bash
git add frontend/src/assets/styles/yuxi-tokens.css \
  frontend/src/assets/styles/yuxi-tokens.dark.css \
  frontend/THIRD_PARTY_NOTICES.md \
  frontend/tests/plugins/yuxiThemeContract.test.js \
  frontend/src/main.js
git commit -m "feat: 引入 Yuxi 视觉 Token"
```

### 任务 2：固定 Ant Design 主题并移除任意主色

**文件：**
- 修改：`frontend/tests/plugins/antdThemeContract.test.js`
- 修改：`frontend/tests/plugins/platformShellContract.test.js`
- 修改：`frontend/src/design/antdTheme.js`
- 修改：`frontend/src/design/useAntdTheme.js`
- 修改：`frontend/src/store/modules/settings.js`
- 修改：`frontend/src/layout/components/Settings/index.vue`
- 修改：`frontend/src/utils/theme.js`

- [ ] **步骤 1：把主题契约改为 Yuxi 固定主色**

```js
const useThemeSource = readFileSync('src/design/useAntdTheme.js', 'utf8')
const settingsSource = readFileSync('src/layout/components/Settings/index.vue', 'utf8')
const settingsStoreSource = readFileSync('src/store/modules/settings.js', 'utf8')

assert.equal(lightTheme.token.colorPrimary, '#046A82')
assert.equal(lightTheme.token.colorInfo, '#1890FF')
assert.equal(lightTheme.token.colorBgLayout, '#F5F7F7')
assert.equal(lightTheme.token.colorBorder, '#EEF0F0')
assert.equal(darkTheme.token.colorPrimary, '#4A9FB8')
assert.equal(darkTheme.token.colorBgLayout, '#030303')
assert.equal(darkTheme.token.colorBgContainer, '#1F1F1F')
assert.doesNotMatch(useThemeSource, /settingsStore\.theme/)
assert.doesNotMatch(settingsSource, /el-color-picker|predefineColors|主题颜色/)
assert.doesNotMatch(settingsStoreSource, /storageSetting\.theme/)
```

- [ ] **步骤 2：运行测试确认旧主题失败**

运行：`cd frontend && node tests/plugins/antdThemeContract.test.js && node tests/plugins/platformShellContract.test.js`

预期：FAIL，当前 `colorPrimary` 为 `#3996AE`，设置面板仍包含颜色选择器。

- [ ] **步骤 3：实现固定主题映射**

`antdTheme.js` 的核心映射：

```js
export const lightTheme = {
  token: {
    colorPrimary: '#046A82',
    colorInfo: '#1890FF',
    colorSuccess: '#52C41A',
    colorWarning: '#FAAD14',
    colorError: '#FF4D4F',
    colorBgLayout: '#F5F7F7',
    colorBgContainer: '#FFFFFF',
    colorBgElevated: '#FFFFFF',
    colorBorder: '#EEF0F0',
    colorBorderSecondary: '#E4E6E6',
    colorText: '#151616',
    colorTextSecondary: '#697070',
    colorTextTertiary: '#979999',
    borderRadius: 6,
    borderRadiusLG: 8,
    controlHeight: 36,
  },
  components: sharedComponents,
}
```

`useAntdTheme()` 只选择明暗算法，不再覆盖 `colorPrimary`。设置面板删除颜色选择器和 `themeChange()`，设置存储不再读写 `theme`。`handleThemeStyle()` 只设置 `data-workspace-theme`，不写 CSS 主色。

- [ ] **步骤 4：运行主题与壳测试**

运行：`cd frontend && node tests/plugins/antdThemeContract.test.js && node tests/plugins/platformShellContract.test.js`

预期：两个测试 PASS。

- [ ] **步骤 5：提交主题入口**

```bash
git add frontend/src/design frontend/src/store/modules/settings.js \
  frontend/src/layout/components/Settings/index.vue \
  frontend/src/utils/theme.js \
  frontend/tests/plugins/antdThemeContract.test.js \
  frontend/tests/plugins/platformShellContract.test.js
git commit -m "feat: 固定 Yuxi Ant Design 主题"
```

### 任务 3：建立工作台与旧组件兼容层

**文件：**
- 修改：`frontend/tests/plugins/yuxiThemeContract.test.js`
- 修改：`frontend/src/assets/styles/workspace.scss`
- 修改：`frontend/src/assets/styles/variables.module.scss`
- 修改：`frontend/src/assets/styles/antd-reset.scss`

- [ ] **步骤 1：增加兼容别名失败测试**

```js
const workspace = readFileSync('src/assets/styles/workspace.scss', 'utf8').toLowerCase()
for (const alias of [
  '--workspace-main: var(--main-color)',
  '--workspace-main-soft: var(--main-50)',
  '--workspace-bg: var(--gray-50)',
  '--workspace-surface: var(--gray-0)',
  '--workspace-border: var(--gray-150)',
  '--workspace-text: var(--color-text)',
]) assert.ok(workspace.includes(alias))
assert.doesNotMatch(workspace, /#2f7e78|#226862|#172a2e|#20383c/)
```

- [ ] **步骤 2：运行测试确认旧别名失败**

运行：`cd frontend && node tests/plugins/yuxiThemeContract.test.js`

预期：FAIL，当前工作台变量仍写死旧主色和深色侧栏。

- [ ] **步骤 3：实现兼容别名和组件映射**

```scss
:root {
  --workspace-main: var(--main-color);
  --workspace-main-deep: var(--main-800);
  --workspace-main-soft: var(--main-50);
  --workspace-bg: var(--gray-50);
  --workspace-surface: var(--gray-0);
  --workspace-surface-muted: var(--gray-10);
  --workspace-border: var(--gray-150);
  --workspace-border-strong: var(--gray-200);
  --workspace-text: var(--color-text);
  --workspace-text-secondary: var(--color-text-secondary);
  --workspace-text-muted: var(--color-text-tertiary);
  --workspace-sidebar-bg: var(--bg-sider);
  --workspace-sidebar-surface: var(--gray-50);
  --workspace-sidebar-border: var(--gray-150);
  --workspace-sidebar-text: var(--color-text);
  --workspace-sidebar-muted: var(--color-text-secondary);
  --el-color-primary: var(--main-color);
  --el-bg-color-page: var(--gray-50);
  --el-bg-color: var(--gray-0);
  --el-border-color: var(--gray-150);
}
```

`variables.module.scss` 的活动菜单色改为 `#046a82`，旧布局所需背景改为 Yuxi 中性色。`antd-reset.scss` 的全局加载层使用语义 Token，普通表面不新增阴影。

- [ ] **步骤 4：运行主题测试和差异检查**

运行：`cd frontend && node tests/plugins/yuxiThemeContract.test.js && npm run test:plugin && git diff --check`

预期：全部 PASS。

- [ ] **步骤 5：提交兼容层**

```bash
git add frontend/src/assets/styles \
  frontend/tests/plugins/yuxiThemeContract.test.js
git commit -m "feat: 统一工作台主题兼容层"
```

### 任务 4：迁移浅色侧栏与全局壳

**文件：**
- 修改：`frontend/tests/plugins/platformShellContract.test.js`
- 修改：`frontend/tests/e2e/platform_shell_check.py`
- 修改：`frontend/src/layout/components/PlatformSidebar.vue`
- 修改：`frontend/src/layout/components/AdminSidebar.vue`
- 修改：`frontend/src/layout/components/RecentSessions.vue`
- 修改：`frontend/src/layout/components/PlatformTopbar.vue`
- 修改：`frontend/src/layout/components/TagsView/index.vue`
- 修改：`frontend/src/layout/components/AiResourceShell.vue`

- [ ] **步骤 1：增加浅色壳契约和浏览器断言**

```js
const tagsView = readFileSync('src/layout/components/TagsView/index.vue', 'utf8')

assert.match(platformSidebar, /background:\s*var\(--bg-sider\)/)
assert.match(platformSidebar, /var\(--main-50\)/)
assert.match(platformSidebar, /var\(--main-700\)/)
assert.doesNotMatch(platformSidebar, /#fff|workspace-sidebar-surface/)
assert.doesNotMatch(recentSessions, /background:\s*var\(--workspace-main\)/)
assert.doesNotMatch(tagsView, /backgroundColor:\s*theme\.value/)
```

在 `check_desktop()` 中增加：

```python
sidebar_colors = await page.locator('.platform-sidebar').evaluate(
    "element => ({background: getComputedStyle(element).backgroundColor, color: getComputedStyle(element).color})"
)
assert sidebar_colors == {'background': 'rgb(252, 254, 254)', 'color': 'rgb(21, 22, 22)'}
active_background = await page.get_by_role('link', name='智能对话', exact=True).evaluate(
    'element => getComputedStyle(element).backgroundColor'
)
assert active_background == 'rgb(225, 246, 251)'
```

- [ ] **步骤 2：运行契约测试确认深色侧栏失败**

运行：`cd frontend && node tests/plugins/platformShellContract.test.js`

预期：FAIL，当前侧栏使用 `--workspace-sidebar-bg` 深色体系。

- [ ] **步骤 3：实现浅色侧栏和中性顶栏**

核心选中态：

```scss
.platform-sidebar {
  background: var(--bg-sider);
  border-right: 1px solid var(--gray-150);
  color: var(--color-text);
}
.platform-nav-link,
.platform-new-chat,
.platform-admin-entry {
  color: var(--color-text-secondary);
}
.platform-nav-link:hover,
.platform-admin-entry:hover {
  background: var(--gray-50);
  color: var(--color-text);
}
.platform-nav-link.router-link-active {
  background: var(--main-50);
  color: var(--main-700);
}
```

最近会话使用相同浅色选中态；顶栏使用白色背景和轻边框；TagsView 活动项使用固定主色 Token，不读取用户主题色；资源壳使用 `--page-padding` 响应式变量。

- [ ] **步骤 4：运行壳契约测试**

运行：`cd frontend && node tests/plugins/platformShellContract.test.js && npm run test:plugin`

预期：全部 PASS。浏览器断言在任务 10 启动 Vite 后统一执行。

- [ ] **步骤 5：提交全局壳**

```bash
git add frontend/src/layout frontend/tests/plugins/platformShellContract.test.js \
  frontend/tests/e2e/platform_shell_check.py
git commit -m "feat: 迁移 Yuxi 浅色工作台壳"
```

### 任务 5：统一公共工作台组件

**文件：**
- 修改：`frontend/tests/plugins/antdComponentsContract.test.js`
- 修改：`frontend/tests/plugins/yuxiVisualContract.test.js`
- 修改：`frontend/src/components/Workspace/WorkspaceToolbar.vue`
- 修改：`frontend/src/components/Workspace/WorkspacePanel.vue`
- 修改：`frontend/src/components/Workspace/WorkspaceState.vue`

- [ ] **步骤 1：创建公共组件视觉失败测试**

```js
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

for (const file of [
  'src/components/Workspace/WorkspaceToolbar.vue',
  'src/components/Workspace/WorkspacePanel.vue',
  'src/components/Workspace/WorkspaceState.vue',
]) {
  const source = readFileSync(file, 'utf8').toLowerCase()
  assert.doesNotMatch(source, /#2f7e78|#226862|#409eff|box-shadow:/)
}
const panel = readFileSync('src/components/Workspace/WorkspacePanel.vue', 'utf8')
assert.match(panel, /border:\s*1px solid var\(--gray-150\)/)
assert.match(panel, /border-radius:\s*8px/)
const toolbar = readFileSync('src/components/Workspace/WorkspaceToolbar.vue', 'utf8')
assert.match(toolbar, /font-size:\s*2[0-4]px/)
```

- [ ] **步骤 2：运行测试确认旧工作台变量失败**

运行：`cd frontend && node tests/plugins/yuxiVisualContract.test.js`

预期：FAIL，组件仍使用旧表面别名且测试文件尚未满足新规则。

- [ ] **步骤 3：实现统一公共组件**

`WorkspacePanel` 使用 `var(--gray-0)`、`var(--gray-150)` 和 `8px`，不加阴影。`WorkspaceToolbar` 标题使用 `22px/600`、说明 `13px`；移动端允许操作换行。`WorkspaceState` 使用语义状态背景，错误和过期状态显示明确图标与重试按钮。

- [ ] **步骤 4：运行公共组件测试**

运行：`cd frontend && node tests/plugins/antdComponentsContract.test.js && node tests/plugins/yuxiVisualContract.test.js`

预期：两个测试 PASS。

- [ ] **步骤 5：提交公共组件**

```bash
git add frontend/src/components/Workspace \
  frontend/tests/plugins/antdComponentsContract.test.js \
  frontend/tests/plugins/yuxiVisualContract.test.js
git commit -m "feat: 统一 Yuxi 工作台公共组件"
```

### 任务 6：迁移智能对话与认证页面

**文件：**
- 修改：`frontend/tests/plugins/antdAiViewsContract.test.js`
- 修改：`frontend/tests/plugins/antdAuthShellContract.test.js`
- 修改：`frontend/plugins/ai/views/chat/index.vue`
- 修改：`frontend/plugins/ai/views/chat/components/AiMessage.vue`
- 修改：`frontend/src/components/Antd/AuthShell.vue`
- 修改：`frontend/src/views/login.vue`
- 修改：`frontend/src/views/register.vue`
- 修改：`frontend/src/views/lock.vue`
- 修改：`frontend/src/views/error/401.vue`
- 修改：`frontend/src/views/error/404.vue`

- [ ] **步骤 1：增加对话与认证视觉契约**

```js
const authShell = readFileSync('src/components/Antd/AuthShell.vue', 'utf8')

assert.doesNotMatch(chatView, /workspace-shadow-soft|box-shadow:/)
assert.match(chatView, /background:\s*var\(--gray-50\)/)
assert.match(chatView, /background:\s*var\(--gray-0\)/)
assert.doesNotMatch(authShell, /workspace-shadow-float|auth-shell::before/)
assert.match(authShell, /border:\s*1px solid var\(--gray-150\)/)
assert.doesNotMatch(login, /AI WORKSPACE/)
```

- [ ] **步骤 2：运行测试确认当前认证和对话表面失败**

运行：`cd frontend && node tests/plugins/antdAiViewsContract.test.js && node tests/plugins/antdAuthShellContract.test.js`

预期：FAIL，认证页仍有英文眉题和阴影，对话输入区仍使用软阴影。

- [ ] **步骤 3：实现对话与认证视觉迁移**

- 对话消息保持连续流布局，用户与助手角色仍清晰，但不增加装饰卡片。
- 输入区使用白色轻边框表面，移除普通阴影；focus 使用 `--main-color`。
- 状态、用量和错误使用语义 Token。
- 认证页删除英文眉题和顶部装饰条，保留智衡品牌、验证码、记住密码和重定向。
- 认证表面使用 `8px`、轻边框和无阴影；移动端保持 `min(100%, 420px)`。

- [ ] **步骤 4：运行对话、认证和 SSE 测试**

运行：`cd frontend && node tests/plugins/antdAiViewsContract.test.js && node tests/plugins/antdAuthShellContract.test.js && node tests/plugins/sseParser.test.js`

预期：全部 PASS。

- [ ] **步骤 5：提交对话与认证页面**

```bash
git add frontend/plugins/ai/views/chat \
  frontend/src/components/Antd/AuthShell.vue \
  frontend/src/views/login.vue frontend/src/views/register.vue \
  frontend/src/views/lock.vue frontend/src/views/error \
  frontend/tests/plugins/antdAiViewsContract.test.js \
  frontend/tests/plugins/antdAuthShellContract.test.js
git commit -m "feat: 迁移 Yuxi 对话与认证视觉"
```

### 任务 7：迁移模型中心

**文件：**
- 修改：`frontend/tests/plugins/antdAiViewsContract.test.js`
- 修改：`frontend/plugins/ai/views/model/index.vue`
- 修改：`frontend/plugins/ai/views/model/components/PlatformModelsPanel.vue`
- 修改：`frontend/plugins/ai/views/model/components/EngineDeploymentsPanel.vue`
- 修改：`frontend/plugins/ai/views/model/components/CredentialsPanel.vue`
- 修改：`frontend/plugins/ai/views/model/components/RuntimeStatusPanel.vue`
- 修改：`frontend/plugins/ai/views/model/components/ModelOnboardingDrawer.vue`
- 修改：`frontend/plugins/ai/views/model/components/CredentialForm.vue`
- 修改：`frontend/plugins/ai/views/model/components/DeploymentForm.vue`

- [ ] **步骤 1：增加模型中心视觉契约**

```js
for (const fileName of modelFiles) {
  const source = readFileSync(fileName, 'utf8').toLowerCase()
  assert.doesNotMatch(source, /#2f7e78|#226862|#409eff|box-shadow:/)
}
const modelIndex = readFileSync('plugins/ai/views/model/index.vue', 'utf8')
assert.match(modelIndex, /border:\s*1px solid var\(--gray-150\)/)
assert.match(modelIndex, /background:\s*var\(--gray-0\)/)
```

- [ ] **步骤 2：运行模型页面契约确认失败**

运行：`cd frontend && node tests/plugins/antdAiViewsContract.test.js`

预期：FAIL，模型页面仍使用旧 `--workspace-*` 表面规则和旧边框层级。

- [ ] **步骤 3：实现模型资源视觉统一**

- 页签保留“平台模型、引擎部署、凭据管理、运行状态”。
- 模型表格使用浅表头、`44px` 至 `48px` 行高和统一状态标签。
- Chat、Embedding、Rerank 显示“对话、向量、排序”。
- 行内低频操作继续使用“更多”菜单。
- 接入抽屉、凭据表单和部署表单使用统一字段间距、helper text 和固定底部操作区。
- 不改变接入步骤、权限门禁、秘密字段和失败保留逻辑。

- [ ] **步骤 4：运行模型契约和接入转换测试**

运行：`cd frontend && node tests/plugins/antdAiViewsContract.test.js && node tests/plugins/modelOnboardingContract.test.js && node tests/plugins/modelEngineTransforms.test.js`

预期：全部 PASS。

- [ ] **步骤 5：提交模型中心**

```bash
git add frontend/plugins/ai/views/model \
  frontend/tests/plugins/antdAiViewsContract.test.js
git commit -m "feat: 统一 Yuxi 模型中心视觉"
```

### 任务 8：迁移开发接入与用量记录

**文件：**
- 修改：`frontend/tests/plugins/antdAiViewsContract.test.js`
- 修改：`frontend/plugins/ai/views/application/index.vue`
- 修改：`frontend/plugins/ai/views/usage/index.vue`

- [ ] **步骤 1：增加应用与用量视觉契约**

```js
for (const fileName of applicationFiles) {
  const source = readFileSync(fileName, 'utf8').toLowerCase()
  assert.doesNotMatch(source, /#2f7e78|#226862|#409eff|box-shadow:/)
  assert.match(source, /var\(--gray-0\)/)
  assert.match(source, /var\(--gray-150\)/)
}
assert.match(applicationView, /application-row\.is-active[\s\S]*var\(--main-50\)/)
```

- [ ] **步骤 2：运行契约确认旧表面规则失败**

运行：`cd frontend && node tests/plugins/antdAiViewsContract.test.js`

预期：FAIL，页面仍使用旧工作台表面与选中态变量。

- [ ] **步骤 3：实现应用、Key 与用量视觉统一**

- 应用主从列表使用一个轻边框容器和行分割线，选中使用 `--main-50`。
- Key 状态、授权数量、最近使用和配额摘要保持当前数据来源。
- 完整 Key 单次展示、关闭清空、轮换和吊销逻辑不变。
- 用量筛选区、摘要和明细表使用统一间距；移动端保持稳定字段顺序。
- 过期数据提示使用 warning 浅背景和深文字。

- [ ] **步骤 4：运行应用转换与页面契约测试**

运行：`cd frontend && node tests/plugins/antdAiViewsContract.test.js && node tests/plugins/applicationTransforms.test.js`

预期：全部 PASS。

- [ ] **步骤 5：提交开发接入与用量**

```bash
git add frontend/plugins/ai/views/application/index.vue \
  frontend/plugins/ai/views/usage/index.vue \
  frontend/tests/plugins/antdAiViewsContract.test.js
git commit -m "feat: 统一 Yuxi 开发接入与用量视觉"
```

### 任务 9：清理旧全局主题污染并兼容低频页面

**文件：**
- 修改：`frontend/tests/plugins/yuxiVisualContract.test.js`
- 修改：`frontend/tests/plugins/elementPlusRemovalContract.test.js`
- 修改：`frontend/src/assets/styles/zhiheng.scss`
- 修改：`frontend/src/assets/styles/sidebar.scss`
- 修改：`frontend/src/assets/styles/index.scss`

- [ ] **步骤 1：增加旧主色扫描测试**

```js
const bannedThemeColors = /#(?:2f7e78|226862|409eff|20b2aa|48d1cc|42b983)\b/i
const compatibilityTargets = [
  'src/assets/styles/zhiheng.scss',
  'src/assets/styles/sidebar.scss',
  'src/assets/styles/index.scss',
]
for (const fileName of compatibilityTargets) {
  assert.doesNotMatch(readFileSync(fileName, 'utf8'), bannedThemeColors)
}

// 在 elementPlusRemovalContract.test.js 中收紧既有基线：
assert.ok(references.length <= 43, 'Yuxi 视觉迁移不得增加 Element Plus 引用文件')
```

- [ ] **步骤 2：运行扫描并记录所有失败文件**

运行：

```bash
cd frontend
node tests/plugins/yuxiVisualContract.test.js
rg -n '#(?:2F7E78|226862|409EFF|20B2AA|48D1CC|42B983)' src plugins/ai -g '*.{vue,js,scss,css}'
```

预期：FAIL，并列出旧全局样式中的硬编码主题色。

- [ ] **步骤 3：只替换主题用途的旧色值**

- 主操作和链接改为 `var(--main-color)`。
- 普通背景改为 `var(--gray-0)`、`var(--gray-10)` 或 `var(--gray-50)`。
- 边框改为 `var(--gray-150)` 或 `var(--gray-200)`。
- 文本改为 `var(--color-text*)`。
- HTTP 方法、业务图表和错误语义中确有分类含义的颜色保留，但必须改用现有语义或图表 Token，不直接复用主色。
- 不改变低频页面 DOM、表单字段和业务事件。

- [ ] **步骤 4：运行全量插件测试和扫描**

运行：`cd frontend && npm run test:plugin && git diff --check`

预期：所有插件测试 PASS；旧主题色扫描无核心或全局样式命中；Element Plus 引用文件数不高于基线。

- [ ] **步骤 5：提交兼容清理**

```bash
git add frontend/src/assets/styles \
  frontend/tests/plugins/yuxiVisualContract.test.js \
  frontend/tests/plugins/elementPlusRemovalContract.test.js
git commit -m "refactor: 清理旧前端主题污染"
```

### 任务 10：完成浏览器视觉验收、构建和部署验证

**文件：**
- 修改：`frontend/tests/e2e/platform_shell_check.py`
- 修改：`frontend/tests/e2e/antd_chat_check.py`
- 验证：`frontend/tests/plugins/*.test.js`

- [ ] **步骤 1：补齐四个目标视口与明暗主题截图场景**

在 `platform_shell_check.py` 中固定以下视口：

```python
VIEWPORTS = {
    'desktop-wide': {'width': 1440, 'height': 900},
    'desktop': {'width': 1280, 'height': 800},
    'tablet': {'width': 768, 'height': 1024},
    'mobile': {'width': 375, 'height': 812},
}
```

每个核心场景执行：

```python
assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
assert console_errors == []
assert page_errors == []
await page.screenshot(path=str(SCREENSHOT_DIR / screenshot_name), full_page=True)
```

暗色场景通过点击“切换主题”进入，断言 `html.dark`、暗色侧栏 `rgb(20, 20, 20)` 和内容容器 `rgb(31, 31, 31)`。

- [ ] **步骤 2：启动独立 Vite 服务并运行浏览器测试**

终端 A：

```bash
cd frontend
NODE_OPTIONS=--max-old-space-size=4096 npm run dev -- --host 127.0.0.1 --port 15174
```

终端 B：

```bash
cd frontend
PLATFORM_SHELL_BASE_URL=http://127.0.0.1:15174 \
PLATFORM_SHELL_SCREENSHOT_DIR=/tmp/yuxi-visual \
/root/miniconda3/envs/dj/bin/python tests/e2e/platform_shell_check.py
PLATFORM_SHELL_BASE_URL=http://127.0.0.1:15174 \
/root/miniconda3/envs/dj/bin/python tests/e2e/antd_chat_check.py
```

预期：两个脚本退出码为 0，截图目录包含桌面、平板、手机、浅色、暗色和对话状态截图。

- [ ] **步骤 3：人工检查截图与像素边界**

逐张检查：

- 侧栏为 Yuxi 浅色或暗色 Token，不再出现深青黑混合主题。
- 主按钮、菜单选中和焦点使用 `#046A82` 或暗色 `#4A9FB8`。
- 普通卡片无阴影，表面圆角不超过 `8px`。
- 标题、按钮、表格、会话、抽屉和移动端控件无重叠。
- 长模型名、长会话名、空状态、错误状态和加载状态不改变布局宽度。

若截图不满足任一项，在对应任务文件中做最小修正并重新运行浏览器脚本。

- [ ] **步骤 4：运行最终全量验证**

```bash
cd frontend
npm run test:plugin
NODE_OPTIONS=--max-old-space-size=4096 npm run build:docker
git diff --check
```

预期：插件测试全部 PASS；Vite 生产构建退出码 0；`dist/index.html` 引用新的哈希资源；差异检查无输出。

验证静态压缩：

```bash
main_asset=$(sed -n 's/.*src="\/\(static\/js\/index-[^"]*\.js\)".*/\1/p' dist/index.html | head -n 1)
test -f "dist/$main_asset"
test -f "dist/$main_asset.gz"
```

- [ ] **步骤 5：提交最终验收并记录部署结果**

```bash
git add frontend/tests/e2e/platform_shell_check.py \
  frontend/tests/e2e/antd_chat_check.py
git commit -m "test: 验证 Yuxi 视觉层迁移"
```

部署环境具备 npm 网络时执行：

```bash
docker compose -p zhiheng-vue3-fastapi \
  --env-file /home/ja/project/ZhiHeng-Vue3-FastAPI/.env \
  -f docker-compose.pg.yml up -d --build zhiheng-frontend
docker compose -p zhiheng-vue3-fastapi \
  --env-file /home/ja/project/ZhiHeng-Vue3-FastAPI/.env \
  -f docker-compose.pg.yml ps
```

若 Docker npm 安装因外部镜像网络超时，保留已通过的本地生产构建证据并明确报告，不把网络失败描述为代码失败或部署成功。

## 最终完成检查

- [ ] Yuxi Token 和 MIT 许可说明存在且测试通过。
- [ ] Ant Design Vue 固定使用 `#046A82` / `#4A9FB8` 主色。
- [ ] 任意主题色选择器和运行时主色覆盖已移除。
- [ ] AI 与管理侧栏、顶栏、标签栏和移动抽屉使用统一视觉体系。
- [ ] 智能对话、模型中心、开发接入、用量和认证页面通过契约测试。
- [ ] 低频旧页面没有新增 Element Plus 引用，旧主题色不再污染核心页面。
- [ ] 四个目标视口、明暗主题和边界状态截图通过人工检查。
- [ ] 浏览器 console error、page error 和文档横向溢出均为零。
- [ ] 前端插件测试、生产构建、gzip 产物和 `git diff --check` 通过。
- [ ] 所有实现提交职责单一，可独立回滚。
