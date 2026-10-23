# Platform LiteLLM 接入层设计

## 背景

LiteLLM Proxy、PostgreSQL 和 Redis 已经独立部署并通过健康、认证与端口隔离验证。现有 ZhiHeng AI 插件仍通过 Agno 和各 Provider SDK 直接调用模型，Platform 后端尚无稳定的 LiteLLM HTTP 边界。

本规格是平台改造的第一阶段。它只建立 Platform 到 LiteLLM 的内部接入层，不迁移现有模型业务数据，也不替换当前 Agno 对话链路。

## 目标

- 为 AI 插件提供独立、可测试的 LiteLLM HTTP 客户端。
- 支持健康检查、模型列表、Chat Completions、流式 Chat Completions 和 Embeddings。
- 为普通响应、流式响应、超时、连接失败和上游错误定义稳定边界。
- 使用单独的内部 Runtime Key 调用数据面，不向 Platform 进程注入 LiteLLM Master Key。
- 复用现有 Trace ID 和 Request ID，并保证日志不泄露 Credential、Prompt 或响应正文。
- 让 Docker 中的 ZhiHeng Backend 通过内部网络直接访问 LiteLLM Proxy。

## 非目标

本阶段不负责：

- 修改 `ai_models`、`ai_chat_config` 或 Agno 会话表。
- Provider Credential、Runtime Deployment 或 Model Catalog 管理界面。
- 模型 Alias、Tenant、Project、Application 或 Customer API Key。
- Platform OpenAI 兼容公开接口。
- Quota、Usage Ledger、Pricing 或 Billing。
- 替换现有 `/ai/chat` 对话链路。
- 删除 Agno、LiteLLM Python SDK 或现有 Provider SDK。
- 向真实 Provider 发起付费测试请求。

## 架构

```text
ZhiHeng AI Plugin Service
          |
          v
LiteLLMRuntimeClient
          |
          | HTTP + Runtime Key
          v
LiteLLM Proxy
          |
          v
Provider
```

AI 插件通过启动和关闭 Hook 管理客户端。每个 FastAPI Worker 持有一个 `httpx.AsyncClient` 连接池，不在每次请求中重新创建客户端。

客户端保存在当前 Worker 的 `app.state.ai_litellm_runtime_client`。业务代码通过一个显式依赖获取客户端，测试可以替换该依赖，不直接引用模块级全局对象。

## 内部认证

使用 LiteLLM 创建一个名为 `platform-runtime` 的内部 Virtual Key，配置为允许模型查询和推理调用。这个 Key 是 Runtime 技术凭据，不是 Customer API Key，也不携带 Platform Tenant、Project 或 Application 身份。

边界要求：

- `LITELLM_RUNTIME_KEY` 只存在于未提交的运行环境中。
- Runtime Key 不写入 Platform 数据库，不返回前端，不进入日志。
- 本阶段 LiteLLM Master Key 只用于部署侧一次性创建 Runtime Key。
- ZhiHeng Backend 进程不注入 `LITELLM_MASTER_KEY`。
- 第二阶段新增管理面客户端时，Master Key 和 Runtime Key 必须继续分离。

真实 Runtime Key 在仓库根目录未提交的 `.env` 中提供给 Docker Compose，文件权限为 `0600`。本地非容器开发通过进程环境变量注入。已有受版本控制的 `.env.dev`、`.env.prod` 和 `.env.dockerpg` 只包含空值或非敏感示例，不保存真实 Key。

## 配置

新增 AI 插件专用 `LiteLLMSettings`，读取：

| 环境变量 | 用途 | 默认值 |
|---|---|---|
| `LITELLM_BASE_URL` | LiteLLM Proxy 基础地址 | 本地开发使用 `http://127.0.0.1:14000` |
| `LITELLM_RUNTIME_KEY` | 数据面内部凭据 | 空 |
| `LITELLM_CONNECT_TIMEOUT_SECONDS` | 建连超时 | `5` |
| `LITELLM_REQUEST_TIMEOUT_SECONDS` | 非流式请求总超时 | `600` |
| `LITELLM_VERIFY_SSL` | 是否校验 TLS 证书 | `true` |

Docker 环境覆盖 `LITELLM_BASE_URL=http://litellm-proxy:4000`。超时值必须大于零，基础地址只接受包含主机名的 `http` 或 `https` URL。

Runtime Key 缺失时 ZhiHeng 主应用仍可启动，AI Runtime 状态为 `misconfigured`，所有需要认证的 LiteLLM 调用快速失败。LiteLLM 暂时不可用也不能阻止 ZhiHeng 管理功能启动；状态接口返回 `unavailable`，由运维修复依赖。

明确配置了非法 URL 时，AI 插件启动失败并由现有插件运行时记录错误，避免带着错误目标地址继续运行。

## Docker 网络

`docker-compose.pg.yml` 中的 `zhiheng-backend-pg` 同时加入：

- 原有 `zhiheng-network`
- 外部网络 `litellm-network`

`litellm-network` 由 LiteLLM Compose 创建，Platform Compose 将其声明为 `external: true`。两个 Compose 项目保持独立，Platform 不通过 `depends_on` 管理 LiteLLM 生命周期。

网络边界保持不变：

- ZhiHeng Backend 通过 `http://litellm-proxy:4000` 访问 Proxy。
- LiteLLM PostgreSQL 和 Redis 不发布宿主机端口。
- ZhiHeng PostgreSQL 和 Redis 不加入 `litellm-network`。
- LiteLLM 故障时 Platform 管理端仍能启动并报告降级状态。

## 客户端接口

`LiteLLMRuntimeClient` 提供以下异步方法：

```text
check_liveliness()
list_models()
create_chat_completion(payload)
stream_chat_completion(payload)
create_embedding(payload)
aclose()
```

Chat 和 Embedding 请求体使用 JSON Mapping 传入，以保留 OpenAI 兼容字段的前向兼容性。健康状态和诊断接口响应使用明确的 Pydantic 模型。

所有认证请求自动加入：

- `Authorization: Bearer <LITELLM_RUNTIME_KEY>`
- 当前请求的 `request-id`
- 当前请求的 `trace-id`
- 标识 Platform 调用方的稳定 `User-Agent`

调用方不能覆盖 `Authorization`。客户端不记录请求体、响应体或完整请求头。

## 生命周期

AI 插件清单声明成对的 `onStartup` 和 `onShutdown` Hook：

1. Worker 启动时解析配置并创建 `LiteLLMRuntimeClient`。
2. 启动 Hook 不主动执行外部健康检查，避免短暂网络故障阻塞 Platform 启动。
3. 客户端写入当前 Worker 的 `app.state`。
4. Worker 关闭时调用 `aclose()` 并清除状态引用。
5. 重复执行关闭 Hook 必须是幂等操作。

每个 Worker 独立持有连接池，不跨进程共享客户端。

## 流式响应

`stream_chat_completion()` 使用 HTTPX 流式上下文连接 LiteLLM，并以异步字节迭代器返回原始 SSE 数据：

- 收到非成功状态码时，在返回任何分片前转换为内部异常。
- 正常情况下不重新序列化 SSE，避免破坏事件边界和 `[DONE]`。
- 下游取消、客户端断连或生成器关闭时，必须在 `finally` 中关闭上游响应。
- 流建立后的读超时和连接中断转换为可识别的流式网关错误。
- 本阶段不写 Usage Ledger；Usage 采集在后续独立阶段实现。

## 错误模型

客户端定义稳定的内部异常层：

| 异常 | 场景 | Platform 状态 |
|---|---|---|
| `LiteLLMMisconfiguredError` | Runtime Key 缺失或配置不可用 | `503` |
| `LiteLLMUnavailableError` | DNS、连接失败或 Proxy 不可达 | `503` |
| `LiteLLMTimeoutError` | 建连、读取或总请求超时 | `504` |
| `LiteLLMAuthenticationError` | Runtime Key 被 LiteLLM 拒绝 | `502` |
| `LiteLLMUpstreamError` | LiteLLM 或 Provider 返回其他错误 | 按调用接口统一映射 |

LiteLLM 的 `401` 或 `403` 不直接返回为客户认证失败，因为它表示 Platform 内部凭据故障。诊断接口返回脱敏的稳定错误码和简短消息，不返回 Provider 原始响应、内部 URL 或 Credential。

## 诊断接口

新增权限 `ai:runtime:query`，并提供两个仅供已登录且具备该权限的用户访问的接口：

### `GET /ai/runtime/health`

返回：

- `status`: `healthy`、`misconfigured` 或 `unavailable`
- `configured`: Runtime Key 是否已配置
- `latencyMs`: 本次检查耗时
- `checkedAt`: 检查时间
- `message`: 脱敏状态说明

### `GET /ai/runtime/models`

使用 Runtime Key 调用 LiteLLM `/v1/models`，返回规范化模型列表。当前没有 Deployment 时返回空列表属于成功状态。

接口不返回 LiteLLM Master Key、Runtime Key、数据库状态、Redis 状态或 Provider Credential。

## 代码边界

预计新增或修改：

```text
backend/plugins/ai/config.py
backend/plugins/ai/infrastructure/litellm_client.py
backend/plugins/ai/infrastructure/litellm_errors.py
backend/plugins/ai/hooks.py
backend/plugins/ai/controller/ai_runtime_controller.py
backend/plugins/ai/entity/vo/ai_runtime_vo.py
backend/plugins/ai/plugin.yaml
backend/.env.dev
backend/.env.prod
backend/.env.dockerpg
docker-compose.pg.yml
backend/tests/plugins/ai/
```

本阶段不修改前端文件和数据库 Migration。

## 测试策略

### 单元测试

- 配置默认值、非法 URL、非正数超时和空 Runtime Key。
- 请求路径、Authorization、Request ID、Trace ID 和 User-Agent。
- 调用方无法覆盖 Authorization。
- 模型列表、Chat、Embedding 普通成功响应。
- DNS/连接错误、超时、认证错误和其他上游错误映射。
- 流式 SSE 分片原样传递、非成功状态预检查和下游取消关闭上游。
- 启动 Hook 创建每 Worker 客户端，关闭 Hook 幂等释放连接池。
- Runtime Key 和请求/响应正文不出现在日志中。
- 诊断接口权限、健康状态和空模型列表响应。

HTTP 测试使用 `httpx.MockTransport`，不依赖真实 Provider，也不产生模型费用。

### 部署契约测试

- Platform Backend 同时加入 `zhiheng-network` 和外部 `litellm-network`。
- Docker Compose 只向 Backend 注入 Runtime Key，不注入 LiteLLM Master Key。
- 三个受控环境模板不包含真实 Key。

### 真实集成验证

- 创建单个 `platform-runtime` 内部 Key，真实值只写入未提交环境。
- ZhiHeng Backend 能通过容器 DNS 访问 LiteLLM。
- `GET /ai/runtime/health` 返回 `healthy`。
- `GET /ai/runtime/models` 认证成功并返回当前空模型列表。
- 删除或替换 Runtime Key 后，诊断接口返回脱敏错误且 ZhiHeng 主应用保持运行。

## 验收标准

第一阶段完成时必须满足：

1. LiteLLM Runtime 三个容器继续健康运行。
2. Platform Backend 通过内部 Docker 网络访问 LiteLLM Proxy。
3. Platform 使用独立 Runtime Key，不持有 Master Key。
4. 健康和模型诊断接口受 ZhiHeng 登录与权限保护。
5. 普通请求、流式请求和 Embedding 客户端契约有自动化测试。
6. LiteLLM 故障不会阻止 ZhiHeng 管理端启动。
7. Credential、Prompt 和响应正文不进入日志。
8. 当前 Agno 模型和对话功能保持原状。
9. 不新增或修改业务数据库表。

## 后续衔接

第二阶段在此客户端和网络边界上增加 LiteLLM 管理面客户端，以及 ZhiHeng Provider、Credential 和 Deployment 管理界面。管理面使用独立 Master Key；Runtime Key 继续只承担模型查询和推理调用。
