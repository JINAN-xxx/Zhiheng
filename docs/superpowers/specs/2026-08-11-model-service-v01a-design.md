# AI Open Platform V0.1-A Model Service 设计

## 文档状态

本规格是当前有效的 V0.1-A 实施基线，替代 `2026-08-11-litellm-model-gateway-design.md`。

当前 ZhiHeng-Vue3-FastAPI 仓库就是新 AI Open Platform 的工程载体。ZhiHeng 提供登录、RBAC、菜单、审计、插件运行时和运营后台外壳；Platform 核心领域在当前仓库中重新建设，而不是把旧 AI 插件直接延伸成最终平台模型。

## 背景与已验证现状

当前环境已经具备：

- LiteLLM Proxy `1.95.0`、PostgreSQL 和 Redis 独立运行。
- ZhiHeng Backend 通过内部 Docker 网络使用 Runtime Key 访问 LiteLLM。
- `/ai/runtime/health` 与 `/ai/runtime/models` 已完成真实联通验证。
- ZhiHeng 和 LiteLLM 共用一个 PostgreSQL 实例，但使用独立数据库和账号。
- LiteLLM 数据库启用了动态模型管理。

当前旧 AI 插件仍存在以下耦合：

- `ai_models` 同时保存模型业务信息和 Provider API Key。
- `AiChatService` 通过 Agno 创建 Provider 模型并执行对话。
- 会话、消息、运行状态、取消和 Usage 依赖 Agno Session 与 Run 对象。
- 模型管理页面是旧式 ZhiHeng CRUD，并直接读写 `ai_models`。

数据库已实查：

- `ai_models` 为 `0` 行。
- `ai_chat_config` 为 `0` 行。
- Agno `ai_sessions` 表尚未创建。

因此本阶段不需要历史模型或会话迁移，也不需要双写兼容。可以直接建立正确的平台边界。

## 架构目标

V0.1-A 完成第一个可运行的 Model Service 垂直闭环：

```text
ZhiHeng Model Center / Debug Chat
                |
                v
          Platform API
                |
        +-------+-------+
        |               |
        v               v
  Model Catalog    Platform Chat
        |               |
        +-------+-------+
                v
          Model Service
                |
                v
       ModelRuntimeEngine
                |
                v
         LiteLLM Adapter
                |
                v
             LiteLLM
                |
                v
            Provider
```

核心原则：

- Platform 是业务主数据和产品契约的所有者。
- Model Service 是稳定的模型能力边界。
- LiteLLM 是当前 Model Runtime，可在未来替换。
- 浏览器和外部调用方只认识 Platform Model ID、Platform 错误和 Platform Usage。
- Provider Credential、物理 Deployment 和路由属于 LiteLLM 技术控制面。
- 模型对话通过 LiteLLM 提供，Agno 不再参与 Model Service 或 Chat 链路。

## 本期目标

- 建立 Platform Model Catalog 和通用 Resource Binding。
- 建立可替换的 `ModelRuntimeEngine` 协议和 LiteLLM Adapter。
- 在 ZhiHeng 中管理 LiteLLM Provider Credential、Engine Deployment 和运行状态。
- 在 ZhiHeng 中发布 Platform Model，并绑定 LiteLLM 模型别名。
- 将现有调试对话迁移为 Platform 自有 Session、Message 和 Run。
- 通过 LiteLLM 完成普通和流式 Chat Completions。
- 保留图片、Reasoning、历史上下文、Usage、取消和会话归属能力。
- 重做模型中心和调试对话页面，使其成为现代运营工作台。
- 从活跃模型链路中移除 Agno、Provider SDK 和旧 `AiUtil`。
- 为后续 Tenant、Project、Application、Developer API Key、Entitlement、Quota 和 Public OpenAPI 保留稳定边界。

## 非目标

本阶段不负责：

- Tenant、Project 和 Application 的正式业务表与管理页。
- Developer API Key、Scope 和 Model Entitlement。
- 最终 Quota、RPM/TPM 执行、Usage Ledger 或 Billing。
- 公共 `/v1/models`、`/v1/chat/completions`、`/v1/responses` 和 `/v1/embeddings`。
- Knowledge Service、BISHENG、RAG、Agent、Workflow 或 MCP。
- 复杂 Router、Fallback、权重和路由策略的 Platform 可视化编辑。
- 删除旧空表；破坏性 Schema 清理放到功能稳定后的独立阶段。
- 自动调用真实收费 Provider。

公共 `/v1/*` 不应在缺少 Application API Key、Scope、Entitlement 和 Quota 时提前暴露。V0.1-B 将复用本规格的 Model Service，而不是绕过它直接代理 LiteLLM。

## 方案选择

采用“Platform Model Service + LiteLLM Adapter + Platform 自有会话”方案。

未采用：

- 不在旧 `AiChatService` 中直接将 Agno 调用替换成 LiteLLM，因为这会继续绑定旧 `ai_models`，后续建设平台 ID、Entitlement 和 OpenAPI 时仍需重写。
- 不让前端直接调用 LiteLLM，因为这会泄露 Runtime 契约和认证边界，并绕过 Platform Usage、审计和资源权限。
- 不将 LiteLLM Model Deployment 当作 Platform Model，因为物理部署、路由和产品模型不是同一生命周期。

## 身份与领域边界

当前 `sys_user`、`sys_role`、`sys_dept` 只承担 Platform 运营人员的登录、后台权限和数据审计。`sys_dept` 不作为 Tenant。

V0.1-A 的调试对话属于已登录运营用户，使用 `sys_user.user_id` 校验会话归属。后续 Tenant、Project 和 Application 使用独立领域模型，并在 Model Service 入口增加身份上下文；Engine Adapter 无需变化。

## Platform Model Catalog

### `ai_model_catalog`

Platform Model 是用户、Entitlement 和未来公共 API 看到的稳定资源。

| 字段 | 约束 | 含义 |
|---|---|---|
| `model_id` | 主键，`mdl_<uuid>` | Platform 不可变资源 ID |
| `model_code` | 唯一 | 对外稳定模型名，如 `model_company_reasoning` |
| `display_name` | 必填 | 运营界面显示名称 |
| `model_type` | 必填 | `chat`、`embedding` 等 |
| `capabilities` | JSON | `vision`、`reasoning`、`tools` 等能力 |
| `context_window` | 可空正整数 | 上下文窗口 |
| `max_output_tokens` | 可空正整数 | 最大输出 Token |
| `default_parameters` | JSON | Platform 允许的默认推理参数 |
| `lifecycle_status` | 必填 | `draft`、`active`、`disabled` |
| 审计字段 | 必填 | 创建人、更新人和时间 |

规则：

- `model_id` 创建后不可修改。
- `model_code` 在模型首次发布后不可修改，避免破坏调用方契约。
- 只有 `active` 模型可以用于调试对话和未来公共 API。
- Capability 来自 Platform 产品定义，不盲目信任 LiteLLM 自动推断。
- `default_parameters` 只允许显式白名单字段，不接受任意 Provider 参数。
- `draft` 可以编辑；发布前必须存在有效 Binding，且绑定的 LiteLLM 模型别名可被 Runtime Key 查询到。
- `active` 可以停用为 `disabled`；重新启用时再次验证 Binding。
- 只有从未发布、没有 Chat 引用的 `draft` 可以物理删除；已发布模型只能停用，保证历史 Run 的外键和审计语义稳定。

### 资源 ID

本阶段采用带类型前缀的 UUID4 紧凑字符串：

- Model：`mdl_<32 hex>`
- Binding：`bnd_<32 hex>`
- Session：`ses_<32 hex>`
- Message：`msg_<32 hex>`
- Run：`run_<32 hex>`

前缀用于识别资源类型，随机部分不承载租户、Provider 或 Engine 信息。

## Resource Binding

### `ai_resource_binding`

Resource Binding 隔离 Platform Resource 与底层 Engine Resource：

| 字段 | 含义 |
|---|---|
| `binding_id` | Platform Binding ID |
| `resource_type` | 本期固定为 `model` |
| `resource_id` | Platform `model_id` |
| `engine_type` | 本期为 `litellm` |
| `engine_resource_id` | LiteLLM 模型别名，不是 Deployment ID |
| `binding_status` | `active` 或 `disabled` |
| `engine_metadata` | 非敏感 Engine 元数据 |
| `version` | 乐观锁版本 |
| 审计字段 | 创建人、更新人和时间 |

约束：

- 一个 Platform Resource 当前只保留一条 Binding 记录，通过更新 Binding 切换 Engine Resource。
- `engine_resource_id` 绑定 LiteLLM 模型别名；同一别名可由 LiteLLM 路由到多个物理 Deployment。
- Platform 不把 LiteLLM Deployment ID 暴露为产品资源 ID。
- Binding 变更使用乐观锁，避免并发编辑覆盖。
- Chat Run 保存 Binding 与 Engine Model 快照，因此 Binding 更新不会修改历史调用归属。

### Platform Model 接口

| 接口 | 用途 |
|---|---|
| `GET /ai/models` | 分页查询 Platform Model |
| `GET /ai/models/{model_id}` | 查询 Model 与当前 Binding |
| `POST /ai/models` | 创建 `draft` Model |
| `PATCH /ai/models/{model_id}` | 编辑允许修改的产品字段 |
| `PUT /ai/models/{model_id}/binding` | 创建或切换 Binding，使用版本号防止覆盖 |
| `POST /ai/models/{model_id}/publish` | 验证 Binding 后发布 |
| `POST /ai/models/{model_id}/disable` | 停用已发布模型 |
| `DELETE /ai/models/{model_id}` | 仅删除未发布且未被引用的草稿 |

Model Catalog 是全局平台资源，V0.1-A 只使用后台 RBAC，不套用基于 `sys_dept` 的 Data Scope。V0.1-B 使用独立 Model Entitlement 将 Platform Model 授予 Tenant、Project 或 Application。

## LiteLLM 控制面

Provider Credential、物理 Deployment、重试和路由仍由 LiteLLM 持久化。Platform 通过安全的管理 Adapter 提供统一界面，不建立 Credential 或 Deployment 镜像表。

### 双凭据

| 配置 | 用途 |
|---|---|
| `LITELLM_RUNTIME_KEY` | 模型查询和推理 |
| `LITELLM_MANAGEMENT_KEY` | Credential、Deployment 和健康管理 |

本机已验证 Runtime Key 调用 `/v2/model/info` 与 `/credentials` 返回 `403`。当前 LiteLLM `1.95.0` 下，`LITELLM_MANAGEMENT_KEY` 使用具备 Proxy Admin 权限的部署凭据；首期为现有 Master Key 的值。代码只依赖管理凭据语义，未来可以替换为满足接口权限的受限服务凭据。

约束：

- 两类 Key 都只注入 ZhiHeng Backend。
- Key 不写入 Platform 数据库、不返回前端、不进入日志。
- Runtime Client 和 Management Client 是独立类，不能混用认证头。
- Management Key 缺失不阻止 Platform 启动；控制面显示未配置并禁用写操作。

### 控制面接口

Platform 提供 Engine 中立的后台接口：

- `GET /ai/model-engine/providers`
- `GET /ai/model-engine/deployments`
- `GET /ai/model-engine/deployments/{id}`
- `POST /ai/model-engine/deployments`
- `PATCH /ai/model-engine/deployments/{id}`
- `DELETE /ai/model-engine/deployments/{id}`
- `POST /ai/model-engine/test-connection`
- `GET /ai/model-engine/credentials`
- `POST /ai/model-engine/credentials`
- `PUT /ai/model-engine/credentials/{name}/values`
- `DELETE /ai/model-engine/credentials/{name}`
- `GET /ai/model-engine/status`

这些接口由 `LiteLLMManagementAdapter` 映射到 LiteLLM `1.95.0` 的实际管理接口。前端不直接调用 LiteLLM。

### 安全响应

Credential 列表只返回：

- Credential 名称。
- Provider。
- 引用 Deployment 数量。
- 可用的创建和更新时间。

不提供查看旧密钥。轮换时用户重新输入 Provider 所需字段。Controller 和 Service 使用字段白名单；LiteLLM 任意附加字段、解密值、认证对象和原始错误正文不返回浏览器。

Provider 字段通过 LiteLLM `/public/providers/fields` 动态获取，但必须规范化为 Platform VO：`key`、`label`、`placeholder`、`tooltip`、`required`、`fieldType`、`options` 和 `defaultValue`。

### 接入和发布流程

“接入模型”界面引导用户完成三个明确步骤：

1. 选择或创建 Provider Credential。
2. 创建并主动测试 LiteLLM Deployment。
3. 创建或选择 Platform Model，保存 Resource Binding 并发布。

三个步骤不伪装成跨数据库事务：

- Credential 创建成功而 Deployment 失败时，Credential 保留供重试。
- Deployment 创建成功而 Platform 发布失败时，该 Deployment 在“引擎部署”中标记为未发布，可继续绑定。
- Platform 发布操作只在本地数据库事务中提交 Model 与 Binding。
- 不自动补偿删除外部资源，避免误删被其他路由引用的 Credential 或 Deployment。

连接测试调用 LiteLLM `/health/test_connection`，可能触发一次真实 Provider 请求。界面明确提示可能产生极小费用，只在用户主动点击后执行。

## Model Service

Controller、Chat Service 和未来 Public OpenAPI 只依赖 Model Service，不直接依赖 LiteLLM Client。

### 组件职责

```text
ModelService
  |- ModelCatalogRepository
  |- ResourceBindingRepository
  `- ModelRuntimeEngine
       `- LiteLLMRuntimeAdapter
```

`ModelService` 负责：

- 读取并校验 Platform Model 生命周期。
- 解析当前有效 Resource Binding。
- 生成 Engine 中立的请求模型。
- 调用 `ModelRuntimeEngine`。
- 归一化响应、Usage 和错误。

`ModelRuntimeEngine` 定义：

- 普通 Chat Completion。
- 流式 Chat Completion。
- Embedding 能力的扩展接口占位，但本阶段不暴露业务入口。
- 关闭资源。

接口模型不得包含 `litellm_*` 命名字段。LiteLLM 特有转换全部位于 Adapter 内。

### 错误协议

Model Service 使用稳定 Platform 错误：

| Platform 错误 | 场景 | HTTP 状态 |
|---|---|---|
| `MODEL_NOT_FOUND` | Platform Model 不存在 | `404` |
| `MODEL_DISABLED` | 模型未发布或停用 | `409` |
| `MODEL_BINDING_MISSING` | 无有效 Binding | `503` |
| `MODEL_RUNTIME_MISCONFIGURED` | Runtime Key 缺失 | `503` |
| `MODEL_RUNTIME_UNAVAILABLE` | LiteLLM 不可达 | `503` |
| `MODEL_RUNTIME_TIMEOUT` | 上游超时 | `504` |
| `MODEL_RUNTIME_AUTH_FAILED` | 内部 Runtime Key 被拒绝 | `502` |
| `MODEL_PROVIDER_AUTH_FAILED` | Provider 凭据失败 | `502` |
| `MODEL_PROVIDER_RATE_LIMITED` | Provider 限流 | `502` |
| `MODEL_RUNTIME_ERROR` | 其他 Engine 故障 | `502` |

LiteLLM `401/403` 不能直接返回为当前用户认证失败。LiteLLM 和 Provider 原始响应、内部 URL、认证头和 Credential 不保存在异常对象中。

## Platform Chat 数据模型

### `ai_chat_session`

| 字段 | 含义 |
|---|---|
| `session_id` | Platform Session ID |
| `user_id` | 当前运营用户 |
| `model_id` | 会话锁定的 Platform Model |
| `title` | 首条用户消息的本地摘要 |
| `status` | `active` 或 `archived` |
| `created_at`、`updated_at` | 时间 |

一个 Session 在首条消息后锁定 Model。用户切换模型时创建新 Session，避免同一历史上下文混入多个模型而难以审计。

### `ai_chat_message`

| 字段 | 含义 |
|---|---|
| `message_id` | Platform Message ID |
| `session_id` | Session 外键 |
| `client_message_id` | 浏览器生成的幂等 ID |
| `role` | `user` 或 `assistant` |
| `content` | 正文 |
| `reasoning_content` | 可空推理内容 |
| `images` | 上传文件引用 JSON，不保存二进制 |
| `status` | `complete`、`interrupted` 或 `cancelled` |
| `created_at`、`updated_at` | 时间 |

`(session_id, client_message_id)` 唯一，防止网络重试重复写入用户消息。

### `ai_chat_run`

| 字段 | 含义 |
|---|---|
| `run_id` | Platform Run ID |
| `session_id` | Session 外键 |
| `model_id` | Platform Model 快照 |
| `binding_id` | Binding 快照 |
| `engine_type` | 本期 `litellm` |
| `engine_model_snapshot` | 当时的 LiteLLM 模型别名 |
| `engine_request_id` | 可空 Engine 请求 ID |
| `trace_id` | Platform Trace ID |
| `status` | `pending`、`running`、`completed`、`cancelled`、`interrupted`、`failed` |
| Usage 字段 | 输入、输出、推理、缓存 Token 和 Engine 原始 Cost |
| `error_code` | 脱敏 Platform 错误码 |
| 时间字段 | 开始、结束和创建时间 |

Run 不保存 Provider Key、认证头或原始错误正文。Usage 是调试和后续 Ledger 投影的原始事实，不是最终计费账本。

### 用户偏好

现有空 `ai_chat_config` 不再作为 Agno 配置使用。新 `ai_chat_preferences` 保存：

- `user_id`
- 默认 Temperature。
- 是否附带历史。
- 历史消息数量。
- System Prompt。
- 是否默认显示 Usage。
- Vision 开关和图片大小上限。

偏好只影响 Platform Chat，不写入 Platform Model Catalog。

## Chat 调用流程

### 发送前

1. 校验当前用户和 Session 归属。
2. 校验 Platform Model 为 `active`。
3. 校验 Session 的锁定模型与请求一致。
4. 使用 Redis Session Lock 阻止同一 Session 并发生成。
5. 根据 `client_message_id` 做幂等检查。
6. 在本地事务中创建或更新 Session、用户 Message、Assistant 草稿和 `pending` Run。
7. Model Service 解析 Binding，并将 Engine 快照写入 Run。

在建立上游流之前发生的校验失败使用正常 HTTP 错误响应，不开启 SSE。

### 历史上下文

Platform 从 `ai_chat_message` 读取已完成或可见的历史消息：

- System Prompt 位于最前。
- 只取用户偏好允许的最近消息数。
- 当前用户消息只出现一次。
- `interrupted` Assistant 内容可在 UI 中显示，但默认不加入下一次模型上下文。
- Reasoning 内容不重新发送给模型。

### 图片

前端只上传 Platform 文件引用。Backend 必须：

- 校验上传前缀、路径边界、文件存在性、MIME 和大小。
- 只允许当前用户可访问的文件。
- 转成 OpenAI 多模态内容块或受控 Data URL。
- 不把宿主机或容器本地路径发送给 LiteLLM。
- 不在日志中记录 Base64 或图片正文。

### 流式协议

内部 `/ai/chat/send` 使用符合 SSE 语法的 Platform 事件：

```text
event: meta
data: {"sessionId":"ses_...","runId":"run_..."}

event: content
data: {"delta":"..."}

event: reasoning
data: {"delta":"..."}

event: usage
data: {"inputTokens":1,"outputTokens":1,"reasoningTokens":0}

event: done
data: {"status":"completed"}
```

错误发生在流建立后时发送 `error` 事件和终止 `done`，不能改变已经发送的 HTTP 状态。

LiteLLM Adapter 必须处理任意网络分片：一个 JSON 事件可能跨多个字节块，一个字节块也可能包含多个事件。Adapter 以 SSE 行和事件边界解析，不按 HTTP Chunk 假定消息边界。

请求设置 `stream_options.include_usage=true`。Adapter 归一化：

- 文本 Delta。
- Reasoning Delta。
- Prompt、Completion、Reasoning 和 Cache Token。
- LiteLLM Request ID 和可用的原始 Cost。

### 持久化与终止

- 生成期间在内存中累计当前 Assistant 内容。
- 正常完成时在一个事务中保存 Assistant 最终内容、Usage 和 Run 终态。
- 用户取消、浏览器断开或可捕获的上游中断时保存已生成部分，并标记 `cancelled` 或 `interrupted`。
- Worker 异常退出留下的 `running` Run 在下次启动时按超时阈值标记 `interrupted`。
- 终态处理和 Redis Lock 释放位于 `finally`，必须幂等。

### 取消

`POST /ai/chat/cancel` 校验 Run 归属后，在 Redis 写入带 TTL 的取消标记。流式生成器在每个上游事件间检查标记，发现后关闭 HTTPX 上游流并保存取消状态。这样取消不依赖请求落到同一个 Worker。

浏览器 AbortController 断开也会关闭上游响应。关闭上游连接是尽力取消，不能承诺 Provider 已经停止计费，界面不作此承诺。

## 权限与审计

Model Catalog 使用：

- `ai:model:list`
- `ai:model:query`
- `ai:model:add`
- `ai:model:edit`
- `ai:model:remove`
- `ai:model:publish`
- `ai:model:test`

Engine Credential 使用：

- `ai:engine:credential:list`
- `ai:engine:credential:add`
- `ai:engine:credential:rotate`
- `ai:engine:credential:remove`

Engine Deployment 使用：

- `ai:engine:deployment:list`
- `ai:engine:deployment:query`
- `ai:engine:deployment:add`
- `ai:engine:deployment:edit`
- `ai:engine:deployment:remove`

运行状态使用 `ai:runtime:query`。调试对话继续使用 `ai:chat:list`。

连接测试可能产生真实请求，必须具备 `ai:model:test`，不能由只有查询权限的用户触发。

操作日志排除：

- Management Key 和 Runtime Key。
- 整个 Credential Values。
- 所有动态 Password/Token/API Key 字段。
- Prompt、System Prompt、图片正文、模型输出和 Reasoning。

审计只记录资源 ID、操作类型、用户、结果、Request ID、Trace ID 和脱敏错误码。

## 前端产品设计

采用已确认的现代运维工作台和调整后的字体体系。

### 导航

Model Service 下提供：

- 模型中心。
- 调试对话。

未来 Platform 导航预留租户与项目、应用与密钥、Usage 与审计，但 V0.1-A 不创建不可用的菜单项。

### 模型中心

同一工作区包含四个标签：

1. **平台模型**：默认视图，展示 Platform Model ID、Model Code、类型、Capability、Runtime Binding 和生命周期。
2. **引擎部署**：展示 LiteLLM 物理 Deployment、Provider、上游模型和健康状态。
3. **Provider 凭据**：只展示安全元数据和引用数。
4. **运行状态**：分别展示 Model Service、Runtime 和 Management 链路。

主操作为“接入模型”，使用分步抽屉完成 Credential、Deployment、Catalog 和 Binding。列表使用紧凑表格，详情和编辑使用右侧抽屉，不使用旧式居中大弹窗。

### 调试对话

- 模型选择器只显示已发布 Platform Model。
- 每个 Session 锁定一个 Platform Model。
- 展示 Content、Reasoning、Token Usage、首 Token 时间和总耗时。
- 提供停止生成、图片上传、历史数量和用户偏好。
- 历史加载、空状态、错误和中断状态使用 Platform 文案，不出现 Agno 或 LiteLLM 技术对象。

### 字体和视觉

- 中文正文使用 `system-ui`、`Noto Sans SC`、`HarmonyOS Sans SC`、`MiSans`、`PingFang SC` 等系统无衬线回退链。
- Model Code 和 Resource ID 使用系统等宽字体。
- 正文字号不低于 `12px`，表头不低于 `11px`，辅助信息不低于 `10px`。
- 字重主要使用 `400`、`500` 和 `600`，避免大面积粗体。
- 采用中性灰工作区和克制的绿、蓝、黄、红状态色，不使用单一紫色主题。
- 圆角不超过 `8px`，不嵌套装饰性卡片。
- 桌面优先横向扫描；移动端隐藏低优先级列、收纳操作并使用全宽抽屉。
- 动态状态、图标和文本不得改变固定控件尺寸或导致布局跳动。

## 故障与降级

- LiteLLM 不可用不能阻止 Platform 启动。
- Model Catalog 仍可读取本地业务主数据，但 Runtime 状态显示不可用。
- 引擎列表刷新失败时保留页面已有数据，标记“可能已过期”和最后成功时间。
- 首次加载 Engine 数据失败时显示错误状态，不能伪装为“暂无数据”。
- Management Key 缺失时禁用控制面写操作，但不影响 Model Catalog 查看。
- Runtime Key 缺失或失效时禁止调试对话并显示稳定 Platform 错误。
- 列表删除使用明确资源 ID 和二次确认。
- 被 Deployment 引用的 Credential 拒绝删除并返回引用数量。

## 数据库迁移策略

- 新增版本化迁移创建 `ai_model_catalog`、`ai_resource_binding`、`ai_chat_session`、`ai_chat_message`、`ai_chat_run` 和 `ai_chat_preferences`。
- 不修改 LiteLLM 自有 Schema。
- 不在本期删除 `ai_models` 和 `ai_chat_config`，也不创建 Agno `ai_sessions`。
- 新代码不读取或写入旧 `ai_models` 和旧 `ai_chat_config`。
- 新表 Migration 同时提供 PostgreSQL 和 MySQL 版本，自动测试二者结构契约。
- 外键、唯一约束和索引在数据库层实现；生命周期转换和 Binding 乐观锁在 Service 层再次校验。

## 部署配置

- `docker-compose.pg.yml` 向 Backend 注入 Runtime Key 和 Management Key。
- Frontend 不接收任何 LiteLLM Key。
- 初始化脚本从部署侧环境安全同步 Platform 所需凭据，不输出 Key，并保证目标 `.env` 权限为 `0600`。
- 受版本控制的环境模板只包含空值或说明。
- ZhiHeng 和 LiteLLM 保持独立 Compose 生命周期，通过 `litellm-network` 互通。
- 现有共享 PostgreSQL 实例继续使用独立数据库和账号，不跨库授予业务表权限。

## 测试策略

### Model Foundation

- Platform ID 前缀、唯一性和不可变性。
- Model 生命周期转换和发布后 `model_code` 不可修改。
- Resource Binding 唯一性、版本冲突和 Engine Resource 切换。
- Model Service 对不存在、停用和缺失 Binding 的处理。
- Controller 权限；确认 Model Catalog 不错误应用 `sys_dept` Data Scope。

### LiteLLM Adapter

使用 `httpx.MockTransport` 覆盖：

- Runtime Key 和 Management Key 严格分离。
- Authorization、Request ID、Trace ID 和 User-Agent。
- Provider、Credential、Deployment 的请求与安全响应映射。
- 普通 Chat Completion。
- SSE 任意分片、多个事件同块、`[DONE]` 和 Usage。
- Content、Reasoning、Cache 和 Token 指标归一化。
- 超时、断连、认证、限流和其他上游错误脱敏。
- 下游取消时上游响应必定关闭。

### Platform Chat

- Session 和 Run 归属校验。
- Session 模型锁定和同 Session 并发锁。
- `client_message_id` 幂等。
- 历史数量、System Prompt、Reasoning 排除和 Interrupted 消息排除。
- 图片路径穿越、MIME、大小和访问权限。
- 完成、取消、断连、失败和 Worker 遗留 Run 终态。
- 部分 Assistant 内容与 Usage 持久化。
- SSE 事件顺序、格式和错误终止。
- Prompt、图片和输出不进入日志。

### 前端

不新增前端测试框架。将查询转换、动态 Provider 表单、SSE Parser、消息状态和秘密字段清理拆为纯 JavaScript，加入现有 `npm run test:plugin`。

执行：

- `npm run test:plugin`
- `npm run build:docker`

在真实 Platform 上检查桌面和移动视口：

- Platform Model 列表。
- Engine Deployment、Credential 和状态标签。
- 接入模型抽屉。
- 调试对话、Reasoning、Usage、取消和错误状态。
- 字体、文本溢出、遮挡和布局跳动。

### 真实无付费集成

通过 LiteLLM 模拟模型完成：

1. 创建临时 Credential。
2. 创建模拟 Deployment。
3. 创建并发布 Platform Model 和 Binding。
4. 发起流式调试对话并刷新会话历史。
5. 验证 Usage、取消和错误状态。
6. 删除 Platform 测试资源、Deployment 和 Credential。
7. 确认无残留测试数据。

自动验收不访问真实收费 Provider。真实 Provider 连接测试只由用户在 UI 中主动执行。

## 实现阶段

### 阶段 1：Model Foundation

- 新表、Repository、Service 和 VO。
- Model Catalog 与 Resource Binding 管理接口。
- `ModelRuntimeEngine` 和 LiteLLM Runtime Adapter。
- 后端单元与契约测试。

### 阶段 2：LiteLLM Control Plane

- Management 配置和 Adapter。
- Provider、Credential、Deployment 和 Status 接口。
- 模型中心四标签和接入模型流程。
- 无付费控制面集成验证。

### 阶段 3：LiteLLM Chat

- Platform Session、Message、Run 和 Preferences Service。
- 历史、多模态、SSE、Reasoning、Usage 和取消。
- 调试对话模型选择和前端流式协议迁移。
- 多 Worker、断连和幂等测试。

### 阶段 4：Agno 清理与端到端验收

- 删除活跃代码中的 Agno Agent、Agno Session 和 Provider Factory。
- 移除 AI 插件不再使用的 Agno 与 Provider SDK 依赖。
- 保留旧空表但不再注册旧模型 CRUD 菜单和接口。
- 完成真实容器、权限、故障降级、响应式 UI 和无付费闭环验证。

每个阶段独立通过测试、构建和真实启动验证后再进入下一阶段。

## 验收标准

V0.1-A 完成时必须满足：

1. 当前 ZhiHeng 仓库作为新 Platform，拥有独立 Model Catalog 和稳定 Platform Model ID。
2. Platform Model 通过 Resource Binding 解析到 LiteLLM 模型别名，不向产品层暴露 Deployment ID。
3. 用户可在模型中心管理 Credential、Deployment、Platform Model、Binding 和 Runtime 状态。
4. Provider 密钥创建后不回显，Key、Prompt、图片和响应正文不进入日志。
5. 调试对话只选择已发布 Platform Model，并通过 Model Service 和 LiteLLM 生成。
6. Platform 自己保存 Session、Message、Run、Engine 快照和原始 Usage。
7. 历史、多模态、Reasoning、Usage、取消、断连和部分响应行为通过测试。
8. 同一 Session 不并发生成，重复提交不产生重复用户消息。
9. LiteLLM 故障或配置缺失不阻止 Platform 主应用启动。
10. 活跃模型和聊天链路不导入或调用 Agno。
11. 前端插件测试、生产构建、后端相关测试和数据库结构测试全部通过。
12. ZhiHeng、PostgreSQL、Redis 和 LiteLLM 实际运行健康。
13. 无付费端到端闭环通过，且无测试资源残留。
14. 模型中心和调试对话在桌面与移动视口无明显溢出、遮挡或布局跳动。

## 后续阶段

V0.1-B 建设 Tenant、Project、Application、Developer API Key、Scope 和 Model Entitlement，并通过同一 Model Service 发布 `/v1/models` 和 `/v1/chat/completions`。

V0.1-C 建设 Quota、RPM/TPM、Usage Ledger 和 Audit 闭环。LiteLLM Raw Token/Cost 作为 Engine 事实输入，Platform Usage 和 Billing 保持自己的业务口径。
