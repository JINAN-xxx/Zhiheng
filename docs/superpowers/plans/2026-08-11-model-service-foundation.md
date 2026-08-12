# Model Service 基础能力实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `subagent-driven-development`（推荐）或 `executing-plans` 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法跟踪进度；每个任务先使用 `test-driven-development`，完成声明前使用 `verification-before-completion`。

**目标：** 建立 Platform 自有 Model Catalog、Resource Binding 和稳定的 Model Service，使后台业务只使用 Platform Model ID，并通过可替换的 Runtime Engine 调用 LiteLLM。

**架构：** `ModelCatalogService` 管理平台模型生命周期，`ModelService` 负责解析活动模型和 Binding，`ModelRuntimeEngine` 定义引擎中立协议，`LiteLLMRuntimeAdapter` 将协议映射到现有 Runtime Client。旧 `ai_models`、旧模型接口和 Agno 对话暂时保留，但不进入新链路。

**技术栈：** FastAPI、Pydantic 2、SQLAlchemy 2 Async、PostgreSQL 16、MySQL 兼容迁移、HTTPX、Pytest、RuoYi 插件运行时

---

## 范围与依赖

本计划是 V0.1-A 的第一阶段，必须先于以下计划执行：

1. `2026-08-11-litellm-control-plane.md`
2. `2026-08-11-platform-chat-litellm-migration.md`

本阶段不修改旧 `ai_models` 和 `ai_chat_config` 表，不删除旧控制器，不修改聊天页面，不创建 Credential 或 Deployment 镜像表。

## 目标文件

- 创建 `ruoyi-fastapi-backend/plugins/ai/migrations/mysql/002_model_service_foundation.sql`
- 创建 `ruoyi-fastapi-backend/plugins/ai/migrations/postgresql/002_model_service_foundation.sql`
- 创建 `ruoyi-fastapi-backend/plugins/ai/domain/__init__.py`
- 创建 `ruoyi-fastapi-backend/plugins/ai/domain/resource_id.py`
- 创建 `ruoyi-fastapi-backend/plugins/ai/domain/model_runtime.py`
- 创建 `ruoyi-fastapi-backend/plugins/ai/domain/model_service_errors.py`
- 创建 `ruoyi-fastapi-backend/plugins/ai/entity/do/ai_model_catalog_do.py`
- 创建 `ruoyi-fastapi-backend/plugins/ai/entity/do/ai_resource_binding_do.py`
- 创建 `ruoyi-fastapi-backend/plugins/ai/entity/vo/ai_model_catalog_vo.py`
- 创建 `ruoyi-fastapi-backend/plugins/ai/dao/ai_model_catalog_dao.py`
- 创建 `ruoyi-fastapi-backend/plugins/ai/dao/ai_resource_binding_dao.py`
- 创建 `ruoyi-fastapi-backend/plugins/ai/service/model_catalog_service.py`
- 创建 `ruoyi-fastapi-backend/plugins/ai/service/model_service.py`
- 创建 `ruoyi-fastapi-backend/plugins/ai/infrastructure/litellm_runtime_adapter.py`
- 创建 `ruoyi-fastapi-backend/plugins/ai/controller/ai_model_catalog_controller.py`
- 创建相应 `ruoyi-fastapi-backend/tests/plugins/ai/test_*.py`
- 修改 `ruoyi-fastapi-backend/plugins/ai/plugin.yaml`
- 修改 `ruoyi-fastapi-backend/tests/plugins/sample_plugins/test_ai_plugin.py`

### 任务 1：锁定数据库契约和插件迁移顺序

**文件：**

- 创建：`ruoyi-fastapi-backend/tests/plugins/ai/test_model_service_schema.py`
- 创建：`ruoyi-fastapi-backend/plugins/ai/migrations/mysql/002_model_service_foundation.sql`
- 创建：`ruoyi-fastapi-backend/plugins/ai/migrations/postgresql/002_model_service_foundation.sql`
- 创建：`ruoyi-fastapi-backend/plugins/ai/entity/do/ai_model_catalog_do.py`
- 创建：`ruoyi-fastapi-backend/plugins/ai/entity/do/ai_resource_binding_do.py`
- 修改：`ruoyi-fastapi-backend/plugins/ai/plugin.yaml`
- 修改：`ruoyi-fastapi-backend/tests/plugins/sample_plugins/test_ai_plugin.py`

- [ ] **步骤 1：先写 Schema 测试**

测试必须验证：

- Manifest 版本升级为 `0.3.0`。
- 两种数据库的 `002` 迁移均在对应 `001` 之后。
- `ai_model_catalog` 包含 `model_id`、唯一 `model_code`、JSON 能力字段、生命周期和审计字段。
- `ai_resource_binding` 对 `(resource_type, resource_id)` 唯一，包含 `version`，并通过外键引用 `ai_model_catalog.model_id`。
- 迁移不包含 `drop table`，不修改旧表，不写任何 Credential 字段。
- 两个 SQLAlchemy 实体的表名、主键长度和可空性与迁移一致。

关键断言：

```python
assert 'drop table' not in mysql_sql.lower()
assert 'drop table' not in postgresql_sql.lower()
assert 'unique (resource_type, resource_id)' in normalize(mysql_sql)
assert AiModelCatalog.__table__.c.model_id.type.length == 36
assert AiResourceBinding.__table__.c.version.nullable is False
```

- [ ] **步骤 2：运行测试并确认红灯**

```bash
cd ruoyi-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_model_service_schema.py tests/plugins/sample_plugins/test_ai_plugin.py -v
```

预期：新迁移和实体不存在，Manifest 仍只有 `001`，测试失败。

- [ ] **步骤 3：实现追加迁移**

PostgreSQL 使用 `varchar(36)` 资源 ID、`jsonb` JSON 字段、`timestamp(0)` 时间；MySQL 使用 `varchar(36)`、`json`、`datetime`。两边均建立：

```text
ai_model_catalog
  model_id varchar(36) primary key
  model_code varchar(100) unique not null
  display_name varchar(100) not null
  model_type varchar(32) not null
  capabilities json/jsonb not null
  context_window integer null
  max_output_tokens integer null
  default_parameters json/jsonb not null
  lifecycle_status varchar(16) not null
  published_at timestamp/datetime null
  create_by varchar(64) not null default ''
  create_time timestamp/datetime not null
  update_by varchar(64) not null default ''
  update_time timestamp/datetime not null

ai_resource_binding
  binding_id varchar(36) primary key
  resource_type varchar(32) not null
  resource_id varchar(36) not null
  engine_type varchar(32) not null
  engine_resource_id varchar(255) not null
  binding_status varchar(16) not null
  engine_metadata json/jsonb not null
  version integer not null default 1
  create_by/update_by/create_time/update_time
```

数据库约束必须包含：生命周期枚举检查、Binding 状态检查、`version > 0`、正数 Token 限制、资源唯一键和必要索引。外键删除策略使用 `RESTRICT`，不级联删除历史关联。

- [ ] **步骤 4：实现 SQLAlchemy 实体**

实体使用 SQLAlchemy 通用 `JSON` 类型，并为 JSON 字段设置 Python 可调用默认值 `default=dict` 或 `default=list`，不得使用共享可变对象。时间默认值使用 `datetime.now`，不能在导入时调用。

不要在实体中加入 `user_id`、`dept_id`、Provider、API Key 或 Base URL；Model Catalog 是全局平台资源，不套 `DataScopeDependency`。

- [ ] **步骤 5：登记迁移并更新插件测试**

在 `plugin.yaml` 中按数据库分别追加 `002` 路径，不更改 `001` 内容。更新示例插件测试的版本和迁移精确列表；此任务暂不更改菜单、权限或依赖。

- [ ] **步骤 6：运行测试并提交**

```bash
cd ruoyi-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_model_service_schema.py tests/plugins/sample_plugins/test_ai_plugin.py -v
cd ..
git add ruoyi-fastapi-backend/plugins/ai/migrations \
  ruoyi-fastapi-backend/plugins/ai/entity/do/ai_model_catalog_do.py \
  ruoyi-fastapi-backend/plugins/ai/entity/do/ai_resource_binding_do.py \
  ruoyi-fastapi-backend/plugins/ai/plugin.yaml \
  ruoyi-fastapi-backend/tests/plugins/ai/test_model_service_schema.py \
  ruoyi-fastapi-backend/tests/plugins/sample_plugins/test_ai_plugin.py
git commit -m "feat: 建立平台模型目录数据结构"
```

### 任务 2：定义资源 ID、VO 和稳定错误协议

**文件：**

- 创建：`ruoyi-fastapi-backend/plugins/ai/domain/__init__.py`
- 创建：`ruoyi-fastapi-backend/plugins/ai/domain/resource_id.py`
- 创建：`ruoyi-fastapi-backend/plugins/ai/domain/model_service_errors.py`
- 创建：`ruoyi-fastapi-backend/plugins/ai/entity/vo/ai_model_catalog_vo.py`
- 创建：`ruoyi-fastapi-backend/tests/plugins/ai/test_model_service_contract.py`

- [ ] **步骤 1：先写领域契约测试**

覆盖以下行为：

```python
model_id = new_resource_id('mdl')
assert re.fullmatch(r'mdl_[0-9a-f]{32}', model_id)
assert validate_resource_id(model_id, 'mdl') == model_id

with pytest.raises(ValueError):
    validate_resource_id('run_' + 'a' * 32, 'mdl')

assert ModelServiceError.not_found().public_dict() == {
    'code': 'MODEL_NOT_FOUND',
    'message': '平台模型不存在',
}
```

VO 测试覆盖 camelCase、严格枚举、正整数限制、`modelCode` 格式、默认参数白名单、Binding `version >= 1`，以及响应中不存在 `apiKey`、`baseUrl`、`litellmModelId` 等字段。

- [ ] **步骤 2：运行测试并确认红灯**

```bash
cd ruoyi-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_model_service_contract.py -v
```

- [ ] **步骤 3：实现资源 ID 工具**

只允许 `mdl`、`bnd`、`ses`、`msg`、`run` 前缀；生成值使用 `uuid4().hex`。校验函数必须检查前缀和 32 位小写十六进制，不从 ID 推导租户、Provider 或 Engine。

- [ ] **步骤 4：实现错误类型**

`ModelServiceError` 只保存 `status_code`、稳定 `code` 和脱敏 `public_message`。实现设计规格中的十个错误映射：`MODEL_NOT_FOUND`、`MODEL_DISABLED`、`MODEL_BINDING_MISSING`、`MODEL_RUNTIME_MISCONFIGURED`、`MODEL_RUNTIME_UNAVAILABLE`、`MODEL_RUNTIME_TIMEOUT`、`MODEL_RUNTIME_AUTH_FAILED`、`MODEL_PROVIDER_AUTH_FAILED`、`MODEL_PROVIDER_RATE_LIMITED`、`MODEL_RUNTIME_ERROR`。

错误对象不得保存上游响应正文、内部 URL、认证头或 Credential。Controller 后续把它转换为 `HTTPException(detail=error.public_dict())`。

- [ ] **步骤 5：实现请求和响应 VO**

至少定义：

- `ModelCatalogCreateModel`
- `ModelCatalogUpdateModel`
- `ModelCatalogPageQueryModel`
- `ResourceBindingUpsertModel`
- `PlatformModelModel`
- `PlatformModelDetailModel`

`default_parameters` 只接受 `temperature`、`top_p`、`max_tokens`、`presence_penalty`、`frequency_penalty`；`capabilities` 只接受明确的布尔能力键。请求模型使用 `extra='forbid'`，阻止任意 Provider 参数穿过 Platform API。

- [ ] **步骤 6：运行测试并提交**

```bash
cd ruoyi-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_model_service_contract.py -v
cd ..
git add ruoyi-fastapi-backend/plugins/ai/domain \
  ruoyi-fastapi-backend/plugins/ai/entity/vo/ai_model_catalog_vo.py \
  ruoyi-fastapi-backend/tests/plugins/ai/test_model_service_contract.py
git commit -m "feat: 定义模型服务领域契约"
```

### 任务 3：实现 Model Catalog 与 Binding DAO

**文件：**

- 创建：`ruoyi-fastapi-backend/plugins/ai/dao/ai_model_catalog_dao.py`
- 创建：`ruoyi-fastapi-backend/plugins/ai/dao/ai_resource_binding_dao.py`
- 创建：`ruoyi-fastapi-backend/tests/plugins/ai/test_model_catalog_dao.py`

- [ ] **步骤 1：使用 SQLite 异步会话先写 DAO 测试**

测试在内存数据库仅创建两个新实体，覆盖：创建、按 ID 查询、按 code 查询、分页筛选、更新、删除草稿、读取当前 Binding，以及乐观锁更新。

乐观锁必须由单条条件更新完成：

```python
update(AiResourceBinding)
.where(
    AiResourceBinding.binding_id == binding_id,
    AiResourceBinding.version == expected_version,
)
.values(..., version=expected_version + 1)
```

测试必须证明两个并发调用使用同一版本时只有一个 `rowcount == 1`，另一个得到冲突结果。

- [ ] **步骤 2：运行测试并确认红灯**

```bash
cd ruoyi-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_model_catalog_dao.py -v
```

- [ ] **步骤 3：实现 DAO**

DAO 只执行数据库查询和对象持久化，不自行 `commit`，不做 Runtime 网络调用，不知道 HTTP 状态。分页返回项目现有 `PageModel`；JSON 列保持 Python `dict`/`list`，禁止手工拼接 JSON 字符串。

Binding 查询始终按 `(resource_type='model', resource_id=model_id)`，Service 不依赖底层主键猜测当前 Binding。

- [ ] **步骤 4：运行测试并提交**

```bash
cd ruoyi-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_model_catalog_dao.py -v
cd ..
git add ruoyi-fastapi-backend/plugins/ai/dao/ai_model_catalog_dao.py \
  ruoyi-fastapi-backend/plugins/ai/dao/ai_resource_binding_dao.py \
  ruoyi-fastapi-backend/tests/plugins/ai/test_model_catalog_dao.py
git commit -m "feat: 实现模型目录持久化层"
```

### 任务 4：定义 Runtime Engine 并实现 LiteLLM Adapter

**文件：**

- 创建：`ruoyi-fastapi-backend/plugins/ai/domain/model_runtime.py`
- 创建：`ruoyi-fastapi-backend/plugins/ai/infrastructure/litellm_runtime_adapter.py`
- 创建：`ruoyi-fastapi-backend/tests/plugins/ai/test_litellm_runtime_adapter.py`

- [ ] **步骤 1：先写 Adapter 契约测试**

领域协议至少定义：

```python
@dataclass(frozen=True)
class ModelChatRequest:
    engine_model: str
    messages: Sequence[ModelMessage]
    parameters: Mapping[str, object]

class ModelRuntimeEngine(Protocol):
    async def list_model_ids(self) -> set[str]: ...
    async def chat(self, request: ModelChatRequest) -> ModelChatResponse: ...
    def stream_chat(self, request: ModelChatRequest) -> AsyncIterator[ModelStreamEvent]: ...
```

测试覆盖：

- Platform 请求被转换为 OpenAI Chat Completion JSON。
- `engine_model` 原样映射为 LiteLLM `model`，领域对象中没有 LiteLLM 命名。
- 普通响应归一化文本、Reasoning、Usage、Request ID 和 Cost。
- Runtime Client 五类错误被映射为稳定 Model Service 错误。
- Provider 认证和限流通过脱敏的 LiteLLM 错误类型或响应类别区分，原始正文不穿透。
- `list_model_ids()` 只返回 `/v1/models` 中合法非空 ID。

- [ ] **步骤 2：运行测试并确认红灯**

```bash
cd ruoyi-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_litellm_runtime_adapter.py -v
```

- [ ] **步骤 3：实现领域协议和普通响应映射**

领域类型使用不可变 dataclass 或严格 Pydantic 模型。Usage 统一为 `input_tokens`、`output_tokens`、`reasoning_tokens`、`cache_read_tokens`、`cache_write_tokens`、`raw_cost`；未知字段丢弃，不把整份 LiteLLM 响应保存到对象。

本任务为流式方法保留协议并实现最小委托；任意 SSE 分片解析和 Chat 持久化在第三阶段完成。普通 Chat 和模型列表必须可用。

- [ ] **步骤 4：必要时细化 LiteLLM 错误类型**

只修改：

- `ruoyi-fastapi-backend/plugins/ai/infrastructure/litellm_errors.py`
- `ruoyi-fastapi-backend/tests/plugins/ai/test_litellm_client.py`

如果当前 Client 无法区分 Provider 认证、限流和一般故障，则根据 HTTP 状态与 LiteLLM 的稳定错误分类增加内部子类。解析内容只用于分类，异常对象和日志不得保存原始响应正文。

- [ ] **步骤 5：运行回归测试并提交**

```bash
cd ruoyi-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_litellm_client.py \
  tests/plugins/ai/test_litellm_streaming.py \
  tests/plugins/ai/test_litellm_runtime_adapter.py -v
cd ..
git add ruoyi-fastapi-backend/plugins/ai/domain/model_runtime.py \
  ruoyi-fastapi-backend/plugins/ai/infrastructure/litellm_runtime_adapter.py \
  ruoyi-fastapi-backend/plugins/ai/infrastructure/litellm_errors.py \
  ruoyi-fastapi-backend/tests/plugins/ai/test_litellm_client.py \
  ruoyi-fastapi-backend/tests/plugins/ai/test_litellm_runtime_adapter.py
git commit -m "feat: 接入可替换模型运行时"
```

### 任务 5：实现 Model Catalog 生命周期服务

**文件：**

- 创建：`ruoyi-fastapi-backend/plugins/ai/service/model_catalog_service.py`
- 创建：`ruoyi-fastapi-backend/tests/plugins/ai/test_model_catalog_service.py`

- [ ] **步骤 1：用 Fake DAO 和 Fake Runtime 先写服务测试**

必须覆盖：

- 创建时服务端生成 `mdl_` ID，状态固定为 `draft`。
- `model_code` 重复返回冲突，不依赖数据库异常文本。
- 从未发布的草稿可改 code；有 `published_at` 后 code 永久不可变。
- Binding 使用 `bnd_` ID，`engine_type` 固定为 `litellm`，更新要求正确版本。
- 发布要求活动 Binding 且 `engine_resource_id` 存在于 Runtime Key 可见模型列表。
- 发布失败时本地事务回滚，不出现部分状态。
- `active -> disabled` 合法；重新发布时重新验证 Runtime。
- 仅 `draft`、从未发布且无 Chat 引用时可删除。
- Service 明确负责 `commit`/`rollback`，DAO 不提交。

- [ ] **步骤 2：运行测试并确认红灯**

```bash
cd ruoyi-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_model_catalog_service.py -v
```

- [ ] **步骤 3：实现生命周期状态机**

公开方法固定为：`list_models`、`get_model`、`create_model`、`update_model`、`upsert_binding`、`publish_model`、`disable_model`、`delete_draft`。审计用户名和当前时间作为显式参数传入，方便测试，不从全局上下文读取。

删除前通过注入的引用检查器查询 Chat 引用；第一阶段默认实现可查询新 Chat 表是否存在或返回 `False`，第三阶段接入真实 DAO。不得通过捕获“表不存在”异常默认为可删除；使用显式接口和测试替身。

- [ ] **步骤 4：运行测试并提交**

```bash
cd ruoyi-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_model_catalog_service.py -v
cd ..
git add ruoyi-fastapi-backend/plugins/ai/service/model_catalog_service.py \
  ruoyi-fastapi-backend/tests/plugins/ai/test_model_catalog_service.py
git commit -m "feat: 实现平台模型生命周期"
```

### 任务 6：实现稳定的 Model Service 调用入口

**文件：**

- 创建：`ruoyi-fastapi-backend/plugins/ai/service/model_service.py`
- 创建：`ruoyi-fastapi-backend/tests/plugins/ai/test_model_service.py`

- [ ] **步骤 1：先写解析和调用测试**

测试覆盖不存在、草稿、停用、缺少 Binding、Binding 停用、正常解析、普通 Chat 参数合并和快照输出。调用结果必须包含：Platform Model ID、Binding ID、Engine Type、Engine Model Snapshot 和归一化响应。

参数合并顺序固定为：Model Catalog 默认值，然后是调用方被允许覆盖的值；调用方不能覆盖 `model`、认证、Base URL 或任意 Provider 私有字段。

- [ ] **步骤 2：运行测试并确认红灯**

```bash
cd ruoyi-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_model_service.py -v
```

- [ ] **步骤 3：实现 Model Service**

Controller、后续 Chat Service 和未来 Public OpenAPI 只依赖此类。`resolve_model()` 在任何网络调用前完成 Platform Model 和 Binding 校验；`chat()` 与 `stream_chat()` 接收 Platform Model ID，不接受 LiteLLM alias。

- [ ] **步骤 4：运行测试并提交**

```bash
cd ruoyi-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_model_service.py -v
cd ..
git add ruoyi-fastapi-backend/plugins/ai/service/model_service.py \
  ruoyi-fastapi-backend/tests/plugins/ai/test_model_service.py
git commit -m "feat: 建立平台 Model Service 入口"
```

### 任务 7：发布 Model Catalog 后台 API 和权限

**文件：**

- 创建：`ruoyi-fastapi-backend/plugins/ai/controller/ai_model_catalog_controller.py`
- 创建：`ruoyi-fastapi-backend/tests/plugins/ai/test_ai_model_catalog_controller.py`
- 修改：`ruoyi-fastapi-backend/plugins/ai/plugin.yaml`
- 修改：`ruoyi-fastapi-backend/tests/plugins/sample_plugins/test_ai_plugin.py`

- [ ] **步骤 1：先写路由和权限测试**

准确断言以下路由和方法：

```text
GET    /ai/models
GET    /ai/models/{model_id}
POST   /ai/models
PATCH  /ai/models/{model_id}
PUT    /ai/models/{model_id}/binding
POST   /ai/models/{model_id}/publish
POST   /ai/models/{model_id}/disable
DELETE /ai/models/{model_id}
```

权限分别使用现有 `ai:model:list/query/add/edit/remove`，发布使用新增 `ai:model:publish`。所有路由要求登录；任何路由都不得有 `DataScopeDependency`。测试还要断言 `ModelServiceError` 被转换成正确 HTTP 状态和 `{code, message}`，且日志装饰器排除完整请求中可能含动态参数的字段。

- [ ] **步骤 2：运行测试并确认红灯**

```bash
cd ruoyi-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_ai_model_catalog_controller.py -v
```

- [ ] **步骤 3：实现 Controller**

使用 `DBSessionDependency` 和 `CurrentUserDependency`，把当前用户名作为审计参数传入 Service。Controller 不访问 Runtime Client，不拼 LiteLLM payload，不捕获数据库通用异常。成功响应继续使用 `ResponseUtil`，稳定领域错误使用 `HTTPException`。

- [ ] **步骤 4：更新 Manifest 权限**

追加 `ai:model:publish` 和 `ai:model:test`，保留旧权限以避免菜单装载中断。菜单切换留到控制面计划；本任务只开放 API。

- [ ] **步骤 5：运行回归并提交**

```bash
cd ruoyi-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_ai_model_catalog_controller.py \
  tests/plugins/ai/test_model_catalog_service.py \
  tests/plugins/sample_plugins/test_ai_plugin.py -v
cd ..
git add ruoyi-fastapi-backend/plugins/ai/controller/ai_model_catalog_controller.py \
  ruoyi-fastapi-backend/plugins/ai/plugin.yaml \
  ruoyi-fastapi-backend/tests/plugins/ai/test_ai_model_catalog_controller.py \
  ruoyi-fastapi-backend/tests/plugins/sample_plugins/test_ai_plugin.py
git commit -m "feat: 发布平台模型目录接口"
```

### 任务 8：在真实 PostgreSQL 上验证第一阶段

**文件：**

- 修改：仅修复本阶段验证暴露的问题，不新增范围。

- [ ] **步骤 1：执行完整后端测试**

```bash
cd ruoyi-fastapi-backend
conda run -n dj pytest tests/plugins/ai tests/plugins/sample_plugins/test_ai_plugin.py -v
```

预期：全部通过，旧 Runtime Client 测试无回归。

- [ ] **步骤 2：重启 Backend 让插件运行时应用 `002`**

```bash
docker compose -f docker-compose.pg.yml restart ruoyi-backend-pg
docker compose -f docker-compose.pg.yml logs --tail=200 ruoyi-backend-pg
```

预期：插件迁移成功，无 checksum 冲突，Backend 健康。

- [ ] **步骤 3：验证真实表与约束**

```bash
docker exec litellm-db psql -U ruoyi -d ruoyi-fastapi -Atc \
  "select tablename from pg_tables where schemaname='public' and tablename in ('ai_model_catalog','ai_resource_binding') order by tablename"
docker exec litellm-db psql -U ruoyi -d ruoyi-fastapi -Atc \
  "select count(*) from ai_model_catalog"
```

预期：返回两个新表，初始模型数为 `0`。不要直接写数据库造模型，API 冒烟通过后由第二阶段界面创建。

- [ ] **步骤 4：验证 API 契约**

通过已登录浏览器或现有认证测试客户端调用 `GET /ai/models`，预期 `200` 和空分页结果；未登录调用预期 `401`。不要把登录令牌写进计划、测试输出或提交。

- [ ] **步骤 5：检查工作区和阶段边界**

```bash
git status --short
git diff --check
git log --oneline -8
```

确认未提交 `.env`、`.superpowers/`、用户的架构汇报文档或任何密钥；确认旧 `ai_models`、`ai_chat_config` 和 Agno 链路仍未删除。

## 第一阶段完成标准

- Platform Model 与 LiteLLM alias 已通过 Resource Binding 解耦。
- 新 Model Service 只接受 Platform Model ID。
- 发布前会真实验证 Runtime Key 可见模型。
- Model Catalog API 有完整 RBAC、生命周期和乐观锁测试。
- PostgreSQL 真实迁移成功，MySQL 迁移通过静态契约测试。
- 旧聊天仍可运行，第二、三阶段可以在稳定基础上继续。
