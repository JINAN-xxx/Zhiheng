# RuoYi LiteLLM 模型网关设计

> **已被替代：** 本规格错误地将 LiteLLM 视为模型业务主数据源，并将旧 Agno 聊天排除在本期迁移之外。请勿据此实施。有效规格为 [2026-08-11-model-service-v01a-design.md](./2026-08-11-model-service-v01a-design.md)。

## 背景

LiteLLM Proxy、PostgreSQL 和 Redis 已经完成独立部署，RuoYi Backend 也已通过内部 Docker 网络使用 Runtime Key 访问 LiteLLM。当前 RuoYi 的“模型管理”页面仍然管理 `ai_models` 业务表，并由 Agno 对话链路直接读取 Provider API Key；它不是 LiteLLM 的控制面。

本规格定义下一阶段改造：将 RuoYi 作为唯一的模型管理界面，在其中管理 LiteLLM Provider 凭据、模型部署和运行状态。同时保留现有 Agno 对话链路，避免在一个阶段内同时改造控制面和推理链路。

## 目标

- 将现有“模型管理”菜单升级为现代化的“模型网关”运维工作台。
- 在 RuoYi 中完成 LiteLLM 模型部署的查询、新增、编辑、删除和连接测试。
- 在 RuoYi 中完成 Provider 凭据的查询、创建、轮换和删除。
- 使用 LiteLLM 数据库作为部署和凭据的唯一数据源，不在 RuoYi 建立镜像表。
- 从 LiteLLM 动态获取 Provider 及其凭据字段，避免维护易过期的硬编码 Provider 列表。
- 将管理凭据限制在 RuoYi Backend，任何管理密钥和 Provider 密钥都不得返回浏览器。
- 保持现有 RuoYi 权限、操作日志、插件加载和页面导航体系。
- LiteLLM 管理能力未配置或暂时不可用时，RuoYi 主应用仍能正常启动和使用其他模块。

## 非目标

本阶段不负责：

- 将 `/ai/chat` 或 Agno 会话迁移到 LiteLLM。
- 删除 `ai_models`、`ai_chat_config`、Agno 或现有 Provider SDK。
- Tenant、Project、Application、Customer API Key 或 BYOK 管理。
- Quota、Usage Ledger、Pricing、Billing 或用量分析。
- Router 策略、回退链、负载权重和复杂路由的可视化编辑。
- 修改 LiteLLM 自身的管理界面。
- 向真实 Provider 执行自动付费验收请求。

## 已选方案

采用 RuoYi 后端代理 LiteLLM 管理 API 的方案：

```text
RuoYi 模型网关页面
        |
        | RuoYi 登录态与 ai:* 权限
        v
RuoYi /ai/gateway/*
        |
        | Backend-only Management Key
        v
LiteLLM 管理 API
        |
        v
LiteLLM PostgreSQL
```

不采用以下方案：

- 不在 RuoYi 建立第二套 Provider、Credential 和 Deployment 表并做双向同步，因为这会引入一致性、迁移和冲突处理问题。
- 不嵌入或反向代理 LiteLLM 原生管理页面，因为它无法统一 RuoYi 的登录、权限、导航和视觉体验。

## 数据所有权与兼容边界

### LiteLLM 数据

以下数据只由 LiteLLM 持久化：

- Provider Credential。
- Model Deployment。
- Deployment ID、模型别名、上游模型和运行参数。
- LiteLLM 健康检查和部署状态。

RuoYi 只做经过白名单规范化的管理 API 代理，不持久化上述数据，也不缓存 Provider 密钥。

### 旧 AI 插件数据

`ai_models` 和现有 `/ai/model` 接口在本阶段保留，继续供 Agno 聊天模块使用。前端“模型网关”页面不再调用这些旧接口。

菜单路由保持 `plugin/ai/model/index`，菜单名称从“模型管理”改为“模型网关”。这样无需新增并列页面，也不会让用户看到两套模型配置入口。第二阶段迁移聊天链路后，再用独立规格删除旧数据模型和 Agno 依赖。

## 认证与配置

### 双凭据边界

RuoYi Backend 同时持有两类不同用途的 LiteLLM 凭据：

| 配置 | 用途 | 允许的操作 |
|---|---|---|
| `LITELLM_RUNTIME_KEY` | 数据面 | 模型列表、Chat、Embedding |
| `LITELLM_MANAGEMENT_KEY` | 控制面 | Provider、Credential、Deployment 管理 |

本机 LiteLLM `1.95.0` 已验证：现有 Runtime Key 访问 `/v2/model/info` 和 `/credentials` 均返回 `403`，当前部署的管理凭据必须具备 Proxy Admin 权限。首期 `LITELLM_MANAGEMENT_KEY` 使用现有 Master Key 的值，但代码只依赖“管理凭据”语义。未来可以在 LiteLLM 支持满足这些接口的受限服务凭据后直接替换环境变量，不改变业务接口。

安全约束：

- Management Key 只存在于未提交且权限为 `0600` 的运行环境文件中。
- Docker Compose 只向 RuoYi Backend 注入 Management Key，不向 Frontend 注入。
- 配置模板只保留空值或非敏感示例。
- Management Key、Runtime Key 和 Provider Credential 不写入 RuoYi 业务数据库。
- 请求方法不允许调用方覆盖 `Authorization`。
- 日志、异常和监控数据不得包含认证头、请求密钥或 LiteLLM 原始错误正文。

### 配置降级

`LiteLLMSettings` 增加独立的 `management_key: SecretStr` 和 `management_configured` 属性。Management Key 缺失不会阻止 AI 插件或 RuoYi 启动：

- Runtime Key 正常时，现有 Runtime 诊断和后续推理能力不受影响。
- 模型网关可打开，但显示“管理能力未配置”，禁用所有管理写操作。
- 管理接口返回稳定、脱敏的 `LITELLM_MANAGEMENT_MISCONFIGURED` 错误。

## 后端组件

### `LiteLLMManagementClient`

新增独立的异步管理客户端，职责包括：

- 自动注入 Management Key、Request ID、Trace ID 和稳定 User-Agent。
- 调用 Provider 元数据、Credential、Deployment 和连接测试接口。
- 对网络、超时、认证、校验和上游错误做稳定映射。
- 只接受明确的请求模型，不提供任意 URL、Header 或原始 JSON 透传入口。
- 关闭时释放 HTTPX 连接池。

Runtime Client 与 Management Client 保持独立类和独立认证头。AI 插件启动 Hook 为每个 FastAPI Worker 创建两个客户端并保存在 `app.state`；关闭 Hook 幂等释放两者。启动时不主动调用 LiteLLM，避免外部故障阻塞 RuoYi 启动。

### `AiGatewayService`

Controller 不直接拼装 LiteLLM Payload。新增服务层负责：

- 将 RuoYi 查询参数转换为 LiteLLM 分页和筛选参数。
- 将 LiteLLM 响应转换为前端安全 VO。
- 根据 Provider 元数据校验动态凭据字段。
- 将受支持的部署参数映射到 LiteLLM `Deployment` 和 `updateDeployment`。
- 执行凭据引用检查和删除冲突判断。
- 保证错误中不保留敏感请求或响应对象。

## RuoYi 管理接口

所有接口使用 `PreAuthDependency` 和对应的 `UserInterfaceAuthDependency`，并返回 RuoYi 统一响应结构。

### Provider

`GET /ai/gateway/providers`

代理 LiteLLM `/public/providers/fields`，返回经过白名单过滤的 Provider 元数据：

- `provider`
- `displayName`
- `litellmProvider`
- `defaultModelPlaceholder`
- `credentialFields[]` 中的 `key`、`label`、`placeholder`、`tooltip`、`required`、`fieldType`、`options` 和 `defaultValue`

接口不把 LiteLLM 任意附加字段直接传给浏览器。Provider 元数据获取失败时页面仍可显示已有部署，但不能创建新凭据或部署。

### Deployment

| RuoYi 接口 | LiteLLM 接口 | 用途 |
|---|---|---|
| `GET /ai/gateway/deployments` | `GET /v2/model/info` | 分页、搜索和筛选 |
| `GET /ai/gateway/deployments/{id}` | `GET /v2/model/info?modelId=...` | 安全详情 |
| `POST /ai/gateway/deployments` | `POST /model/new` | 新增部署 |
| `PATCH /ai/gateway/deployments/{id}` | `PATCH /model/{id}/update` | 局部更新 |
| `DELETE /ai/gateway/deployments/{id}` | `POST /model/delete` | 删除部署 |
| `POST /ai/gateway/test-connection` | `POST /health/test_connection` | 保存前连接测试 |

部署响应只返回：

- `deploymentId`
- `modelAlias`
- `provider`
- `upstreamModel`
- `credentialName`
- `status`
- `latencyMs`
- `blocked`
- `createdAt`
- `updatedAt`
- 首期支持的超时、重试和速率限制值

`api_key`、解密后的 `api_base`、认证对象和 LiteLLM 任意扩展字段不得返回。`api_base` 只在新建表单输入和连接测试中使用；如需长期复用，应归入 Credential 而不是 Deployment 响应。

部署写入模型不接受任意嵌套 JSON。首期允许：

- 模型别名和上游模型。
- Provider 和凭据名称。
- `timeout`、`stream_timeout`、`max_retries`。
- `rpm`、`tpm`。
- 备注或描述等非敏感 `model_info` 字段。

LiteLLM 允许多个 Deployment 使用同一个模型别名，以支持负载均衡。RuoYi 使用 `deploymentId` 作为编辑、删除和列表行的唯一标识，不对模型别名施加唯一约束。

连接测试会真实访问 Provider。界面必须明确提示“可能产生一次极小调用费用”，只在用户主动点击后执行，不在页面加载、表单输入或保存后自动执行。

### Credential

| RuoYi 接口 | 用途 |
|---|---|
| `GET /ai/gateway/credentials` | 返回安全元数据列表 |
| `POST /ai/gateway/credentials` | 创建凭据 |
| `PUT /ai/gateway/credentials/{name}/values` | 重新输入并轮换凭据值 |
| `DELETE /ai/gateway/credentials/{name}` | 删除未被引用的凭据 |

凭据列表只返回：

- `credentialName`
- `provider`
- `referencedDeploymentCount`
- `createdAt`
- `updatedAt`

不提供“查看密钥”能力。轮换时前端不会预填旧值，用户必须重新输入该 Provider 所需的凭据字段。空值不代表清除或保持不变；前端在提交前按动态字段要求校验，后端再次校验。

删除前由服务层检查部署引用。存在引用时返回 `409` 和引用数量，不调用 LiteLLM 删除接口。

### Status

`GET /ai/gateway/status` 聚合：

- Runtime 是否配置和是否可达。
- Management 是否配置和是否可用。
- LiteLLM 连接延迟与最后检查时间。
- 当前部署数量和凭据数量；依赖不可用时为 `null`，不伪装为零。

接口不返回内部数据库、Redis、容器地址或任何密钥。

## 权限和审计

为避免插件升级后现有角色失去模型管理权限，Deployment 继续使用已有权限：

- `ai:model:list`
- `ai:model:query`
- `ai:model:add`
- `ai:model:edit`
- `ai:model:remove`
- `ai:model:test`

新增 Credential 权限：

- `ai:credential:list`
- `ai:credential:add`
- `ai:credential:rotate`
- `ai:credential:remove`

运行状态继续使用 `ai:runtime:query`。连接测试可能触发一次真实 Provider 请求，因此使用独立的 `ai:model:test` 权限，不允许只有查询权限的用户触发。前端按钮和后端接口同时做权限控制，后端权限是最终边界。

Credential 创建、轮换和连接测试使用操作日志的排除模式，至少排除整个 `credential_values`、`api_key`、Token、Authorization 和动态 password 字段。日志只记录操作类型、凭据名称或 Deployment ID、执行用户、结果状态和 Trace ID。

## 前端信息架构

采用已确认的“A · 运维工作台”方向，保持 RuoYi 整体布局但重做页面内容。

### 页面头部

- 标题“模型网关”和一行简短上下文。
- LiteLLM 状态、最近延迟和刷新操作。
- 主操作“新增部署”。
- 不使用营销式 Hero、大号指标卡或装饰性背景。

### 视图标签

同一页面提供三个标签：

1. **模型部署**：默认视图，紧凑表格展示模型别名、Provider、上游模型、状态、延迟和操作。
2. **凭据**：展示凭据名称、Provider、引用数和更新时间，不显示秘密字段。
3. **运行状态**：展示 Runtime 与 Management 两条链路的配置和可达性。

标签切换不改变左侧导航层级。筛选状态保留在当前标签中，不跨标签混用。

### 新增和编辑 Deployment

使用右侧抽屉而不是居中大弹窗或独立跳转页：

1. **模型与路由**：选择 Provider、填写模型别名和上游模型。
2. **访问凭据**：选择已有 Credential，或在当前流程中先创建新 Credential。
3. **验证与高级设置**：用户主动执行连接测试；高级区域折叠展示超时、重试、RPM 和 TPM。

内联创建 Credential 与创建 Deployment 是可见的两步操作。Credential 创建成功后即独立持久化；若 Deployment 创建失败，Credential 保留供修正后重试，不执行跨 HTTP 接口的补偿删除。

编辑 Deployment 时不读取或显示 Provider 密钥，只允许切换 Credential 或修改公开路由参数。

### 视觉和响应式规则

- 使用 Element Plus 现有组件和图标库，不引入第二套 UI 组件库。
- 采用中性浅灰工作区、白色内容面和克制的绿、蓝、黄、红状态色，不使用单一紫色或深蓝主题。
- 圆角不超过 `8px`，不嵌套装饰性卡片。
- 桌面端优先保证表格横向比较效率；窄屏隐藏低优先级列并将操作收纳进菜单。
- 抽屉在桌面使用受约束宽度，在移动端占满可用宽度。
- 图标按钮提供 Tooltip；文本、按钮和固定格式控件使用稳定尺寸，加载或状态变化不得推动布局。

## 错误与一致性

后端使用稳定的内部错误模型：

| 场景 | HTTP 状态 | 前端行为 |
|---|---|---|
| Management Key 缺失 | `503` | 显示未配置并禁用写操作 |
| LiteLLM 不可达 | `503` | 保留已有数据，标记可能过期 |
| LiteLLM 超时 | `504` | 提示稍后重试，保留表单 |
| Management Key 被拒绝 | `502` | 显示内部管理凭据故障 |
| RuoYi 请求字段非法 | `400` 或 `422` | 定位到具体表单字段 |
| Credential 被引用 | `409` | 展示引用数量并阻止删除 |
| 上游 Provider 认证失败 | `502` | 显示脱敏的上游认证失败 |
| Provider 限流 | `502` | 显示上游限流，不透传原文 |

LiteLLM 的 `401/403` 表示 RuoYi 内部管理凭据故障，不能作为当前用户认证失败直接返回。Provider 原始响应、内部 URL、认证头和 Credential 不进入异常对象。

列表刷新失败时保留当前页面已有数据并显示“数据可能已过期”和最后成功刷新时间。首次加载失败时显示错误空状态，不把依赖故障展示为“暂无数据”。

删除操作必须二次确认。前端提交期间禁用重复提交，后端使用 LiteLLM Deployment ID 执行明确目标的修改和删除。

## 测试策略

### 后端单元和契约测试

使用 `pytest` 和 `httpx.MockTransport` 覆盖：

- Management Key 配置、缺失降级和 SecretStr 表现。
- Management Client 的路径、方法、认证头、Trace 头和超时。
- Runtime Key 与 Management Key 不混用。
- Provider 字段白名单和动态字段校验。
- Deployment 分页、详情、创建、局部更新、删除和同名别名。
- Credential 列表脱敏、创建、轮换和引用删除冲突。
- 连接测试的请求转换和脱敏错误映射。
- Controller 登录、权限和 RuoYi 统一响应结构。
- 操作日志排除所有动态秘密字段。
- 启动和关闭 Hook 的双客户端生命周期与幂等性。

### 前端测试

不新增前端测试框架。将查询参数、LiteLLM/RuoYi 展示模型转换、动态表单校验和秘密字段清理拆为纯 JavaScript，并加入现有 `npm run test:plugin` 测试入口。

执行：

- `npm run test:plugin`
- `npm run build:docker`

构建验证插件动态视图解析、组件导入和生产样式编译。

### 真实集成验证

使用无付费的 LiteLLM 模拟配置执行一次完整管理闭环：

1. 使用 RuoYi 登录态读取 Provider、Status、空或已有 Deployment 列表。
2. 创建临时测试 Credential，不使用真实 Provider Key。
3. 创建、查询、编辑并删除一个模拟 Deployment。
4. 删除临时 Credential。
5. 确认测试结束后 LiteLLM 中无残留测试数据。

真实 Provider 连接测试只由用户在界面主动执行，不作为自动验收步骤。

### 回归和视觉验证

- 回归现有 AI 插件、Runtime Client、Compose 和共享 PostgreSQL 测试。
- 确认旧 Agno 聊天页面仍能读取 `ai_models` 并保持现有行为。
- 实际启动 RuoYi 和 LiteLLM 后，在桌面与移动视口检查部署列表、空状态、错误状态、Credential 页和新增抽屉。
- 检查文本溢出、固定控件尺寸、抽屉遮挡、按钮权限和加载状态引起的布局变化。

## 实现阶段

### 阶段 1：管理面后端

- 增加 Management Key 配置和 `LiteLLMManagementClient`。
- 增加安全 VO、Service、Controller、权限和日志脱敏。
- 完成 MockTransport 契约测试和真实 LiteLLM 无付费 CRUD 验证。

### 阶段 2：模型网关前端

- 重构现有模型页面为工作台。
- 实现 Deployment、Credential 和 Status 三个标签。
- 实现新增/编辑抽屉、动态 Provider 字段和连接测试。
- 完成纯 JS 测试、生产构建和响应式检查。

### 阶段 3：部署与端到端验收

- 将 Management Key 只注入 Backend 运行环境。
- 更新部署初始化脚本和说明文档，不输出或提交密钥。
- 启动 RuoYi 与 LiteLLM，执行权限、CRUD、故障降级和 Agno 回归验证。

## 验收标准

首期完成时必须满足：

1. 用户只通过 RuoYi 的“模型网关”管理 LiteLLM Deployment 和 Credential。
2. LiteLLM 是管理数据的唯一数据源，RuoYi 不新增镜像业务表。
3. Runtime Key 与 Management Key 分离，浏览器无法获取两类 Key。
4. Provider Credential 创建后不再回显，列表、日志和异常中不存在秘密字段。
5. Deployment 可分页查询、新增、编辑、删除和主动测试连接。
6. Credential 可查询安全元数据、创建、轮换和在无引用时删除。
7. Management 配置缺失或 LiteLLM 故障不会阻止 RuoYi 主应用启动。
8. 工作台在桌面和移动视口无明显溢出、遮挡或布局跳动。
9. 所有新增后端测试、前端插件测试、生产构建和相关回归测试通过。
10. RuoYi 与 LiteLLM 实际启动后完成无付费 CRUD 闭环，且无测试数据残留。
11. 现有 Agno 聊天功能在本阶段保持可用。

## 后续衔接

下一阶段单独设计聊天迁移：让聊天模型选项读取 LiteLLM Runtime 模型列表，让 `/ai/chat` 通过 `LiteLLMRuntimeClient` 调用模型，迁移必要的会话能力，并在完成数据迁移和回归后删除 Agno 直连 Provider 的代码与旧 `ai_models` 管理接口。
