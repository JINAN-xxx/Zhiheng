# 后端应用查询边界实施计划

**目标：** 让 Agent 的应用绑定校验消费公共查询，去除对 AI 应用 DAO 的直接依赖。

**架构：** 新建 `plugins/ai/public/application_queries.py`，提供 `is_active_owned_application(db, application_id, owner_user_id) -> bool`。复用既有 DAO 的“按 ID 查询且未软删除”规则，再检查所有者和 active 状态。查询不管理会话、提交或回滚，不返回 ORM。Agent 保留错误码与事务边界。

查询只需要回答是否满足绑定条件，因此选择布尔契约；不额外创建消费者不会使用的应用 DTO，也不改应用 CRUD 或开发密钥认证。

## 必须保留的规则

- 创建 Agent 按创建者检查应用；更新/发布按 Agent 的原所有者检查，管理员身份不改变归属规则。
- 未绑定应用的草稿可以创建/预览；发布仍返回原 `AGENT_APPLICATION_REQUIRED`。
- 缺失、停用、软删除或其他用户的应用返回 false，Agent 继续映射为原 `AGENT_APPLICATION_UNAVAILABLE`、错误文本与 HTTP 状态。
- 发布快照保持冻结，后续草稿更改仍需重新发布才生效。
- 查询使用调用方会话，保留原查询及自动 flush 行为，没有额外 commit、rollback、锁或新会话。

## 步骤

- [x] 保存当前 AgentService/test 前态和 Ruff 基线，确认原 AgentService 测试通过。
- [x] 新增 `tests/plugins/ai/test_application_queries.py`，先验证公共入口缺失；真实 SQLite 覆盖可用/停用/软删除/其他所有者/缺失及调用方提交/回滚。
- [x] 增加 Agent 消费者测试：公共查询使用原所有者、false 保持原错误、未绑定不调用查询；保留原真实数据库回归。
- [x] 实现公共查询，AgentService 从 DAO 注入改为查询函数注入，替换唯一调用点。
- [x] 增加限定静态导入检查，防止 Agent 再次依赖 AI 应用持久化实现。
- [x] 运行 Agent 全部、模型/应用公共查询、应用管理、开发密钥与公开 API 相关回归；检查触及文件 Ruff 无新增问题。
- [x] 独立只读审查权限、事务和范围，更新架构状态和操作记录，完成差异检查。

现有工作区与分支继续使用，保留前序改动。不迁表、不改前端，不提交、推送或部署。本轮结束后剩余主线为模型删除引用规则和费率归属。
