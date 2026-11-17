# 普通对话的 Agent、会话状态与摘要

普通“对话体验”通过官方 `create_agent`、`SummarizationMiddleware` 与 `AsyncPostgresSaver` 执行。现有 Run 继续负责单次模型调用的准入、权限、取消、用量和计费，聊天记录与回复版本继续保存在聊天领域。

这条正式路径对应 Console Cookie 接口 `/api/chat/conversations`。当前没有另一个普通Chat资源API入口。智能助手也复用公共Agent适配器，正式会话与调试/API的状态规则见[智能助手执行与记忆](assistant-langchain.md)。Work、模型试用和其他公开推理入口继续各自原生执行行为。

普通Chat保留附件、图片与SKILL指令输入，`tools=[]`。它不接RAG，不接受 `knowledge_base_ids`，不展示知识引用，也不提供技能脚本或Sandbox执行。知识库和其他业务的检索能力保持在各自模块；旧普通Chat检索胶水与手工摘要路径已经移除。

## 正式请求与执行边界

主要接口：

| 接口 | 职责 |
| --- | --- |
| `POST /api/chat/conversations` | 创建属于当前用户的会话 |
| `POST /api/chat/conversations/{id}/runs` | 幂等接纳本轮消息，返回原生Run |
| `POST /api/chat/conversations/{id}/messages/{message_id}/regenerate` | 为最新一轮创建回复新版本 |
| `GET /api/chat/conversations/{id}/messages` | 分页读取业务消息与版本 |
| `GET /api/chat/conversations/{id}/context` | 读取摘要、预算与上下文状态投影 |
| 现有Run查询、观察和取消接口 | 跟随原Run，不重复提交模型请求 |

API校验请求、owner和模型配置，将附件解析等长IO放在业务事务外，再在事务内冻结输入、创建Run和预占。正式Chat没有旧路径切换开关；缺少必要的Agent装配或可验证的上下文时明确拒绝。

聊天适配器通过公开 `ModelProvider` 组合既有模型实现。供应商凭据、HTTP隔离、reasoning处理与原始usage仍归模型模块，聊天代码不继承供应商实现或读取其私有属性。模型输入前的Middleware注入本轮可信前缀并检查完整输入预算。

## 配置与Token策略

| 设置 | 默认值 | 直接运行环境变量 | Compose环境文件变量 |
| --- | --- | --- | --- |
| 摘要触发 | 自动：可用消息预算的 70% | `ZHIHENG_CHAT_SUMMARY_TRIGGER_TOKENS` | `CORE_CHAT_SUMMARY_TRIGGER_TOKENS` |
| 保留近期消息预算 | 自动：可用消息预算的 20% | `ZHIHENG_CHAT_SUMMARY_KEEP_TOKENS` | `CORE_CHAT_SUMMARY_KEEP_TOKENS` |
| 摘要输入预算 | 自动：随有效输入容量扩大 | `ZHIHENG_CHAT_SUMMARY_INPUT_TOKENS` | `CORE_CHAT_SUMMARY_INPUT_TOKENS` |
| 摘要输出上限 | 自动：有容量预算时最多 4096 Token | `ZHIHENG_CHAT_SUMMARY_OUTPUT_TOKENS` | `CORE_CHAT_SUMMARY_OUTPUT_TOKENS` |
| 普通输入预算 | 可不设置 | `ZHIHENG_CHAT_CONTEXT_INPUT_TOKENS` | 使用后端实例配置 |

四个摘要配置默认均为 `auto`，不填写或留空也使用自动策略；填写整数可分别覆盖。既有整数配置继续生效，升级不会擅自清除管理员设置。若已有实例显式配置了旧的 16000/5000/65536/1024，需改为 `auto` 并重建 API/Worker 容器才能启用自动策略。

有效输入容量先扣除本轮模型输出预留和 256 Token 安全余量，并受平台输入预算约束；触发和保留比例再扣除本轮系统提示与技能等可信前缀。摘要输入随有效容量扩大，不再默认把 1M 模型限制在 65536 Token。摘要输入仍受摘要模型自己的窗口约束；手工缩小摘要输入预算时，同步降低触发阈值，避免等历史积累后才发现摘要装不下。

例如模型窗口为 1000000 Token、输出预留 4096、没有其他输入上限与前缀时：可用输入为 995648，约 696953 Token 触发摘要，保留约 199129 Token 近期原文，摘要输入最多 995648、摘要输出最多 4096。这些都是预算估计，不代表实际服务商计量或保证长上下文质量。

没有模型窗口也没有平台输入预算时，继续采用 16000 触发、5000 保留、65536 摘要输入、1024 摘要输出的保守值，不推断模型支持 1M。手动触发和保留值同时设置时，触发必须大于保留；实际保留还不超过有效触发预算的三分之一。小窗口、可信前缀和本轮问题会继续收紧预算，摘要输出也受该模型配置上限约束。

计算结果在每次接纳时冻结。下一轮可随所选模型或管理员配置重新计算；已接纳请求与重新生成沿用原执行配置。主模型调用前再检查完整 checkpoint 消息、本轮前缀与输入。超过有效容量时明确拒绝，不静默丢弃当前问题，也不为修复容量自动重复付费调用。

Token是保守离线估计：每条消息有固定开销，ASCII按约两字符一个Token，非ASCII按每字符两个Token，图片沿用固定预估。摘要触发、保留和输入校验使用同一口径，它不等于供应商实际tokenizer。费用预占使用更保守的UTF-8字节与图片口径，最终按原始供应商usage结算。

模型窗口未知时，不把最大输出当成上下文窗口，也不声称摘要阈值就是模型容量。输出留空仍不向供应商传输出限制；预占沿用4096 Token参考值，目录参考更小时取较小值。已知窗口的输入预算为本次输出限制或该预占参考预留空间。

旧30/15轮配置、手工滚动摘要规划和手动“重试整理”接口已经删除。上下文DTO不再携带旧dirty/truncated/dropped字段；摘要处理中由status明确表示。

## 官方Agent与摘要Middleware

Agent运行在原Run的模型执行边界内，不为主回复再创建一层Run。它只执行一次 `astream`，使用 `messages` / `updates` 的v2流与同步checkpoint持久化，转换为平台原有快照。流结束后不再次invoke。

SummarizationMiddleware判断何时摘要及保留哪些近期消息。Callable摘要端口只连接原生模型准入与账本，不再实现另一套摘要规划；`trim_tokens_to_summarize=None` 关闭默认4000 Token尾部裁剪。较早历史不能在尚未阅读时被静默裁掉。摘要输入超过配置或已知模型预算时明确报错。

SMM仍独立选择摘要前缀。模型Middleware先记录SDK输入；当SMM真正调用摘要端口时，按它已经选定的保留消息、可信前缀、冻结摘要输出上限和64 Token消息封装余量检查容量。空间不足在创建付费子Run之前拒绝；这个检查不替SDK选择历史。实际摘要完成后，主模型前仍检查完整消息。

每次摘要创建独立 `chat_summary` Run，并使用本次冻结的有限摘要输出上限。原始usage、Invocation、Hold和结算都使用现有服务。Callable分支避免默认的隐式重试，供应商SDK也保持retry=0。摘要和主回复分别记录实际发生的费用。

摘要明确投递short队列，主回复使用主模型队列；等待摘要不会占住摘要自己的消费槽。摘要与主模型各受一次供应商超时约束，Agent总预算及Worker任务超时覆盖两次调用和协调开销。

## 摘要父身份、取消与孤儿恢复

新SummaryTask必须保存 `parent_run_id` 与 `parent_attempt_id`。摘要准入及实际供应商调用前，均重新检查父Run处于允许执行的状态、attempt和租约有效，且未请求取消。摘要本身也要通过自己的准入、预占和执行校验。

数据库保证父身份成对存在，pending摘要必须有父身份。迁移前的终态历史允许父字段为空，以保留未知的历史事实；不能从不可逆的UUID5身份猜出父Run。

摘要终结核对pending指针、state版本、父身份和子Run。相同终态结果可以幂等重放，不能替换已经终结的结果。摘要成功会推进摘要版本计数，但只有主回复正常完成并通过head发布条件时，该次执行才成为后续有效记忆。

用户停止先持久化取消意图，再中止模型流并传播给在途摘要Run。请求尚未执行时释放预占；供应商已开始但计量未确认时保留unknown。主Run的 `provider_started` 在摘要及checkpoint准备完成后、真正请求主模型前记录，不能因摘要收费而虚构主调用已经开始。

维护循环只核对已接纳摘要的孤儿状态，不规划摘要、不重发模型请求。父Run已终结而子Run仍未结束时请求取消，等待其真实终态后收口pending状态。Run和账务事实继续保留。

## Checkpoint与业务版本

服务端使用真实会话ID作为 `thread_id`，并在读取前验证当前owner、领域权限、附件与技能修订。前端不能任意提供thread或checkpoint读取参数。

新会话从显式root开始，已有有效head时只追加本轮用户消息，使用固定消息身份，避免重复回灌历史。旧版记录缺少Agent快照，或已有业务成功历史却没有匹配head时，要求新建对话，不自动导入或悄悄忘记历史。

state记录有效head及成功历史指纹，Run快照记录冻结base、消息身份、预算、模型输入checkpoint与执行结果引用。只有正常stop且有正文的主回复才能通过expected-head比较并推进有效head。failed、cancelled、length与孤立物理分支不会成为下一轮历史。空正文stop转为明确失败，保留已发生usage。

checkpoint的物理latest不等于业务有效head。适配器按本次执行metadata定位结果，发布时仍执行数据库行锁与head比较，防止失联旧worker留下的晚到分支覆盖有效历史。

重新生成创建新Run及业务回复版本。已保存主模型输入checkpoint时，从该输入位置继续，只调用主模型，避免重复整理已经完成的摘要。尚未形成该引用的本实现记录按原冻结base和消息执行；显式重新生成可能再次触发摘要并产生独立费用。旧版没有Agent快照的记录明确拒绝导入。下一轮使用最新成功业务版本对应的head，原回复版本继续保留。

增量输入不代表费用预占只计算本轮：接纳时同时计入checkpoint完整有效上下文的保守估计，主模型前再验证实际输入预算。系统前缀和SKILL指令按本轮冻结配置注入，用户技能内容不获得工具或额外权限。

## 观察、故障与状态投影

刷新、断网、切页后恢复观察原Run，不自动replay。官方checkpoint恢复能力不意味着模型调用免费，不能用replay替代只读状态查询。

checkpoint准备失败且尚未调用主供应商时，不记造主消费。主模型已经完成但保存checkpoint失败或在保存期间被取消时，保留已取得的内容与原始usage，继续传播失败或取消并按事实结算；不为修复存储再请求模型，也不推进无有效引用的head。

费用结算、Run终态、聊天投影和head发布在同一业务事务内完成。官方Saver使用独立连接持久化执行状态，业务事务失败不能通过选择物理latest来绕过原生Run所有权。

上下文接口明确区分两种事实：

- effective_memory只读取业务接受的head快照，包括是否包含摘要、覆盖/保留轮数及上下文估计。summary任务完成或物理checkpoint较新，不会自动改变这些事实。
- last_run_context读取最近执行的上下文准备、冻结预算及实际模型输入估计。summary_prepared表示已准备摘要；没有完成元数据时estimated_input_tokens保持null，不拿预占或增量估计冒充实际输入。
- status由有效记忆及当前摘要任务状态决定。摘要成功而主回复失败时，last_run_context可以显示已准备摘要，effective_memory仍是原记忆。

摘要失败时界面说明原有效记忆仍保留，并引导明确重新发送或重新生成；不再展示未经事实支持的“历史被截断”。旧“重试整理”按钮、请求意图和后端入口已经移除。运行状态进入摘要时刷新观察，终态再次刷新；切换会话或隐藏页面时停止当前观察作用域。

## 不可变执行配置

modules/chat/execution.py中的ChatExecutionSpec是普通Chat冻结执行配置的唯一类型。版本、消息ID、增量base、预期head、预算及完成计数在同一处验证；不接受布尔数值、未知版本或不完整身份。跨用例及装配传类型，JSONB只作为持久化形式。

新重生成沿用原模型输入checkpoint、消息身份及预占输入估计，同时清空上一执行的完成计数。失败的新执行不会因此显示旧执行的输入数据。完成时仅合并已声明的SDK结果字段，预期head比较仍保护业务发布。没有旧配置分支或宽松字符串转数字。

## 前端职责

frontend/src/modules/chat/composables下按实际状态生命周期组合：

| 文件 | 职责 |
| --- | --- |
| composer.ts | 模型与生成参数、技能、附件队列及输入配置 |
| messageHistory.ts | 消息合并、较早分页、滚动锚点及反馈确认竞态 |
| runObservation.ts | Run投影、流观察、停止确认与重连 |
| commands.ts | 发送/重生成幂等意图、未知接纳结果及错误处理 |
| workspace.ts | 会话目录/元数据、选中会话、作用域及上述职责的显式组合 |
| messagePresentation.ts | 回复版本选择与展示、活动回复及失败问题投影 |
| contextObservation.ts | 只读上下文观察、隐藏暂停、退避及永久错误停止 |

业务状态由其所有者持有，workspace组合实际引用与动作。切换会话中止旧观察，后台取消只由明确停止动作触发。原发送/重生成的未知响应继续使用原请求；停止响应丢失可以只读确认，迟到停止或反馈不能改写其他会话。ChatView保留页面布局、焦点、键盘与DOM交互；样式和模板布局保持。

## 删除会话与Checkpoint回收

删除会话首先检查没有活跃主Run，取消相关摘要并释放现有业务文件引用，再记录会话墓碑。后台维护每批最多选取25个已删除且到期的会话，重新确认owner及没有活跃主Run后释放数据库事务，再调用官方 `AsyncPostgresSaver.aprune(..., strategy="delete")`，在同一个 SDK 事务中删除该线程的 checkpoint、writes 和 blobs。

删除IO完成后，在独立短事务内重新确认owner与墓碑，清空有效head/hash，并设置 `checkpoint_cleanup_after=当前时间+1小时`。后续重复扫描用于清理失联旧worker可能留下的迟到物理写入。失败时保留候选以便重试，不在持有业务数据库锁时执行Saver IO。

只删除该会话的SDK checkpoint、blob和writes，不删除原生Run、消息审计或账本。单纯归档不会清理checkpoint；非删除会话不进入这条回收路径。

长期活跃会话的中间及失败分支GC仍是后续工作。不能直接使用keep_latest删除当前有效head或仍支持重新生成的模型输入checkpoint。

## 显式迁移与部署

`0035_chat_checkpoints` 增加state与Run快照的四个可空Agent引用及元数据字段，并初始化独立 `chat_checkpoints` schema。DDL冻结自锁定的官方PostgreSQL saver 3.2.0；运行时只检查官方迁移版本，不执行setup，不在API启动时建表。

`0036_chat_runtime_cleanup` 负责：

- 删除手工摘要state、旧摘要重试链与快照指针字段。
- 增加摘要父Run/attempt、父身份约束及查询索引。
- 增加checkpoint清理时间与head快照复合索引。
- 从聊天request_context中仅移除 `knowledge_base_ids`，其他生成设置保持原值。
- 移除普通Chat的 `knowledge_sources` 列；其他业务的知识数据和检索实现不变。

0036发现旧pending摘要时拒绝升级，要求先在匹配旧运行时中完成或明确取消，不能编造其父身份。终态摘要、原生Run和账务记录继续保留。

升级前备份数据库，先构建含锁定依赖的镜像，显式执行迁移，再统一部署API与Worker。源码热覆盖不能替代SDK依赖和数据库版本升级。验证使用独立测试数据库。

0036删除的手工状态无法从新状态自动重建，downgrade明确拒绝。回退需使用迁移前数据库备份和匹配代码版本，不能切回已经删除的旧执行分支。

## 代码位置

以下后端路径相对于 `backend/src/zhiheng/`：

| 路径 | 职责 |
| --- | --- |
| `api/console/chat.py` | 正式Console聊天入口与业务消息接口 |
| `application/chat/runs.py` | 发送、重新生成、附件和原生Run准入 |
| `application/chat/agent.py` | 冻结来源与预算、摘要子Run、有效head发布和墓碑清理组合 |
| `application/chat/context.py` | owner/附件/技能校验、摘要父执行guard、孤儿核对和状态投影 |
| `modules/chat/execution.py` | 不可变冻结执行配置和受控完成/重生成转换 |
| `modules/chat/context_*.py` | state、SummaryTask、Run快照及其持久化状态转换 |
| `infrastructure/agents/adapters/agent.py` | 官方create_agent、摘要Middleware与公开原生模型端口桥接 |
| `infrastructure/agents/adapters/checkpoints.py` | 官方Saver连接、版本检查及线程删除 |
| `modules/models/adapters/` | 供应商协议、HTTP隔离、reasoning和原始usage |
| `application/execution/`、`runtime/`、`worker/` | 统一准入、账本、Run、租约、Outbox、队列与恢复 |

SDK类型限制在adapters，其他业务模块沿用其现有公开协议。当前实现没有AgentTask、ToolOperation、脚本Runner或第二套会话Graph。


## 图片上下文容量

图片容量与Token预算分别检查。有效checkpoint及重生成模型输入的元数据保存图片字节计数；新准入计算历史加本轮增量，超过16 MiB时在创建Run和预占费用前返回attachment_images_too_large。官方摘要压缩后的有效checkpoint重新计算计数，避免把已移除图片继续累加。

实际模型Middleware仍对完整消息做同一上限检查，覆盖没有计数的旧checkpoint和受控分支恢复；校验发生在provider_started之前。该计数仅保存大小事实，不复制base64或另建会话存储。
