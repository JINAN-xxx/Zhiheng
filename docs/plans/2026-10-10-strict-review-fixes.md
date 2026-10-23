# 严格代码审查问题修复与验收计划

**目标：** 修复 `c062464c` 审查确认的 6 项问题，逐项保留失败复现、修复后的回归结果及最终集成验收记录。

**架构：** 保持现有模块化单体和公开接口。流式资源由显式生命周期负责收尾；发布恢复保留合法插件启停状态，同时继续拒绝版本/迁移不一致；保留业务数据的插件继续承担资源引用检查义务。

**技术栈：** FastAPI / Starlette / AnyIO、SQLAlchemy、Redis Lua、Vue / Pinia、Python 镜像发布管理器。

**工作位置：** 按用户既定要求在 `/home/ja/project/RuoYi-Vue3-FastAPI` 的 `release/platform-integration-merge` 分支实施。修改源码、测试和相关文档；本计划的验收不操作生产数据，不生成大型离线镜像归档。

审查复现及根因已完成，用户已授权逐项修复。每项执行 TDD：新增行为测试 → 确认当前实现失败 → 最小修复 → 相关回归 → 独立审查。任务完成情况在本文更新。

## 1. 公开 API 取消收尾

- [x] 在 `backend/tests/plugins/ai/test_public_invocation_coordinator.py` / 新的流式取消测试中加入实际 AnyIO 取消域、Starlette 断连和结算断点场景。验收断言：账本终态为 `cancelled`、并发计数回到 0、同一 Key 可以再次请求。
- [x] 修改 `plugins/ai/service/public_invocation_coordinator.py`：保护关闭、配额结算和账本写入；终止标记与清理状态保持一致，重复关闭不重复结算。核对流建立失败和未开始迭代时的资源所有权。
- [x] 核对 `plugins/ai/public/api_transport.py` 的响应结束路径，保证请求取消时清理能够执行，读取异常不被清理异常覆盖。
- [x] 运行相关回归：121 项通过；Ruff 通过；规格与质量独立审查通过。真实 PostgreSQL/Redis 的 12 个流式生命周期场景通过。独立审查发现的结算重试终态覆盖问题也已加入红绿回归并修复。

## 2. 发布与恢复竞态

- [x] 将审查中的旧应用运行期间切换 `agent_extension` 启停状态场景加入 `tests/deploy/test_release_enablement_race.py`；覆盖正常升级、升级失败恢复与 pending deploy 重试。
- [x] 修改 `deploy/release/manager.py`，在停止旧应用后重新确定受保护的恢复基线；对未完成发布恢复保留合法启停状态。不得放宽插件版本、迁移、实例身份及数据兼容检查。
- [x] 运行 `python3 -m pytest -q -p no:cacheprovider tests/deploy`，验证历史中断恢复场景仍通过，并完成独立审查。

## 3. 普通聊天首次迭代前断连

- [x] 在 `tests/plugins/ai/test_platform_chat_cancellation.py` 加入结果创建后立即关闭、响应头发送时断连、重复关闭、保存失败仍解锁的行为测试。
- [x] 修改 `plugins/ai/service/platform_chat_service.py` 与 `plugins/ai/controller/ai_chat_controller.py`：显式拥有已获取的会话锁和 pending 运行，收尾不依赖异步生成器第一次迭代；所有路径最多收尾一次。
- [x] 验收断言：运行成为中断/取消终态、Redis 会话锁消失、同会话下一次发送成功；继续保持终止事件在提交与解锁之后输出。
- [x] 运行 `python3 -m pytest -q -p no:cacheprovider tests/plugins/ai/test_platform_chat_cancellation.py tests/plugins/ai/test_platform_chat_service.py tests/plugins/ai/test_platform_chat_controller.py`，完成独立审查。

## 4. 活动会话删除保护

- [x] 在 `tests/plugins/ai/test_platform_chat_service.py` 中增加“旧 running / 新 failed”及“旧 pending / 新 completed”场景，要求删除返回 `CHAT_SESSION_BUSY`，不改变消息与运行。
- [x] 修改 `PlatformChatService.delete_session()`，结束旧只读快照、锁定所属会话后调用 `has_active_run()` 检查所有活动运行。无活动运行时正常删除；不存在和越权仍返回既有错误。
- [x] 加入真实 SQLAlchemy DAO 回归；执行聊天相关测试，核对与发送和归档的锁顺序，并完成独立审查。

## 5. 安全卸载后的持久引用

- [x] 将“已安装 → 安全卸载保留数据 → 重建注册表 → 删除模型 → 重新安装”加入核心资源引用和 AI 模型删除测试。保留引用时删除必须拒绝；从未安装插件不得查询不存在的表。
- [x] 在 `plugins/core/management/entity/do/models.py` 新增独立的资源归属表，并提供 DAO 与生命周期写入。记录确认从未安装、保留数据、历史未知三类状态及资源回调快照；元数据清理不删除这份记录。现有字段无法可靠区分旧版安全卸载与从未安装，不能靠可清理的审计日志或任意迁移成功记录猜测。
- [x] 修改 `plugins/core/runtime/resource_references.py`，在调用方事务中只读汇总持久归属与当前注册表。保留数据但缺源码/声明/回调时拒绝不完整检查；未知旧状态保守拒绝，完整重装可恢复可信状态；新发现的从未安装插件不查询业务表。
- [x] 同步初始化 SQL、提供 PostgreSQL/MySQL 幂等增量建表 SQL。服务启动的 `create_all` 创建新表；CLI 独立运行前需先完成建表，不在引用检查事务里执行 DDL。
- [x] 运行核心注册表、生命周期、引用端口及 AI/Agent 模型引用测试；验证卸载并未被变相重新启用、公开端口不提交调用方事务，并完成独立审查。

## 6. 登录路由失败传播

- [x] 新增 `frontend/tests/plugins/permissionRouteFailure.test.js`：菜单 HTTP 500、超时、路由转换异常均使 `generateRoutes()` reject；失败后可重试成功，成功路由内容和注册次数保持正确。
- [x] 修改 `src/store/modules/permission.js`，直接返回请求 Promise 链或使用 async/await，把错误交给已有导航守卫恢复逻辑；核对 `src/views/login.vue` 在守卫重定向回登录页时也能结束 loading 并再次提交。
- [x] 运行 `npm run test:plugin`，进行受控浏览器菜单失败与恢复验收，检查导航/权限回归，并完成独立审查。

## 7. 集成与文档

- [x] 更新后端目录说明中与上述 6 项相关的已实现机制，保留仍未实现的锁续租、硬退出孤儿扫描和容量限制。
- [x] 执行后端完整测试（真实数据库并发用例使用隔离 PostgreSQL）、前端插件测试、Ruff、必要的浏览器与构建检查。
- [x] 在隔离环境验证真实 PostgreSQL/Redis 的公开流取消及聊天续发；发布用例验收覆盖状态机和恢复探针，清楚记录是否包含真实 Docker 演练。
- [x] 将结果写入 `docs/operations/2026-10-10-strict-review-fixes.md`，记录执行范围、通过数量、跳过项和上线状态；检查 diff 与文档链接。


## 验收完成记录

六项修复均完成规格与代码质量独立复核，复核发现的重试终态、矛盾安装证据及并发清理漏查也已补红绿回归并修复。
最终结果：后端 3539 passed、5 skipped、2 warnings；前端 57 个测试文件；浏览器辅助器 14 项；Ruff、格式与生成 SQL 一致性检查通过。真实 PostgreSQL/Redis、增量 PostgreSQL/MySQL DDL、源码和构建页面验收的详细范围见 [验收记录](../operations/2026-10-10-strict-review-fixes.md)。
源码与构建产物位于开发仓库，生产数据库和在线服务尚未更新；发布恢复验收使用模拟 Docker 运行时。
