# 后端应用查询边界实施记录

日期：2026-09-29。工作区 `/home/ja/project/zhiheng`，分支 `fix/platform-integration-review`，保留此前未提交改动。

## 改动与规则

新增 [application_queries.py](../../zhiheng-fastapi-backend/plugins/ai/public/application_queries.py)，向消费者提供：

```python
async def is_active_owned_application(db, application_id, owner_user_id) -> bool: ...
```

该查询复用已有应用 DAO 的软删除过滤，再判断所有者和 active 状态。消费者只需要绑定条件判断，因此返回 bool；不额外引入应用 DTO，也不暴露 ORM。它使用调用方会话，没有创建、关闭会话或提交/回滚事务，自动 flush 行为继续由原会话配置决定。

[AgentService](../../zhiheng-fastapi-backend/plugins/agent_extension/service/agent_service.py) 从应用 DAO 注入改为公共查询函数注入。仓库中未发现旧 `application_dao=` 调用者。唯一的绑定查询调用点改为公共函数，其余创建、更新、发布、冻结快照与事务代码保持原语义。

创建按创建者检查；更新和发布按 Agent 原所有者检查，即使操作人是管理员也一样。缺失、软删除、停用和其他所有者应用仍映射为 `AGENT_APPLICATION_UNAVAILABLE`，原英文文案和 HTTP 422 保留。未绑定应用的草稿仍可创建，发布仍返回 `AGENT_APPLICATION_REQUIRED`。

## 验证

| 检查 | 结果 |
| --- | --- |
| 原 AgentService 基线 | 18 项通过 |
| 新入口失败验证 | 新查询与消费者测试先因公共模块缺失失败 |
| 定向查询/AgentService 回归 | 33 项通过，其中新增查询 12 项、消费者 3 项 |
| 扩大后端回归 | 335 项通过；包含 Agent/工作任务、模型/应用公共查询、应用管理、开发密钥、授权及公开 API 集成/传输 |
| Ruff | 本轮四个相关源码/测试文件通过，新查询与测试格式检查通过 |
| 独立复核 | 权限、软删除、事务边界、未绑定和发布语义通过审查；新增 15 项测试独立复验通过 |

新查询测试使用真实 SQLite 应用表，覆盖启用、停用、软删除、其他所有者及缺失。成功/不可绑定两种结果均验证调用方仍可提交或回滚尚未完成的写入。数据库异常和 CancelledError 原样传播，不擅自回滚调用方事务。新增静态检查约束 Agent 不导入 AI 应用 DAO/ORM，实现范围限定于当前消费者。

扩大回归有 1 个 async_lru 跨测试事件循环自动清空缓存的警告，来自既有应用 Controller 审计测试；没有因此修改日志/缓存或放宽测试。

复验命令（后端目录，已安装测试依赖）：

```bash
python3 -m pytest -q -p no:cacheprovider \
  tests/plugins/agent_extension \
  tests/plugins/ai/test_application_queries.py \
  tests/plugins/ai/test_model_queries.py \
  tests/plugins/ai/test_application_service.py \
  tests/plugins/ai/test_ai_application_controller.py \
  tests/plugins/ai/test_public_api_dao.py \
  tests/plugins/ai/test_developer_key_service.py \
  tests/plugins/ai/test_developer_key_authenticator.py \
  tests/plugins/ai/test_api_entitlement_service.py \
  tests/plugins/ai/test_public_api_integration.py \
  tests/plugins/ai/test_public_api_transport.py \
  --tb=short --show-capture=no
```

本机使用 Python 3.12、`PYTHONDONTWRITEBYTECODE=1` 和既有临时依赖目录；日志与前态位于 `/tmp/zhiheng-application-query-v03qjehv`。没有访问运行中的业务数据库或真实模型上游，也没有重新运行本轮未涉及的前端构建。此结果不代表后端全量测试、全仓 Ruff 或云端 CI 全部通过。

## 后续边界

模型删除引用与费率归属仍未实施。只读梳理已确认引用包括 SQL 外键以外的 Agent 草稿/发布配置、Work 冻结快照及知识库 metadata；默认空引用检查器仍存在。后续需同时考虑插件启停后的持久引用和跨存储并发，不能只把空检查器替换成一次不完整扫描就宣称引用保护完整。

费率列表/创建仍在模型目录服务中直接访问开发接入 DAO，后续抽取须保留计费模式校验、版本生成和提交/回滚语义。

本轮改动尚未提交、推送或部署，运行服务未更新。[当前模块状态](../architecture/2026-09-29-module-boundaries.md)已同步。
