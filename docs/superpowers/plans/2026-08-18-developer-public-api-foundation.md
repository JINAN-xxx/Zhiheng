# Developer Public API 基础实现计划

> 执行要求：逐任务使用 TDD；完成前运行完整 AI 插件回归与安全检查。

**目标：** 在既有 Application、Developer API Key、Model Grant 和 Model Service 基础上，提供默认关闭的 OpenAI 兼容 `GET /v1/models` 与非流式 `POST /v1/chat/completions`，使平台生成的 Key 首次具备真实模型调用能力。

**边界：** 本里程碑实现认证、授权、严格请求协议和非流式调用；不实现 Redis 配额、Usage Ledger 结算、流式 SSE、幂等重放或开发者中心前端。公开入口默认由 `AI_PUBLIC_API_ENABLED=false` 关闭。

## 固定架构约束

- Public API 不使用 RuoYi JWT、`PreAuth`、后台 `ResponseUtil` 或操作日志装饰器。
- HTTP 错误保持真实状态码和 OpenAI `error` 对象。
- Developer Key 只允许存在于入站 `Authorization` 和认证敏感 helper；不得进入 URL、日志、异常、Traceback locals 或下游 Runtime。
- 鉴权和授权查询必须在 Runtime 调用前结束数据库事务。
- 对外模型 ID 使用不可变 `model_code`；内部调用只使用 Platform Model ID。
- Controller 不直接接触 LiteLLM Client、Runtime Key、Management Key 或 Provider Credential。
- 请求只接受设计列出的字段；`stream=true` 在本阶段明确返回 `400`。

## 任务 1：定义 Public API 协议和固定错误

**新增：**

- `plugins/ai/domain/public_api_errors.py`
- `plugins/ai/entity/vo/ai_openai_compat_vo.py`
- `tests/plugins/ai/test_public_api_contract.py`

先写红灯，覆盖：固定不可变错误、OpenAI error envelope、模型列表响应、严格 Chat 请求、未知字段、消息角色、参数范围、`stream=true`、控制字符和请求大小配置。实现仅做纯领域与 Pydantic 转换。

提交：`feat: 定义 Developer Public API 契约（Public 1/7）`

## 任务 2：增加调用面只读 DAO

**修改：**

- `plugins/ai/dao/ai_developer_api_dao.py`

**新增：**

- `tests/plugins/ai/test_public_api_dao.py`

DAO 提供：按 Key ID/Prefix 获取认证快照；按 Key ID 列出 active Grant 对应的 active/published Chat Model；按 `model_code` 解析单个授权模型。查询返回纯值快照，不暴露 digest 给 Controller，不 commit。用 SQLite 真实双会话覆盖撤销、Application 停用、Grant 停用和模型停用后的可见性。

提交：`feat: 增加 Public API 授权查询（Public 2/7）`

## 任务 3：实现 Developer Key 认证 clean boundary

**新增：**

- `plugins/ai/infrastructure/developer_key_authenticator.py`
- `tests/plugins/ai/test_developer_key_authenticator.py`

认证器严格解析单个 Bearer Header，校验长度、Key 格式、Prefix、HMAC、Application/Key 状态、过期与轮换宽限。任何失败只返回固定分类；最终异常在不持有 Header、完整 Key、ORM 或 digest 的 frame 中构造。成功上下文只含 Application ID、Key ID 和非秘密身份字段。成功、失败、数据库异常和取消均结束只读事务；配置缺失零数据库访问。

提交：`feat: 实现 Developer Key 认证边界（Public 3/7）`

## 任务 4：实现模型授权服务

**新增：**

- `plugins/ai/service/api_entitlement_service.py`
- `tests/plugins/ai/test_api_entitlement_service.py`

服务按认证上下文列出授权模型，或将外部 `model_code` 解析为 Platform Model ID。只返回公开模型快照；不返回 Binding、Deployment、Provider 或 Grant ID。所有查询均为短事务，错误固定区分未授权与不存在而不泄露资源状态。

提交：`feat: 实现 Public API 模型授权（Public 4/7）`

## 任务 5：实现非流式 Chat 编排与 OpenAI 转换

**新增：**

- `plugins/ai/service/openai_compat_service.py`
- `tests/plugins/ai/test_openai_compat_service.py`

服务执行 entitlement -> Model Service chat，严格映射 messages 和五项通用参数。`stop`、`user` 仅作为已验证协议字段处理：现有 Runtime 不支持时不得静默下发；本阶段固定拒绝无法安全表达的非空值。响应生成 `chat.completion`、稳定 ID、UTC created、assistant choice、finish_reason、usage；Runtime/Model Service 错误映射为固定 `404/502/503`，不保留上游 payload。

提交：`feat: 实现 OpenAI 非流式调用编排（Public 5/7）`

## 任务 6：发布独立 `/v1` Controller

**新增：**

- `plugins/ai/controller/openai_compat_controller.py`
- `plugins/ai/infrastructure/public_api_provider.py`
- `tests/plugins/ai/test_openai_compat_controller.py`

Controller 无 `PreAuth`、无 RuoYi `ResponseUtil`，返回原生 JSON；Feature Flag 关闭时返回固定 `404` 且零数据库/Runtime访问。认证分两层 dependency：敏感层只返回脱敏 outcome，clean boundary 再产生错误响应。请求体按 `AI_API_MAX_REQUEST_BYTES` 有界读取后手动验证，缺失/非法 Content-Length、chunked 超限、重复 Authorization、未知字段均 fail closed。Controller 只依赖抽象 Service/provider，不读取共享 Client。

提交：`feat: 发布 OpenAI 兼容 Public API（Public 6/7）`

## 任务 7：联合回归与安全验收

**新增：**

- `tests/plugins/ai/test_public_api_integration.py`

使用真实 SQLite DAO、真实认证器/授权服务/Model Service 和 Mock Runtime 覆盖：

- Key 创建后 `/v1/models` 只见显式授权模型。
- 非流式 Chat 使用 Platform Model ID 解析并由内部 Runtime Key 路径执行。
- Key 撤销、Application/Grant/Model 停用立即拒绝。
- 跨 Application 模型不可调用。
- 完整 Key 不出现在响应错误、日志、异常 cause/context/args/vars 和所有生产 traceback locals。
- DB 事务在 Runtime 调用前结束；取消不被清理错误覆盖。
- Feature Flag 默认关闭。

最终验证：Public 定向测试、完整 `tests/plugins/ai`、变更集 Ruff/format、compileall、`git diff --check`。明确记录 Redis 配额、Ledger、SSE 和真实 OpenAI SDK 属于下一里程碑，不能据此开启生产公开入口。

提交：仅在测试暴露实现问题时提交对应修复；工作区干净时不创建空提交。
