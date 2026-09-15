# 对话归档实现计划

> 使用 subagent-driven-development 执行独立后端任务，主代理完成前端与集成；用户已批准设计并授权实现，无需重复请求执行确认。

**目标：** 完成对话归档、归档列表、历史查看与恢复，保留消息和已有删除功能。

**架构：** 复用会话 status 字段；后端暴露状态更新与过滤分页，前端以现有 Workspace Store 驱动侧栏和阅读页。

**技术栈：** FastAPI、SQLAlchemy、pytest、Vue 3、Pinia、Ant Design Vue、Node 测试、Playwright。

## 任务 1：后端状态与查询

文件：`ruoyi-fastapi-backend/plugins/ai/{entity/vo/ai_platform_chat_vo.py,dao/platform_chat_dao.py,service/platform_chat_service.py,controller/ai_chat_controller.py}`。
测试：`ruoyi-fastapi-backend/tests/plugins/ai/test_platform_chat_{dao,service,controller}.py`。

- [x] 添加归档隔离、状态变更保留消息、越权、生成中保护、幂等和无效参数测试，运行确认因缺少功能失败。
- [x] 查询增加 `status='active'`，DAO 在分页前过滤所有权和状态。
- [x] 实现 `set_session_status(user_id, session_id, status)`，使用现有行锁并检查运行状态，更新成功返回公开会话模型。
- [x] 增加 PATCH status 路由和审计；归档会话发送返回明确错误。
- [x] 运行 `python -m pytest -q tests/plugins/ai/test_platform_chat_dao.py tests/plugins/ai/test_platform_chat_service.py tests/plugins/ai/test_platform_chat_controller.py`，以及修改文件的 Ruff 检查。

## 任务 2：前端状态管理

文件：`ruoyi-fastapi-frontend/src/api/platformWorkspace.js`、`src/store/modules/workspace.js`。
测试：`ruoyi-fastapi-frontend/tests/plugins/chatArchiveStore.test.js`。

- [x] 使用真实 Pinia 加网络替身验证状态筛选、分页、归档/恢复、迟到查询、操作失败和当前页回退，先运行失败测试。
- [x] API 增加 status 请求与分页参数；Store 增加状态筛选、分页和已确认的会话变更通知。
- [x] 操作成功失效旧列表请求并刷新，操作失败保留现有状态；避免按钮重复操作。
- [x] 运行 `node tests/plugins/chatArchiveStore.test.js`。

## 任务 3：侧栏与归档阅读

文件：`ruoyi-fastapi-frontend/src/layout/components/{RecentSessions,PlatformSidebar}.vue`、`plugins/ai/views/chat/index.vue`。

- [x] 最近会话区域增加已归档切换与分页，按当前状态显示归档或恢复菜单项。
- [x] 归档当前对话返回新对话，创建新对话切回最近会话；永久删除保持确认。
- [x] 详情加载读取 session.status；归档对话显示历史与恢复入口，恢复后可继续发送。
- [x] 用 Playwright 对真实前端加隔离接口替身验证完整交互和窄屏布局。

## 任务 4：审查与交付

- [x] 审查规格覆盖，再审查代码质量与并发边界，修复发现的问题。
- [x] 运行后端聊天相关测试、`npm run test:plugin`、`npm run build:docker -- --outDir /tmp/chat-session-archive-dist`。
- [x] 将验证后的源码和文档同步回当前工作区，保持用户已有移动端删除与前端构建产物。
- [x] 记录功能入口、验证结果和未发布状态。

## 验证记录

- 后端聊天 DAO、Service、Controller、Schema、Provider、Coordinator：96 项通过。
- 前端插件测试：33 组通过，包含列表请求竞态和归档详情恢复竞态。
- Playwright：桌面、375px 手机、延迟详情恢复三个场景通过；恢复后使用原会话和模型完成流式消息调用。接口使用隔离测试替身。
- Docker 模式生产构建通过；首次默认 2GB Node 堆不足，使用 `NODE_OPTIONS=--max-old-space-size=4096` 后成功。最终构建产物再次通过以上浏览器验证。
- 修改的后端文件 Ruff 通过，Git 差异空白检查通过。
- 规格独立审查、代码质量复审均通过。
- 数据库验收范围按用户最新确认收敛为 PostgreSQL；未新增 MySQL 兼容测试维护入口。
- 功能提交 `03b781b3` 已快进合入 `release/platform-integration`；合入后后端 96 项、前端 33 组测试再次通过。随后已发布到 `12580`，入口位于侧栏会话菜单及“已归档”标签，详见 [发布记录](../../operations/2026-09-15-chat-session-archive-release.md)。
