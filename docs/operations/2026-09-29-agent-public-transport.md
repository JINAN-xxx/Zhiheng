# Agent 公共调用传输模块化记录

日期：2026-09-29。工作区：`/home/ja/project/zhiheng`；分支：`fix/platform-integration-review`。本轮基于此前模型查询与前端会话 API 试点继续实施。

## 问题与结果

此前 Agent 直接导入 `openai_compat_controller`，调用其中的四个私有 helper；修改模型调用 Controller 的内部组织可能连带影响 Agent。

现在两种 Controller 共同使用 [api_transport.py](../../zhiheng-fastapi-backend/plugins/ai/public/api_transport.py)。HTTP 规则已有独立入口和测试，原 Controller 只保留路由、业务调用与结果处理。

| 公共函数 | 负责的行为 | 调用方责任 |
| --- | --- | --- |
| `authenticate_request` | 仅接受单 Authorization 头，通过已有开发密钥认证器取得身份；重复头不转发任何密钥 | 构建认证器、控制功能开关；认证器继续负责原有数据库读取和事务结束 |
| `read_json_request` | Content-Type、Content-Length、逐块字节上限和传入的 Pydantic schema 校验 | 处理空结果与取消状态；决定对应业务请求类型 |
| `error_response` | 固定公开错误体、真实 HTTP 状态和 401 Bearer challenge | 选择领域错误及业务错误映射 |
| `streaming_response` | SSE 响应头、原始字节转发、开始迭代后的流关闭；保留取消/读取异常优先级 | 提供支持异步 bytes 迭代和 `aclose` 的流 |

`RequestBody` 仅返回经验证的 value 或取消标记；`ByteStream` 使用协议约束，无需消费者绑定某个具体流适配器。传输层没有 Controller、DAO 或数据库装配的直接导入。

保持原有 URL、schema、权限和错误语义，包括请求体超限继续按原路径返回 `invalid_request`/400。Agent 的应用绑定、模型授权先于检索的顺序，数据库回滚位置，以及兼容服务中的额度预留、推理和结算逻辑均未改动。OpenAI Controller 原有的取消结果清理和认证信息不留在取消堆栈中的保护继续保留。

## 验证

- 改动前两个 Controller 测试：27 项通过。
- 新公共传输契约在公共模块缺失时先失败；实现与接入后，公共传输和两个 Controller 共 61 项通过。
- 扩大回归：323 项通过，覆盖 Agent/工作任务、密钥认证、模型授权、调用协调器、配额结算、OpenAI 兼容服务、流适配器及公开 API 集成。
- 独立审查确认迁移的 8 个 helper 函数体在名称等归一化后保持一致，未发现本轮引入的实现问题；独立复验公共传输与 Agent 路由 45 项、原 Controller 取消与认证信息清理 2 项通过。
- 新文件、两个生产 Controller 及 Agent 路由测试 Ruff 通过。涉及的 6 个文件与本轮开始时基线比较无新增诊断；旧 OpenAI Controller 测试仍有 1 个既有 `PLW0108`，不代表全仓 Ruff 通过。
- `git diff --check` 通过。

新增测试检查认证重复头不进入业务调用、请求体声明长度与分块上限、三类额度拒绝仍返回 JSON/429、认证或读取取消不会转换为 503、SSE 响应头与字节原样透传、正常/提前结束均清理已开始消费的流、读取或取消异常优先于关闭异常。导入约束防止 Agent 再次引用 AI Controller；它只覆盖当前范围的静态导入，并非全项目的架构检查系统。

回归命令（在后端目录、已安装测试依赖的环境执行）：

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

配额测试需要 `fakeredis` 的 Lua 支持，即其 `lua` extra 所需的 `lupa`。本机最初缺少该依赖，最小 `EVAL return 1` 也报 unknown command；仅向 `/tmp/zhiheng-agent-transport-qwsb3ti8/test-deps` 安装 `lupa 2.8` 后，最小命令和上述完整回归均通过。项目依赖清单没有因此改变。

本机测试使用 `PYTHONDONTWRITEBYTECODE=1` 与 `PYTHONPATH=/tmp/zhiheng-agent-transport-qwsb3ti8/test-deps:/tmp/zhiheng-cli-test-deps`。本轮前态源码、Ruff 基线及测试日志保存在 `/tmp/zhiheng-agent-transport-qwsb3ti8`；临时目录不是长期交付位置。

## 范围与后续

公开 API 集成测试使用真实 SDK/HTTP 处理、SQLite、模拟 Redis 的 Lua 执行及模拟模型上游。没有验证线上 PostgreSQL 并发或真实外部模型，也未运行本轮未涉及的前端构建。

Agent 的应用 DAO、模型运行时装配、预览调用等仍有后续收拢空间。前端 Work 状态归属、退出清理与共享 SSE 协议属于另一个试点；本轮没有迁表或实现模块独立发布。

改动尚未提交、推送或部署，运行服务未更新。总体状态见[模块边界梳理](../architecture/2026-09-29-module-boundaries.md)。
