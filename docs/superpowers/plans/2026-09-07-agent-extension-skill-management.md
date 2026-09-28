# 智能体扩展与技能管理实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 新增独立的智能体扩展插件，交付平台共享技能的上传、查看、启停、删除闭环，并允许普通用户在智能对话中按消息选择技能。

**架构：** `agent_extension` 插件拥有技能元数据、安全解析、持久文件和管理接口；`ai` 插件通过应用状态中的窄接口解析已选技能并注入当前模型请求。前端技能管理与对话选择分别位于独立组件中，知识库实现不在本计划范围内。

**技术栈：** FastAPI、SQLAlchemy 2、Pydantic 2、PostgreSQL/MySQL、Vue 3、Ant Design Vue、现有插件运行时、pytest、Node 契约测试、Playwright。

---

## 文件结构

### 新增后端插件

- `zhiheng-fastapi-backend/plugins/agent_extension/plugin.yaml`：插件、菜单、迁移和权限声明。
- `zhiheng-fastapi-backend/plugins/agent_extension/config.py`：技能存储路径与固定安全上限。
- `zhiheng-fastapi-backend/plugins/agent_extension/hooks.py`：创建存储目录并注册技能上下文提供器。
- `zhiheng-fastapi-backend/plugins/agent_extension/domain/skill_package.py`：Markdown/ZIP 解析与路径安全校验。
- `zhiheng-fastapi-backend/plugins/agent_extension/domain/skill_errors.py`：固定公开错误契约。
- `zhiheng-fastapi-backend/plugins/agent_extension/infrastructure/skill_storage.py`：草稿、正式目录和垃圾目录的原子文件操作。
- `zhiheng-fastapi-backend/plugins/agent_extension/entity/do/skill_do.py`：`AgentSkill` ORM 模型。
- `zhiheng-fastapi-backend/plugins/agent_extension/entity/vo/skill_vo.py`：分页、上传、状态和响应模型。
- `zhiheng-fastapi-backend/plugins/agent_extension/dao/skill_dao.py`：技能查询与状态写入。
- `zhiheng-fastapi-backend/plugins/agent_extension/service/skill_service.py`：上传确认、启停、删除和对话上下文服务。
- `zhiheng-fastapi-backend/plugins/agent_extension/controller/skill_controller.py`：管理接口和可用技能接口。
- `zhiheng-fastapi-backend/plugins/agent_extension/migrations/{mysql,postgresql}/001_skill_management.sql`：技能表结构。
- `zhiheng-fastapi-backend/plugins/agent_extension/buildin/{deep-research,document-summary}/SKILL.md`：两项纯提示词内置技能。

### 新增前端插件

- `zhiheng-fastapi-frontend/plugins/agent_extension/api/skill.js`：技能管理和可用技能接口。
- `zhiheng-fastapi-frontend/plugins/agent_extension/utils/skill.js`：响应标准化与状态转换。
- `zhiheng-fastapi-frontend/plugins/agent_extension/views/skill/index.vue`：技能列表页。
- `zhiheng-fastapi-frontend/plugins/agent_extension/views/skill/components/SkillUploadModal.vue`：两步上传确认。
- `zhiheng-fastapi-frontend/plugins/agent_extension/views/skill/components/SkillDetailDrawer.vue`：详情和主文件预览。
- `zhiheng-fastapi-frontend/plugins/ai/views/chat/components/SkillPicker.vue`：对话技能选择器。

### 修改现有对话与部署

- `zhiheng-fastapi-backend/plugins/ai/entity/do/ai_chat_platform_do.py`：消息技能快照列。
- `zhiheng-fastapi-backend/plugins/ai/entity/vo/ai_platform_chat_vo.py`：请求技能标识与消息快照模型。
- `zhiheng-fastapi-backend/plugins/ai/infrastructure/skill_context_adapter.py`：应用状态提供器适配和错误映射。
- `zhiheng-fastapi-backend/plugins/ai/infrastructure/platform_chat_provider.py`：注入适配器。
- `zhiheng-fastapi-backend/plugins/ai/service/platform_chat_service.py`：解析、快照和当前请求注入。
- `zhiheng-fastapi-backend/plugins/ai/migrations/{mysql,postgresql}/008_chat_skill_snapshots.sql`：消息快照字段。
- `zhiheng-fastapi-backend/plugins/ai/plugin.yaml`：登记迁移。
- `zhiheng-fastapi-frontend/plugins/ai/views/chat/index.vue`：技能标签与发送参数。
- `docker-compose.pg.yml`、`docker-compose.my.yml`：技能数据持久目录挂载和环境变量。
- `deploy/zhiheng/init-shared-postgres.sh`：创建技能数据目录。

### 测试

- `zhiheng-fastapi-backend/tests/plugins/sample_plugins/test_agent_extension_plugin.py`
- `zhiheng-fastapi-backend/tests/plugins/agent_extension/test_skill_schema.py`
- `zhiheng-fastapi-backend/tests/plugins/agent_extension/test_skill_package.py`
- `zhiheng-fastapi-backend/tests/plugins/agent_extension/test_skill_storage.py`
- `zhiheng-fastapi-backend/tests/plugins/agent_extension/test_skill_service.py`
- `zhiheng-fastapi-backend/tests/plugins/agent_extension/test_skill_controller.py`
- 修改 `zhiheng-fastapi-backend/tests/plugins/ai/test_platform_chat_service.py`
- 修改 `zhiheng-fastapi-backend/tests/plugins/ai/test_platform_chat_schema.py`
- `zhiheng-fastapi-frontend/tests/plugins/agentExtensionContract.test.js`
- `zhiheng-fastapi-frontend/tests/plugins/skillTransforms.test.js`
- 修改 `zhiheng-fastapi-frontend/tests/plugins/chatBehaviorContract.test.js`
- 修改 `zhiheng-fastapi-frontend/tests/e2e/platform_shell_check.py`

## 任务 1：插件清单、表结构和领域模型

- [ ] **步骤 1：编写失败的插件和迁移契约测试**

测试必须断言插件 ID 为 `agent_extension`，一级菜单为“智能体扩展”，技能菜单组件为 `plugin/agent_extension/skill/index`，六项权限完整，MySQL/PostgreSQL 都包含 `agent_skill`、唯一 `skill_slug` 和状态检查约束。

```python
def test_agent_extension_manifest_declares_skill_menu_and_permissions():
    plugin = PluginScanner(BACKEND_ROOT / 'plugins').load_manifest(PLUGIN_ROOT / 'plugin.yaml')
    assert plugin.manifest.id == 'agent_extension'
    assert plugin.manifest.frontend.menus[0].name == '智能体扩展'
    assert plugin.manifest.frontend.menus[0].children[0].component == 'plugin/agent_extension/skill/index'
```

- [ ] **步骤 2：运行测试确认因插件不存在而失败**

运行：

```bash
cd zhiheng-fastapi-backend
pytest -q tests/plugins/sample_plugins/test_agent_extension_plugin.py tests/plugins/agent_extension/test_skill_schema.py
```

预期：测试收集成功，但因 `plugins/agent_extension/plugin.yaml` 或迁移不存在而失败。

- [ ] **步骤 3：实现插件清单、迁移、ORM 和 VO**

`AgentSkill` 必须与规格字段一致，状态只接受 `enabled`/`disabled`，响应模型使用驼峰别名。清单只声明技能管理，不创建知识库占位路由。

```yaml
frontend:
  basePath: extensions
  pluginId: agent_extension
  menus:
    - name: 智能体扩展
      path: extensions
      component: Layout
      type: M
      children:
        - name: 技能管理
          path: skill
          component: plugin/agent_extension/skill/index
          routeName: AgentExtensionSkill
          perms: agent_extension:skill:list
          type: C
```

- [ ] **步骤 4：运行插件、迁移和格式检查**

```bash
pytest -q tests/plugins/sample_plugins/test_agent_extension_plugin.py tests/plugins/agent_extension/test_skill_schema.py
ruff check plugins/agent_extension tests/plugins/agent_extension
```

预期：全部通过。

- [ ] **步骤 5：提交任务 1**

```bash
git add zhiheng-fastapi-backend/plugins/agent_extension zhiheng-fastapi-backend/tests/plugins/agent_extension zhiheng-fastapi-backend/tests/plugins/sample_plugins/test_agent_extension_plugin.py
git commit -m "feat: 建立智能体扩展技能插件"
```

## 任务 2：技能包解析和持久文件操作

- [ ] **步骤 1：编写失败的解析与存储测试**

覆盖单 Markdown、单根 ZIP、父目录跳转、绝对路径、符号链接、重复路径、文件数量、单文件大小、总大小、缺少主文件、草稿归属、原子确认和垃圾目录恢复。

```python
def test_zip_rejects_parent_path(tmp_path: Path):
    archive = build_zip({'../escape.txt': b'unsafe', 'SKILL.md': VALID_SKILL})
    with pytest.raises(SkillPackageError, match='不安全'):
        SkillPackageParser().parse(archive, filename='skill.zip', target_dir=tmp_path)
```

- [ ] **步骤 2：运行测试确认解析器和存储类缺失**

```bash
pytest -q tests/plugins/agent_extension/test_skill_package.py tests/plugins/agent_extension/test_skill_storage.py
```

预期：因模块或类不存在而失败。

- [ ] **步骤 3：实现最小安全解析器和存储类**

解析器使用 `zipfile` 和 `yaml.safe_load`，以读取字节数而非只信任 ZIP 声明大小实施限制；路径经 `PurePosixPath` 校验。存储类仅接受解析器生成的安全 slug 和服务端草稿 ID。

```python
@dataclass(frozen=True, slots=True)
class ParsedSkillPackage:
    slug: str
    name: str
    description: str
    version: str
    content_hash: str
    file_count: int
    total_bytes: int
    root: Path
```

- [ ] **步骤 4：运行解析、存储和格式测试**

```bash
pytest -q tests/plugins/agent_extension/test_skill_package.py tests/plugins/agent_extension/test_skill_storage.py
ruff check plugins/agent_extension/domain plugins/agent_extension/infrastructure tests/plugins/agent_extension
```

预期：全部通过。

- [ ] **步骤 5：提交任务 2**

```bash
git add zhiheng-fastapi-backend/plugins/agent_extension/domain zhiheng-fastapi-backend/plugins/agent_extension/infrastructure zhiheng-fastapi-backend/plugins/agent_extension/config.py zhiheng-fastapi-backend/tests/plugins/agent_extension
git commit -m "feat: 增加技能包安全解析与存储"
```

## 任务 3：管理服务、接口和内置技能

- [ ] **步骤 1：编写失败的服务与控制器测试**

测试列表分页、上传准备、确认安装、同名冲突、状态乐观锁、启用/停用、启用技能禁止删除、内置技能禁止删除、停用技能删除、可用技能过滤和公开字段脱敏。

```python
@pytest.mark.asyncio
async def test_enabled_skill_must_be_disabled_before_delete(service):
    skill = await service.install_prepared(DRAFT_ID, operator=ADMIN)
    with pytest.raises(SkillServiceError, match='先停用'):
        await service.delete_skill(skill.skill_id, operator=ADMIN)
```

- [ ] **步骤 2：运行测试确认 DAO、服务和接口缺失**

```bash
pytest -q tests/plugins/agent_extension/test_skill_service.py tests/plugins/agent_extension/test_skill_controller.py
```

预期：因实现缺失而失败。

- [ ] **步骤 3：实现 DAO、服务、控制器和启动钩子**

写接口使用现有权限依赖，上传读取过程实施 10MB 硬限制。启动钩子创建存储目录、同步两个内置纯提示词技能并注册 `agent_skill_context_provider`；只允许启动写 Worker 执行内置数据同步。

```python
async def on_startup(context: PluginHookContext) -> None:
    storage = SkillStorage(AgentSkillSettings().data_dir)
    storage.ensure_layout()
    if context.startup_write_enabled and context.query_db is not None:
        await SkillService(context.query_db, storage).sync_builtin_skills(BUILTIN_ROOT)
    setattr(context.app.state, 'agent_skill_context_provider', SkillContextProvider(storage))
```

- [ ] **步骤 4：运行插件后端测试**

```bash
pytest -q tests/plugins/agent_extension tests/plugins/sample_plugins/test_agent_extension_plugin.py
ruff check plugins/agent_extension tests/plugins/agent_extension
```

预期：全部通过。

- [ ] **步骤 5：提交任务 3**

```bash
git add zhiheng-fastapi-backend/plugins/agent_extension zhiheng-fastapi-backend/tests/plugins/agent_extension
git commit -m "feat: 完成平台技能管理接口"
```

## 任务 4：技能管理前端

- [ ] **步骤 1：编写失败的转换和页面契约测试**

测试响应分页转换、状态标签、管理员按钮权限、中文卡片字段、两步上传、详情预览、启停与删除状态约束，以及禁止独立硬编码品牌色。

```javascript
assert.match(skillView, /WorkspaceToolbar[^>]+title="技能管理"/)
assert.match(skillView, /can\('agent_extension:skill:add'\)/)
assert.doesNotMatch(skillView, /#[0-9a-f]{6}/i)
```

- [ ] **步骤 2：运行测试确认前端插件不存在**

```bash
cd zhiheng-fastapi-frontend
node tests/plugins/skillTransforms.test.js
node tests/plugins/agentExtensionContract.test.js
```

预期：因 API、转换函数或页面不存在而失败。

- [ ] **步骤 3：实现 API、转换函数、列表、上传和详情**

页面复用 `WorkspaceToolbar`、`WorkspaceState`、`WorkspaceStatus`、Ant Design Vue 弹窗和现有主题变量。列表卡片只展示中文名称、说明、状态和查看入口；技术标识只在详情抽屉显示。

```javascript
export function normalizeSkillPage(response) {
  const source = response?.data || response || {}
  const rows = Array.isArray(source.rows) ? source.rows : []
  return { rows, total: Number(source.total) || rows.length }
}
```

- [ ] **步骤 4：运行前端插件测试**

```bash
npm run test:plugin
```

预期：包括新增测试在内全部通过。

- [ ] **步骤 5：提交任务 4**

```bash
git add zhiheng-fastapi-frontend/plugins/agent_extension zhiheng-fastapi-frontend/tests/plugins
git commit -m "feat: 增加技能管理页面"
```

## 任务 5：后端对话技能注入

- [ ] **步骤 1：编写失败的对话 VO、服务和迁移测试**

覆盖最多 5 个唯一 slug、技能提供器不可用、技能错误映射、技能系统上下文顺序、用户消息技能快照、幂等请求技能冲突、历史响应快照和未选择技能行为不变。

```python
@pytest.mark.asyncio
async def test_send_injects_selected_skills_only_for_current_request(service):
    request = build_request(skillSlugs=['deep-research'])
    await consume(await service.send(USER_ID, request))
    sent = model_service.stream_calls[0].messages
    assert sent[0].role == 'system'
    assert '<selected_skills>' in sent[0].content
```

- [ ] **步骤 2：运行测试确认请求字段、适配器和消息列缺失**

```bash
pytest -q tests/plugins/ai/test_platform_chat_service.py tests/plugins/ai/test_platform_chat_schema.py
```

预期：新增断言失败。

- [ ] **步骤 3：实现迁移、实体、VO、适配器和服务注入**

技能解析必须在任何会话写入前完成。`skill_context_adapter.py` 将应用状态缺失和提供器固定错误映射为 `PlatformChatError`，不把物理路径或原始异常传给客户端。

```python
class PlatformChatRequestModel(_StrictRequestModel):
    skill_slugs: list[SkillSlug] = Field(default_factory=list, max_length=5)

class PlatformMessageModel(_ResponseModel):
    skill_snapshots: list[SkillSnapshotModel] = Field(default_factory=list)
```

- [ ] **步骤 4：运行 AI 对话相关测试和格式检查**

```bash
pytest -q tests/plugins/ai/test_platform_chat_service.py tests/plugins/ai/test_platform_chat_schema.py tests/plugins/ai/test_ai_chat_controller.py
ruff check plugins/ai tests/plugins/ai/test_platform_chat_service.py tests/plugins/ai/test_platform_chat_schema.py
```

预期：全部通过。

- [ ] **步骤 5：提交任务 5**

```bash
git add zhiheng-fastapi-backend/plugins/ai zhiheng-fastapi-backend/tests/plugins/ai
git commit -m "feat: 支持对话按消息加载技能"
```

## 任务 6：前端对话技能选择

- [ ] **步骤 1：编写失败的选择器和聊天契约测试**

测试可用技能加载、`@` 打开、点击按钮打开、多选去重、最多 5 个、标签移除、请求携带 `skillSlugs`、历史标签展示和发送后清空。

```javascript
assert.match(chatView, /skillSlugs:\s*outgoingSkills\.map/)
assert.match(chatView, /selectedSkills\.value = \[\]/)
assert.match(skillPicker, /最多选择 5 个技能/)
```

- [ ] **步骤 2：运行测试确认选择器和发送字段缺失**

```bash
node tests/plugins/chatBehaviorContract.test.js
node tests/plugins/agentExtensionContract.test.js
```

预期：新增断言失败。

- [ ] **步骤 3：实现技能选择器和聊天集成**

输入内容以独立 `@` 结尾时打开选择器，选择完成后移除该触发字符；工具栏也提供“选择技能”按钮。发送前复制技能快照到本地用户消息，请求只传 slug，随后清空选择。

```javascript
const outgoingSkills = selectedSkills.value.map((skill) => ({ ...skill }))
selectedSkills.value = []
await createChatStream({ ...payload, skillSlugs: outgoingSkills.map((skill) => skill.skillSlug) }, signal)
```

- [ ] **步骤 4：运行前端全部插件测试**

```bash
npm run test:plugin
```

预期：全部通过。

- [ ] **步骤 5：提交任务 6**

```bash
git add zhiheng-fastapi-frontend/plugins/ai zhiheng-fastapi-frontend/plugins/agent_extension zhiheng-fastapi-frontend/tests/plugins
git commit -m "feat: 支持对话选择平台技能"
```

## 任务 7：部署、构建和浏览器验收

- [ ] **步骤 1：编写失败的部署契约测试**

断言 PostgreSQL/MySQL Compose 都挂载技能目录并传入 `AGENT_SKILL_DATA_DIR`，初始化脚本创建目录且不输出敏感配置。

```javascript
assert.match(composePg, /agent-skills:\/app\/data\/agent-skills/)
assert.match(composePg, /AGENT_SKILL_DATA_DIR/)
```

- [ ] **步骤 2：运行部署契约确认失败**

```bash
cd zhiheng-fastapi-frontend
node tests/plugins/deployScriptContract.test.js
```

预期：因技能目录挂载缺失而失败。

- [ ] **步骤 3：修改 Compose、初始化脚本和部署说明**

宿主目录固定为仓库 `data/agent-skills`，容器路径为 `/app/data/agent-skills`。初始化只执行权限安全的目录创建，不删除已有文件。

- [ ] **步骤 4：执行完整验证**

```bash
cd zhiheng-fastapi-backend
pytest -q tests/plugins/agent_extension tests/plugins/ai/test_platform_chat_service.py tests/plugins/ai/test_platform_chat_schema.py tests/plugins/ai/test_ai_chat_controller.py
ruff check plugins/agent_extension plugins/ai tests/plugins/agent_extension

cd ../zhiheng-fastapi-frontend
npm run test:plugin
NODE_OPTIONS=--max-old-space-size=4096 npm run build:docker
```

预期：所有针对性后端测试、Ruff、前端插件测试和生产构建通过。

- [ ] **步骤 5：启动平台并执行浏览器验收**

```bash
cd ..
docker compose --project-name zhiheng-vue3-fastapi --env-file .env -f docker-compose.pg.yml up -d --build
PLATFORM_SHELL_BASE_URL=http://127.0.0.1:12580 python zhiheng-fastapi-frontend/tests/e2e/platform_shell_check.py
```

验收技能列表、上传、详情、启停、删除、对话选择和流式回复；检查 1280×800、1440×900、浅色和深色主题截图。

- [ ] **步骤 6：提交部署和构建产物**

```bash
git add docker-compose.pg.yml docker-compose.my.yml deploy/zhiheng/init-shared-postgres.sh README.md zhiheng-fastapi-frontend/dist zhiheng-fastapi-frontend/tests
git commit -m "build: 交付智能体技能管理功能"
```

## 基线说明

计划开始前已确认：

- `npm run test:plugin`：24 项通过。
- 模型目录相关后端测试：65 项通过。
- `pytest -q tests/plugins/ai`：1217 项通过，16 项失败。失败集中在依赖外部运行时或共享 Redis 状态的公共 API/配额测试，与本功能开始前的代码状态一致。最终验证需继续单独报告这些环境型失败，不能宣称全量 AI 测试全部通过。
- 当前 worktree 存在上一个模型删除修复的未提交源码、测试和构建产物。执行每个任务时必须使用精确 `git add`，不得将其误并入技能任务提交。
