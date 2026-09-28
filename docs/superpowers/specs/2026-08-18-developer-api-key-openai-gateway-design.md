# AI Open Platform Developer API Key 与 OpenAI 兼容网关设计

## 文档状态

本规格定义 AI Open Platform V0.1-B 的产品和技术边界，承接
`2026-08-11-model-service-v01a-design.md` 中预留的 Application、Developer API Key、
Model Entitlement、Quota、Usage Ledger 和 Public OpenAPI 能力。

本规格已经完成方案确认，可作为后续实施计划的输入。实施前仍需将任务拆分为独立迁移、
后端、前端、限流、可靠性和真实环境验收步骤；本文本身不授权直接开启生产公开入口。

## 背景

当前平台已经具备：

- Platform Model Catalog、Resource Binding 和稳定的 Platform Model ID。
- Model Service、ModelRuntimeEngine 和 LiteLLM Runtime Adapter。
- LiteLLM Provider Credential、Deployment 和 Management 控制面。
- 平台调试聊天、Session、Message、Run、Usage 和流式取消能力。
- 一个仅供 ZhiHeng Backend 使用的 LiteLLM `platform-runtime` Key。

当前三类凭据的含义必须区分：

| 凭据 | 所有者 | 用途 | 是否允许发给外部调用方 |
|---|---|---|---|
| Provider Credential | LiteLLM 控制面 | LiteLLM 访问模型 Provider | 否 |
| Management Key | 平台运维 | 管理 LiteLLM Credential 和 Deployment | 否 |
| Platform Runtime Key | ZhiHeng Backend | Model Service 通过 LiteLLM 执行模型调用 | 否 |

目前不存在面向平台用户或应用的 Developer API Key。浏览器用户使用 ZhiHeng JWT 调用
`/ai/chat/*`，Backend 再使用共享 Runtime Key 访问 LiteLLM。共享 Runtime Key 不携带
Application 身份、模型授权、业务配额或最终计费语义，不能直接当作用户 Key。

## 已确认的产品选择

- Developer API Key 绑定 Application，不直接绑定登录用户。
- 首期按单租户运行，领域字段预留 `tenant_id` 和 `project_id`。
- 每把 Key 必须显式授权可调用的 Platform Model。
- 公开协议首期采用 OpenAI 兼容 `/v1/models` 和 `/v1/chat/completions`。
- 配额包含 RPM、TPM、并发数、月度预算和过期时间。
- 月度预算默认 80% 告警，100% 拒绝新请求。
- Developer API Key 由 Platform 生成和托管，不下放给 LiteLLM 生成。
- 公开请求继续复用现有 Model Service，不能直接代理到 LiteLLM。

## 目标

- 建立 Application 和 Developer API Key 的稳定领域模型。
- 支持 Key 一次性展示、轮换、撤销、过期和状态审计。
- 支持每把 Key 的显式 Platform Model 授权。
- 提供 OpenAI SDK 可直接使用的模型列表、普通 Chat Completion 和流式 Chat Completion。
- 在多 Worker 部署中原子执行 RPM、TPM、并发和预算限制。
- 建立不可变 Usage Ledger，并区分 Provider 原始成本和 Platform 结算成本。
- 在客户端断开、任务取消、Provider 错误和基础设施故障时保持配额与 Ledger 可恢复。
- 确保 Developer Key、Runtime Key、Management Key 和 Provider Secret 不进入日志、异常或前端持久化。
- 提供独立的开发者中心管理 Application、Key、授权、配额和调用统计。

## 非目标

首期不负责：

- 正式启用多 Tenant、多 Project 的完整组织管理。
- 将 Developer API Key 直接同步为 LiteLLM Virtual Key。
- 对外提供 `/v1/responses`、Embedding、Image、Audio、Realtime、Batch 或 Fine-tuning API。
- 首期支持 Tool Calling、结构化输出、图片输入或多模态响应。
- 向客户暴露 Provider Credential、LiteLLM Deployment 或 Binding Alias。
- 建立充值、发票、支付和完整财务账务系统。
- 允许调用方自行覆盖 Provider、Base URL、认证头或 LiteLLM 私有参数。
- 用本地进程锁替代 Redis 分布式限流。
- 在公开流式响应生命周期中持有数据库事务或数据库连接。

## 方案选择

### 采用：Platform 托管的不透明 Key

```text
External Client
       |
       | Authorization: Bearer sk-zh_<key_id>_<secret>
       v
Platform OpenAI-compatible API
       |
       +--> Developer Key Authentication
       +--> Application / Model Entitlement
       +--> Distributed Quota Reservation
       +--> Usage Ledger
       |
       v
Existing Model Service
       |
       v
ModelRuntimeEngine
       |
       | Internal platform-runtime Key
       v
LiteLLM
       |
       v
Provider
```

Platform 是 Application、Developer Key、Entitlement、Quota 和 Usage Ledger 的唯一所有者。
LiteLLM 只承担模型 Runtime 和 Provider 控制面职责。

### 未采用：直接发放 LiteLLM Virtual Key

该方案虽然能较快提供调用凭据，但会使 Application 身份、权限、预算和审计分散到 Platform
与 LiteLLM 两套系统，并允许外部流量绕过 Model Service。它还会把 Platform 的长期公共契约
绑定到当前 Runtime 实现，因此不采用。

### 未采用：自包含 JWT Key

自包含 Token 仍无法避免撤销、授权变更和预算检查时访问状态存储，并会在 Token 中暴露更多
业务结构。相较不透明随机 Key 没有足够收益，因此不采用。

## 领域模型

### Application

`ai_application` 表示机器调用方或产品应用，而不是登录会话。

| 字段 | 约束 | 含义 |
|---|---|---|
| `application_id` | 主键，`app_<uuid>` | 不可变 Application ID |
| `application_code` | 唯一，创建后不可修改 | 稳定业务标识 |
| `application_name` | 必填 | 展示名称 |
| `owner_user_id` | 必填 | 首期所有者 |
| `tenant_id` | 可空 | 后续 Tenant 边界预留 |
| `project_id` | 可空 | 后续 Project 边界预留 |
| `status` | `active`、`disabled` | Application 状态 |
| `version` | 正整数 | 乐观锁版本 |
| 审计字段 | 必填 | 创建、更新和停用信息 |

规则：

- Application 停用后，其下所有 Key 立即失效。
- 已创建过 Key 或 Usage 的 Application 不物理删除，只允许停用。
- `owner_user_id` 只表达首期所有权；后续成员与角色使用独立关联表扩展。
- `tenant_id` 和 `project_id` 不得用 `sys_dept` 临时代替。

### Developer API Key

`ai_api_key` 只保存验证所需摘要，不保存可恢复的完整 Key。

Key 格式：

```text
sk-zh_<key_id>_<32-byte-random-secret>
```

| 字段 | 约束 | 含义 |
|---|---|---|
| `api_key_id` | 主键，`key_<uuid>` | Key 资源 ID |
| `application_id` | 外键，RESTRICT | 所属 Application |
| `key_name` | 必填 | 环境或用途名称 |
| `key_prefix` | 唯一 | 定位记录的非秘密前缀 |
| `secret_digest` | 必填 | 完整 Key 的 HMAC-SHA256 摘要 |
| `digest_version` | 必填 | Pepper 版本 |
| `fingerprint` | 必填 | 仅用于界面识别的末尾指纹 |
| `status` | `active`、`retiring`、`revoked`、`expired` | 生命周期状态 |
| `expires_at` | 可空 | 到期时间 |
| `retire_at` | 可空 | 轮换宽限期结束时间 |
| `last_used_at` | 可空 | 最近成功鉴权时间 |
| `created_at` | 必填 | 创建时间 |
| `revoked_at` | 可空 | 撤销时间 |

完整 Key 只在创建或轮换事务成功后返回一次。列表、详情、日志和审计只显示类似
`sk-zh_ab12...9f3c` 的掩码。

摘要计算：

```text
secret_digest = HMAC-SHA256(pepper[digest_version], complete_api_key)
```

验证使用常量时间比较。Pepper 由 Secret Manager 或部署环境提供，数据库只保存版本号。

### Model Grant

`ai_api_key_model_grant` 表示一把 Key 对 Platform Model 的显式授权。

| 字段 | 约束 | 含义 |
|---|---|---|
| `api_key_id` | 联合唯一键 | Developer Key |
| `platform_model_id` | 联合唯一键 | Platform Model ID |
| `status` | `active`、`disabled` | 授权状态 |
| 审计字段 | 必填 | 授权操作信息 |

Grant 永远引用 Platform Model ID，不引用 LiteLLM Alias、Deployment ID 或 Provider 名称。
Binding 变化不要求重新签发 Key。只有 Grant、Key、Application 和 Platform Model 同时有效时
才能调用。

### Quota Policy

`ai_api_quota_policy` 保存 Key 当前生效的配额策略。

| 字段 | 约束 | 含义 |
|---|---|---|
| `api_key_id` | 唯一外键 | 对应 Key |
| `rpm_limit` | 可空正整数 | 每分钟请求数 |
| `tpm_limit` | 可空正整数 | 每分钟 Token 数 |
| `concurrency_limit` | 可空正整数 | 最大并发数 |
| `monthly_budget` | 可空非负 Decimal | UTC 自然月预算 |
| `warning_ratio` | `(0, 1]`，默认 `0.8` | 告警阈值 |
| `hard_limit_ratio` | `>= warning_ratio`，默认 `1.0` | 硬限制阈值 |
| `currency` | 必填 | 预算币种 |
| `version` | 正整数 | CAS 版本 |

空限制表示对应维度不限制，不得使用 `0` 同时表达禁用和不限制。

### Model Rate Card

仅依赖调用完成后的 LiteLLM `raw_cost` 无法实现请求前预算硬限制，因此增加
`ai_model_rate_card`。

| 字段 | 约束 | 含义 |
|---|---|---|
| `rate_card_id` | 主键 | Rate Card ID |
| `platform_model_id` | 外键 | Platform Model |
| `version` | 正整数 | 价格版本 |
| `input_price_per_token` | 非负 Decimal | 输入 Token 单价 |
| `output_price_per_token` | 非负 Decimal | 输出 Token 单价 |
| `cache_price_per_token` | 可空非负 Decimal | Cache Token 单价 |
| `currency` | 必填 | 结算币种 |
| `effective_from` | 必填 | 生效时间 |
| `effective_to` | 可空 | 结束时间 |
| `status` | `active`、`disabled` | 状态 |

面向 Public API 开放且启用预算限制的模型必须存在有效 Rate Card。每次请求固定使用调用开始时
的 Rate Card 快照，避免调用过程中价格变更影响结算。

### Usage Ledger

`ai_api_usage_ledger` 是外部 API 调用的不可变业务事实，不与网页调试聊天的 `AiChatRun`
混用。

| 字段 | 含义 |
|---|---|
| `request_id` | 全局唯一请求 ID |
| `application_id`、`api_key_id` | 调用身份快照 |
| `platform_model_id`、`model_code` | Platform Model 快照 |
| `binding_id`、`engine_type`、`engine_model_snapshot` | Runtime 路由快照 |
| `rate_card_id`、`rate_card_version` | 价格快照 |
| `input_tokens`、`output_tokens`、`cache_tokens`、`reasoning_tokens` | Usage |
| `raw_cost` | LiteLLM 或 Provider 报告的原始成本 |
| `billable_cost` | Platform Rate Card 计算结果 |
| `currency` | 结算币种 |
| `cost_estimated` | 是否采用保守预估 |
| `status` | `started`、`completed`、`failed`、`cancelled`、`abandoned` |
| `error_code` | 稳定脱敏错误码 |
| `upstream_request_id` | 可空上游请求标识 |
| `idempotency_key`、`request_hash` | 可空幂等信息 |
| `started_at`、`finished_at` | 时间信息 |

Ledger 原始记录不原地删除。后续对账差异使用调整记录表达，保留完整审计链。

### Quota Bucket

`ai_api_quota_bucket` 保存可重建的持久化聚合结果：

- Key 与 UTC 时间窗口。
- 请求、Token 和费用累计。
- 预算预留、已结算和调整金额。
- 最后对账时间与版本。

Redis 是在线原子决策层，PostgreSQL Ledger 和 Bucket 是最终事实层。Redis 状态必须能够从
Ledger 与 Bucket 重建。

## Key 生命周期

```text
active ---- revoke ----> revoked
   |
   +---- expires_at ----> expired
   |
   +---- rotate ----> retiring ---- retire_at ----> revoked
```

### 创建

1. 校验 Application、授权模型、过期时间和配额。
2. 使用操作系统密码学安全随机源生成 32 字节 Secret。
3. 计算 HMAC 摘要，只将摘要与指纹写入数据库。
4. 在同一事务写入 Key、Grant、Quota 和审计资源信息。
5. 事务提交成功后只返回一次完整 Key。
6. 操作日志的请求和响应白名单排除完整 Key。

### 轮换

- 创建一把新的独立 Key，不修改旧 Secret。
- 默认旧 Key 进入 `retiring`，宽限期默认 24 小时，可由操作者缩短或立即撤销。
- 新旧 Key 在宽限期内共享 Application，但各自拥有独立 Grant、Quota、Usage 和指纹。
- 调用方完成切换后可立即撤销旧 Key。
- 不支持查看旧 Key 或原地重新生成 Secret。

### 撤销与过期

- 撤销操作幂等。
- 撤销后更新认证缓存版本并发布失效通知。
- 所有节点最长在认证缓存 TTL 内停止接受旧 Key，目标上限为 15 秒。
- 定时任务将超过 `expires_at` 或 `retire_at` 的记录归一化为终态。

## 请求鉴权与授权

外部请求使用：

```http
Authorization: Bearer sk-zh_<key_id>_<secret>
```

鉴权顺序：

1. 限制 Header 总长度并严格解析单个 Bearer Token。
2. 校验 Key 字符集和结构，不接受查询参数、Cookie 或自定义 Header 中的 Key。
3. 使用 `key_id` 定位记录，并用常量时间比较 HMAC 摘要。
4. 检查 Key 状态、到期时间和轮换宽限期。
5. 检查 Application 状态。
6. 将请求 `model` 解析为发布后不可变的 Platform `model_code`。
7. 检查 Key 对 Platform Model ID 的 active Grant。
8. 检查 Platform Model 与 Binding 当前可用状态。
9. 进入分布式配额预留。

认证失败使用固定文案，不暴露 Key 是否存在、所属 Application、具体失败字段或内部资源。
失败鉴权按来源 IP 和 Key ID 前缀额外限速，抑制枚举和暴力破解。

## 管理 API

管理 API 使用 ZhiHeng 登录 JWT、PreAuth、RBAC 和操作审计，不接受 Developer API Key。

```text
GET    /ai/applications
POST   /ai/applications
GET    /ai/applications/{applicationId}
PUT    /ai/applications/{applicationId}
POST   /ai/applications/{applicationId}/disable

GET    /ai/applications/{applicationId}/keys
POST   /ai/applications/{applicationId}/keys
POST   /ai/applications/{applicationId}/keys/{keyId}/rotate
POST   /ai/applications/{applicationId}/keys/{keyId}/revoke

GET    /ai/applications/{applicationId}/usage
GET    /ai/applications/{applicationId}/usage/requests
```

创建 Key 请求示例：

```json
{
  "keyName": "production",
  "modelIds": ["mdl_0123456789abcdef0123456789abcdef"],
  "expiresAt": "2027-08-18T00:00:00Z",
  "quota": {
    "rpmLimit": 60,
    "tpmLimit": 100000,
    "concurrencyLimit": 10,
    "monthlyBudget": "100.00",
    "warningRatio": "0.80",
    "hardLimitRatio": "1.00",
    "currency": "USD"
  }
}
```

创建响应只在本次返回完整 Key：

```json
{
  "code": 200,
  "data": {
    "keyId": "key_0123456789abcdef0123456789abcdef",
    "apiKey": "sk-zh_key_0123456789abcdef0123456789abcdef_<secret>",
    "fingerprint": "9f3c"
  }
}
```

建议新增权限：

```text
ai:application:list
ai:application:query
ai:application:add
ai:application:edit
ai:application:disable
ai:application:key:list
ai:application:key:add
ai:application:key:rotate
ai:application:key:revoke
ai:application:usage:query
```

首期只有 Application owner 和具备对应管理权限的运营管理员可以管理资源。后续成员授权不得复用
模型 Grant 表。

## OpenAI 兼容 Public API

Public API 不使用 ZhiHeng JWT，也不经过会将认证错误转换为 HTTP 200 的后台异常包装。它必须保留
真实 HTTP 状态码和 OpenAI 兼容错误结构。

### `GET /v1/models`

只返回当前 Key 已授权、已发布且 active 的模型：

```json
{
  "object": "list",
  "data": [
    {
      "id": "deepseek-chat",
      "object": "model",
      "created": 1786982400,
      "owned_by": "zhiheng"
    }
  ]
}
```

对外 `id` 使用发布后不可变的 `model_code`，不返回 Platform Model ID、Binding Alias、Deployment
ID 或 Provider 名称。

### `POST /v1/chat/completions`

首期支持：

- `model`
- `messages`
- `stream`
- `temperature`
- `top_p`
- `max_tokens`
- `presence_penalty`
- `frequency_penalty`
- `stop`
- `user`

不支持字段返回 `400`，不能静默忽略或原样透传到 LiteLLM。调用方不能传入 Provider、Base URL、
认证、Runtime 私有字段或任意 LiteLLM 参数。

普通响应使用 `chat.completion` 结构。流式响应使用 `text/event-stream`、
`chat.completion.chunk`，以 `data: [DONE]` 结束。客户端断开必须关闭 Model Service 单次流并执行
Usage 结算。

### 错误结构

```json
{
  "error": {
    "message": "The API key is invalid or inactive.",
    "type": "authentication_error",
    "param": null,
    "code": "invalid_api_key"
  }
}
```

| 场景 | HTTP 状态 |
|---|---|
| Key 无效、撤销或过期 | `401` |
| Application 停用或模型未授权 | `403` |
| 模型不存在 | `404` |
| 请求字段或参数非法 | `400` |
| 幂等 Key 与请求体冲突 | `409` |
| RPM、TPM、并发或预算超限 | `429` |
| Provider 错误 | `502` |
| Runtime、Redis 或必要依赖不可用 | `503` |

错误详情不得包含 Key、Grant、预算余额、Binding、Provider URL 或上游响应正文。

## 分布式配额设计

### 请求前预留

Redis Lua 脚本在一个原子操作内：

1. 增加当前 UTC 分钟 RPM。
2. 估算 Prompt Token。
3. 按 `estimated_prompt_tokens + requested_max_tokens` 预留 TPM。
4. 获取带 TTL 的并发租约。
5. 按 Rate Card 预留本次最大可能费用。
6. 检查 UTC 月度累计是否超过硬限制。

任一检查失败时不保留部分修改。请求没有显式 `max_tokens` 时，使用 Platform Model 的受控默认值，
不得以无限输出参与执行。

在线 Redis Key 只能包含内部 Application ID、API Key ID、Request ID 和时间窗口，不能包含完整
Developer Key。

### 请求后结算

调用结束后：

1. 取得 Runtime 的实际 Usage。
2. 按调用开始时 Rate Card 快照计算 `billable_cost`。
3. 释放未使用的 TPM 和费用预留。
4. 完成 Usage Ledger。
5. 更新持久化 Bucket。
6. 释放并发租约。

若 Provider 已开始执行但最终无法取得 Usage，则保守保留预估 Token 和费用，标记
`cost_estimated=true`，由后台 Reconciler 后续生成调整记录。不能因为客户端主动断开而直接免除
全部费用。

### 流式租约

每个并发租约包含 Request ID、Key ID、获取时间、TTL 和心跳时间。SSE 期间定时续租；正常完成、
异常、取消和客户端断开均在 `finally` 中释放。Worker 崩溃后由 TTL 回收。

### 故障策略

- Redis 不可用时 Public API 失败关闭并返回 `503`，不能绕过配额。
- Ledger `started` 记录写入失败时不调用 Provider。
- Provider 完成后 Ledger 更新失败时保留预留，进入可靠重试并告警。
- Reconciler 定期处理超时的 `started` Ledger、孤立租约和未完成结算。
- 不在 Redis、数据库和 Provider 网络调用之间维持分布式事务。
- 不使用单进程锁模拟多 Worker 的配额一致性。

## 幂等

非流式 Chat Completion 接受：

```http
Idempotency-Key: caller-generated-id
```

唯一范围为 `application_id + idempotency_key`。首次请求保存规范化请求哈希：

- 相同 Key、相同请求体且已完成：返回已保存结果，不重复调用和扣费。
- 相同 Key、不同请求体：返回 `409`。
- 首次请求仍在执行：返回稳定冲突或等待策略，不启动第二次 Provider 调用。

流式请求首期不支持响应重放，但 Request ID 和 Ledger 唯一约束必须阻止重复结算。

## 后端组件边界

建议新增：

```text
plugins/ai/
|-- controller/
|   |-- ai_application_controller.py
|   `-- openai_compat_controller.py
|-- service/
|   |-- application_service.py
|   |-- developer_key_service.py
|   |-- api_entitlement_service.py
|   |-- api_quota_service.py
|   `-- usage_ledger_service.py
|-- infrastructure/
|   |-- developer_key_authenticator.py
|   |-- redis_quota_store.py
|   `-- usage_reconciler.py
`-- domain/
    |-- application.py
    |-- developer_api_key.py
    |-- quota.py
    `-- usage.py
```

职责约束：

- `openai_compat_controller` 只负责 OpenAI 协议解析和响应转换。
- Authenticator 只负责 Developer Key 验证和 Application 身份建立。
- Entitlement Service 只使用 Platform Model ID 判定授权。
- Quota Service 负责预留、续租和结算，不调用 Runtime。
- Usage Ledger Service 不读取或保存完整 Key。
- Model Service 仍是唯一稳定模型调用入口。
- Public API 模块不得直接读取 LiteLLM Runtime Client、Management Client 或 Provider Credential。
- 每次数据库操作使用短生命周期会话，调用 Runtime 前结束事务。
- SSE 结果只持有 Request ID 和配额租约，不持有数据库会话。

## 安全和审计

硬性安全约束：

- Developer Key 只允许出现在入站 `Authorization` Header 和创建成功的一次性响应。
- Developer Key 不进入 URL、查询参数、Cookie、日志、异常、Trace、Metric Label 或审计正文。
- Developer Key 不转发给 LiteLLM；LiteLLM 仅接收内部 Runtime Key。
- Authorization、Provider Secret、Management Key 和 Runtime Key 使用统一脱敏边界。
- 对异常对象检查 cause、context、args、vars 和所有生产 traceback frame locals。
- Public API 不记录动态请求体；只记录 Request ID、Application ID、Key ID、Model ID、结果和稳定错误码。
- 请求体有固定大小上限；错误响应正文有固定读取上限或零读取策略。
- CORS 默认不开放通配来源；该接口主要面向服务到服务调用。

审计事件：

- Application 创建、修改和停用。
- Key 创建、轮换、撤销和过期。
- 模型 Grant 变化。
- Quota 和 Rate Card 变化。
- 预算告警与硬阻断。
- 管理员查询 Usage。
- 连续鉴权失败和异常流量。

审计仅记录资源 ID、操作者、操作类型、结果、稳定错误码、Request ID 和 Trace ID。

## 开发者中心

新增独立“开发者中心”，不嵌入模型中心：

```text
Developer Center
|-- Applications
|-- API Keys
|-- Model Grants and Quotas
`-- Usage
```

交互要求：

- 创建 Key 后使用一次性 Secret 对话框。
- Secret 默认遮罩，通过明确动作显示或复制。
- 用户确认已保存后才能关闭。
- 关闭或刷新后不能从浏览器存储恢复 Secret。
- 列表只显示掩码、指纹、状态、过期时间、授权模型、配额和最近使用时间。
- 轮换明确显示新旧 Key 的宽限期和撤销动作。
- 预算显示 80% 告警与 100% 阻断状态。
- Management Key 或 Runtime 状态不得展示给 Developer Key 使用者。

## 配置与部署

新增配置：

```text
AI_PUBLIC_API_ENABLED=false
AI_API_KEY_PEPPER_CURRENT_VERSION=v1
AI_API_KEY_PEPPER_V1=<secret-manager-value>
AI_API_QUOTA_REDIS_PREFIX=zhiheng:api-quota
AI_API_AUTH_CACHE_TTL_SECONDS=15
AI_API_MAX_REQUEST_BYTES=1048576
```

约束：

- 生产 Pepper 来自 Secret Manager 或未跟踪部署环境，不写入模板、数据库或前端。
- `digest_version` 对应的旧 Pepper 在所有旧 Key 撤销前必须保持可用。
- Public API Feature Flag 默认关闭。
- Feature Flag 关闭 Public API 时不得影响后台管理和平台内部聊天。
- 反向代理必须关闭响应缓冲以支持 SSE，并设置请求体、Header 和连接超时边界。
- 认证缓存只保存内部 ID、摘要验证结果和版本，TTL 不超过 15 秒；撤销同时发布失效通知。

## 可观测性

Metric Label 不得包含完整 Key、User Prompt、Application Name 或动态 Model Alias。建议指标：

- Public API 请求数、延迟、状态码和流式连接数。
- 按稳定内部 Model ID 聚合的 Token 和成本。
- 各配额维度拒绝次数。
- 预算 80% 告警和 100% 阻断次数。
- Ledger finalize/reconcile 延迟和失败数。
- Redis 配额脚本错误、租约过期和孤立状态数。
- Key 鉴权失败率和缓存命中率。

日志通过 Request ID、Ledger Request ID 和上游 Request ID 关联，但不保存完整认证信息或上游正文。

## 实施阶段

### 阶段 1：领域和数据基础

- 新增 Application、Developer Key、Grant、Quota、Rate Card、Ledger 和 Bucket 迁移。
- 建立 Entity、DAO、VO、Service 和稳定领域错误。
- 实现 Key 生成、一次性展示、轮换、撤销和 Pepper 版本验证。
- 增加 RBAC、操作审计和后端契约测试。

### 阶段 2：Public API 基础

- 建立独立 Public API 错误处理与 Developer Key 认证依赖。
- 实现 `/v1/models`。
- 实现非流式 `/v1/chat/completions`。
- 实现严格参数白名单、请求体限制、Model Grant 和 Model Service 集成。

### 阶段 3：配额、Ledger 和流式

- 实现 Redis Lua 原子预留。
- 实现 Rate Card 预算预检和结算。
- 实现 SSE、并发租约续期、取消和断连清理。
- 实现 Idempotency、Reconciler 和告警。

### 阶段 4：开发者中心

- 实现 Application、Key、Grant、Quota 和 Usage 页面。
- 实现一次性 Secret 交互和轮换流程。
- 实现响应式布局、空态、错误态、失效态和权限边界。

### 阶段 5：真实环境验收与灰度

- PostgreSQL、Redis 和真实 LiteLLM 容器联合验证。
- 使用标准 OpenAI SDK 验证普通和流式调用。
- 验证跨 Application 和跨 Key 隔离。
- 验证多 Worker 并发下 RPM、TPM、并发和预算原子性。
- 验证取消、断流、Provider 超时、Redis/DB 故障和 Worker 崩溃恢复。
- 验证日志、异常栈、审计、Trace 和 Metric 中无 Secret。
- 对账 Ledger、Runtime Usage 和 Provider 账单。
- 完成灰度后再开启 `AI_PUBLIC_API_ENABLED`。

## 测试策略

### 单元测试

- Key 格式、随机性、摘要、常量时间比较和 Pepper 版本。
- Application、Key、Grant、Quota 和 Rate Card 生命周期。
- OpenAI 请求白名单与错误转换。
- Token 与费用预估、预留和结算算法。
- 异常 cause/context/traceback locals 脱敏。

### 集成测试

- 真实 PostgreSQL 事务、CAS、并发撤销和 Grant 变化。
- 真实 Redis Lua 脚本的竞争、TTL、续租和原子失败。
- MockTransport 下 Model Service、Runtime、普通和流式协议闭环。
- 客户端断开、CancelledError、Provider 错误和双重关闭语义。
- Ledger finalize 失败、重试和 Reconciler 恢复。

### 兼容与安全测试

- OpenAI Python SDK 普通和流式调用。
- Key 不进入 URL、query、日志、sessionStorage、异常和所有生产 traceback locals。
- 无效 Key、枚举、超长 Header、重复 Authorization 和控制字符攻击。
- 跨 Application 使用 Model、Key、Idempotency Key 和 Usage ID 的隔离。
- 未授权字段、Provider 私有字段和认证覆盖全部 fail closed。

### 前端测试

- Key Secret 只显示一次且不持久化。
- RBAC、Application owner、禁用、过期、轮换和撤销状态。
- Grant 与 Quota 编辑的 CAS 冲突。
- 预算告警、硬阻断和 Usage 空态/错误态。
- 320、390 和桌面视口无敏感内容回填或布局溢出。

## 验收标准

只有同时满足以下条件才允许开启 Public API：

- 用户能够创建 Application 和一次性 Developer API Key。
- Key 只能调用显式授权的 active Platform Model。
- OpenAI SDK 可以调用普通和流式 Chat Completion。
- Key 撤销后最长 15 秒内在所有 Worker 失效。
- 多实例下 RPM、TPM、并发和预算检查保持原子。
- 预算 80% 产生告警，100% 拒绝新请求。
- 请求失败、取消和断流均产生可恢复的 Ledger 结果。
- Redis 故障不能绕过配额。
- 数据库事务不跨越 Provider 请求和 SSE 生命周期。
- Developer Key、Runtime Key、Management Key 和 Provider Secret 不出现在日志、异常栈、审计和 Trace。
- Public API Feature Flag 可以立即关闭公开入口且不影响平台内部聊天。

## 已知权衡与后续演进

- 首期单租户通过 `owner_user_id` 管理 Application；后续引入 Tenant、Project 和成员角色时保持
  Application ID、Key ID 和 Public API 不变。
- Prompt Token 预估和最大费用预留可能造成保守拒绝；这是硬预算正确性的代价，后续可按模型引入更精确 tokenizer。
- Provider 已执行但 Usage 丢失时采用保守预估结算，后续通过调整 Ledger 修正，不覆盖原事实。
- 认证缓存 TTL 在性能与撤销实时性之间取舍，当前上限 15 秒；高安全场景可配置为零缓存。
- 首期只支持 Chat Completions。Responses、Embedding 和多模态能力必须复用同一 Application、Key、Grant、Quota 和 Ledger 边界，不另建旁路。
