# 对话停止后继续发送被阻塞

## 现象与证据

2026-10-10 用户反馈停止生成后继续对话卡住。运行中的后端返回 `409 CHAT_SESSION_BUSY`。

按北京时间核对：09:25:55 的取消请求成功，对应运行正常记录为 `cancelled`；09:25:59 开始的下一次运行在 09:26:33 发生请求取消，日志出现 AnyIO 取消域中断 asyncpg 连接清理。该运行一直停留在 `running`，后续两次发送被 Redis 会话锁拒绝。检查时锁仍有 565 秒 TTL，配置的完整租期为 900 秒。

客户端断连时，Starlette/AnyIO 会继续取消该请求作用域中的异步等待。原先的 `finally` 虽然执行了，数据库保存和 Redis 解锁也可能再次被取消。持久化异常还会直接跳过后续的解锁语句。

## 修复

修改运行服务对应工作区 `/home/ja/project/zhiheng` 中的两个文件：

- `plugins/ai/service/platform_chat_service.py`：建立运行失败时的收尾，以及流式生成结束后的收尾，放进 `anyio.CancelScope(shield=True)`。保存结束状态失败时仍尝试释放会话锁；正常 `done` 及错误终止事件在保存和解锁之后发送。屏蔽范围不包含 `yield`。
- `plugins/ai/infrastructure/litellm_client.py`：在上游 HTTP 流生成器自己的 `finally` 中保护响应关闭操作，避免嵌套生成器提前退出后无法再次清理连接。

取消与 `GeneratorExit` 在收尾后继续传播；没有放宽会话并发限制、缩短锁 TTL 或删除用户消息。

## 验证

新增 `tests/plugins/ai/test_platform_chat_cancellation.py`，覆盖取消作用域中的关闭、等待 token 时取消、结束事件顺序、持久化失败、建立运行时取消、Starlette 断连后续发，以及 HTTPX 上游响应关闭。最初的 5 项状态/锁测试及后补的 HTTPX 关闭测试均在修复前按预期失败。

相关回归共 **218 passed**，Ruff 检查与格式检查通过。独立代码审查未发现阻断或重要问题。

候选镜像另通过真实 PostgreSQL/Redis 集成检查：使用临时独立 schema、随机会话 ID、真实 ORM/DAO/ModelService/LiteLLM 适配器和 Starlette 中间件，模拟上游 SSE 数据。断连后保存部分回答，运行状态为 `cancelled`，锁消失，上游响应已关闭；同一会话下一次请求返回完整回答并记录为 `completed`。测试 schema 和测试锁已清理；测试不调用真实供应商、不产生模型费用。

## 部署与遗留状态恢复

在已运行的 `zhiheng-backend:probe-fix-20260930-17da38d07eee` 镜像上仅替换上述两个 Python 文件，生成 `zhiheng-backend:chat-cancel-fix-20261010-1148afe7b4c2`。更新当前工作区 `.env` 的后端镜像配置，仅重建后端容器，并重新加载 Nginx。

旧进程退出后，确认目标会话没有其他活动运行、旧锁已过期且不存在新锁，才将已确认遗留的运行标记为 `interrupted`，记录 `CHAT_CONNECTION_INTERRUPTED`，保留原有会话和消息。该旧运行未成功保存的临时生成内容无法从运行状态记录中重建。

上线后确认：运行中代码哈希与候选文件一致，后端健康，未结束聊天运行数量为 0，本机页面、后端文档和 FRP 公网入口均返回 HTTP 200。

小型验证与恢复记录位于 `/home/ja/.local/state/zhiheng-chat-validation/cancel-20261010-bskkd1hw/`。其中环境配置备份为私有文件，包含实例配置，不应输出或提交。未生成离线镜像归档；此前保留的发布包未更新。
