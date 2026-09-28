# 最近会话重命名与永久删除实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为最近会话增加重命名，并将现有归档操作改为不可恢复的永久删除。

**架构：** 后端在会话所有权锁内完成标题更新或关联记录删除，删除顺序遵守现有外键限制。前端由侧栏负责输入和确认，状态仓库只在接口成功后更新列表。

**技术栈：** FastAPI、SQLAlchemy、Pydantic、Vue 3、Pinia、Ant Design Vue、Pytest、Node 合约测试、Playwright。

---

### 任务一：定义会话修改接口契约

**文件：**
- 修改：`zhiheng-fastapi-backend/plugins/ai/entity/vo/ai_platform_chat_vo.py`
- 修改：`zhiheng-fastapi-backend/plugins/ai/controller/ai_chat_controller.py`
- 测试：`zhiheng-fastapi-backend/tests/plugins/ai/test_platform_chat_dao.py`
- 测试：`zhiheng-fastapi-backend/tests/plugins/ai/test_ai_chat_controller.py`

- [ ] 编写失败测试：标题为空、超过 200 字或包含控制字符时校验失败；控制器包含 `PATCH /session/{session_id}`。
- [ ] 运行目标测试，确认因 `PlatformSessionRenameRequestModel` 和接口缺失而失败。
- [ ] 新增严格请求模型：

```python
class PlatformSessionRenameRequestModel(_StrictRequestModel):
    title: Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=200)]
```

- [ ] 控制器增加重命名接口，现有删除接口响应改为“会话已删除”。
- [ ] 运行目标测试并提交：`feat: 增加会话重命名接口`。

### 任务二：实现事务重命名与永久删除

**文件：**
- 修改：`zhiheng-fastapi-backend/plugins/ai/dao/platform_chat_dao.py`
- 修改：`zhiheng-fastapi-backend/plugins/ai/service/platform_chat_service.py`
- 测试：`zhiheng-fastapi-backend/tests/plugins/ai/test_platform_chat_service.py`
- 测试：`zhiheng-fastapi-backend/tests/plugins/ai/test_platform_chat_dao.py`

- [ ] 编写失败测试：重命名提交标题；删除按运行、消息、会话顺序执行；运行中会话拒绝删除；失败时回滚。
- [ ] 运行目标测试，确认因服务方法与删除方法缺失而失败。
- [ ] DAO 增加：

```python
delete(AiChatRun).where(AiChatRun.session_id == session_id)
delete(AiChatMessage).where(AiChatMessage.session_id == session_id)
delete(AiChatSession).where(AiChatSession.session_id == session_id, AiChatSession.user_id == user_id)
```

- [ ] Service 增加 `rename_session()`，并把 `archive_session()` 替换为 `delete_session()`；两者复用所有权锁和事务处理。
- [ ] 运行聊天 DAO、Service 和控制器测试并提交：`feat: 永久删除平台会话`。

### 任务三：实现前端重命名与删除交互

**文件：**
- 修改：`zhiheng-fastapi-frontend/src/api/platformWorkspace.js`
- 修改：`zhiheng-fastapi-frontend/src/store/modules/workspace.js`
- 修改：`zhiheng-fastapi-frontend/src/layout/components/RecentSessions.vue`
- 修改：`zhiheng-fastapi-frontend/src/layout/components/PlatformSidebar.vue`
- 测试：`zhiheng-fastapi-frontend/tests/plugins/platformShellContract.test.js`
- 测试：`zhiheng-fastapi-frontend/tests/plugins/workspaceTransforms.test.js`

- [ ] 编写失败合约测试：菜单包含“重命名”“删除会话”，接口使用 PATCH 和 DELETE，状态仓库具有 `renameSession` 与 `deleteSession`。
- [ ] 运行测试确认失败。
- [ ] 增加重命名、删除请求和仓库动作；只有请求成功后才更新本地列表。
- [ ] 侧栏处理输入弹窗和不可恢复删除确认；删除当前会话后跳转 `/ai/chat`。
- [ ] 运行全量插件测试并提交：`feat: 增加最近会话管理操作`。

### 任务四：构建、部署与真实验收

**文件：**
- 修改：`zhiheng-fastapi-frontend/tests/e2e/platform_shell_check.py`

- [ ] 更新浏览器模拟接口，支持 PATCH 重命名与 DELETE 删除。
- [ ] 运行后端目标测试、Ruff 和前端 15 组插件测试。
- [ ] 运行 `NODE_OPTIONS=--max-old-space-size=4096 npm run build:docker`。
- [ ] 构建可回滚前后端镜像，只重建前端与后端容器。
- [ ] 使用真实会话执行“重命名 → 刷新确认 → 删除 → 数据库确认关联记录为零”。
- [ ] 运行真实浏览器检查，确认删除当前会话后进入新对话，无控制台和接口错误。
- [ ] 提交验收更新：`test: 验证会话重命名与永久删除`。
