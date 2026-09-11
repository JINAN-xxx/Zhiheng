# 知识库扩展多页面实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将知识库归入“智能体扩展”，重构为风格一致的卡片总览，以及文档、检索、图谱三个独立工作页面。

**架构：** 保留 AI 插件的 `/ai/knowledge` 动态路由与后端 API，通过 `dbId`、`view` 查询参数表达可恢复的子页面状态。`index.vue` 作为数据与弹窗外壳，四个专用视图组件负责呈现，总览卡片和页面内导航使用路由跳转。

**技术栈：** Vue 3 Composition API、Vue Router 4、Ant Design Vue 4、现有 Workspace 组件、Node 断言测试、Playwright E2E、Vite 6。

---

## 文件结构

- 修改：`ruoyi-fastapi-frontend/src/components/Workspace/workspaceTransforms.js` — 将知识库加入智能体扩展资源组。
- 修改：`ruoyi-fastapi-frontend/src/layout/components/PlatformSidebar.vue` — 删除知识库一级入口，保留智能体扩展分组入口。
- 修改：`ruoyi-fastapi-frontend/tests/plugins/agentNavigation.test.js` — 固化菜单归组和权限过滤行为。
- 修改：`ruoyi-fastapi-frontend/plugins/ai/utils/knowledge.js` — 提供子页面状态归一化和路由地址构造函数。
- 修改：`ruoyi-fastapi-frontend/tests/plugins/knowledgeTransforms.test.js` — 覆盖路由状态、图谱回退和地址构造。
- 创建：`ruoyi-fastapi-frontend/plugins/ai/views/knowledge/components/KnowledgeSectionNav.vue` — 子页面权限感知导航。
- 创建：`ruoyi-fastapi-frontend/plugins/ai/views/knowledge/components/KnowledgeOverview.vue` — 知识库筛选、卡片和直接入口。
- 创建：`ruoyi-fastapi-frontend/plugins/ai/views/knowledge/components/KnowledgeDocuments.vue` — 文档筛选、目录、表格和上传入口。
- 创建：`ruoyi-fastapi-frontend/plugins/ai/views/knowledge/components/KnowledgeQuery.vue` — 查询表单、指标和结果。
- 创建：`ruoyi-fastapi-frontend/plugins/ai/views/knowledge/components/KnowledgeGraphPage.vue` — 图谱筛选、状态和全宽画布。
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/knowledge/index.vue` — 路由外壳、共享状态、数据请求和弹窗编排。
- 修改：`ruoyi-fastapi-frontend/tests/e2e/knowledge_check.py` — 验证多页面导航、创建后进入文档页、权限和移动端。

### 任务 1：菜单归组与子页面路由契约

**文件：**
- 修改：`ruoyi-fastapi-frontend/tests/plugins/agentNavigation.test.js`
- 修改：`ruoyi-fastapi-frontend/tests/plugins/knowledgeTransforms.test.js`
- 修改：`ruoyi-fastapi-frontend/src/components/Workspace/workspaceTransforms.js`
- 修改：`ruoyi-fastapi-frontend/src/layout/components/PlatformSidebar.vue`
- 修改：`ruoyi-fastapi-frontend/plugins/ai/utils/knowledge.js`

- [ ] **步骤 1：补充失败的菜单与路由测试**

在 `knowledgeTransforms.test.js` 的导入中加入 `buildKnowledgeLocation` 和 `normalizeKnowledgeView`，并增加：

```js
assert.deepEqual(buildKnowledgeLocation('kb /中文', 'query'), {
  path: '/ai/knowledge',
  query: { dbId: 'kb /中文', view: 'query' },
})
assert.equal(normalizeKnowledgeView('documents', false), 'documents')
assert.equal(normalizeKnowledgeView('query', false), 'query')
assert.equal(normalizeKnowledgeView('graph', true), 'graph')
assert.equal(normalizeKnowledgeView('graph', false), 'documents')
assert.equal(normalizeKnowledgeView('unknown', true), 'documents')
```

在 `agentNavigation.test.js` 中断言管理员分组顺序为 `['agent', 'skill', 'knowledge']`，仅有 `ai:knowledge:list` 时仍返回“智能体扩展”分组且入口路径为 `/ai/knowledge`。

- [ ] **步骤 2：运行测试并确认按预期失败**

运行：

```bash
node tests/plugins/knowledgeTransforms.test.js
node tests/plugins/agentNavigation.test.js
```

预期：路由辅助函数尚未导出，且未完成菜单归组时断言失败。

- [ ] **步骤 3：实现最少路由辅助函数与菜单归组**

在 `knowledge.js` 中加入：

```js
const KNOWLEDGE_VIEWS = new Set(['documents', 'query', 'graph'])

export function normalizeKnowledgeView(view, graphEnabled = false) {
  const normalized = text(view)
  if (!KNOWLEDGE_VIEWS.has(normalized)) return 'documents'
  return normalized === 'graph' && !graphEnabled ? 'documents' : normalized
}

export function buildKnowledgeLocation(dbId, view = 'documents') {
  return {
    path: '/ai/knowledge',
    query: { dbId: text(dbId), view: normalizeKnowledgeView(view, true) },
  }
}
```

在 `AGENT_EXTENSION_TABS` 中加入知识库项；`PlatformSidebar.vue` 删除独立的 `canKnowledge` 和一级知识库 `navItems` 项。

- [ ] **步骤 4：运行定向测试确认通过**

运行：

```bash
node tests/plugins/knowledgeTransforms.test.js
node tests/plugins/agentNavigation.test.js
```

预期：两个命令均退出码 0。

- [ ] **步骤 5：提交菜单与路由契约**

```bash
git add ruoyi-fastapi-frontend/src/components/Workspace/workspaceTransforms.js ruoyi-fastapi-frontend/src/layout/components/PlatformSidebar.vue ruoyi-fastapi-frontend/plugins/ai/utils/knowledge.js ruoyi-fastapi-frontend/tests/plugins/agentNavigation.test.js ruoyi-fastapi-frontend/tests/plugins/knowledgeTransforms.test.js
git commit -m "feat(knowledge): 将知识库归入智能体扩展"
```

### 任务 2：卡片总览与创建后进入文档页

**文件：**
- 创建：`ruoyi-fastapi-frontend/plugins/ai/views/knowledge/components/KnowledgeOverview.vue`
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/knowledge/index.vue`
- 修改：`ruoyi-fastapi-frontend/tests/e2e/knowledge_check.py`

- [ ] **步骤 1：把 E2E 改为新的总览与创建行为**

将桌面测试中创建知识库后的断言改为：

```python
await button(modal(page), '确定').click()
await expect(modal(page)).to_have_count(0)
await expect(page).to_have_url(re.compile(r'dbId=kb-new.*view=documents'))
await expect(button(page, '上传文档')).to_be_visible()
await button(page, '返回知识库').click()
row = page.locator('.knowledge-card').filter(has_text='测试新库修正')
await expect(button(row, '管理文档')).to_be_visible()
await expect(button(row, '测试检索')).to_be_visible()
```

总览测试不再点击知识库名称进入旧详情，而是从具体卡片点击“管理文档”。

- [ ] **步骤 2：运行知识库 E2E 并确认失败**

运行开发服务器：

```bash
npm exec vite -- --host 127.0.0.1 --port 12582
```

另一个终端运行：

```bash
KNOWLEDGE_CHECK_BASE_URL=http://127.0.0.1:12582 python tests/e2e/knowledge_check.py
```

预期：创建后 URL 未进入 `view=documents`，且 `.knowledge-card` 或“管理文档”不存在。

- [ ] **步骤 3：创建知识库总览组件**

`KnowledgeOverview.vue` 使用以下公开接口：

```js
const props = defineProps({
  databases: { type: Array, default: () => [] },
  loading: Boolean,
  error: { type: String, default: '' },
  permissions: { type: Array, default: () => [] },
})
const emit = defineEmits(['refresh', 'create', 'edit', 'remove', 'open'])
```

组件内部维护搜索和存储类型筛选，渲染 `.knowledge-grid` 与 `.knowledge-card`。卡片操作通过 `open` 事件传出 `{ database, view }`，按钮可访问名称分别为“管理文档”“测试检索”“查看图谱”“编辑知识库”“删除知识库”。图谱按钮仅在 `enable_knowledge_graph` 为真时显示。

样式复用智能体与技能管理的 20/24px 内容边距、三列卡片、8px 圆角、36px 图标、13px 描述和带顶部边框的卡片操作区。

- [ ] **步骤 4：让外壳根据路由显示总览并在创建后导航**

`index.vue` 注入 `useRoute`、`useRouter`，实现：

```js
function openKnowledgeView(database, view) {
  router.push(buildKnowledgeLocation(database.db_id, view))
}

async function enterCreatedDatabase(result) {
  const created = unwrapKnowledgeResponse(result)
  await loadDatabases()
  const dbId = created.db_id || created.database?.db_id
  if (!dbId) throw new Error('知识库已创建，但服务器未返回知识库标识')
  await router.push(buildKnowledgeLocation(dbId, 'documents'))
}
```

创建分支调用 `enterCreatedDatabase(result)`；总览只在路由没有 `dbId` 时显示。

- [ ] **步骤 5：运行 E2E 到创建和总览断言通过**

运行：

```bash
KNOWLEDGE_CHECK_BASE_URL=http://127.0.0.1:12582 python tests/e2e/knowledge_check.py
```

预期：创建后进入文档地址，总览卡片和直接入口可见；后续旧页签断言仍失败，留给任务 3、4。

- [ ] **步骤 6：提交总览页面**

```bash
git add ruoyi-fastapi-frontend/plugins/ai/views/knowledge/components/KnowledgeOverview.vue ruoyi-fastapi-frontend/plugins/ai/views/knowledge/index.vue ruoyi-fastapi-frontend/tests/e2e/knowledge_check.py
git commit -m "feat(knowledge): 增加知识库卡片总览"
```

### 任务 3：独立文档管理页与子页面导航

**文件：**
- 创建：`ruoyi-fastapi-frontend/plugins/ai/views/knowledge/components/KnowledgeSectionNav.vue`
- 创建：`ruoyi-fastapi-frontend/plugins/ai/views/knowledge/components/KnowledgeDocuments.vue`
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/knowledge/index.vue`
- 修改：`ruoyi-fastapi-frontend/tests/e2e/knowledge_check.py`

- [ ] **步骤 1：把文档 E2E 从页签改为路由页面**

增加以下断言并移除 `get_by_role('tab')`：

```python
await expect(page.get_by_text('文档管理', exact=True).first).to_be_visible()
await expect(button(page, '上传文档')).to_be_visible()
await button(page, '检索测试').click()
await expect(page).to_have_url(re.compile(r'view=query'))
await button(page, '文档管理').click()
await expect(page).to_have_url(re.compile(r'view=documents'))
```

移动端只读权限测试从知识库卡片点击“管理文档”，继续断言上传、新建文件夹和重新切片均不可见。

- [ ] **步骤 2：运行 E2E 并确认独立导航断言失败**

运行：

```bash
KNOWLEDGE_CHECK_BASE_URL=http://127.0.0.1:12582 python tests/e2e/knowledge_check.py
```

预期：找不到“检索测试”页面按钮或 URL 未更新。

- [ ] **步骤 3：创建共享子页面导航**

`KnowledgeSectionNav.vue` 接口为：

```js
const props = defineProps({
  activeView: { type: String, required: true },
  graphEnabled: Boolean,
})
const emit = defineEmits(['navigate'])
```

渲染三个普通 `a-button`，活动按钮增加 `is-active` 类；文档与检索始终显示，图谱只在 `graphEnabled` 时显示。点击发出 `documents`、`query` 或 `graph`。

- [ ] **步骤 4：创建文档管理组件**

`KnowledgeDocuments.vue` 接收知识库、目录、文档、加载状态、错误和权限；发出 `refresh`、`navigate`、`select-folder`、`create-folder`、`upload`、`preview`、`edit`、`remove`。组件把“上传文档”放在顶部页面操作区，把现有筛选、面包屑和表格原样迁入。

外壳只在 `activeView === 'documents'` 时调用 `loadDocuments()`；离开文档页时增加代次并中止上传控制器。顶部“返回知识库”调用 `router.push('/ai/knowledge')`。

- [ ] **步骤 5：运行文档 E2E 断言确认通过**

运行：

```bash
KNOWLEDGE_CHECK_BASE_URL=http://127.0.0.1:12582 python tests/e2e/knowledge_check.py
```

预期：总览、创建后导航、文档 CRUD、上传失败重试和移动端只读权限均通过；图谱旧断言仍待任务 4 更新。

- [ ] **步骤 6：提交文档页面**

```bash
git add ruoyi-fastapi-frontend/plugins/ai/views/knowledge/components/KnowledgeSectionNav.vue ruoyi-fastapi-frontend/plugins/ai/views/knowledge/components/KnowledgeDocuments.vue ruoyi-fastapi-frontend/plugins/ai/views/knowledge/index.vue ruoyi-fastapi-frontend/tests/e2e/knowledge_check.py
git commit -m "feat(knowledge): 拆分独立文档管理页"
```

### 任务 4：独立检索测试与知识图谱页

**文件：**
- 创建：`ruoyi-fastapi-frontend/plugins/ai/views/knowledge/components/KnowledgeQuery.vue`
- 创建：`ruoyi-fastapi-frontend/plugins/ai/views/knowledge/components/KnowledgeGraphPage.vue`
- 修改：`ruoyi-fastapi-frontend/plugins/ai/views/knowledge/index.vue`
- 修改：`ruoyi-fastapi-frontend/tests/e2e/knowledge_check.py`

- [ ] **步骤 1：改写检索和图谱 E2E 导航断言**

使用页面按钮和 URL 断言替代旧页签操作：

```python
await button(page, '检索测试').click()
await expect(page).to_have_url(re.compile(r'view=query'))
await page.get_by_label('检索问题', exact=True).fill('验证知识')
await button(page, '检索').click()
await expect(page.locator('.query-passage p')).to_contain_text(UNSAFE)

await button(page, '知识图谱').click()
await expect(page).to_have_url(re.compile(r'view=graph'))
await expect(page.locator('.knowledge-graph canvas')).to_be_visible()
```

为不启用图谱的知识库增加直接访问 `view=graph` 后回退 `view=documents` 的断言。

- [ ] **步骤 2：运行 E2E 并确认按预期失败**

运行：

```bash
KNOWLEDGE_CHECK_BASE_URL=http://127.0.0.1:12582 python tests/e2e/knowledge_check.py
```

预期：检索与图谱独立页面尚不存在或 URL 未更新。

- [ ] **步骤 3：创建检索页面组件**

`KnowledgeQuery.vue` 接收 `database`、`loading`、`error`、`result`、`elapsed`，发出 `navigate`、`refresh` 和 `query`。`query` 负载为：

```js
{
  text: queryText.trim(),
  target: queryTarget,
  mode: queryMode,
}
```

组件保留现有检索目标、图谱模式、结果指标、回答和片段列表；外壳继续持有 `AbortController` 和请求代次。

- [ ] **步骤 4：创建知识图谱页面组件**

`KnowledgeGraphPage.vue` 接收 `database`、`labels`、`graph`、`loading`、`error`，发出 `navigate`、`refresh` 和 `load`。`load` 负载为 `{ node_label, max_depth, max_nodes }`。组件直接渲染全宽 `KnowledgeGraph`，不增加外层卡片。

外壳解析数据库后用 `normalizeKnowledgeView(route.query.view, database.enable_knowledge_graph)` 校正视图；若发生回退，使用 `router.replace` 更新地址。进入图谱页加载标签和默认图谱，离开页面后旧结果不得写回。

- [ ] **步骤 5：运行完整知识库 E2E**

运行：

```bash
KNOWLEDGE_CHECK_BASE_URL=http://127.0.0.1:12582 python tests/e2e/knowledge_check.py
```

预期：桌面 CRUD、上传、检索、图谱、地址状态和移动端权限全部通过，浏览器无 page error。

- [ ] **步骤 6：提交检索与图谱页面**

```bash
git add ruoyi-fastapi-frontend/plugins/ai/views/knowledge/components/KnowledgeQuery.vue ruoyi-fastapi-frontend/plugins/ai/views/knowledge/components/KnowledgeGraphPage.vue ruoyi-fastapi-frontend/plugins/ai/views/knowledge/index.vue ruoyi-fastapi-frontend/tests/e2e/knowledge_check.py
git commit -m "feat(knowledge): 拆分检索与知识图谱页面"
```

### 任务 5：回归、构建和视觉验收

**文件：**
- 修改：前述文件中验证发现的本任务相关问题
- 不修改：`ruoyi-fastapi-app/**`、现有无关 `dist/**` 和 `.superpowers/**`

- [ ] **步骤 1：运行全部前端插件契约测试**

运行：

```bash
npm run test:plugin
```

预期：全部插件契约测试通过，输出无失败堆栈。

- [ ] **步骤 2：运行知识库桌面和移动端 E2E**

运行：

```bash
KNOWLEDGE_CHECK_BASE_URL=http://127.0.0.1:12582 python tests/e2e/knowledge_check.py
```

预期：桌面和 390px 移动端场景通过，并生成 `/tmp/knowledge-desktop-graph.png` 与 `/tmp/knowledge-mobile-documents.png`。

- [ ] **步骤 3：检查截图和页面像素**

检查两张截图：工具栏、卡片、按钮、表格、查询结果和图谱不得重叠；移动端文档页宽度不得超过视口。若截图存在布局问题，只修改知识库页面相关样式，然后重复步骤 1、2。

- [ ] **步骤 4：执行生产构建**

运行：

```bash
NODE_OPTIONS=--max-old-space-size=4096 npm exec vite build -- --outDir /tmp/ruoyi-knowledge-multipage-build --emptyOutDir
```

预期：Vite 构建成功退出，生成 `index.html` 和静态资源，不写入仓库 `dist`。

- [ ] **步骤 5：检查最终差异范围**

运行：

```bash
git diff --check
git status --short
```

预期：本任务只涉及计划列出的前端源文件和测试；既有 `ruoyi-fastapi-app` 删除、构建产物和视觉伴侣文件保持原状且未进入提交。

- [ ] **步骤 6：提交最终验证修正**

仅在步骤 1-4 产生额外修正时执行：

```bash
git add ruoyi-fastapi-frontend/plugins/ai/views/knowledge ruoyi-fastapi-frontend/plugins/ai/utils/knowledge.js ruoyi-fastapi-frontend/src/components/Workspace/workspaceTransforms.js ruoyi-fastapi-frontend/src/layout/components/PlatformSidebar.vue ruoyi-fastapi-frontend/tests/plugins/agentNavigation.test.js ruoyi-fastapi-frontend/tests/plugins/knowledgeTransforms.test.js ruoyi-fastapi-frontend/tests/e2e/knowledge_check.py
git commit -m "fix(knowledge): 完成多页面响应式回归"
```
