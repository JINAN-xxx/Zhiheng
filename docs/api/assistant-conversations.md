# 私人智能助手与正式对话

所需迁移版本为 0040_assistant_agents。助手只维护一份最新配置与启用状态，保存后用于下一次调用。配置调试无需启用；正式会话与外部 API 要求启用。

## 配置与操作

配置包括模型、提示词、欢迎语、温度、输出长度、技能和知识库。输出长度必须符合模型配置。启用和停用使用 POST /api/agents/{agent_id}/enable 与 /disable，请求携带当前 expected_version，防止并发覆盖；它不是可选择的历史发布版本。

私人使用不要求开发者应用；外部 API 仍要求应用、原 Key、agents:invoke、模型授权和限额。助手不再维护草稿/发布两套配置，也不提供 history_rounds 设置。

已受理的 Run 保存实际输入、模型绑定、技能修订、来源和费用事实。更新配置不能改写正在执行的请求。知识准备期间依赖变化会要求重新发送。停用后不接纳新正式调用，排队调用在执行前复核启用状态与权限。

## 会话与记忆

会话按用户、助手和会话隔离。管理员不自动获得其他用户的私人会话。欢迎语仅展示，不发送给模型，不产生推理费用。

正式会话使用持续 checkpoint 和自动摘要，保存完整业务问答。系统按模型输入容量整理较早历史并保留近期原文；摘要和主回答各自记录模型用量。配置调试和外部 API 每请求独立，调用方提交自己的 messages，不会共享服务器隐式记忆。

知识检索使用已有持久准备链，检索原文只作为本轮参考资料，不累积进用户问题。历史回答与摘要涉及的知识来源也会复核权限；资料被撤权或移出配置后，请新建会话。技能提供冻结指令，上传脚本不会自动执行。

刷新、断线、关闭页面后只恢复观察原 Run，不重新调用模型。相同请求标识返回原结果，内容变化返回冲突；同一会话不同时受理两个主回复。

## 网页接口

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| GET | /api/agents/{agent_id}/conversations | 本人的会话列表，支持 before 游标 |
| POST | /api/agents/{agent_id}/conversations | 使用 client_request_id 幂等创建 |
| GET | /api/agents/{agent_id}/conversations/{conversation_id} | 会话及问答，支持更早消息游标 |
| PATCH | /api/agents/{agent_id}/conversations/{conversation_id} | 使用 title 和 expected_version 重命名 |
| DELETE | /api/agents/{agent_id}/conversations/{conversation_id} | 使用 expected_version 删除，成功返回 204 |
| POST | /api/agents/{agent_id}/conversations/{conversation_id}/runs | 提交 message 和 client_request_id，返回 Run 或知识准备信息 |

运行查询、SSE 与停止继续使用 /api/runs。会话 DTO 的 version 用于并发修改校验；memory_available 表示能否继续新版记忆，summary_used 表示已接受记忆是否使用过摘要。回合保存助手名称与对应 Run，不再携带发布版本。

浏览器保存待确认提交的原标识。受理结果未知时“确认原提交”沿用原命令，正式收到受理信息后清除待确认记录。发送后立即显示临时问题，检索、等待与失败显示在对应助手消息中；preparing_message 和 request_id 用于刷新恢复与去重。

## 删除与升级

删除要求当前会话没有正在检索、排队或生成的主回复。已删除会话从列表、详情和对应内容读取入口隐藏；原创建请求不能恢复它。后台独立回收 SDK 状态，原 Run、审计与账务保留。

升级保留旧问答和精确调用快照。旧会话只读，新建会话使用自动记忆；不自动导入或清空历史。迁移前完成或明确取消活跃助手/Work 运行与知识准备，备份数据库，再统一升级 API 与 Worker。

执行、摘要计费和存储边界见[智能助手执行与记忆](../architecture/assistant-langchain.md)。
