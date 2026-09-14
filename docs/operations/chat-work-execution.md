# 聊天与工作实现记录

基线：`ac31bef7`。工作分支：`feat/chat-work-dual-mode`。

后续任务按可验证的提交递进；已获用户授权的实现不重复确认。

用户已确认中心模式切换、手动选择多个已发布智能体、独立任务历史，以及任务目标、生命周期和来源标记。

## 执行进度

- [x] 任务 1：ORM、VO 和双数据库迁移（71affd88、c24abb6f；59 项定向测试及两阶段审查通过）
- [x] 任务 2：任务与成员服务（8b2db169；随任务 3–4 完成两阶段审查）
- [x] 任务 3：流式运行、取消和恢复（8b2db169、8dce790c；280 项相关回归通过）
- [x] 任务 4：内部接口、权限和插件接入（8b2db169；权限、资源复检与敏感配置隔离审查通过）
- [x] 任务 5：聊天空态与模式切换（3dab664c；定向测试、SFC 编译和两阶段审查通过；工作路由随任务 6 接入）
- [ ] 任务 6：工作 API、状态和独立历史
- [ ] 任务 7：工作任务页面
- [ ] 任务 8：联调、审查和验收

## 代码核查后的实现约束

- `resolveWorkspaceMode` 保持 `ai/admin` 布局语义；另设聊天/工作模式判断，避免工作页渲染管理后台。
- `/ai/work` 的路由注册必须纳入登录后的可访问路径校验，不能只加入路由器后被工作台守卫重定向。
- 服务端同一任务串行运行，使用数据库行锁或原子更新以及持久化取消状态，避免只在单个进程内互斥或取消。
- 幂等请求重放须先校验所有权和内容一致性；用户主动重试与网络重传区分，失败历史不可丢失。
- 每次运行重新检查智能体发布状态及资源可用性；冻结配置选择，技能正文和知识库数据保持实时。
- 工作任务成员响应不包含配置正文；智能体目录使用已发布快照中的名称和描述。
- 创建任务幂等键为所有者与 `createClientMessageId`，消息幂等键为任务与 `clientMessageId`。
- 归档任务只读；已完成任务可重新打开；运行期间阻止目标、成员版本及任务生命周期变更。
- 旧聊天知识库 ID 保留只读标记，新聊天不加载知识库目录或提交绑定。

## 基线验证

前端插件契约 29 组通过，后端智能体扩展 161 项通过。运行中的 Docker 服务不在本次迁移测试写入范围内。

## 后端复审记录

任务 2–4 已在 `8b2db169` 实现，`8dce790c` 修复审查发现的取消并发与迟到创建消息问题。相关回归 280 项通过，独立复审另运行 6 项关键测试通过，规格与代码质量审查均通过。

- 取消成功只代表已记录停止请求；前端需等 `currentRunId` 清空再开放下一次执行。
- 运行重放的 `done.status` 可能仍为 `pending` 或 `streaming`；此时继续轮询详情，不能将其当作已完成。
- 迟到且尚未执行的首次创建消息返回 409；已有运行的同键网络重放继续保持幂等。

## 前端验证记录

前端实现提交为 `84e822a0`，审查修复提交为 `65e3e569`，独立规格与质量审查正在收口。

- `npm run test:plugin`：31 组通过，包含 9 个实际 Pinia 场景：版本升级、断流刷新、迟到取消、退出登录隔离、筛选保持、创建失效和运行恢复等。
- `tests/e2e/chat_work_mode_check.py`：桌面创建失败恢复、聊天技能、多智能体接续、历史切换和 390px 任务页通过。
- `tests/e2e/work_access_check.py`：5 个场景通过；覆盖查询权限深链、缺少智能体使用权限、无工作权限、320px 长标题/目标/成员及按钮横纵向边界。
- `tests/e2e/work_task_actions_check.py`：3 个场景通过；覆盖成员失效、显式升级后旧回复版本不变、移除后历史保留、完成/重开/归档/删除，以及活动重放、取消后等待 claim 释放和刷新恢复部分输出。真实失败流保留输入与失败历史，主动重试发送新的幂等键。
- 首次生产构建成功；构建期间追加了提示文案，最终版本将在审查修复完成后重新构建。

浏览器验收使用真实前端与拦截的 API，后端持久化与权限由数据库会话及接口测试验证。截图位于 `/tmp/chat-work-acceptance/`。

后端 280 项回归的完整命令（在 `ruoyi-fastapi-backend` 执行）：

```bash
pytest -q --tb=short --show-capture=no tests/plugins/agent_extension tests/plugins/sample_plugins/test_agent_extension_plugin.py tests/plugins/ai/test_platform_chat_service.py tests/plugins/ai/test_platform_chat_controller.py tests/plugins/ai/test_platform_chat_dao.py tests/plugins/ai/test_platform_chat_schema.py tests/plugins/ai/test_platform_chat_provider.py tests/plugins/ai/test_skill_context_adapter.py
```

## 发布接入

本分支需同时发布前后端。智能体扩展清单版本为 `0.4.0`，新增三个工作任务表以及工作权限；沿用插件升级流程执行对应数据库的 `004_work_tasks.sql`，不能只替换前端静态资源。

普通使用者需要 `work:list`、`work:query`、`work:add`、`work:run` 和 `agent:use`（均带 `agent_extension:` 前缀）；编辑和删除分别使用 `work:edit`、`work:remove`。只读角色可以仅授予列表和查询权限。管理员通配权限继续有效，权限更新后重新登录获取新权限。

2026-09-14 核查：`12580` 与 `19099` 为现有 Docker 服务；隔离工作区的 Vite 监听 `127.0.0.1:12584`，仅用于拦截 API 的浏览器验收，原 `9099` 开发代理没有运行。此记录不代表新功能已经部署到 `12580`。
