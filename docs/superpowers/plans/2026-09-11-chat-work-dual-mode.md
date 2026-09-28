# 聊天与工作双模式实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将现有智能对话拆为聊天与工作双模式，使聊天只使用模型和用户技能，工作任务可持久化调用多个已发布智能体。

**架构：** 保留 `/ai/chat` 的现有会话与 SSE，实现新的工作任务表、服务和 `/agent_extension/work-tasks` 内部接口；`/ai/work` 使用独立 Pinia store 和工作页面。工作任务把已发布智能体配置固定为成员快照，每条消息由用户选择执行智能体，所有成员共享带发言者标记的任务上下文。

**技术栈：** FastAPI、SQLAlchemy async、Pydantic v2、MySQL/PostgreSQL 迁移、Vue 3、Pinia、Vue Router 4、Ant Design Vue、Fetch SSE、pytest、Node assert、Playwright、Vite 6。

---

## 文件结构

### 后端

- 创建：`zhiheng-fastapi-backend/plugins/agent_extension/entity/do/work_task_do.py` — 工作任务、成员和消息 ORM。
- 创建：`zhiheng-fastapi-backend/plugins/agent_extension/entity/vo/work_task_vo.py` — 请求、响应、查询和流式运行模型。
- 创建：`zhiheng-fastapi-backend/plugins/agent_extension/dao/work_task_dao.py` — 所有者隔离的任务持久化与行锁。
- 创建：`zhiheng-fastapi-backend/plugins/agent_extension/service/work_task_service.py` — CRUD、状态流转、成员快照和恢复。
- 创建：`zhiheng-fastapi-backend/plugins/agent_extension/service/work_task_run_service.py` — 幂等运行、上下文装配、模型流和终态持久化。
- 创建：`zhiheng-fastapi-backend/plugins/agent_extension/controller/work_task_controller.py` — 工作任务内部接口和 SSE。
- 修改：`zhiheng-fastapi-backend/plugins/agent_extension/dao/agent_dao.py` — 当前用户可使用的已发布智能体目录查询。
- 修改：`zhiheng-fastapi-backend/plugins/agent_extension/service/agent_service.py` — 查询当前用户可使用的已发布智能体快照。
- 修改：`zhiheng-fastapi-backend/plugins/agent_extension/hooks.py` — 启动时恢复遗留运行状态。
- 创建：`zhiheng-fastapi-backend/plugins/agent_extension/migrations/mysql/004_work_tasks.sql`。
- 创建：`zhiheng-fastapi-backend/plugins/agent_extension/migrations/postgresql/004_work_tasks.sql`。
- 修改：`zhiheng-fastapi-backend/plugins/agent_extension/plugin.yaml` — 迁移、版本和权限声明。
- 创建：`zhiheng-fastapi-backend/tests/plugins/agent_extension/test_work_task_schema.py`。
- 创建：`zhiheng-fastapi-backend/tests/plugins/agent_extension/test_work_task_service.py`。
- 创建：`zhiheng-fastapi-backend/tests/plugins/agent_extension/test_work_task_run_service.py`。
- 创建：`zhiheng-fastapi-backend/tests/plugins/agent_extension/test_work_task_controller.py`。
- 修改：`zhiheng-fastapi-backend/tests/plugins/sample_plugins/test_agent_extension_plugin.py` — 插件清单契约。

### 前端

- 修改：`zhiheng-fastapi-frontend/plugins/ai/views/chat/index.vue` — 删除新聊天知识库选择并接入中心模式空态。
- 修改：`zhiheng-fastapi-frontend/tests/plugins/chatBehaviorContract.test.js` — 固化聊天无知识库、技能保留行为。
- 修改：`zhiheng-fastapi-frontend/src/router/index.js` — 注册权限感知的隐藏 `/ai/work` 路由。
- 修改：`zhiheng-fastapi-frontend/src/components/Workspace/workspaceTransforms.js` — 工作路由、模式和落地页规则。
- 修改：`zhiheng-fastapi-frontend/src/layout/components/PlatformSidebar.vue` — 新建聊天/工作与模式化历史。
- 创建：`zhiheng-fastapi-frontend/src/layout/components/RecentWorkTasks.vue`。
- 创建：`zhiheng-fastapi-frontend/src/components/Workspace/WorkspaceModeSwitch.vue`。
- 创建：`zhiheng-fastapi-frontend/plugins/ai/views/chat/components/ChatEmptyState.vue`。
- 创建：`zhiheng-fastapi-frontend/plugins/agent_extension/api/workTask.js`。
- 创建：`zhiheng-fastapi-frontend/plugins/agent_extension/utils/workTask.js`。
- 创建：`zhiheng-fastapi-frontend/src/store/modules/workTask.js`。
- 创建：`zhiheng-fastapi-frontend/plugins/agent_extension/views/work/index.vue`。
- 创建：`zhiheng-fastapi-frontend/plugins/agent_extension/views/work/components/WorkEmptyState.vue`。
- 创建：`zhiheng-fastapi-frontend/plugins/agent_extension/views/work/components/WorkAgentPicker.vue`。
- 创建：`zhiheng-fastapi-frontend/plugins/agent_extension/views/work/components/WorkMemberBar.vue`。
- 创建：`zhiheng-fastapi-frontend/plugins/agent_extension/views/work/components/WorkMessageTimeline.vue`。
- 创建：`zhiheng-fastapi-frontend/plugins/agent_extension/views/work/components/WorkTaskPage.vue`。
- 创建：`zhiheng-fastapi-frontend/tests/plugins/workTaskTransforms.test.js`。
- 修改：`zhiheng-fastapi-frontend/tests/plugins/workspaceTransforms.test.js`。
- 修改：`zhiheng-fastapi-frontend/tests/plugins/platformShellContract.test.js`。
- 创建：`zhiheng-fastapi-frontend/tests/e2e/chat_work_mode_check.py`。

### 任务 1：工作任务数据结构与双数据库迁移

**文件：**
- 创建：`zhiheng-fastapi-backend/plugins/agent_extension/entity/do/work_task_do.py`
- 创建：`zhiheng-fastapi-backend/plugins/agent_extension/entity/vo/work_task_vo.py`
- 创建：`zhiheng-fastapi-backend/plugins/agent_extension/migrations/mysql/004_work_tasks.sql`
- 创建：`zhiheng-fastapi-backend/plugins/agent_extension/migrations/postgresql/004_work_tasks.sql`
- 创建：`zhiheng-fastapi-backend/tests/plugins/agent_extension/test_work_task_schema.py`

- [ ] **步骤 1：编写失败的 schema 测试**

创建测试并断言 ORM 表、约束和 Pydantic 别名：

```python
def test_work_task_schema_exposes_camel_case_and_rejects_invalid_state():
    task = WorkTaskModel(
        taskId='task-1', ownerUserId=10, title='发布资料', goal='整理发布资料',
        status='active', activeAgentId='agent-1', version=1,
    )
    assert task.model_dump(by_alias=True)['activeAgentId'] == 'agent-1'
    with pytest.raises(ValidationError):
        WorkTaskUpdateModel(expectedVersion=1, status='running')

def test_work_tables_define_idempotency_and_status_constraints():
    assert {'agent_work_task', 'agent_work_task_member', 'agent_work_message'} <= set(Base.metadata.tables)
    task = AgentWorkTask.__table__
    message = AgentWorkMessage.__table__
    assert any(constraint.name == 'uk_agent_work_task_create_client' for constraint in task.constraints)
    assert any(constraint.name == 'uk_agent_work_message_client' for constraint in message.constraints)
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```bash
pytest -q tests/plugins/agent_extension/test_work_task_schema.py
```

预期：FAIL，工作任务模块尚不存在。

- [ ] **步骤 3：实现三个 ORM 模型**

`work_task_do.py` 定义：

```python
class AgentWorkTask(Base):
    __tablename__ = 'agent_work_task'
    task_id = Column(String(36), primary_key=True)
    owner_user_id = Column(BigInteger, nullable=False)
    create_client_message_id = Column(String(100), nullable=False)
    title = Column(String(100), nullable=False)
    goal = Column(Text, nullable=False)
    status = Column(String(16), nullable=False, default='active')
    active_agent_id = Column(String(36), nullable=True)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)

class AgentWorkTaskMember(Base):
    __tablename__ = 'agent_work_task_member'
    task_id = Column(String(36), ForeignKey('agent_work_task.task_id', ondelete='RESTRICT'), primary_key=True)
    agent_id = Column(String(36), primary_key=True)
    agent_name_snapshot = Column(String(100), nullable=False)
    published_version = Column(Integer, nullable=False)
    config_snapshot = Column(JSON().with_variant(JSONB(), 'postgresql'), nullable=False)
    added_at = Column(DateTime, nullable=False)
    removed_at = Column(DateTime, nullable=True)

class AgentWorkMessage(Base):
    __tablename__ = 'agent_work_message'
    message_id = Column(String(36), primary_key=True)
    task_id = Column(String(36), ForeignKey('agent_work_task.task_id', ondelete='RESTRICT'), nullable=False)
    client_message_id = Column(String(100), nullable=True)
    reply_to_message_id = Column(String(36), nullable=True)
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    agent_id = Column(String(36), nullable=True)
    agent_name_snapshot = Column(String(100), nullable=True)
    published_version = Column(Integer, nullable=True)
    run_id = Column(String(36), nullable=True)
    run_status = Column(String(16), nullable=True)
    usage_snapshot = Column(JSON().with_variant(JSONB(), 'postgresql'), nullable=True)
    error_snapshot = Column(JSON().with_variant(JSONB(), 'postgresql'), nullable=True)
    created_at = Column(DateTime, nullable=False)
```

增加任务状态、角色、运行状态 CheckConstraint，以及任务表 `(owner_user_id, create_client_message_id)`、消息表 `(task_id, client_message_id)`、`run_id` 唯一约束和任务时间索引。

- [ ] **步骤 4：实现请求与响应模型**

`work_task_vo.py` 定义严格模型：

```python
class WorkTaskCreateModel(_WorkRequest):
    goal: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32000)]
    agent_ids: list[AgentId] = Field(min_length=1, max_length=10)
    active_agent_id: AgentId
    client_message_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]

class WorkTaskRunRequestModel(_WorkRequest):
    agent_id: AgentId
    client_message_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32000)]

class WorkTaskUpdateModel(_WorkRequest):
    expected_version: int = Field(ge=1)
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None
    goal: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32000)] | None = None
    status: Literal['active', 'completed', 'archived'] | None = None
```

补齐任务、成员、消息、分页、成员更新、取消请求和 `WorkAgentOptionModel` 响应模型，统一 camelCase alias。可用智能体响应只包含 ID、名称、描述、发布版本和状态。

- [ ] **步骤 5：编写 MySQL/PostgreSQL 对等迁移**

两份 `004_work_tasks.sql` 创建相同三张表、约束和索引。MySQL 使用 `json`、`datetime`、`engine=innodb`；PostgreSQL 使用 `jsonb`、`timestamp` 和独立 `create index if not exists`。

- [ ] **步骤 6：运行 schema 测试确认通过**

运行：

```bash
pytest -q tests/plugins/agent_extension/test_work_task_schema.py
```

预期：PASS。

- [ ] **步骤 7：提交数据结构**

```bash
git add zhiheng-fastapi-backend/plugins/agent_extension/entity/do/work_task_do.py zhiheng-fastapi-backend/plugins/agent_extension/entity/vo/work_task_vo.py zhiheng-fastapi-backend/plugins/agent_extension/migrations/mysql/004_work_tasks.sql zhiheng-fastapi-backend/plugins/agent_extension/migrations/postgresql/004_work_tasks.sql zhiheng-fastapi-backend/tests/plugins/agent_extension/test_work_task_schema.py
git commit -m "feat(agent): 增加工作任务数据模型"
```

### 任务 2：任务 CRUD、所有者隔离与成员快照

**文件：**
- 创建：`zhiheng-fastapi-backend/plugins/agent_extension/dao/work_task_dao.py`
- 创建：`zhiheng-fastapi-backend/plugins/agent_extension/service/work_task_service.py`
- 修改：`zhiheng-fastapi-backend/plugins/agent_extension/dao/agent_dao.py`
- 修改：`zhiheng-fastapi-backend/plugins/agent_extension/service/agent_service.py`
- 创建：`zhiheng-fastapi-backend/tests/plugins/agent_extension/test_work_task_service.py`

- [ ] **步骤 1：编写失败的服务测试**

覆盖创建幂等、所有者隔离、管理员访问、状态流转和成员版本：

```python
@pytest.mark.asyncio
async def test_create_is_idempotent_and_snapshots_owned_published_agents(database):
    service = WorkTaskService(database)
    first = await service.create_task(payload(), actor_user_id=10, is_admin=False)
    repeated = await service.create_task(payload(), actor_user_id=10, is_admin=False)
    assert repeated.task_id == first.task_id
    assert first.members[0].published_version == 2
    assert 'systemPrompt' not in first.model_dump(by_alias=True)['members'][0]

@pytest.mark.asyncio
async def test_usable_agent_catalog_only_returns_owned_published_agents(database):
    rows = await AgentService(database).list_usable_published_agents(actor_user_id=10, is_admin=False)
    assert [(row.agent_id, row.status) for row in rows] == [('agent-published', 'published')]

@pytest.mark.asyncio
async def test_owner_isolation_state_transitions_and_member_removal(database):
    task = await WorkTaskService(database).create_task(payload(), actor_user_id=10, is_admin=False)
    with pytest.raises(AgentServiceError, match='not found'):
        await WorkTaskService(database).get_task(task.task_id, actor_user_id=11, is_admin=False)
    completed = await WorkTaskService(database).update_task(
        task.task_id, WorkTaskUpdateModel(expectedVersion=1, status='completed'),
        actor_user_id=10, is_admin=False,
    )
    assert completed.status == 'completed'
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```bash
pytest -q tests/plugins/agent_extension/test_work_task_service.py
```

预期：FAIL，DAO 和服务尚不存在。

- [ ] **步骤 3：增加可使用智能体查询**

`AgentDao.list_published_agents` 按 `status='published'`、`published_config is not null` 过滤；普通用户再按 `owner_user_id` 过滤，管理员不加所有者条件。

在 `AgentService` 增加：

```python
async def get_usable_published_agent(
    self, agent_id: str, *, actor_user_id: int, is_admin: bool
) -> AgentPublishedModel:
    agent = await self._require_agent(agent_id, actor_user_id, is_admin)
    if agent.status != 'published' or not agent.published_config or agent.published_version is None:
        raise AgentServiceError('AGENT_NOT_PUBLISHED', '智能体尚未发布', status_code=422)
    config = AgentConfigResponseModel.model_validate(agent.published_config)
    return AgentPublishedModel(
        **config.model_dump(), agent_id=agent.agent_id,
        owner_user_id=agent.owner_user_id, published_version=agent.published_version,
    )

async def list_usable_published_agents(
    self, *, actor_user_id: int, is_admin: bool
) -> list[WorkAgentOptionModel]:
    rows = await self._dao.list_published_agents(
        self._db, owner_user_id=None if is_admin else actor_user_id
    )
    return [
        WorkAgentOptionModel(
            agentId=row.agent_id, name=row.name, description=row.description,
            status='published', publishedVersion=row.published_version,
        )
        for row in rows
    ]
```

非管理员继续受 `_require_agent` 的所有者限制。

- [ ] **步骤 4：实现 DAO 原子操作**

`WorkTaskDao` 提供 `get_owned_task(for_update)`、`list_tasks`、`get_by_client_message_id`、`create_task_with_members_and_message`、`update_task_with_version`、`list_members`、`list_messages`、`add_member`、`soft_remove_member` 和 `delete_task_graph`。所有写操作在服务层单事务提交。

- [ ] **步骤 5：实现任务服务**

`WorkTaskService`：

```python
async def create_task(self, payload, *, actor_user_id, is_admin):
    existing = await self._dao.get_by_create_client_message_id(
        self._db, actor_user_id, payload.client_message_id
    )
    if existing:
        return await self._detail(existing.task_id, actor_user_id, is_admin)
    snapshots = [await self._snapshot(agent_id, actor_user_id, is_admin) for agent_id in payload.agent_ids]
    if payload.active_agent_id not in payload.agent_ids:
        raise AgentServiceError('WORK_ACTIVE_AGENT_INVALID', '当前执行智能体必须属于任务成员', 422)
    task = self._new_task(payload, actor_user_id)
    message = self._first_user_message(task.task_id, payload)
    await self._dao.create_task_with_members_and_message(self._db, task, snapshots, message)
    await self._db.commit()
    return await self._detail(task.task_id, actor_user_id, is_admin)
```

更新任务使用 `version` 乐观锁；`active` 可进入 `completed` 或 `archived`，`completed` 可回到 `active` 或进入 `archived`，`archived` 只允许查询和删除。成员更新重新读取当前发布配置并覆盖快照；移除活动成员后选择首个可用成员。

- [ ] **步骤 6：运行服务测试确认通过**

运行：

```bash
pytest -q tests/plugins/agent_extension/test_work_task_service.py
```

预期：PASS。

- [ ] **步骤 7：提交 CRUD 与成员服务**

```bash
git add zhiheng-fastapi-backend/plugins/agent_extension/dao/work_task_dao.py zhiheng-fastapi-backend/plugins/agent_extension/dao/agent_dao.py zhiheng-fastapi-backend/plugins/agent_extension/service/work_task_service.py zhiheng-fastapi-backend/plugins/agent_extension/service/agent_service.py zhiheng-fastapi-backend/tests/plugins/agent_extension/test_work_task_service.py
git commit -m "feat(agent): 实现工作任务与成员管理"
```

### 任务 3：多智能体共享上下文与幂等流式运行

**文件：**
- 创建：`zhiheng-fastapi-backend/plugins/agent_extension/service/work_task_run_service.py`
- 修改：`zhiheng-fastapi-backend/plugins/agent_extension/dao/work_task_dao.py`
- 创建：`zhiheng-fastapi-backend/tests/plugins/agent_extension/test_work_task_run_service.py`

- [ ] **步骤 1：编写失败的运行服务测试**

覆盖发言者标记、上下文上限、幂等、并发、取消和资源失效：

```python
@pytest.mark.asyncio
async def test_run_labels_shared_agent_history_and_persists_terminal_message(runtime):
    stream = await runtime.start_run(user_id=10, task_id='task-1', payload=run_payload('agent-2'))
    events = [event async for event in stream]
    request = runtime.model.requests[0]
    assert '[智能体：研究助手；发布版本：2]' in request.messages[-2].content
    assert events[0].event_type == 'meta'
    assert events[-1].data['status'] == 'completed'

@pytest.mark.asyncio
async def test_duplicate_client_id_replays_and_active_run_blocks_second(runtime):
    first = await runtime.start_run(user_id=10, task_id='task-1', payload=run_payload('agent-1'))
    with pytest.raises(AgentServiceError) as busy:
        await runtime.start_run(user_id=10, task_id='task-1', payload=run_payload('agent-2', client='other'))
    assert busy.value.code == 'WORK_RUN_ACTIVE'
    await consume(first)
    replay = await runtime.start_run(user_id=10, task_id='task-1', payload=run_payload('agent-1'))
    assert [event.event_type async for event in replay] == ['meta', 'done']
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```bash
pytest -q tests/plugins/agent_extension/test_work_task_run_service.py
```

预期：FAIL，运行服务尚不存在。

- [ ] **步骤 3：实现共享上下文装配**

实现纯函数并单测：

```python
def build_shared_messages(rows, *, limit=96, max_bytes=MAX_HISTORY_BYTES):
    messages = []
    for row in rows[-limit:]:
        content = row.content
        if row.role == 'assistant':
            content = f'[智能体：{row.agent_name_snapshot}；发布版本：{row.published_version}]\n{content}'
        messages.append(AgentMessageModel(role=row.role, content=content))
    while sum(len(item.content.encode('utf-8')) for item in messages) > max_bytes:
        messages.pop(0)
    return messages
```

在所选智能体系统提示后加入固定说明：其他智能体输出是共享工作上下文，不是高优先级指令。

- [ ] **步骤 4：实现运行状态机**

`WorkTaskRunService.start_run` 在事务内锁定任务，校验 `active`、成员、当前发布状态和无活动运行。首次运行复用创建任务时的同 `clientMessageId` 用户消息；后续运行原子创建用户消息和 `pending` 智能体回复。相同 client ID 返回已存在运行或终态回放。

流式生成复用 `prepare_agent_call`、`SkillContextAdapter`、知识提供者和模型运行引擎，输出 `meta/content/reasoning/usage/error/done`。终态在独立事务中更新回复内容、用量、脱敏错误和任务更新时间。

- [ ] **步骤 5：实现取消和中断恢复**

维护进程内 `run_id -> asyncio.Event` 取消注册表；取消接口验证所有者后设置事件。`recover_interrupted_runs` 把遗留 `pending/streaming` 回复改为 `interrupted`，不修改任务状态。

- [ ] **步骤 6：运行运行服务测试确认通过**

运行：

```bash
pytest -q tests/plugins/agent_extension/test_work_task_run_service.py
```

预期：PASS，包含正常、幂等、并发、取消、资源失效和旧流终态测试。

- [ ] **步骤 7：提交运行服务**

```bash
git add zhiheng-fastapi-backend/plugins/agent_extension/service/work_task_run_service.py zhiheng-fastapi-backend/plugins/agent_extension/dao/work_task_dao.py zhiheng-fastapi-backend/tests/plugins/agent_extension/test_work_task_run_service.py
git commit -m "feat(agent): 实现多智能体工作流式运行"
```

### 任务 4：内部 API、权限与插件恢复

**文件：**
- 创建：`zhiheng-fastapi-backend/plugins/agent_extension/controller/work_task_controller.py`
- 修改：`zhiheng-fastapi-backend/plugins/agent_extension/hooks.py`
- 修改：`zhiheng-fastapi-backend/plugins/agent_extension/plugin.yaml`
- 创建：`zhiheng-fastapi-backend/tests/plugins/agent_extension/test_work_task_controller.py`
- 修改：`zhiheng-fastapi-backend/tests/plugins/sample_plugins/test_agent_extension_plugin.py`

- [ ] **步骤 1：编写失败的控制器和清单测试**

```python
def test_work_routes_require_specific_permissions(openapi):
    assert '/agent_extension/work-tasks' in openapi['paths']
    assert '/agent_extension/work-tasks/available-agents' in openapi['paths']
    assert '/agent_extension/work-tasks/{task_id}/runs' in openapi['paths']

def test_agent_manifest_declares_work_migrations_and_permissions(manifest):
    assert 'migrations/mysql/004_work_tasks.sql' in manifest.backend.migrations
    assert 'agent_extension:work:run' in {item.code for item in manifest.permissions}
    assert 'agent_extension:agent:use' in {item.code for item in manifest.permissions}
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```bash
pytest -q tests/plugins/agent_extension/test_work_task_controller.py tests/plugins/sample_plugins/test_agent_extension_plugin.py
```

预期：FAIL，路由和清单项不存在。

- [ ] **步骤 3：实现 CRUD 与成员控制器**

创建 `APIRouterPro(prefix='/agent_extension/work-tasks')`。可用智能体目录端点声明 `agent_extension:agent:use`；任务端点分别声明 `work:list/query/add/edit/remove`，调用 `_actor` 和服务层。管理员标记只影响所有者过滤，不绕过接口权限。

- [ ] **步骤 4：实现 SSE 与取消端点**

复用 `ai_chat_controller._serialize_stream` 的事件格式和 `PLATFORM_SSE_HEADERS`：

```python
@work_task_controller.post('/{task_id}/runs', dependencies=[UserInterfaceAuthDependency('agent_extension:work:run')])
async def run_task(...):
    stream = await _call(work_task_runtime(request, query_db).start_run(user_id, task_id, payload))
    return StreamingResponse(_serialize_work_stream(stream), media_type='text/event-stream', headers=WORK_SSE_HEADERS)
```

取消端点返回普通成功响应。

- [ ] **步骤 5：接入插件清单与启动恢复**

插件版本升级为 `0.4.0`，加入两份 004 迁移、6 个 work 权限及 `agent:use`。`on_startup` 仅在 `startup_write_enabled` 时调用 `recover_interrupted_runs`。

- [ ] **步骤 6：运行控制器和插件测试确认通过**

运行：

```bash
pytest -q tests/plugins/agent_extension/test_work_task_controller.py tests/plugins/sample_plugins/test_agent_extension_plugin.py
```

预期：PASS。

- [ ] **步骤 7：运行智能体扩展后端回归**

运行：

```bash
pytest -q tests/plugins/agent_extension
```

预期：全部通过。

- [ ] **步骤 8：提交 API 与插件声明**

```bash
git add zhiheng-fastapi-backend/plugins/agent_extension/controller/work_task_controller.py zhiheng-fastapi-backend/plugins/agent_extension/hooks.py zhiheng-fastapi-backend/plugins/agent_extension/plugin.yaml zhiheng-fastapi-backend/tests/plugins/agent_extension/test_work_task_controller.py zhiheng-fastapi-backend/tests/plugins/sample_plugins/test_agent_extension_plugin.py
git commit -m "feat(agent): 暴露工作任务内部接口"
```

### 任务 5：聊天去知识库与中心模式入口

**文件：**
- 修改：`zhiheng-fastapi-frontend/tests/plugins/chatBehaviorContract.test.js`
- 修改：`zhiheng-fastapi-frontend/tests/plugins/workspaceTransforms.test.js`
- 修改：`zhiheng-fastapi-frontend/src/components/Workspace/workspaceTransforms.js`
- 修改：`zhiheng-fastapi-frontend/src/router/index.js`
- 创建：`zhiheng-fastapi-frontend/src/components/Workspace/WorkspaceModeSwitch.vue`
- 创建：`zhiheng-fastapi-frontend/plugins/ai/views/chat/components/ChatEmptyState.vue`
- 修改：`zhiheng-fastapi-frontend/plugins/ai/views/chat/index.vue`

- [ ] **步骤 1：编写失败的聊天与模式测试**

删除旧测试对知识库选择器的正向断言，改为：

```js
assert.doesNotMatch(chatView, /listAvailableKnowledgeBases|selectedKnowledgeBaseIds|knowledge-picker/)
assert.match(chatView, /<SkillPicker/)
assert.match(chatView, /skillSlugs:\s*outgoingSkills\.map/)
assert.deepEqual(resolveWorkspaceMode('/ai/work'), 'work')
assert.equal(resolveWorkspaceLandingPath('/index', ['/ai/work']), '/ai/work')
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```bash
node tests/plugins/chatBehaviorContract.test.js
node tests/plugins/workspaceTransforms.test.js
```

预期：FAIL，聊天仍包含知识库且 work 模式不存在。

- [ ] **步骤 3：移除聊天知识库新请求**

从聊天模板、状态、watch、加载函数和 `send()` 中删除知识库选择；发送体不再包含 `knowledgeBaseIds`。增加 `legacyKnowledgeLabels(message)`：优先显示响应中可选的 `knowledgeBaseNames`，否则显示截断后的历史知识库 ID；不得为了旧消息触发知识库列表请求。

- [ ] **步骤 4：实现中心模式组件和聊天空态**

`WorkspaceModeSwitch.vue` 接收 `modelValue: 'chat' | 'work'` 并发出 `update:modelValue`。`ChatEmptyState.vue` 在无消息时展示标题、中心分段控件和建议项；切到 work 时 `router.push('/ai/work')`。

- [ ] **步骤 5：注册 `/ai/work` 权限路由**

在 `dynamicRoutes` 增加隐藏路由，权限为 `agent_extension:work:list`，组件使用 `() => import('~/plugins/agent_extension/views/work/index.vue')`。`workspaceTransforms.js` 识别 `/ai/work` 为 `work` 模式并加入落地页优先级。

- [ ] **步骤 6：运行定向测试确认通过**

运行：

```bash
node tests/plugins/chatBehaviorContract.test.js
node tests/plugins/workspaceTransforms.test.js
```

预期：PASS。

- [ ] **步骤 7：提交聊天双模式入口**

```bash
git add zhiheng-fastapi-frontend/tests/plugins/chatBehaviorContract.test.js zhiheng-fastapi-frontend/tests/plugins/workspaceTransforms.test.js zhiheng-fastapi-frontend/src/components/Workspace/workspaceTransforms.js zhiheng-fastapi-frontend/src/router/index.js zhiheng-fastapi-frontend/src/components/Workspace/WorkspaceModeSwitch.vue zhiheng-fastapi-frontend/plugins/ai/views/chat/components/ChatEmptyState.vue zhiheng-fastapi-frontend/plugins/ai/views/chat/index.vue
git commit -m "feat(chat): 拆分聊天与工作空态"
```

### 任务 6：工作任务 API、store、空态和独立历史

**文件：**
- 创建：`zhiheng-fastapi-frontend/plugins/agent_extension/api/workTask.js`
- 创建：`zhiheng-fastapi-frontend/plugins/agent_extension/utils/workTask.js`
- 创建：`zhiheng-fastapi-frontend/src/store/modules/workTask.js`
- 创建：`zhiheng-fastapi-frontend/plugins/agent_extension/views/work/components/WorkAgentPicker.vue`
- 创建：`zhiheng-fastapi-frontend/plugins/agent_extension/views/work/components/WorkEmptyState.vue`
- 创建：`zhiheng-fastapi-frontend/src/layout/components/RecentWorkTasks.vue`
- 修改：`zhiheng-fastapi-frontend/src/layout/components/PlatformSidebar.vue`
- 创建：`zhiheng-fastapi-frontend/tests/plugins/workTaskTransforms.test.js`
- 修改：`zhiheng-fastapi-frontend/tests/plugins/platformShellContract.test.js`

- [ ] **步骤 1：编写失败的工作转换和侧栏测试**

```js
assert.deepEqual(normalizeWorkTaskPage({ rows: [{ taskId: 't1', title: '任务', status: 'active' }], total: 1 }), {
  rows: [{ taskId: 't1', title: '任务', goal: '', status: 'active', members: [] }], total: 1,
})
assert.deepEqual(buildWorkCreatePayload({ goal: ' 目标 ', agentIds: ['a', 'a', 'b'], activeAgentId: 'a', clientMessageId: 'c1' }), {
  goal: '目标', agentIds: ['a', 'b'], activeAgentId: 'a', clientMessageId: 'c1',
})
assert.match(sidebarSource, /RecentWorkTasks/)
assert.match(sidebarSource, /新建工作/)
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```bash
node tests/plugins/workTaskTransforms.test.js
node tests/plugins/platformShellContract.test.js
```

预期：FAIL，新模块不存在。

- [ ] **步骤 3：实现工作 API 和转换函数**

`workTask.js` API 封装可用智能体目录、列表、详情、创建、更新、删除、成员管理、运行和取消。可用目录调用 `GET /agent_extension/work-tasks/available-agents`；流式运行使用 `getToken()` 和现有 `parsePlatformSSE`。

转换函数负责响应归一化、任务创建校验、成员去重、执行者校验、路由构造和状态标签，不读取 Vue 状态。

- [ ] **步骤 4：实现独立 workTask store**

store 状态包含 `tasks/currentTask/listGeneration/detailGeneration/runId/runState/runGeneration`。任务列表失败保留旧数据；任务切换和旧 SSE 事件通过 generation 隔离；聊天 store 不保存工作任务。

- [ ] **步骤 5：实现工作空态与智能体选择器**

`WorkAgentPicker` 只加载当前用户可使用的已发布智能体，最多选择 10 个。`WorkEmptyState` 展示中心模式切换、任务目标、成员和当前执行者；创建失败保留所有输入，成功后进入 `/ai/work?taskId=<id>` 并启动首次运行。

- [ ] **步骤 6：实现模式化侧栏历史**

`PlatformSidebar` 始终在有权限时显示“新建聊天”和“新建工作”。`route.path === '/ai/work'` 时只渲染 `RecentWorkTasks`，其他 AI 聊天路由只渲染 `RecentSessions`。两者拥有独立刷新、打开、重命名和删除事件。

- [ ] **步骤 7：运行转换和侧栏测试确认通过**

运行：

```bash
node tests/plugins/workTaskTransforms.test.js
node tests/plugins/platformShellContract.test.js
```

预期：PASS。

- [ ] **步骤 8：提交工作空态和历史**

```bash
git add zhiheng-fastapi-frontend/plugins/agent_extension/api/workTask.js zhiheng-fastapi-frontend/plugins/agent_extension/utils/workTask.js zhiheng-fastapi-frontend/src/store/modules/workTask.js zhiheng-fastapi-frontend/plugins/agent_extension/views/work/components/WorkAgentPicker.vue zhiheng-fastapi-frontend/plugins/agent_extension/views/work/components/WorkEmptyState.vue zhiheng-fastapi-frontend/src/layout/components/RecentWorkTasks.vue zhiheng-fastapi-frontend/src/layout/components/PlatformSidebar.vue zhiheng-fastapi-frontend/tests/plugins/workTaskTransforms.test.js zhiheng-fastapi-frontend/tests/plugins/platformShellContract.test.js
git commit -m "feat(work): 增加工作空态与独立历史"
```

### 任务 7：多智能体工作任务页面

**文件：**
- 创建：`zhiheng-fastapi-frontend/plugins/agent_extension/views/work/index.vue`
- 创建：`zhiheng-fastapi-frontend/plugins/agent_extension/views/work/components/WorkMemberBar.vue`
- 创建：`zhiheng-fastapi-frontend/plugins/agent_extension/views/work/components/WorkMessageTimeline.vue`
- 创建：`zhiheng-fastapi-frontend/plugins/agent_extension/views/work/components/WorkTaskPage.vue`
- 修改：`zhiheng-fastapi-frontend/tests/plugins/workTaskTransforms.test.js`

- [ ] **步骤 1：扩充失败的任务页面契约测试**

```js
assert.equal(resolveWorkExecutor(task, 'missing').agentId, 'agent-1')
assert.equal(canRunWorkTask({ status: 'completed', members: [{ available: true }] }), false)
assert.equal(canRunWorkTask({ status: 'active', members: [{ available: true }] }), true)
assert.deepEqual(applyWorkEvent(state, { event: 'content', data: { delta: '完成' } }).content, '完成')
assert.equal(applyWorkEvent(state, { event: 'done', data: { status: 'cancelled' } }).status, 'cancelled')
```

- [ ] **步骤 2：运行测试确认失败**

运行：

```bash
node tests/plugins/workTaskTransforms.test.js
```

预期：FAIL，运行状态函数尚未实现。

- [ ] **步骤 3：实现成员栏和消息时间线**

`WorkMemberBar` 显示名称、发布版本、资源可用性和当前执行者；添加、移除、更新版本均发出显式事件。`WorkMessageTimeline` 使用用户与智能体不同头像，智能体消息显示名称和 `v<version>`，失败/取消/中断状态可见。

- [ ] **步骤 4：实现任务页面**

`WorkTaskPage` 组合任务头部、目标、状态命令、成员栏、消息时间线和输入区。运行中禁止第二条并发消息但允许停止；完成状态提供“重新打开”，归档状态只读。

- [ ] **步骤 5：实现页面编排和流式状态**

`work/index.vue` 根据 `taskId` 显示空态或任务页。发送时先创建本地 user/assistant pending 消息，调用 store 流式 action；`meta/content/reasoning/usage/error/done` 只更新匹配 generation、taskId 和 runId 的消息。切任务或卸载时 abort 本地流，刷新详情恢复服务端终态。

- [ ] **步骤 6：运行任务转换测试确认通过**

运行：

```bash
node tests/plugins/workTaskTransforms.test.js
```

预期：PASS。

- [ ] **步骤 7：提交工作任务页面**

```bash
git add zhiheng-fastapi-frontend/plugins/agent_extension/views/work/index.vue zhiheng-fastapi-frontend/plugins/agent_extension/views/work/components/WorkMemberBar.vue zhiheng-fastapi-frontend/plugins/agent_extension/views/work/components/WorkMessageTimeline.vue zhiheng-fastapi-frontend/plugins/agent_extension/views/work/components/WorkTaskPage.vue zhiheng-fastapi-frontend/plugins/agent_extension/utils/workTask.js zhiheng-fastapi-frontend/src/store/modules/workTask.js zhiheng-fastapi-frontend/tests/plugins/workTaskTransforms.test.js
git commit -m "feat(work): 实现多智能体任务页面"
```

### 任务 8：端到端、视觉、构建与回归

**文件：**
- 创建：`zhiheng-fastapi-frontend/tests/e2e/chat_work_mode_check.py`
- 修改：本计划列出的文件中验证发现的本功能问题

- [ ] **步骤 1：编写失败的 Playwright 双模式流程**

fixture 拦截聊天、智能体和工作任务 API，覆盖：

```python
await page.goto(BASE_URL + '/ai/chat', wait_until='networkidle')
await expect(page.get_by_role('button', name='聊天', exact=True)).to_be_visible()
await expect(page.get_by_text('选择知识库', exact=True)).to_have_count(0)
await button(page, '工作').click()
await expect(page).to_have_url(re.compile(r'/ai/work$'))
await select_agent(page, '研究助手')

await page.get_by_label('工作目标', exact=True).fill('整理发布资料')
await button(page, '开始工作').click()
await expect(page).to_have_url(re.compile(r'taskId=task-created'))
await select_executor(page, '数据分析师')
await send_work_message(page, '补充关键指标')
await expect(page.get_by_text('数据分析师', exact=True)).to_be_visible()
```

增加聊天/工作历史分离、创建失败保留输入、刷新恢复、back/forward、取消、版本更新、无效成员、旧 SSE 隔离和 390px 全权限布局断言。

- [ ] **步骤 2：启动 Vite 并确认 E2E 失败**

运行：

```bash
npm exec vite -- --host 127.0.0.1 --port 12584
CHAT_WORK_CHECK_BASE_URL=http://127.0.0.1:12584 python tests/e2e/chat_work_mode_check.py
```

预期：FAIL，首次运行时新工作流程尚未完整接通；修复 E2E 暴露的本功能问题直至通过。

- [ ] **步骤 3：运行后端工作任务回归**

运行：

```bash
pytest -q tests/plugins/agent_extension/test_work_task_schema.py tests/plugins/agent_extension/test_work_task_service.py tests/plugins/agent_extension/test_work_task_run_service.py tests/plugins/agent_extension/test_work_task_controller.py
```

预期：全部通过。

- [ ] **步骤 4：运行完整插件测试**

运行：

```bash
npm run test:plugin
pytest -q tests/plugins/agent_extension
```
预期：前端插件契约和智能体扩展后端测试全部通过。

- [ ] **步骤 5：运行完整 E2E 并检查截图**

运行：

```bash
CHAT_WORK_CHECK_BASE_URL=http://127.0.0.1:12584 python tests/e2e/chat_work_mode_check.py
```

检查聊天空态、工作空态、桌面任务、移动任务四张截图和页面宽度断言。中心切换、任务成员、消息来源、输入区和侧栏历史不得重叠或裁切。

- [ ] **步骤 6：执行生产构建**

运行：

```bash
NODE_OPTIONS=--max-old-space-size=4096 npm exec vite build -- --logLevel error --outDir /tmp/zhiheng-chat-work-build --emptyOutDir
```

预期：退出码 0，仓库 `dist` 不产生新改动。

- [ ] **步骤 7：检查最终差异范围**

运行：

```bash
git diff --check
git status --short
```

预期：只包含计划列出的后端、前端和测试文件；现有 `zhiheng-fastapi-app` 删除、`dist` 构建产物和 `.superpowers` 原型保持未暂存。

- [ ] **步骤 8：提交最终验证修正**

只暂存本功能源文件和测试：

```bash
git add zhiheng-fastapi-backend/plugins/agent_extension zhiheng-fastapi-backend/tests/plugins/agent_extension zhiheng-fastapi-backend/tests/plugins/sample_plugins/test_agent_extension_plugin.py zhiheng-fastapi-frontend/plugins/ai/views/chat zhiheng-fastapi-frontend/plugins/agent_extension zhiheng-fastapi-frontend/src/components/Workspace zhiheng-fastapi-frontend/src/layout/components/PlatformSidebar.vue zhiheng-fastapi-frontend/src/layout/components/RecentWorkTasks.vue zhiheng-fastapi-frontend/src/router/index.js zhiheng-fastapi-frontend/src/store/modules/workTask.js zhiheng-fastapi-frontend/tests/plugins zhiheng-fastapi-frontend/tests/e2e/chat_work_mode_check.py
git commit -m "fix(work): 完成聊天与工作双模式回归"
```
