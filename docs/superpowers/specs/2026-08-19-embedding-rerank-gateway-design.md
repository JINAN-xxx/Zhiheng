# AI Open Platform Embedding 与 Rerank 统一网关设计

## 文档状态

本文是截至 `feat-litellm-deployment` 当前实现的 V0.2 增量设计基线，定义 Chat、Embedding、
Rerank 三类模型能力如何统一接入 Platform Model、Developer API Key、授权、配额、计费和审计。

本文增量替代以下旧约束：

- `2026-08-11-model-service-v01a-design.md` 中“公共 `/v1/embeddings` 不在本期”的限制。
- `2026-08-18-developer-api-key-openai-gateway-design.md` 中“首期只支持 Chat Completions”的限制。
- `2026-08-18-developer-public-api-foundation.md` 中将 Embedding、配额执行和 Usage Ledger
  结算留给后续里程碑的范围说明。

没有被本文明确替代的 Application、Developer API Key、Model Grant、安全、流式 Chat 和
资源生命周期规则继续有效。

本文是设计规格，不表示功能已经实现。完成实施计划、测试和真实环境验收前，
`AI_PUBLIC_API_ENABLED` 必须继续默认关闭。

## 当前真实基线

### 已经实现

- Platform Model Catalog 和 Resource Binding。
- `chat`、`embedding` 两种 Catalog 模型类型校验。
- Model Runtime、Model Service 和普通/流式 Chat 调用。
- LiteLLM Runtime Client 的 `/v1/embeddings` 底层 HTTP 方法。
- Application、Developer API Key、Model Grant、Quota Policy、Rate Card、Usage Ledger 和
  Quota Bucket 的表、Entity 与基础 DAO。
- `/v1/models` 和 `/v1/chat/completions` 的 Developer API Key 鉴权与 Chat 模型授权。
- LiteLLM 1.95.0 当前运行实例具备 `/v1/embeddings`、`/v1/rerank` 和 `/v2/rerank`。

### 尚未实现

- `ModelRuntimeEngine.embed()` 和 `ModelRuntimeEngine.rerank()`。
- LiteLLM Runtime Adapter 对 Embedding、Rerank 响应的领域归一化。
- Model Service 的 Embedding、Rerank 入口和模型类型隔离。
- 对外 `/v1/embeddings`、`/v1/rerank`。
- Rerank Catalog 类型、管理界面和类型化连接测试。
- Developer API 的 Redis 配额原子执行、Usage Ledger 完整写入、结算和恢复任务。
- Embedding、Rerank 的计费维度与用量展示。

因此，“已有 Quota/Ledger 表”不能表述为“公开 API 已有限流和计费”。在统一调用协调器完成前，
公开 API 不满足生产开放条件。

## 已确认决策

- 模型类型固定扩展为 `chat`、`embedding`、`rerank`。
- 对外提供 `/v1/chat/completions`、`/v1/embeddings`、`/v1/rerank`。
- 三类端点统一使用 Platform 生成的 Developer API Key。
- 三类端点统一经过 Application 状态、Key 状态、Model Grant、Quota、Rate Card 和 Usage Ledger。
- 三类端点统一复用 Model Service，不允许 Controller 直接代理 LiteLLM。
- Embedding 和 Rerank 首期只支持非流式响应。
- `/v1/embeddings` 采用 OpenAI 兼容结构；`/v1/rerank` 采用 LiteLLM/Cohere 风格的稳定子集。
- Rerank 的 Platform 结算按 Search Unit；Chat 和 Embedding 按 Token。
- Runtime 内部仍只使用共享 Platform Runtime Key，外部 Developer Key 永不下发给 LiteLLM。

## 目标

- 让同一 Application 和 Developer API Key 调用三类 Platform Model。
- 保持 Platform Model Code 为公开 API 唯一模型标识，不暴露 Binding Alias 和 Deployment ID。
- 在领域层提供类型化 Chat、Embedding、Rerank 请求与响应，而不是传递任意 JSON。
- 在模型类型不匹配时拒绝调用，避免把 Embedding 模型发送到 Chat 端点。
- 为三类请求执行统一的 RPM、TPM、并发和月度预算限制。
- 为三类请求写入统一且可恢复的 Usage Ledger。
- 对 Provider 错误、超时、取消、客户端断开和结算失败保持稳定语义。
- 在后台模型中心完成 Rerank 接入、类型化测试、授权和价格配置。

## 非目标

本期不负责：

- `/v1/responses`、Image、Audio、Realtime、Batch 或 Fine-tuning API。
- Embedding 的 Base64 编码输出或整数 Token ID 输入。
- Rerank 的 Provider 私有参数、混合检索、文档分块和多字段排序。
- 外部调用方覆盖 Provider、Base URL、Credential、认证头或 LiteLLM 私有参数。
- 将 Developer API Key 同步为 LiteLLM Virtual Key。
- 充值、支付、发票和正式财务账务。
- 对流式 Embedding 或流式 Rerank 建立非标准协议。
- 用单进程内存锁替代 Redis 分布式配额。

## 总体架构

```text
External Client
      |
      | Authorization: Bearer sk-zh_...
      v
Platform Public API
      |
      +--> Body Limit / Protocol Validation
      +--> Developer Key Authentication
      +--> Application / Model Grant / Model Type
      +--> Quota Reservation + Started Ledger
      |
      v
Public Invocation Coordinator
      |
      +--> ModelService.chat / stream_chat
      +--> ModelService.embed
      `--> ModelService.rerank
                    |
                    v
            ModelRuntimeEngine
                    |
                    v
          LiteLLM Runtime Adapter
                    |
                    | Platform Runtime Key
                    v
                 LiteLLM
                    |
                    v
                 Provider
```

边界规则：

- Public Controller 只处理协议、认证头、请求大小和 HTTP/SSE 响应。
- Public Invocation Coordinator 负责授权快照、配额预留、Ledger 和调用生命周期。
- Model Service 负责 Platform Model 解析、模型类型检查和 Runtime 请求构造。
- Runtime Adapter 负责 LiteLLM 协议与 Platform 领域对象之间的转换。
- LiteLLM Client 只负责有界 HTTP I/O，不包含业务授权和计费逻辑。
- Runtime 调用期间不得持有数据库事务或数据库连接。

## Platform Model 类型

### 类型集合

```python
ModelType = Literal['chat', 'embedding', 'rerank']
```

每个 Platform Model 只有一种类型。类型在创建后不可修改；需要改变类型时必须新建 Model Code，
避免既有 Grant、Rate Card 和调用方协议发生语义漂移。

### 类型化默认参数

`default_parameters` 继续存储 JSON，但必须按 `model_type` 使用互斥白名单验证：

| 类型 | 允许的默认参数 |
|---|---|
| `chat` | 现有 `temperature`、`top_p`、`max_tokens`、`presence_penalty`、`frequency_penalty` |
| `embedding` | `dimensions` |
| `rerank` | `top_n`、`return_documents` |

不得在默认参数中保存 `model`、Provider、Credential、Base URL、认证信息或未知字段。

### 类型化 Capability

Catalog Capability 只使用 Platform 明确定义的布尔字段：

- Chat：`streaming`、`vision`、`reasoning`、`tools`。
- Embedding：`dimensions_override`。
- Rerank：`return_documents`。

未知 Capability 拒绝写入，不透传 LiteLLM 的任意元数据。

## 公开 API 协议

### 通用规则

- Base URL 仍为 Platform `/v1`，不暴露 LiteLLM 地址。
- 认证固定为单个 `Authorization: Bearer <Developer API Key>`。
- 请求必须为 `application/json`，不接受重复 Authorization 或 Content-Length。
- 请求体硬上限继续使用 `AI_API_MAX_REQUEST_BYTES`，默认 1 MiB、最大 10 MiB。
- 字段采用 OpenAI/LiteLLM 兼容的 snake_case；未知字段一律拒绝。
- `model` 必须是已授权且 active 的 Platform Model Code。
- Model Code 存在但类型与端点不匹配时，统一返回 `model_not_found`，不泄露其他类型授权。
- 响应不包含 Binding ID、Runtime Alias、Deployment ID、Provider Credential 或原始上游正文。

### `GET /v1/models`

返回当前 Key 有权调用的全部 active Platform Model，包含 Chat、Embedding 和 Rerank。

保持现有 OpenAI 兼容形状：

```json
{
  "object": "list",
  "data": [
    {
      "id": "company-embedding-v1",
      "object": "model",
      "created": 1787068800,
      "owned_by": "platform"
    }
  ]
}
```

为保持 OpenAI SDK 兼容，不在该响应增加 Platform 私有 `model_type` 字段。调用方通过对应端点使用模型；
类型错误按固定 404 返回。后台管理 API 继续显式展示模型类型。

### `POST /v1/embeddings`

请求：

```json
{
  "model": "company-embedding-v1",
  "input": ["first document", "second document"],
  "encoding_format": "float",
  "dimensions": 1024,
  "user": "external-user-42"
}
```

首期字段：

| 字段 | 规则 |
|---|---|
| `model` | 必填，Platform Model Code，类型必须为 `embedding` |
| `input` | 必填，非空字符串或 1 到 256 个非空字符串 |
| `encoding_format` | 可空或固定为 `float`，拒绝 `base64` |
| `dimensions` | 可空，1 到 65536；仅 Capability 允许覆盖时接受 |
| `user` | 可空，1 到 128 个安全字符，只用于审计关联，不传认证信息 |

额外边界：

- 单个输入 UTF-8 编码后最多 32768 bytes。
- 所有输入仍受总请求体上限限制。
- 不接受整数 Token ID、嵌套数组、空字符串、NaN 或未知参数。

响应：

```json
{
  "object": "list",
  "data": [
    {
      "object": "embedding",
      "index": 0,
      "embedding": [0.0123, -0.0456]
    }
  ],
  "model": "company-embedding-v1",
  "usage": {
    "prompt_tokens": 8,
    "total_tokens": 8
  }
}
```

响应校验：

- `data` 数量必须与输入数量一致，`index` 唯一且覆盖 `0..n-1`。
- 每个向量必须是有限浮点数数组，禁止 NaN、Infinity 和字符串数值。
- 所有向量维度必须一致；指定 `dimensions` 时必须与结果一致。
- 响应 `model` 始终回写 Platform Model Code，不使用上游 Alias。
- Usage 缺失时允许成功，但 Ledger 使用保守预估并标记 `cost_estimated=true`。

### `POST /v1/rerank`

请求：

```json
{
  "model": "company-rerank-v1",
  "query": "Which document describes the refund policy?",
  "documents": [
    "Shipping usually takes three days.",
    {"text": "Refunds are accepted within thirty days."}
  ],
  "top_n": 2,
  "return_documents": true
}
```

首期字段：

| 字段 | 规则 |
|---|---|
| `model` | 必填，Platform Model Code，类型必须为 `rerank` |
| `query` | 必填，UTF-8 编码后 1 到 8192 bytes |
| `documents` | 必填，1 到 256 个字符串或严格 `{ "text": string }` |
| `top_n` | 可空，默认文档数；必须为 1 到文档数 |
| `return_documents` | 可空，默认 `false` |

单个文档文本 UTF-8 编码后最多 32768 bytes。对象文档除 `text` 外不接受其他字段；
首期不支持 `rank_fields`、`max_chunks_per_doc` 或 Provider 私有参数。

响应：

```json
{
  "id": "rrk_01H...",
  "results": [
    {
      "index": 1,
      "relevance_score": 0.9921,
      "document": {"text": "Refunds are accepted within thirty days."}
    }
  ],
  "meta": {
    "billed_units": {"search_units": 1},
    "tokens": {"input_tokens": 22}
  }
}
```

响应校验：

- `index` 必须唯一且位于原始文档范围内。
- `relevance_score` 必须是有限数值。
- Platform 按分数降序、相同分数按原索引升序稳定排序。
- `results` 数量不得超过 `top_n`。
- `return_documents=false` 时不返回 `document`。
- `return_documents=true` 时文档从已验证的请求快照重建，不信任上游回传正文。
- 上游 `id` 不合法或缺失时由 Platform 生成 `rrk_<uuid>`。
- Meta 只允许 `search_units` 和 `input_tokens`，未知上游元数据丢弃。

## 领域与 Runtime 契约

### 领域对象

新增不可变领域对象：

```text
ModelEmbeddingRequest
  engine_model
  inputs
  dimensions
  user

ModelEmbeddingResponse
  vectors[index, values]
  input_tokens
  raw_cost

ModelRerankRequest
  engine_model
  query
  documents
  top_n
  return_documents

ModelRerankResponse
  request_id
  results[index, relevance_score]
  input_tokens
  search_units
  raw_cost
```

所有集合在领域边界转为 tuple，响应对象使用 frozen dataclass，不保存原始 Provider payload。

### `ModelRuntimeEngine`

协议增加：

```python
async def embed(request: ModelEmbeddingRequest) -> ModelEmbeddingResponse: ...
async def rerank(request: ModelRerankRequest) -> ModelRerankResponse: ...
```

既有 `chat()`、`stream_chat()` 和 `aclose()` 语义不变。Embedding/Rerank 的取消信号必须原样传播；
关闭错误不得覆盖活动异常。

### LiteLLM Client 与 Adapter

- Client 增加类型中立的 `/v1/rerank` POST 方法；已有 `/v1/embeddings` 方法继续复用。
- Client 只接受 Adapter 构造的严格 payload。
- Adapter 只向 LiteLLM 发送 Runtime Alias 和业务输入，不发送 Platform Model ID 或 Developer Key。
- Adapter 对成功 JSON 设置上限，默认 `AI_RUNTIME_MAX_JSON_RESPONSE_BYTES=33554432`。
- 非 2xx 错误不依赖完整正文分类；正文读取保持既有 raw-byte 上限和压缩拒绝规则。
- 解析 helper 只返回规范化结果或固定失败分类；最终异常在不持有 Request、Response、URL、认证头、
  输入文档和 Provider payload 的 clean boundary 抛出。
- LiteLLM 1.95 Rerank 支持的 Provider 由 LiteLLM 自身决定，Platform 不硬编码 Provider 列表作为
  产品契约。

### Model Service

`ResolvedModel` 增加不可变 `model_type` 快照。新增：

```python
async def embed(model_id, inputs, parameters=None) -> ModelServiceEmbeddingResult: ...
async def rerank(model_id, query, documents, parameters=None) -> ModelServiceRerankResult: ...
```

规则：

- `chat/stream_chat` 只接受 `chat`。
- `embed` 只接受 `embedding`。
- `rerank` 只接受 `rerank`。
- Model、Binding 和默认参数解析完成后立即结束独占只读事务，再调用 Runtime。
- Runtime 执行期间 Session 必须满足 `in_transaction() == false`。
- 每次调用结果包含 Platform Model ID、Binding ID、Engine Type、Engine Model Snapshot 和
  类型化领域响应，供 Ledger 固化快照。

## Developer API 授权

现有公开授权查询移除 `model_type == 'chat'` 的硬编码过滤，改为返回 `model_type` 快照。

授权决策必须同时满足：

1. Application 为 `active`。
2. Developer API Key 为有效状态且未过期。
3. Model Grant 为 `active`。
4. Platform Model 为 `active`。
5. Resource Binding 为 `active`。
6. Model 类型与目标端点完全一致。
7. 存在当前有效 Rate Card。

`/v1/models` 可列出所有类型；具体调用在统一授权方法
`resolve_invocation(identity, model_code, expected_type)` 中验证类型。授权失败、模型停用、绑定失效和
类型不匹配均使用同一 `model_not_found` 外部错误，避免资源枚举。

## 统一配额、计费与 Usage Ledger

### 强制统一调用协调器

新增 `PublicInvocationCoordinator`，Chat、Embedding、Rerank 都必须经过同一生命周期：

1. 在短事务中完成 Key、Application、Grant、Model、Binding、Quota 和 Rate Card 快照读取。
2. 提取纯值快照，结束并释放数据库事务。
3. 使用 Redis Lua 原子预留 RPM、TPM、并发和预算。
4. 在独立短事务写入 `started` Ledger 并提交；失败则释放 Redis 预留且不调用 Runtime。
5. 在无数据库事务状态调用 Model Service/Runtime。
6. 根据规范化 Usage 和 Rate Card 计算实际结算量。
7. 在独立短事务将 Ledger 置为 `completed`、`failed`、`cancelled` 或 `abandoned`，更新 Bucket。
8. 原子结算或释放 Redis 租约。

若 Runtime 已完成但 Ledger 结算失败，保留租约并进入可靠重试；不得把请求当作未发生。
Reconciler 定期恢复超时 `started` 记录和孤立租约。

### 预留量

首期使用确定性保守上限，不依赖 Provider Tokenizer：

- Chat：输入文本 UTF-8 bytes + 允许的最大输出 Token。
- Embedding：所有输入文本 UTF-8 bytes，输出 Token 固定 0。
- Rerank：query 与 documents 文本 UTF-8 bytes，输出 Token 固定 0。

UTF-8 byte 数是保守预留单位；完成后使用上游规范化 Token Usage 结算。Usage 缺失时保留预估量，
并设置 `cost_estimated=true`，避免因为 Provider 没有返回 Usage 而免费放行。

### Rate Card

`ai_model_rate_card` 增加：

| 字段 | 规则 |
|---|---|
| `billing_mode` | `token` 或 `search_unit` |
| `search_price_per_unit` | `search_unit` 模式必填非负 Decimal，其他模式为空 |

规则：

- Chat、Embedding 使用 `token`。
- Rerank 使用 `search_unit`。
- Token 模式按 input/output/cache 单价结算；Embedding 的 output 为 0。
- Search Unit 模式按 `search_units * search_price_per_unit` 结算，不与 Token 单价重复收费。
- Rerank 上游未返回 `search_units` 时按每次请求 1 Search Unit 保守结算并标记估算。
- `raw_cost` 只记录 LiteLLM 提供的 Provider 成本，不能替代 Platform Rate Card。

### Usage Ledger

`ai_api_usage_ledger` 增加：

| 字段 | 规则 |
|---|---|
| `operation_type` | `chat`、`embedding`、`rerank`，必填 |
| `search_units` | 可空非负整数 |

现有行迁移时回填 `operation_type='chat'`。Ledger 不保存 Chat 内容、Embedding 输入、Rerank query、
documents、向量或完整 API Key。

状态语义：

- `started`：配额已预留，Runtime 尚未最终结算。
- `completed`：Runtime 成功且用量已结算。
- `failed`：Runtime 返回稳定失败，按实际或保守规则结算。
- `cancelled`：调用方取消或断开，保留已发生用量。
- `abandoned`：Reconciler 判定无法确认结果，采用保守结算并告警。

计费失败边界：

- 协议校验、鉴权、授权和配额拒绝发生在 Runtime 前，不创建调用 Ledger，不计模型费用，只写脱敏访问审计。
- `started` Ledger 创建后、Runtime 请求发出前发生平台内部失败，Ledger 记 `failed`、费用为 0，并完整释放预留。
- Runtime 已明确返回 Usage 时按实际用量结算，不因最终业务状态为失败或取消而丢弃已发生用量。
- Runtime 请求已经发出但结果不可确认时，Ledger 进入 `abandoned`，按预留量保守结算并标记
  `cost_estimated=true`；后续只能追加调整事实，不能覆盖原 Ledger。

## 数据库迁移

新增 PostgreSQL/MySQL `005_embedding_rerank_gateway.sql`，插件版本提升到 `0.6.0`。

迁移内容：

1. 为 `ai_model_catalog.model_type` 增加 `chat|embedding|rerank` 检查约束。
2. 为 `ai_model_rate_card` 增加 `billing_mode` 和 `search_price_per_unit`。
3. 回填既有 Rate Card 的 `billing_mode='token'`。
4. 为 `ai_api_usage_ledger` 增加 `operation_type` 和 `search_units`。
5. 回填既有 Ledger 的 `operation_type='chat'`。
6. 增加 operation/type、Ledger 查询所需索引。

迁移必须满足：

- PostgreSQL 和 MySQL 语义一致。
- 不删除既有数据，不重建 Key，不修改 Grant。
- 既有 Chat Model、Rate Card 和 Ledger 在迁移后保持有效。
- migration checksum、statement count 和 plugin version 正式登记。

## 错误协议

继续使用 OpenAI 风格错误体：

```json
{
  "error": {
    "message": "请求超过当前配额限制",
    "type": "rate_limit_error",
    "param": null,
    "code": "rate_limit_exceeded"
  }
}
```

固定映射：

| HTTP | code | 场景 |
|---|---|---|
| 400 | `invalid_request` | JSON、字段、范围或模型参数非法 |
| 401 | `invalid_api_key` | Key 缺失或无效 |
| 403 | `permission_denied` | Application/Key 明确无权执行该操作 |
| 404 | `model_not_found` | 模型不存在、未授权、未激活、绑定无效或类型不匹配 |
| 413 | `request_too_large` | 请求体或类型化输入超过硬上限 |
| 429 | `rate_limit_exceeded` | RPM 或 TPM 超限 |
| 429 | `concurrency_limit_exceeded` | 并发超限 |
| 429 | `budget_exceeded` | 月度预算达到硬限制 |
| 502 | `upstream_error` | LiteLLM/Provider 返回错误或畸形成功响应 |
| 503 | `service_unavailable` | Redis、数据库、Runtime 或配置不可用 |

错误 message 为固定中文公共文案，不回显 Model 原始输入、query、documents、向量、上游 URL、
Provider body 或任何 Key。错误链与生产 traceback frame locals 也必须满足相同脱敏要求。

## 安全与资源边界

- Developer API Key 只用于 Platform 鉴权，绝不进入 Runtime request。
- Platform Runtime Key 只存在于 Backend 到 LiteLLM 的 Authorization。
- Provider Credential 只由 LiteLLM 持有，不进入 Platform Catalog、Ledger 或公开响应。
- Embedding 输入和 Rerank 文档属于敏感业务内容，不写操作日志和异常摘要。
- 日志只允许 Request ID、Application ID、API Key ID、Platform Model ID、operation、状态、延迟和用量。
- 上游成功 JSON、向量总量和 Rerank 结果数量全部有界。
- 取消时优先传播 `CancelledError`，同时尽力关闭 response/stream、释放并发租约并记录 Ledger。
- Redis 配额不可用时 fail closed，不能降级为无限调用。
- Controller 不接受调用方提供的转发头、Provider 选择或目标 URL。

## 模型中心改造

### 接入流程

模型接入抽屉的类型选择扩展为：

- Chat
- Embedding
- Rerank

选择类型后：

- 只显示该类型允许的默认参数和 Capability。
- 创建 Platform Model 时固定写入类型，后续不可编辑。
- 发布前执行类型化 Runtime Smoke Test。

Smoke Test：

- Chat：现有最小消息调用。
- Embedding：固定非敏感文本 `platform connectivity test`，验证向量非空且有限。
- Rerank：固定 query 和两条非敏感文档，验证索引、分数和 top_n。

Smoke Test 可能产生 Provider 费用，继续使用显式确认；失败保留 Credential、Deployment、Draft Model
和 Binding，不做自动补偿删除。

### 列表与详情

- Platform Model 列表显示类型标签。
- Chat 显示上下文、输出上限和流式 Capability。
- Embedding 显示默认/可覆盖维度。
- Rerank 显示默认 top_n 和是否允许返回文档。
- Developer Application 授权选择器允许选择三类 Model，并显示类型。
- Rate Card 编辑器按 billing mode 切换 Token 单价或 Search Unit 单价。
- Usage 页面按 operation type 筛选，并分别显示 Token 或 Search Unit。

前端不得显示或缓存 Provider Credential、Management Key、Runtime Key 或完整 Developer API Key。

## 配置

新增配置使用明确默认值：

| 配置 | 默认值 | 说明 |
|---|---:|---|
| `AI_EMBEDDING_MAX_INPUTS` | `256` | 单次 Embedding 输入数量 |
| `AI_EMBEDDING_MAX_INPUT_BYTES` | `32768` | 单条输入 UTF-8 bytes |
| `AI_RERANK_MAX_DOCUMENTS` | `256` | 单次 Rerank 文档数量 |
| `AI_RERANK_MAX_QUERY_BYTES` | `8192` | query UTF-8 bytes |
| `AI_RERANK_MAX_DOCUMENT_BYTES` | `32768` | 单文档 UTF-8 bytes |
| `AI_RUNTIME_MAX_JSON_RESPONSE_BYTES` | `33554432` | Runtime 成功 JSON 上限 |

配置必须使用严格整数范围校验。Docker 和 Backend 模板只写非秘密默认值；Key 继续由既有受控环境注入。

## 兼容性

- 既有 Chat Model、Developer API Key、Grant 和 `/v1/chat/completions` 请求保持兼容。
- 既有 Key 不需要轮换即可授权 Embedding/Rerank Model。
- `/v1/models` 响应形状不变，只会在授权后出现更多 Model ID。
- LiteLLM 仍是可替换 Runtime；Platform 公共协议不暴露 LiteLLM 私有字段。
- 旧 Catalog 中 `chat`、`embedding` 数据可直接迁移；不存在合法类型外的数据时才能加约束。
- 若迁移前发现未知 `model_type`，升级必须 fail fast 并要求运维修复，不能静默改成 Chat。

## 测试策略

### 契约与单元测试

- Catalog 接受三种类型、拒绝大小写变体和未知类型。
- 三类默认参数互斥，未知参数和敏感字段拒绝。
- Embedding/Rerank 请求边界、UTF-8 byte 上限和未知字段拒绝。
- Embedding 向量 finite、维度、索引和数量验证。
- Rerank index、score、top_n、document 重建和稳定排序验证。
- Model Service 类型不匹配、事务释放、取消和 close 语义。
- Runtime Adapter 对正常、畸形、超限、压缩和错误响应的脱敏验证。
- 异常 `str/repr/vars/args/cause/context/traceback locals` 不包含输入和 Key。

### 授权、配额与 Ledger

- Key 对 Chat/Embedding/Rerank 的独立 Grant。
- 类型不匹配与未授权均返回同一 404。
- Redis Lua 在多 Worker 并发下原子执行 RPM、TPM、并发和预算。
- started Ledger 写入失败时不调用 Runtime并释放预留。
- 成功、Provider 失败、超时、取消、客户端断开均完成结算。
- Usage 缺失时使用保守预估并标记 estimated。
- Reconciler 恢复 started/abandoned 和孤立租约。
- Rerank Search Unit 不与 Token 重复收费。

### 集成与真实环境

- 使用 `httpx.MockTransport` 穿过真实 Client、Adapter、Model Service、授权、配额和 Ledger。
- 使用真实 PostgreSQL 验证 005 迁移、回填、约束、索引和回滚。
- 使用真实 Redis 验证多连接并发、租约过期和原子结算。
- 使用 OpenAI Python SDK 验证 `/v1/embeddings`。
- 使用普通 HTTP Client 验证 `/v1/rerank` 稳定协议。
- 在 LiteLLM 1.95.0 测试实例执行无真实 Provider Secret 的协议探针。
- 收费 Provider Smoke Test 只在运维显式授权后执行，测试数据必须非敏感。

### 前端

- 三类模型接入、类型不可变和类型化字段。
- 类型化 Smoke Test 失败后资源保留与安全重试。
- Grant、Rate Card、Usage 的类型展示。
- RBAC、Management 未配置、stale 数据和移动端布局。
- 所有秘密字段为空、不回填、不进入 URL/sessionStorage/日志。

## 实施顺序

### 阶段 1：Schema 与领域契约

- 完成 005 PostgreSQL/MySQL 迁移和 plugin `0.6.0`。
- 扩展 Catalog、Rate Card、Ledger Entity/VO/DAO。
- 建立 Embedding/Rerank 不可变领域对象与错误契约。

### 阶段 2：Runtime 与 Model Service

- 扩展 Client、Adapter 和 `ModelRuntimeEngine`。
- 实现 Model Service 类型化解析、embed/rerank 与事务边界。
- 完成成功、错误、取消、关闭和 traceback 脱敏测试。

### 阶段 3：统一调用协调器

- 实现 Redis 配额预留/续租/释放/结算。
- 实现 started/final Ledger 和 Reconciler。
- 将既有 Chat 普通/流式调用迁入同一协调器。

### 阶段 4：公开协议

- 实现 `/v1/embeddings` 和 `/v1/rerank`。
- 移除公开授权查询的 Chat 硬编码。
- 完成固定错误、请求上限和 SDK/HTTP 契约测试。

### 阶段 5：模型中心与开发者中心

- 增加 Rerank 类型和三类 Smoke Test。
- 增加类型化 Rate Card、Grant 和 Usage 视图。
- 完成桌面/390px/320px 视觉与交互验收。

### 阶段 6：真实环境验收与灰度

- 在真实 PostgreSQL、Redis 和 LiteLLM 1.95.0 完成迁移与协议验收。
- 核对 Ledger、Redis、Runtime Usage 和 Provider 账单。
- 先对测试 Application 开启，再按 Application 灰度。
- 只有配额、Ledger、Reconciler 和告警全部通过后才允许设置
  `AI_PUBLIC_API_ENABLED=true`。

## 验收标准

- [ ] Catalog 只接受 `chat|embedding|rerank`，类型创建后不可修改。
- [ ] `/v1/embeddings` 可被 OpenAI SDK 使用并返回 Platform Model Code。
- [ ] `/v1/rerank` 返回稳定、有界、脱敏的排序结果。
- [ ] 三类调用都验证相同的 Application、Key、Grant 和 active Model/Binding。
- [ ] Endpoint 与 Model 类型不匹配时返回固定 `model_not_found`。
- [ ] 三类调用都执行 Redis RPM、TPM、并发和预算限制。
- [ ] 三类调用都产生可恢复的 Usage Ledger。
- [ ] Rerank Search Unit 正确结算且不与 Token 重复收费。
- [ ] Runtime 调用期间没有活动数据库事务。
- [ ] 取消、断开和关闭错误不覆盖原异常或遗失租约。
- [ ] 日志、异常、Ledger 和前端不包含任何 Key、Provider Secret、输入正文或向量。
- [ ] PostgreSQL/MySQL 005 迁移、回填、约束和 plugin 版本登记通过。
- [ ] 模型中心可接入、测试、发布三种模型并正确展示类型。
- [ ] 完整后端、前端、MockTransport、真实 PostgreSQL/Redis/LiteLLM 验证通过。
- [ ] 生产开关只在配额、Ledger、Reconciler 和监控同时就绪后开启。

## 调用示例

Embedding：

```bash
curl -sS https://platform.example.com/v1/embeddings \
  -H 'Authorization: Bearer <DEVELOPER_API_KEY>' \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "company-embedding-v1",
    "input": ["first document", "second document"],
    "encoding_format": "float"
  }'
```

Rerank：

```bash
curl -sS https://platform.example.com/v1/rerank \
  -H 'Authorization: Bearer <DEVELOPER_API_KEY>' \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "company-rerank-v1",
    "query": "refund policy",
    "documents": ["shipping policy", "refund within thirty days"],
    "top_n": 1,
    "return_documents": true
  }'
```

示例中的 Key 仅为占位符。真实完整 Key 只在 Platform 创建或轮换成功后展示一次。
