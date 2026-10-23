# 模块边界首批试点实施计划

> 面向 AI 代理的工作者：使用 subagent-driven-development 分工，采用 test-driven-development 验证新增契约，完成后独立复核。

**目标：** 完成用户同意的两个小试点：统一前端聊天会话 API，公开后端模型可用性查询。

**架构：** 保持单体运行和现有 HTTP、权限、表结构、插件 ID。前端由 AI Chat 的公开客户端拥有会话请求定义，旧入口提供兼容转发。后端模型查询返回不可变数据快照，复用调用方会话但不提交或回滚事务；消费者保留自身错误映射。

**范围约束：** 不迁表、不改授权/计费流程、不修改运行服务。保留当前工作区已有改动；验证构建输出放临时目录，不能覆盖正在服务的 dist。

## 1. 聊天会话公开客户端

文件：新建 `frontend/plugins/ai/public/chatSessions.js`；调整 `plugins/ai/api/chat.js`、`src/api/platformWorkspace.js` 及必要的消费者；测试位于 `tests/plugins/`。

- [x] 为列表分页、详情、重命名、归档/恢复、删除、路径编码、响应/异常透传添加行为测试，先看到缺失入口导致失败。
- [x] 会话的 `/ai/chat/session/*` 请求统一归公开客户端；继续使用已有共享 request，保持认证、加密和响应结构。
- [x] 旧 API 名称兼容转发，聊天和侧栏使用同一契约；不移动或改写已有状态机。
- [x] 原归档、详情竞态、文件 API 和前端整体检查通过；构建到隔离临时目录。

## 2. 只读模型查询接口

文件：新建 `backend/plugins/ai/public/model_queries.py` 及包初始化文件；接入 `agent_service.py`、`agent_invocation_controller.py`、`work_task_run_service.py`、`ai_knowledge_controller.py`。

公开契约：

```python
async def get_published_chat_model(db, model_id) -> PublishedChatModel | None: ...
async def list_active_bound_models(db, *, model_type) -> list[ModelOption]: ...
```

`PublishedChatModel` 仅暴露 model_id、model_code、max_output_tokens；`ModelOption` 暴露 model_id、display_name、model_code。返回值不得携带 ORM 对象或凭据。

- [x] 用真实 SQLite 会话覆盖模型缺失、类型、active 状态、published_at、binding 状态及列表排序，先验证失败。
- [x] 聊天查询保持“chat + active + 已发布”；知识库选项保持原有“类型匹配 + active 模型 + active binding”，不额外增加原先没有的 published_at 条件。
- [x] 查询不 commit/rollback，测试调用方能够提交或回滚自己尚未完成的写入；快照在会话结束后仍可使用。
- [x] 替换调用点的模型 DAO/ORM 依赖，保留各自 HTTP 状态、错误码、输出 token 限制及事务结束时机。
- [x] 增加本试点范围的依赖边界检查，防止调用点重新导入模型 DAO/ORM。

## 3. 验收与记录

- [x] 运行新查询测试、Agent/工作任务/知识库相关回归、前端整体检查及临时构建。
- [x] 独立复核契约、权限、事务和范围，修复发现的问题。
- [x] 更新模块梳理文档和试点记录，清楚区分已收拢与仍待处理的边界。
- [x] 保留本地改动，不推送、不自动部署。
