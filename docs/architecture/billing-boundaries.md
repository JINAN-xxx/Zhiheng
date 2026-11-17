# 账务职责与事务边界

账务按不变量和事务拆分。CreditService保留钱包、预占、结算、核对、失败免计及流水，显式组合queries、rates、commands。协作者使用调用方的同一个session，不自行提交或创建第二条计费路径。

## 代码归属

| 文件 | 职责 |
|---|---|
| service.py / CreditService | 发放/回收、暂停、模式切换、预占、结算、核对、失败免计，及账户/Hold/月桶锁与流水 |
| queries.py / CreditQueries | 状态、单个/批量余额、流水与预占单的只读查询，保留分页、过滤和跨用户读权限 |
| rates.py / CreditRates | 积分价格列表、新增版本、当前报价及模型计量类型校验；只新增版本，不改写已接受调用的价格 |
| commands.py / CreditCommandJournal | 管理命令幂等锁、原结果重放、请求冲突和收据写入；价格与资金操作共享该实例 |
| projections.py | 精确账户计算、余额快照、预占单快照及failure_waived解释，查询和写入复用同一份实现 |
| pricing.py | 18位积分Decimal、计量单位、价格计算与既有舍入规则 |
| gate.py | 模式共享/排他门锁与积分事务advisory lock原语；原键值和获取时机保持一致 |
| usage.py / UsageService | Token/Key与积分原子组合，原savepoint保护部分失败 |
| metering.py / MeteringService | Token预占/实际用量、Invocation和平台配置货币费率快照，保持现有Key策略端口 |
| tokens.py | 未知Token用量核对及独立幂等命名空间 |
| facts.py | 跨域只读用量事实 |
| trace.py / trace_contracts.py | 原付费身份下的调用、预占、流水和相关调用的只读财务事实 |
| application/billing/trace.py | 在同一只读快照中组合账务、最小执行事实和显式摘要关系，不获得业务内容权限 |
| application/billing/management.py | 目标身份/模型准入、模式切换前在途任务检查、管理审计与事务组合 |

API与跨域调用从public.py取得服务和公开契约，直接访问实际职责：

```python
summary = await credits.queries.summary(session, user_id)
rate = await credits.rates.quote(session, model_id)
history = await credits.rates.list(session, actor, model_id)
await credits.rates.create(session, actor, model_id, data, model_type=model_type)
```

根服务已移除原扁平查询、价格和命令实现，不保留转发方法、Mixin或动态属性。资金核心依赖共享快照、价格和命令协作者；只读层及价格不反向导入CreditService。

## 金额与事实

available = allocated - reclaimed - used - reserved，聚合使用80位Decimal上下文，沿用18位小数和HTTP精确字符串。缺失账户的查询返回version=0的零余额，不INSERT账户。

实际用量、积分消费与货币计量分别保存。unknown不能补零；已确认用量只能补充未知部分，不能覆盖或改成未执行。按search_units结算积分而Token诊断未知是允许的状态。当前billable_cost来自平台配置费率，不代表已核验的供应商账单。

预占保存原付费人、Key、上海时区月桶及价格版本/快照。新价格、切换模式或轮换Key不追溯改写已有调用。实际用量超出预占仍记录真实消费，可用积分可能为负并阻止下一次消费。

空失败私人聊天的商业免计只在原有条件下释放预占，保留未知的供应商用量。Run、Invocation、Hold和流水各自保存执行、计量与财务事实，不以其中一项覆盖其他记录。

## 事务与锁

RunAdmission将Run/Outbox与用量预占放入同一外层事务，UsageService用savepoint协调MeteringService和CreditService。资金账户、Hold、Key月桶、流水和命令收据共用session；管理审计也在调用方事务内完成。

| 用例 | 关键锁序 |
|---|---|
| 准入 | 原模式门锁/业务锁 → Key限额 → Token账户 → 积分账户/月桶 → 新Hold/流水 |
| 结算 | Run → Invocation → Key → Token账户 → 积分账户 → Hold → 原月桶/流水 |
| 发放/回收/暂停 | 目标身份校验与锁 → 管理命令锁 → 账户 → 流水/收据 |
| 核对 | 原Invocation/Key/Token流程 → 原位置上的积分命令锁 → 积分账户/Hold/月桶/流水 |
| 失败免计 | Run → Invocation → Token账户 → 积分账户/Hold → 免计流水 |

价格创建先取得原命令锁，再取得模型价格锁，在同一事务内追加版本和收据。抽出实现不提前或延后锁调用，也不改变billing:前缀与原advisory key。

积分命令的幂等范围是(operator_id, client_request_id)，不同kind/target/payload复用同一标识会冲突；等价Decimal写法重放原结果。TokenCommand另有包含operation的命名空间，两者不合并。

模式切换继续持有排他门锁并检查在途Run及可派生模型调用的知识任务；准入持有共享门锁。事务失败不留下部分Run、Outbox、Invocation、Hold或流水；抽出的协作者不会付费重试。

## 退役的累计Token额度

PATCH /api/usage/accounts/{user_id}/quota和TokenQuotaPatch已删除，OpenAPI不再声明该写入口。用量DTO及前端解析不再返回limit_tokens/quota_version；Token累计使用、预占、分页和未知用量核对继续保留。核对操作明确要求ai:credits:reconcile，发放积分权限不等同于核对权限。

0039已删除developer_accounts中的limit_tokens/quota_version，usage账户只保存user_id、used_tokens、reserved_tokens，并保留非负约束。输入窗口预算、积分余额、Key按分钟/月的限额各自按原规则执行。

UsageService不再保留无生产消费者的summary转发；用量查询由MeteringService负责。准入/计量层无效的token_limit透传已移除；知识重排实际批次预算使用独立knowledge_rerank_batch_tokens设置，冻结请求继续使用原诊断预算。

## 验证与后续边界

通过数据库强制READ ONLY事务验证只读接口，使用隔离PostgreSQL/Redis及本地HTTP/Fake模型验证并发、幂等、回滚、权限撤销、价格快照、原Key/月桶、unknown补全和免计。结构调整无需DDL，不调整余额、价格、历史流水或限额政策。

真实供应商成本核验、欠费产品政策与存储性能分别设计。源码中保留稳定架构说明；本机财务快照、备份、过程记录和检查报告保存在仓库外。


## 调用与费用追溯

GET /api/billing/usage/{run_id}?ledger_page=1 是财务详情入口。BillingTraceQueries 以 Invocation.user_id 的原付费身份授权；本人读取自己的事实，跨账户读取要求 ai:credits:read 及其模块门禁。HTTP 同时要求现有用量或业务入口权限。Run 的 owner_id 仅是执行发起人，尤其在 Work 中不等同于付费人，也不单独授予另一账户的财务读取权。无权限与不存在均返回 404。

InvocationTraceService 使用一个 PostgreSQL REPEATABLE READ READ ONLY 事务组合结果，不创建账户，不锁定结算记录，不改写计费事实。runtime 的 RunFacts 只选择执行标识、发起人、原付费人、类型、状态、尝试次数和受理时间；不选择或返回 payload、文本、推理、结果、资源内容 ID 和供应商错误原文。财务读取不调用 RunSnapshot，不扩大提示词、文件或工作空间的内容权限。历史 Invocation 没有可关联的原生 Run 时保留财务记录，execution 返回 null。

Chat 的 ContextMemory/ContextStore 只返回 SummaryTask 中已有的 parent_run_id/run_id。追溯不读取摘要、源消息、附件或模型绑定，不猜测没有父执行身份的历史关系。相关 ID 再经过原财务身份过滤，详情跳转重新授权；最多展示 20 条关联调用并说明截断。单个 Run 的积分流水按受理时间与 ID 稳定分页，每页 20 条；同一时间的 ID 排序不代表额外业务因果顺序。

响应分别保存执行状态、Invocation 计量状态和 CreditHold 结算状态。按检索单元结算积分、但 Token 诊断未知，或平台异常免计、但供应商用量未知，都保留原事实。实际积分、平台配置货币费率金额、Token 数量使用各自单位；金额使用精确十进制字符串。billable_cost 不表示已核验供应商成本；没有货币费率时金额未知，不补零。

现有表没有首次进入 unknown 的时间，前端以 Invocation.created_at 和查询时刻展示“距受理时长”，明确不声称是首次待核对时间；没有补造历史时间，也不因时间变长而释放预占。

Token、积分消费和带 Run 的流水共用 InvocationTraceDialog。UsageReconciliationDialog 继续使用现有积分/Token 核对命令、CAS、幂等标识及审计；合并两个计量快照的已确认字段并禁改，确认值冲突时不提供自动选择或覆盖入口。search-unit 已结算以及商业免计不会生成第二次结算入口。发放/回收、暂停/恢复、核对分别检查 adjust、configure、reconcile 权限及模块门禁；身份/权限或账户变化终止旧请求并关闭旧详情和核对表单。
