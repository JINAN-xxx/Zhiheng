# 智能助手：最新配置、标准 Agent 与持久记忆

本文对应智能助手的正式接入方案与实现边界。助手只维护一份最新配置和启用状态；正式会话采用官方 create_agent、SummarizationMiddleware 和 AsyncPostgresSaver。Run、权限、文件和账本继续由平台负责。

## 用户操作与配置

助手页面提供编辑、保存、启用/停用和配置调试。保存后，下一次调用采用最新配置；已受理的请求使用当时冻结的模型绑定、输入、技能和资料。配置修改期间的知识准备若依赖已变化，明确要求重新发送。

不维护草稿/发布两套配置，不记录发布版本目录，不绑定会话到某个发布版本。单行 version 只是防止并发保存覆盖的修订号，schema version 则用于验证运行格式；二者均不是可供用户选择的历史版本。

每次实际调用的配置、技能修订和来源保存在 Run 审计快照中。这些记录用于执行与费用核对，不能因为编辑助手而改写。

## 三种入口

| 入口 | 配置与历史 | 状态管理 |
| --- | --- | --- |
| 正式会话 | 最新已启用配置，服务端会话记忆 | 持续的业务有效 checkpoint；每轮只追加本轮问题 |
| 配置调试 | 最新已保存配置，页面提交 messages | 每请求独立，checkpointer=None |
| 外部 API | 最新已启用配置，调用方提交 messages | 每请求独立，checkpointer=None |

调试和 API 不使用 agent_id、用户或 Key 作为隐式共享记忆线程。其客户端继续负责请求历史，保留既有 96 条消息和 64 KiB 输入协议上限。助手 history_rounds 设置已退役，不能在 Worker 中再选择一次历史。

外部 API 继续检查应用、原 Key、agents:invoke、模型 allowlist 与当前权限。启用/停用沿用现有权限编码 agents:publish，界面语义为启停，不再创建发布快照。

## 公共执行机制

~~~mermaid
flowchart TD
    A[正式会话 / 配置调试 / 外部 API] --> B[助手用例：身份、配置与知识准备]
    B --> C[原生 Run：冻结请求、预占与 Outbox]
    C --> D[执行器：租约、取消与执行权]
    D --> E[官方 create_agent + Middleware]
    E --> F[共用 ModelProvider 桥接与流转换]
    E --> G[正式会话 checkpoint]
    F --> H[供应商响应与原始 usage]
    H --> I[原生结算、Run 终态与有效记忆发布]
~~~

create_agent 内部使用 LangGraph 运行时，直接使用其官方 checkpointer；当前没有手写 StateGraph 的业务需要。每次 Run 创建独立模型桥接和 Middleware 实例，不共享请求内的 started、usage 或执行标识。

首版 tools=[]。SKILL 包的主文件作为冻结指令使用，脚本执行仍需独立沙箱与单独授权的工具设计。

| 路径（相对 backend/src/zhiheng） | 职责 |
| --- | --- |
| infrastructure/agents/adapters/agent.py | 官方 Agent、消息/模型桥接、Middleware、流与结果转换 |
| infrastructure/agents/adapters/checkpoints.py | 官方 Saver 连接、版本检查和原子线程清理 |
| shared/agent.py | 无 SDK 依赖的不可变执行设置 |
| modules/models/capacity.py、summary.py | 共用输入估计与自适应摘要预算 |
| application/agents/execution.py | 助手记忆准入、摘要子调用、执行装配与维护 |
| modules/agents/execution.py | 助手冻结执行输入和完成事实 |
| modules/agents/conversations.py | 会话、问答、有效记忆及持久状态 |
| application/chat/agent.py | 普通 Chat 的记忆、摘要用例与业务结果 |

普通 Chat 与助手共用技术机制，分别拥有业务状态和结果接受规则。助手不调用整个 ChatAgentService，也不维护旧执行器后备。

## 正式会话记忆与摘要

新会话从显式、不可匹配 ROOT checkpoint 开始，后续调用使用数据库接受的 head 并只追加本轮用户消息。固定消息身份防止重复回灌。物理最新 checkpoint 不代表业务有效记忆，前端不能提供任意 thread/checkpoint 参数。

线程由 owner_id、agent_id、conversation_id 派生，并使用独立 assistant_checkpoints schema；普通 Chat 的 chat_checkpoints、线程和现有引用保持原位置。读取和执行前仍验证业务所有权，字符串前缀不能替代授权。

助手与普通对话共用 SummaryPolicy：默认依据有效输入预算计算约 70% 触发摘要、20% 保留近期原文，受模型窗口、输出预留、系统前缀与当前问题约束。窗口未知使用保守配置，不从模型名称猜容量。现有实例的会话摘要预算配置同时用于助手。

SummarizationMiddleware 决定摘要前缀与保留消息，平台只提供有准入和账本的摘要端口。主回答一次模型调用；摘要使用独立 agent_summary 子 Run。没有隐式重试、自动 replay 或模型溢出后的再次付费调用。

容量检查覆盖完整有效 checkpoint、当轮前缀、当前问题与输出预留。增量输入不代表只预占当前问题的费用；实际供应商仍会接收完整有效上下文。

## 知识与技能边界

助手已有知识检索继续走准备链：检索准备、embedding/rerank、来源检查、冻结材料，然后生成主回答。检索 IO 不搬进持有业务 SQL 锁的事务，不改为模型自主调用次数的工具循环。普通 Chat 仍不接知识检索。

知识材料通过本轮临时前缀提供，不拼入会被持久保存的用户问题。SDK 记忆保存问答和摘要，不持续累加检索原文。资料是参考数据，其中的指令不获得平台权限。

历史回答或摘要也可能包含知识库派生内容。因此将有效记忆的历史来源与本轮来源合并依赖，在准入、摘要执行和主模型开始前检查当前权限与助手允许的知识库集合。移出配置、撤权或来源不可用时明确拒绝，并引导新建会话；不能仅隐藏引用后继续向模型发送旧内容。

来源依赖去除重复正文，只保留授权核对所需事实；数量有界。技能按本轮最新已选修订冻结，执行前检查该修订仍可用；不会读取一次新修订来替换已受理请求的实际输入。

## 运行、取消和账本

主回答使用已有原生 Run，不增加一层独立 Agent 账本。每个摘要子 Run 保存明确的 parent_run_id、parent_attempt_id、conversation_id 和 owner_id。子调用准入及供应商开始前，均复核父执行仍有效且未取消。

主模型开始标记在摘要和 checkpoint 准备完成后、真正调用主模型前设置。摘要有消费不代表主模型已开始。

原始 usage 是结算事实，LangChain 的输入估计只用于预算。缺失计量保持 unknown；冻结身份、价格、原付费人和原 Key 不随查询或配置修改变化。普通 Chat 的失败免计规则不扩展到助手。

取消先保存原生取消意图，再中止流并取消在途摘要。父执行已终结的摘要由维护任务核对、请求取消，不会为了恢复状态重新请求模型。

模型完成后 checkpoint 保存失败，仍保留正文和已取得的原始 usage，再传播失败或取消。不得为补存储重复调用供应商；GeneratorExit 直接关闭流。

Run 终态、结算、助手完成元数据与有效 head 在同一业务事务提交。接受 head 时检查当前执行、最新消息序号、预期 head 和 base Run；改名不会使合法结果失去记忆。非空 completed 回复可以成为助手历史，length 保留其真实结束原因；failed、cancelled 和晚到孤立分支不推进 head。

## 数据与生命周期

0040_assistant_agents 显式完成：

- 将助手当前配置与启用状态收敛到 agents，退役发布目录和轮数设置。
- 在移除旧发布目录前，将每个历史助手问答的原配置、技能和来源转存到其准确的 Run 审计，不使用当前配置补历史。
- 增加助手执行元数据、有效 checkpoint 与摘要父子身份，创建锁定官方 Saver 表结构。
- 原助手会话继续可读，新建会话启用增量记忆；旧会话不自动导入，不退回旧执行器。
- Work 保留已受理任务成员的配置快照，仅调整配置修订的连接方式，不迁移 Work 的模型执行框架。

迁移发现活跃助手/Work Run、知识准备或缺失原配置的历史问答时拒绝升级，先完成或明确取消原任务。API/Worker 启动不运行 Saver.setup，不自动改表。

删除会话或助手时，后台有界清理对应 SDK checkpoint、writes 和 blobs，采用官方原子删除策略；延迟复扫处理失联 Worker 的迟到写入。Run、调用审计与账本保留。

升级前备份数据库，构建匹配代码和依赖，显式迁移后统一切换 API 与 Worker。旧发布目录无法由当前配置重建，回退需要原数据库备份与匹配代码。

## 验证范围

使用独立 PostgreSQL、Redis 和可控本地 HTTP 模型，验证最新配置、冻结请求、跨进程 checkpoint、摘要子调用、权限撤销、来源边界、取消、存储失败、原始用量、账本追溯、旧数据迁移与清理。普通 Chat 的摘要、附件、重新生成和现有账务同时回归。

真实供应商连通性和质量需要真实供应商验证；本地模型测试证明的是执行协议与平台行为。
