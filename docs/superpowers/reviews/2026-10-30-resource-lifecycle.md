# 资源删除与生命周期排查

2026-10-30 用户反馈技能不能删除，并要求检查类似问题。本轮检查技能、模型、凭据、智能体、工作空间、应用及知识库的删除与引用校验，核对正常实例日志和只读数据。没有删除正常资源，没有更改删除规则；以下区分当前实现与建议。

## 技能问题的直接原因

正常实例目前有两个内置技能：“深度研究”已启用、“文档总结”已停用。日志中对“文档总结”执行过停用操作，没有技能 DELETE 请求。管理员停用后仍看不到删除按钮，原因是前端按 builtin 隐藏入口，后端也明确拒绝所有内置技能的删除。上传技能则必须先停用。

- 前端：[SkillDetailDrawer.vue](../../../frontend/src/modules/skills/components/SkillDetailDrawer.vue)，`canRemove && detail.sourceType !== 'builtin'`，并在 `remove()` 再次排除 builtin 和 enabled。
- 后端：[skills/service.py](../../../backend/src/zhiheng/modules/skills/service.py)，`remove()` 要求非 builtin 且 disabled，随后检查引用，实际删除只是填写 `deleted_at`。

内置的这两个技能是可选提示词资源，来源标签不应成为永久禁止管理员移除的理由。建议允许管理员移除，保留已有内容修订与运行快照。

## 已确认的问题

| 优先级 | 问题 | 当前实现及影响 | 建议 |
| --- | --- | --- | --- |
| 高 | 内置技能无法删除 | 前后端同时禁止；停用无助于删除 | 管理员可以移除，历史内容保留 |
| 高 | 历史引用永久阻挡技能、模型删除 | 智能体引用查询同时检查全部草稿和所有发布版本，不过滤 deleted_at；工作成员查询不区分 removed_at 或所属任务已删除 | 将当前有效引用、在途使用和历史快照分别处理；历史快照继续留存 |
| 高 | 模型使用过后很难再移除 | RunStore 对所有历史 Run 返回引用；旧金额费率也直接阻止删除；模型删除本身却是软删除 | 允许从目录下架/移除，保留历史模型身份、账单和绑定；保护实际在途调用 |
| 高 | 已撤销/过期 Key 仍阻挡模型删除 | KeyService.references_model 仅查 model_ids，不检查 Key 是否可用或应用是否删除 | 删除阻断只考虑仍有效的授权与当前依赖，保留旧授权审计 |
| 高 | 已删除模型仍阻挡凭据删除 | 连接引用检查读取所有 ModelDefinition，没有排除 deleted_at；即使模型已软删除，连接仍被挡住 | 当前目录移除与历史凭据修订留存独立处理 |
| 中 | 技能无法按同一标识更新或重装 | 导入确认总是新建 Skill；slug 全表唯一，包含已软删除记录；没有恢复/更新分支 | 提供明确的恢复/重装/更新动作并生成新修订，不能简单释放标识导致旧引用悄悄改绑 |
| 中 | 应用停用后不能改名称 | ApplicationService.update 同时要求 active 和版本匹配；为改名称必须重新启用，期间仍有效的 Key 又可调用 | 允许停用状态修改展示信息；状态改变作为独立操作 |
| 中 | 删除前置步骤与引用提示不统一 | 技能先停用、智能体先取消发布、应用先停用，模型前端只在停用后显示删除但后端没有同一前置要求；引用错误只返回泛化文案 | 统一确认流程，并给出引用资源、状态和可跳转的处理入口 |

知识库也参与模型的长期阻断：`KnowledgeService.references_model()` 查询库与图谱配置时不排除已删除库。应结合删除任务、索引/图谱回收状态判断实际依赖，不能仅按 deleted_at 机械放开。

## 代码依据

- [agents/service.py](../../../backend/src/zhiheng/modules/agents/service.py)：`references_model()`、`references_skill()` 查询 Agent 和 AgentVersion，包含已删除实体与旧发布版本。
- [work/service.py](../../../backend/src/zhiheng/modules/work/service.py)：`references_model()`、`references_skill()` 查询全部 WorkMember。
- [runtime/runs.py](../../../backend/src/zhiheng/runtime/runs.py)：`references_connection()`、`references_model()` 不区分进行中与历史 Run。
- [models/service.py](../../../backend/src/zhiheng/modules/models/service.py)：`delete_model()` 检查引用与 RateCard；`delete_connection()` 查询全部模型；两者最后均保留数据库行并填写 deleted_at。
- [developer/keys.py](../../../backend/src/zhiheng/modules/developer/keys.py)：`references_model()` 未使用同文件 `usable()` 的可用性判断。
- [developer/applications.py](../../../backend/src/zhiheng/modules/developer/applications.py)：`update()` 拒绝非 active 应用。
- [skills/orm.py](../../../backend/src/zhiheng/modules/skills/orm.py)、[skills/service.py](../../../backend/src/zhiheng/modules/skills/service.py)：slug 全表唯一，确认导入仅有新建和冲突两条路径。
- [knowledge/service.py](../../../backend/src/zhiheng/modules/knowledge/service.py)：`references_model()` 查询 KnowledgeBase 与 GraphConfiguration，不检查逻辑删除或回收进度。
- [test_agents.py](../../../backend/tests/core/integration/test_agents.py)：`test_agent_versions_immutable_republish_and_history_references` 明确断言“智能体删除后，关联技能删除仍返回 409”。这说明问题包含原设计和测试预期，需要一起调整。

## 建议统一的行为

1. 列表中的“删除/移除”采用软删除，停止新选择与新绑定；历史聊天、发布快照、用量账单及必要凭据修订继续可追溯。
2. 进行中的任务和仍有效的依赖提供明确阻断，返回资源名称、类型、状态及处理入口。对当前依赖进行解绑或下线，不能静默破坏其他人的资源。
3. 无当前引用时，一次确认完成必要的停用/取消发布和移除，避免用户来回切换多个按钮。
4. 内置可选业务资源同样可由管理员移除；系统身份、核心权限等基础保护另按其实际职责处理。
5. 提供恢复、重装与版本更新的明确流程；技能标识不应因隐藏的历史记录而让用户无法处理，也不应通过换绑旧标识破坏历史语义。

建议首先修技能可删除与引用明细，再统一模型/凭据的历史引用处理，最后补技能重装和应用编辑。涉及快照保留、当前依赖、并发接纳和重装标识的规则，需要配套真实数据库回归；不能仅放开前端按钮或删掉所有引用检查。

本轮是代码与只读数据排查，没有运行删除动作，也没有新增功能验证。当前结论不等于已完成全平台业务穷举测试。
