# 模块边界首批试点记录

日期：2026-09-29。工作区：`/home/ja/project/zhiheng`，分支：`fix/platform-integration-review`，基于 `ec860293`，包含前序尚未提交的修复。

## 已实现的范围

本次完成用户同意的两个小试点，为后续模块内修改提供清晰的公共入口。项目仍整体构建、整体发布，尚未部署本轮源码改动。

| 试点 | 唯一实现入口 | 消费者与兼容方式 |
| --- | --- | --- |
| 前端聊天会话 API | [chatSessions.js](../../frontend/plugins/ai/public/chatSessions.js) | 原 `plugins/ai/api/chat.js` 和 `src/api/platformWorkspace.js` 直接 re-export；旧函数名与调用位置兼容 |
| 后端模型查询 | [model_queries.py](../../backend/plugins/ai/public/model_queries.py) | Agent 配置校验、公开调用、工作任务执行、知识库模型选项通过公共函数查询 |

前端入口统一列表、详情、重命名、归档/恢复和删除请求，使用同一个共享 request，保持原认证、加密、响应与异常行为。会话 ID 在入口统一编码，既有聊天文件接口和业务状态机继续使用原逻辑。

后端返回不可变的字段快照，不向消费者暴露 ORM 或引擎配置。`get_published_chat_model` 保留聊天类型、启用、已发布的条件；`list_active_bound_models` 保留知识库原有的类型与启用绑定筛选及名称排序，没有增加发布时间条件。这两个函数不替代调用授权或上游健康检查。

查询复用调用方的数据库会话，不提交或回滚事务。原错误码、HTTP 状态、输出 token 上限、权限检查、URL、插件 ID 和表结构保持原语义。

## 验证结果

| 检查 | 结果与覆盖范围 |
| --- | --- |
| 新模型查询测试 | 13 项通过；真实 SQLite 查询覆盖类型、发布/启用/绑定规则、排序、不可变快照、会话关闭后读取、调用方提交/回滚，以及限定消费者的模型持久化导入约束 |
| 后端相关回归 | 431 项通过；Agent/技能/工作任务、模型目录和服务、知识库 Controller/runtime、插件运行时。不是后端全部测试 |
| 前端插件检查 | `npm run test:plugin`：48 个测试文件通过；其中 Node 子测试 9/9。包含原归档、详情竞态、文件 API 和状态管理测试 |
| 前端隔离构建 | 在临时源码副本中运行 `npm run build:docker`，退出码 0；未覆盖运行服务使用的 `dist` |
| 构建文件核对 | 仓库原 `dist` 的 1,460 个文件逐一比较 SHA-256，前后相同 |
| 独立审查 | 公开契约、事务边界、消费者错误映射和前端真实 ESM 测试加载方式通过复核 |
| Ruff | 新查询与测试文件通过；本轮 11 个后端相关文件与 HEAD 比较无新增诊断，旧文件仍有既有问题，不代表全仓 Ruff 已通过 |
| 差异检查 | `git diff --check` 通过 |

新增契约测试曾先在缺少公共入口时失败，再验证实现通过。前端测试加载真实 ESM 导出，仅替换共享 HTTP 请求边界，检查兼容导出的函数身份、分页参数、路径编码、状态变更、Promise/响应/异常透传。

后端回归命令（在 `backend`、已安装测试依赖的环境中执行）：

```bash
python3 -m pytest -q -p no:cacheprovider \
  tests/plugins/agent_extension \
  tests/plugins/ai/test_model_queries.py \
  tests/plugins/ai/test_model_catalog_dao.py \
  tests/plugins/ai/test_model_catalog_service.py \
  tests/plugins/ai/test_model_service.py \
  tests/plugins/ai/test_knowledge_controller.py \
  tests/plugins/ai/test_knowledge_runtime.py \
  tests/server/test_plugin_runtime.py \
  --tb=short --show-capture=no
```

本机使用已有临时依赖目录 `/tmp/zhiheng-cli-test-deps`，没有修改项目依赖清单。前端构建与日志位于临时目录 `/tmp/zhiheng-module-pilots-ndujsw51`，不作为发布产物保管。

## 回归中处理的既有测试问题

扩大回归时，工作任务 EOF 测试偶发把预期 `interrupted` 判成 `failed`。临时诊断捕获到 `_persist` 在 commit 时抛出 SQLite `database is locked`；并发的 watcher 读事务与写提交竞争，最终触发原有失败兜底。

将 HEAD 后端完整导出到临时目录后，旧实现和旧测试也在第 3 次运行复现同一锁错误。本轮公开查询并不参与该测试的 fake loader 路径。

修正仅在共享 `work_db` 测试夹具建表前启用 SQLite WAL，避免读者阻塞写提交。保留多连接池、原并发调用和全部状态断言，没有修改生产任务状态机或部署数据库配置。修正后 EOF 连续 5 次通过，随后 431 项回归通过；独立复验两个并发用例与 Work Controller 共 4 项通过。最后调整测试 fixture 的显式导入后，该工作任务文件 16 项通过。

这些结果用于验证本轮代码契约与 SQLite 测试行为，不等于完成 PostgreSQL 并发、真实模型上游或端到端部署验收。

## 后续边界

本轮没有完成所有模块隔离。Agent 对应用 DAO 与公开 API Controller 私有函数的依赖、模型费率归属、资源删除引用规则、Work store 所有权、退出清理及共享 SSE 协议仍需逐项收拢。公共接口变更仍应回归相关消费者；模块内部实现调整可先从所属模块测试开始。

当前改动保留在本地工作区，未提交、未推送，运行服务未更新。完整基线和已完成状态见[模块梳理](../architecture/2026-09-29-module-boundaries.md)。
