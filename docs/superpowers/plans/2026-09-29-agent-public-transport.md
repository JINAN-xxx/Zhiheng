# Agent 公共调用传输接口实施计划

**目标：** 按用户“继续模块化”的指令，实施已建议的第三个试点，消除 Agent 对 AI Controller 私有函数的调用。

**架构：** 将已有 HTTP 认证、JSON 读取、公开错误响应和 SSE 响应清理迁入 `plugins/ai/public/api_transport.py`，由两个 Controller 共同消费。公共传输层不负责装配服务、访问数据库、模型授权、配额预留或结算。

**技术栈：** FastAPI/Starlette、Pydantic、异步 Python、pytest/httpx。

## 设计与取舍

采用移动实现并公开契约的方案，原规则保持一致。仅添加转发到旧 Controller 的 facade 会保留反向依赖；重新设计整个调用服务则会扩大事务和计费改动范围，因此本次均不采用。

公共函数与类型：

```python
async def authenticate_request(request, authenticator) -> DeveloperApiIdentity: ...
async def read_json_request(request, max_bytes, model_type) -> RequestBody[BodyT]: ...
def error_response(error: PublicApiError) -> JSONResponse: ...
def streaming_response(stream: ByteStream) -> StreamingResponse: ...
```

`RequestBody` 是不可变结果，包含经 Pydantic 验证的 value 或 cancelled 状态；原始请求字节不作为结果返回。`ByteStream` 只约束异步 bytes 迭代和 `aclose`，不要求消费者依赖具体 OpenAIStreamAdapter。SSE 字节保持原样，公共接口负责标准响应头及流关闭。

保留单 Authorization 头规则、Content-Type/Content-Length 校验、逐块字节上限、固定错误体和 401 challenge。取消仍是取消；清理异常不得覆盖已有取消/读取异常。OpenAI Controller 原有的取消结果清理机制保留。Agent 的 feature flag、应用绑定、模型授权先于检索的顺序、原事务结束时机和错误映射保留。

本次不调整前端、表结构、URL、插件 ID、运行服务或部署拓扑。继续在现有独立工作区与修复分支实现，保留前序未提交成果。本轮开始时相关文件已在临时目录保存源码与 Ruff 基线。

## 1. 契约与回归基线

- [x] 运行现有 `test_openai_compat_controller.py` 与 `test_agent_public_controller.py`，确认基线。
- [x] 新增 `tests/plugins/ai/test_public_api_transport.py`，先验证缺失公共入口导致失败。
- [x] 覆盖认证重复头、请求体分块/长度/类型校验、取消结果、SSE 字节/响应头/关闭与错误优先级。
- [x] 增加导入约束：Agent 不导入 AI Controller；公共传输不依赖 Controller、DAO 或数据库装配。

## 2. 提取和接入

- [x] 新建 `plugins/ai/public/api_transport.py`，迁移已有规则并补充公共函数文档。
- [x] 修改 `plugins/ai/controller/openai_compat_controller.py` 与 `plugins/agent_extension/controller/agent_invocation_controller.py`，共用公共函数。
- [x] 原 `_stream_bytes` 直接测试改用公共流式响应的 `body_iterator`，保留原取消优先级断言。
- [x] 补充 Agent 路由回归：重复认证、超限请求、额度拒绝、取消和 SSE 头；确认不会继续调用模型。

## 3. 验证和记录

- [x] 运行新契约、两个 Controller、Agent 全部、密钥认证、授权、调用协调器、兼容服务、SSE adapter 和公开 API 集成测试。
- [x] 按 requesting-code-review 技能进行独立只读审查，同时完成本地回归和 Ruff 检查。
- [x] 与本轮开始时源码比较，确认没有新增 Ruff 诊断；`git diff --check` 通过。
- [x] 更新架构现状和操作记录，列出已收拢入口与仍保留的应用/模型运行时依赖。

基础命令（后端目录，使用已安装依赖的环境）：

```bash
python3 -m pytest -q -p no:cacheprovider \
  tests/plugins/ai/test_public_api_transport.py \
  tests/plugins/ai/test_openai_compat_controller.py \
  tests/plugins/agent_extension \
  tests/plugins/ai/test_developer_key_authenticator.py \
  tests/plugins/ai/test_api_entitlement_service.py \
  tests/plugins/ai/test_public_invocation_coordinator.py \
  tests/plugins/ai/test_openai_compat_service.py \
  tests/plugins/ai/test_openai_stream_adapter.py \
  tests/plugins/ai/test_public_api_integration.py \
  --tb=short --show-capture=no
```
