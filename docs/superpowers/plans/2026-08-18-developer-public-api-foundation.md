# Developer Public API 基础实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框跟踪进度；每个任务先使用 test-driven-development，完成声明前使用 verification-before-completion。

**目标：** 在既有 Application、Developer API Key、Model Grant 和 Model Service 基础上，提供默认关闭的 OpenAI 兼容 `GET /v1/models`，以及同时支持普通和真实 SSE 流式响应的 `POST /v1/chat/completions`。

**架构：** Public Controller 使用独立 OpenAI 错误协议；Developer Key 认证与 Model Grant 查询在短数据库事务中完成；普通和流式请求均通过 Model Service 调用共享 Runtime。流式适配器只消费规范化 `ModelStreamEvent`，负责 SSE 帧和单次流关闭，不接触数据库或 LiteLLM Client。

**技术栈：** FastAPI、Pydantic v2、SQLAlchemy AsyncSession、StreamingResponse、async generator、Pytest、httpx ASGITransport

---

**边界：** 不实现 Redis 配额、Usage Ledger 结算、幂等重放或开发者中心前端。公开入口默认由 `AI_PUBLIC_API_ENABLED=false` 关闭，配额与 Ledger 完成前不建议生产开放。

## 固定架构约束

- Public API 不使用 ZhiHeng JWT、`PreAuth`、后台 `ResponseUtil` 或操作日志装饰器。
- HTTP 错误保持真实状态码和 OpenAI `error` 对象。
- Developer Key 只允许存在于入站 `Authorization` 和认证敏感 helper；不得进入 URL、日志、异常、Traceback locals 或下游 Runtime。
- 鉴权和授权查询必须在 Runtime 调用前结束数据库事务。
- 对外模型 ID 使用不可变 `model_code`；内部调用只使用 Platform Model ID。
- Controller 不直接接触 LiteLLM Client、Runtime Key、Management Key 或 Provider Credential。
- 请求只接受设计列出的字段；`stream` 必须是严格布尔值，true/false 均受支持。

## 任务 1：定义 Public API 协议和固定错误

**状态：已完成；Task 5 将按流式增量规格演进 `stream` 契约。**

**新增：**

- `plugins/ai/domain/public_api_errors.py`
- `plugins/ai/entity/vo/ai_openai_compat_vo.py`
- `tests/plugins/ai/test_public_api_contract.py`

首轮已覆盖固定不可变错误、OpenAI error envelope、模型列表响应、严格 Chat 请求、未知字段、消息角色、参数范围、控制字符和请求大小配置。初始非流式限制将在 Task 5 先以红灯改为 true/false 均支持。

提交：`feat: 定义 Developer Public API 契约（Public 1/7）`

## 任务 2：增加调用面只读 DAO

**状态：已完成。**

**修改：**

- `plugins/ai/dao/ai_developer_api_dao.py`

**新增：**

- `tests/plugins/ai/test_public_api_dao.py`

DAO 提供：按 Key ID/Prefix 获取认证快照；按 Key ID 列出 active Grant 对应的 active/published Chat Model；按 `model_code` 解析单个授权模型。查询返回纯值快照，不暴露 digest 给 Controller，不 commit。用 SQLite 真实双会话覆盖撤销、Application 停用、Grant 停用和模型停用后的可见性。

提交：`feat: 增加 Public API 授权查询（Public 2/7）`

## 任务 3：实现 Developer Key 认证 clean boundary

**状态：已完成。**

**新增：**

- `plugins/ai/infrastructure/developer_key_authenticator.py`
- `tests/plugins/ai/test_developer_key_authenticator.py`

认证器严格解析单个 Bearer Header，校验长度、Key 格式、Prefix、HMAC、Application/Key 状态、过期与轮换宽限。任何失败只返回固定分类；最终异常在不持有 Header、完整 Key、ORM 或 digest 的 frame 中构造。成功上下文只含 Application ID、Key ID 和非秘密身份字段。成功、失败、数据库异常和取消均结束只读事务；配置缺失零数据库访问。

提交：`feat: 实现 Developer Key 认证边界（Public 3/7）`

## 任务 4：实现模型授权服务

**状态：已完成。提交：`eb9bca9`。**

**新增：**

- `plugins/ai/service/api_entitlement_service.py`
- `tests/plugins/ai/test_api_entitlement_service.py`

- [x] **步骤 1：编写授权服务红灯**

测试 `list_models(identity)` 只输出 `id/object/created/owned_by`，`resolve_model(identity, model_code)` 只返回 Platform Model ID 与公开 code；无授权固定抛 `PublicApiError.model_not_found()`。断言查询成功、空结果、DAO 异常和 `CancelledError` 后 `db.in_transaction()` 均为 false。

- [x] **步骤 2：运行红灯**

运行：`conda run -n dj pytest tests/plugins/ai/test_api_entitlement_service.py -q`

预期：`api_entitlement_service` 模块不存在。

- [x] **步骤 3：实现短事务授权服务**

服务只持有 `DeveloperApiIdentity` 和 `PublicModelRecord`，将 `published_at` 转为 UTC Unix 秒。DAO 异常映射 `service_unavailable`；取消原样传播；任何清理异常不覆盖活动异常。

- [x] **步骤 4：运行绿灯并提交**

运行目标测试、DAO 回归、Ruff 和 format；提交 `feat: 实现 Public API 模型授权（Public 4/7）`。

## 任务 5：实现普通与流式 Chat 编排

**状态：已完成。提交：`507b316`。**

**新增：**

- `plugins/ai/service/openai_compat_service.py`
- `plugins/ai/infrastructure/openai_stream_adapter.py`
- `tests/plugins/ai/test_openai_compat_service.py`
- `tests/plugins/ai/test_openai_stream_adapter.py`

- [x] **步骤 1：把 `stream=true` 契约改为红灯**

修改 `test_public_api_contract.py`：true/false 均通过；新增 chunk VO，固定 `chat.completion.chunk`、assistant role 首帧、content delta、空 delta finish 帧。运行后预期旧 validator 拒绝 true。

- [x] **步骤 2：编写 Service 红灯**

普通路径断言 entitlement 后以 Platform Model ID 调 `ModelService.chat`；流式路径断言调用 `ModelService.stream_chat`。messages、temperature、top_p、max_tokens、presence_penalty、frequency_penalty、stop 和 user 必须精确映射；为此受控扩展 `ModelService` 参数白名单与验证，未知字段仍拒绝。

- [x] **步骤 3：编写 SSE adapter 红灯**

用可阻塞 async generator 覆盖精确字节序列：role chunk -> content chunks -> finish chunk -> `[DONE]`。reasoning 不输出；同一 completion ID/created/model 贯穿全流；慢消费者不预读；提前 `aclose`、取消和流中错误都恰好关闭一次底层流且不发送 `[DONE]`。

- [x] **步骤 4：实现最小编排与流适配器**

`OpenAICompatService` 在 Runtime 前完成 entitlement；`OpenAIStreamAdapter` 只持公开 completion 快照和 `ModelServiceStreamResult`。正常耗尽后发送 finish 与 `[DONE]`；任何活动异常在 `finally` 关闭并原样传播。

- [x] **步骤 5：运行绿灯并提交**

运行：

```bash
conda run -n dj pytest \
  tests/plugins/ai/test_public_api_contract.py \
  tests/plugins/ai/test_openai_compat_service.py \
  tests/plugins/ai/test_openai_stream_adapter.py \
  tests/plugins/ai/test_model_service.py -q
```

提交：`feat: 实现 OpenAI 普通与流式调用编排（Public 5/7）`

## 任务 6：发布独立 `/v1` Controller

**状态：已完成。提交：`f83506c`。**

**新增：**

- `plugins/ai/controller/openai_compat_controller.py`
- `plugins/ai/infrastructure/public_api_provider.py`
- `tests/plugins/ai/test_openai_compat_controller.py`

- [x] **步骤 1：编写 Controller 红灯**

断言 `/v1/models` 和 `/v1/chat/completions` 无 PreAuth、无 ZhiHeng `ResponseUtil`，Feature Flag 关闭返回真实 404。重复 Authorization、非法 Content-Length、chunked 超限、未知字段返回 OpenAI JSON 400/401，且零 Runtime 调用。

- [x] **步骤 2：锁定普通与流式 HTTP 契约**

ASGITransport 断言普通响应 `application/json`；流式响应 `text/event-stream`、`Cache-Control: no-cache`、`X-Accel-Buffering: no`，最终 `[DONE]`。在发送响应头前的认证、授权和流构造错误返回 JSON `401/404/502/503`。

- [x] **步骤 3：实现 provider 与 Controller**

敏感 Authorization/body reader 只返回脱敏 outcome；clean boundary 生成固定错误响应。Controller 仅依赖 Authenticator、Entitlement 和 OpenAICompatService；共享 Runtime provider 使用非拥有 wrapper。StreamingResponse 的迭代器在 `finally` 调用 adapter `aclose()`。

- [x] **步骤 4：运行绿灯并提交**

运行 Controller、认证器、Service 和现有 Runtime provider 测试；提交 `feat: 发布 OpenAI 兼容 Public API（Public 6/7）`。

## 任务 7：联合回归与安全验收

**状态：已完成。集成测试提交：`1823b74`。**

**新增：**

- `tests/plugins/ai/test_public_api_integration.py`

使用真实 SQLite DAO、真实认证器/授权服务/Model Service 和 Mock Runtime 覆盖：

- Key 创建后 `/v1/models` 只见显式授权模型。
- 普通与流式 Chat 都使用 Platform Model ID 解析并由内部 Runtime Key 路径执行。
- Key 撤销、Application/Grant/Model 停用立即拒绝。
- 跨 Application 模型不可调用。
- 完整 Key 不出现在响应错误、日志、异常 cause/context/args/vars 和所有生产 traceback locals。
- DB 事务在 Runtime 调用及整个 SSE 生命周期均已结束；取消不被清理错误覆盖。
- 流首帧、增量、finish 和 `[DONE]` 顺序精确；客户端断开只关闭单次流，不关闭共享 Runtime Client。
- Feature Flag 默认关闭。

最终验证：Public 定向测试、完整 `tests/plugins/ai`、变更集 Ruff/format、compileall、`git diff --check`。使用本地 OpenAI Python SDK 对 ASGI/真实测试服务验证普通和流式协议。明确记录 Redis 配额、Ledger 和生产灰度属于下一里程碑，不能据此开启生产公开入口。

提交：仅在测试暴露实现问题时提交对应修复；工作区干净时不创建空提交。
