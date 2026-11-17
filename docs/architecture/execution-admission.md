# 模型执行准入

所有持久模型调用使用 `application/execution/admission.py` 的 `RunAdmission.reserve` 创建原生Run、Outbox和用量预占。bootstrap只构造一个共享实例，注入普通聊天、摘要、模型试用、助手、公开API、Work和知识用例。

## 职责与事务

领域用例负责身份与资源授权、模型与依赖冻结、请求指纹、幂等锁和已有请求查询。工作流子调用保留父任务、attempt与step的验证。公共准入接收冻结的ModelRequest和AdmissionIdentity，执行以下步骤：

1. 计算输入、输出与检索单位预占；需要额外输入上限的入口显式传入限制。
2. 在调用方事务内打开savepoint，写入Run及Outbox。
3. 读取货币费率，通过既有UsageService预占Token/Key限额和积分。
4. 返回Run，由领域用例继续写消息、审计或父步骤绑定；调用方统一提交。

savepoint覆盖Run、Outbox和整个费用预占。费用失败时这些事实一起撤销，调用方即使捕获错误并继续提交其他变更也不会留下可派发的孤儿Run。调用方事务最终回滚时，已经返回的准入结果也全部回滚。

计费模式门锁、请求锁及模型/资源锁仍按领域原有顺序获取；公共准入不会重选幂等命名空间。业务提交前不向供应商发请求，实际派发由提交后的Outbox承担。

## 身份与来源

AdmissionIdentity有三个明确字段：owner_id是原生Run所属操作人，billing_owner_id是实际付费人，key_id是原始API Key（适用时）。它是已授权事实的值对象，不是登录或资源授权凭证。

| 场景 | Run owner | 付费人 | Key | 用量来源 |
|---|---|---|---|---|
| 普通聊天、摘要、模型试用、助手正式会话 | 当前合法用户 | 同一用户 | 无 | chat / chat_summary / connection_test / agent_chat / agent_summary |
| 助手配置调试 | 实际调用人 | 助手所有者 | 无 | agent_preview |
| 助手公开调用 | 原Key所有者 | 助手所有者 | 原Key | agent_invocation |
| 公开模型API | 原Key所有者 | 原Key所有者 | 原Key | public |
| 工作空间 Agent 模型子调用 | 任务所有者 | 同一用户 | 无 | work_agent_model |
| 知识构建向量子调用 | 构建所有者 | 构建所有者 | 原接入Key（如有） | knowledge_embedding |
| 知识检索向量/重排子调用 | 准备任务owner | 准备任务owner | 原接入Key（如有） | knowledge_retrieval |
| 图谱模型回调 | 当前claim owner | 当前claim owner | 无 | knowledge_graph |

知识检索的Run类型仍分别为knowledge_embedding与knowledge_rerank，用量来源通过usage_source显式保留knowledge_retrieval。Key的application_id仍由原生计量服务从该Key取得，不从当前重放请求覆盖。

工作空间父运行 `work_agent` 负责监督与聚合展示，不额外预占一次模型费用。分析、摘要和最终答复各自通过公共准入创建 `work_agent_model` 子运行，冻结父 Run/attempt，执行前复核归属与当前权限。旧 `work_turn` 保留历史，不能作为新任务入口。任务与工具协议见[工作空间任务 Agent](../api/workspace-agent.md)。

## 估算与收费

财务预占按UTF-8内容字节、每条消息16单位开销与受管图片固定成本估算。普通聊天的历史checkpoint输入估算作为下界补充，取二者较大值，增量消息不会导致只预占本轮输入。

聊天输出使用ModelBinding.output_reservation_tokens：显式上限按本轮上限预留，供应商默认输出按既有min(4096,目录上限)规则预留；embedding/rerank不预留聊天输出。rerank按候选资料数估算search_units；图谱回调显式保留空候选为0的既有规则，其他入口保留至少1单位的默认估算。

模型上下文计数与财务预占各有用途；实际结算继续使用供应商原始usage，未知用量保持unknown。当前事务内的积分费率快照、Key月桶、Hold、流水、取消释放和失败免计仍由原生账务模块负责。

货币费率是兼容的可选元数据：未配置时按原规则继续。费率读取的其他错误明确传播，图谱调用不再把任意读取故障都当作“无费率”。

普通聊天和摘要继续使用已配置窗口/输入预算；助手会话与模型试用保留原64000输入预占上限。其他领域保留自己的输入选择和长度约束。

## 修改约束与验证

新增模型入口在领域授权和请求锁之后调用公共准入，不在领域里复制RunStore.create加UsageService.reserve，也不在公共准入里增加按业务类型分派的流程。领域负责消息和版本行为；执行器负责lease/attempt；UsageService负责计量和结算。

关键回归包括：预占失败后捕获并提交、Key RPM槽回滚、补充余额后同请求重试、操作人与付费人分离、原Key/application追溯、图片/历史输入预占、调用方事务回滚、并发幂等、取消、unknown及重排单位。

此调整不修改数据库表、历史数据、余额、费率或已有Run payload格式。普通聊天继续使用既有官方Agent执行，其他领域保留各自执行与版本规则。
