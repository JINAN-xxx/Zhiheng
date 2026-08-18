# Developer Public API 流式增量设计

## 状态

本规格是 `2026-08-18-developer-api-key-openai-gateway-design.md` 的已确认增量，调整 Public API 首个可用版本的交付顺序：`POST /v1/chat/completions` 必须同时支持 `stream=false` 和 `stream=true`，不能把流式能力推迟到公开接口之后。

## 目标与边界

- `stream=false` 返回标准 OpenAI `chat.completion` JSON。
- `stream=true` 返回真实端到端 `text/event-stream`，不缓冲完整模型响应。
- 继续复用 `ModelService` 和 `ModelRuntimeEngine`，Public Controller 不直接访问 LiteLLM。
- Developer Key 只用于 Platform 入站认证，不转发给 Runtime。
- Redis 配额、Usage Ledger、预算结算和幂等仍属于下一里程碑；这些能力完成前 `AI_PUBLIC_API_ENABLED` 默认关闭，不建议生产开放。

## 请求处理顺序

```text
Authorization Header
  -> Developer Key sensitive authentication helper
  -> clean identity boundary
  -> Model Grant / model_code resolution
  -> close read transaction
  -> ModelService.stream_chat(platform_model_id, ...)
  -> OpenAI SSE adapter
  -> client
```

鉴权、Application 状态和 Model Grant 校验必须在发送 SSE 响应头前完成。所有数据库读取在创建 Runtime 流之前结束事务；SSE 生命周期不得持有数据库连接或 ORM Entity。

## SSE 协议

响应头至少包含：

```http
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
```

每帧使用：

```text
data: <compact-json>\n\n
```

事件顺序：

1. 首帧：`choices[0].delta.role = "assistant"`。
2. 内容帧：`choices[0].delta.content` 为单次 Runtime 内容增量。
3. 推理内容不对外暴露；首期忽略规范化 reasoning event，不能混入普通 content。
4. Runtime usage event 缓存在流适配器内，用于最终 usage；若客户端未请求 usage 扩展，chunk 不携带 usage。
5. 完成帧：`delta = {}`、`finish_reason = "stop"`。
6. 最后一帧固定为 `data: [DONE]\n\n`。

所有 JSON chunk 使用同一个稳定 `chatcmpl_<uuid>`、调用开始时的 UTC Unix `created`、外部 `model_code`，不得包含 Platform Model ID、Binding、Deployment、Provider、Runtime request ID 或成本。

## 生命周期与错误

- Runtime 流在首个事件前构造失败：返回标准 OpenAI JSON 错误及真实 `502/503`，不建立伪 SSE 成功响应。
- StreamingResponse 已开始后发生 Runtime 错误：立即终止连接，不发送 `[DONE]`，避免把失败伪装为完成。
- 客户端断开、响应写失败、任务取消、生成器提前关闭：必须在 `finally` 中关闭 `ModelServiceStreamResult`。
- 活动 Runtime 错误或 `CancelledError` 优先于关闭错误；正常自然耗尽时关闭错误必须可见并终止成功完成序列。
- `aclose()` 由单次 Public 流拥有，不关闭 Worker 共享 Runtime Client。
- 不设置无限内部队列；采用异步迭代背压，让慢消费者限制上游读取速度。

## 安全边界

- 完整 Developer Key 不进入 URL、query、Cookie、响应、SSE、日志、异常、cause/context/args/vars 或生产 traceback frame locals。
- 动态 prompt 不记录到操作审计或稳定错误。
- SSE chunk 只从规范化 `ModelStreamEvent` 构建，不透传上游原始 payload。
- Feature Flag 关闭、Key 无效、模型未授权及请求验证失败均在 Runtime 调用前结束。
- `stream` 必须为严格布尔值；未知字段和 Runtime 私有参数固定 `400`。

## 组件调整

- `ai_openai_compat_vo.py`：允许 `stream=true`，新增严格 chunk VO。
- `openai_compat_service.py`：普通与流式共享模型解析、参数转换和公开响应快照。
- `openai_stream_adapter.py`：唯一负责 `ModelStreamEvent -> SSE bytes`、事件顺序和关闭语义。
- `openai_compat_controller.py`：请求验证后根据 `stream` 返回 JSONResponse 或 StreamingResponse。

## 测试与验收

- 普通与流式请求均使用同一 Developer Key 认证和 Model Grant。
- 标准 OpenAI SDK 能消费 chunk，拼接后等于 Runtime 完整内容。
- 首帧 role、内容增量、finish chunk 和 `[DONE]` 顺序精确。
- 客户端提前关闭只关闭一次底层流；并发 `anext/aclose` 不泄漏资源。
- `CancelledError`、首帧前错误、流中错误和正常 close 错误语义明确。
- Runtime 调用与整个 SSE 期间数据库 `in_transaction()` 均为 false。
- 遍历最终错误和生产 traceback locals，确认 Developer Key、Runtime Key、Provider URL 与上游正文均不存在。
- 流式测试不能只用预先构造的列表；必须使用可阻塞 async generator 验证背压与断连。

## 明确不采用

- 不将完整响应缓冲后切片伪装为流式。
- 不从 Public Controller 直接代理 LiteLLM SSE。
- 不在配额未完成前默认开启生产 Public API。
