# AI 核心平台整体重构设计

日期：2026-10-23。分支：refactor/ai-core-modularization。状态：工程基础与首条 AI 链路已实现并通过本地验收；整体平台按下述目标分阶段建设。新建基线取代先前的旧平台原位升级要求。

## 1. 已确认的目标与本稿范围

用户已确认：AI 是产品核心；核心业务内置；参考 Yuxi 的职责划分；重视代码规范；模型接入替换为 Python langchain==1.4.7。

用户进一步明确：**保留原前端界面，内部目录和代码可以整理**。这取代此前把最小验收工作台作为产品前端替代品的做法。以 `legacy/frontend/` 中的现有页面、视觉 token、布局、品牌和已完成优化作为界面基准；本轮按新接口接通当前已实现能力，不主动重做页面设计。原有未提交文件保持原样，复用代码进入正式前端时另行整理和验证。

本稿覆盖产品能力、模块职责、运行进程、模型接入、Agent 执行、数据所有权、前端工程、交付规范及实现顺序。按新项目建立代码、数据和发布基线，现有代码作为复用来源，经过新边界与行为验收后纳入。不能用一次大规模移动文件替代新平台实现。

原有未提交优化及源代码完整保留并已在仓库外备份。新平台首版不承担旧管理 API、插件生命周期、数据库原位升级和旧发布包更新/回滚兼容；未来若需要导入旧数据，另建有明确范围的导入工具，不让旧结构成为新核心的依赖。

当前交付依据：[首条 AI 链路验收](../verification/2026-10-23-first-ai-slice.md)。强制规范：[代码规范](../../architecture/2026-10-23-code-standards.md)。

首个交付范围为单实例、组织内多用户的私有部署产品。新模型按实际隔离需求定义字段，不继承只有占位含义的 tenant_id/project_id，也不声明尚未实现的 SaaS 多租户能力。

## 2. 参考 Yuxi 的设计原则

参考版本固定为 Yuxi 提交 1d66405280631d30c8dd006703d9f270606d45a1，避免按持续变化的 main 假定兼容。

采用的原则：内置 AI 主体；入口与业务包分开；请求接入与长任务执行分开；PostgreSQL 保存事实；数据库升级有独立所有者；扩展通过稳定接口接入。

我们的领域和存储按实际能力设计。现有 PostgreSQL/Milvus 图谱能力有独立价值；不因参考 Yuxi 就同时切换 Neo4j、沙盒和文件存储。

可行的代码组织有两种：

| 方案 | 特点 | 本稿决策 |
| --- | --- | --- |
| 按业务模块收拢服务与仓储 | 每个模块拥有完整业务代码，跨模块经公开接口和用例编排 | 采用，便于明确数据与修改责任 |
| Yuxi 式能力包配合全局 services/repositories | 入口统一、层次直观，所有权主要依赖命名和约束 | 参考其分层思路，避免把所有业务服务再次集中成大目录 |

整体保持模块化单体：一个核心包、同一版本发布、同一业务数据库；API、worker、migrator 使用相同应用镜像和不同入口。

## 3. 产品能力与精简范围

| 类别 | 能力及归属 |
| --- | --- |
| AI 核心 | 模型中心、普通对话、知识库、图谱、智能体、技能、工作任务 |
| 开放接入 | 应用、API Key、模型授权、配额、费率、用量记录、OpenAI 兼容 API |
| 平台支撑 | 身份权限、组织、文件、配置、审计、运行监控 |
| 可选接入 | 外部工具、MCP、文档解析引擎、供应商适配 |
| 开发工具 | CLI、迁移诊断、开发脚手架；不作为普通产品业务模块 |
| 退出线上运行 | CRUD generator、调度演示任务、核心 AI 插件启停管理 |

新平台不设置线上 generator 模块。若团队确有持续使用需求，另行作为 tools/codegen 离线工具维护；旧源代码和配置保留在原始记录中。

module_task 不整体删除：scheduler_test 演示代码和种子任务退出生产；file_task 中到期提醒、回收站清理、存储对账迁入 files/jobs；通用调度、租约和执行移入 runtime 与 worker。

新平台首版不实现第三方业务插件安装、启停和卸载。扩展通过供应商 adapter、工具协议和 MCP 接入，核心模块由 bootstrap 显式装配。模块自身管理资源引用与审计。

## 4. 目标目录

以下包含后续业务域，当前不为未实现的能力预建空模块。实际已交付目录见[代码结构说明](../../../代码结构说明.md)：旧工程归档在 `legacy/`，新后端仅使用 `backend/src/zhiheng`。

~~~text
zhiheng/
├── backend/
│   ├── src/zhiheng/
│   │   ├── bootstrap/                 # 配置、依赖装配、进程生命周期
│   │   ├── api/
│   │   │   ├── main.py                # FastAPI 入口
│   │   │   ├── routes/                # 显式注册，HTTP 请求/响应适配
│   │   │   ├── dependencies.py
│   │   │   └── middleware.py
│   │   ├── worker/
│   │   │   ├── main.py                # ARQ 入口
│   │   │   ├── registry.py            # 受控 handler 注册
│   │   │   └── schedules.py           # 投递与恢复周期任务
│   │   ├── application/               # 真实跨模块用例与事务编排
│   │   │   ├── inference.py           # 所有模型调用的授权/配额/用量入口
│   │   │   ├── chat_runs.py
│   │   │   ├── agent_runs.py
│   │   │   ├── work_runs.py
│   │   │   └── knowledge_jobs.py
│   │   ├── modules/
│   │   │   ├── models/                # 模型、连接、能力及供应商适配
│   │   │   ├── chat/                  # 会话、消息、普通生成运行
│   │   │   ├── knowledge/             # 知识元数据、解析、分块、索引、图谱
│   │   │   ├── agents/                # 定义、发布版本、AgentRun、执行适配
│   │   │   ├── skills/                # 技能目录、版本、包与授权
│   │   │   ├── work/                  # 工作任务、成员、快照与业务编排
│   │   │   ├── developer/             # 应用、授权、配额、价格与账本
│   │   │   ├── identity/              # 用户、组织、角色和权限
│   │   │   ├── files/                 # 文件元数据、访问与保留规则
│   │   │   └── operations/            # 审计、系统配置和受控调度管理
│   │   ├── runtime/                   # outbox、租约、事件、取消、恢复原语
│   │   ├── infrastructure/            # DB/Redis/字节存储等技术实现
│   │   ├── shared/                    # 少量公共值类型与基础错误
│   │   └── cli/                       # API/worker/migrate/诊断命令
│   ├── migrations/                    # 新平台独立的单一 schema 版本图
│   ├── tests/
│   │   ├── unit/
│   │   └── core/                       # unit、integration、contract、e2e
│   ├── pyproject.toml
│   └── requirements-core.lock         # 固定版本与哈希的部署依赖
├── frontend/
│   ├── src/
│   │   ├── app/                       # 入口、显式路由和能力装配
│   │   ├── layouts/                   # 工作台、管理后台
│   │   ├── modules/                   # 与产品业务对应
│   │   │   ├── chat/
│   │   │   ├── models/
│   │   │   ├── knowledge/
│   │   │   ├── agents/
│   │   │   ├── skills/
│   │   │   ├── work/
│   │   │   ├── developer/
│   │   │   └── system/
│   │   └── shared/                    # UI、HTTP、样式与轻量公共逻辑
│   ├── tests/
│   ├── package.json
│   └── package-lock.json
├── deploy/
├── docs/
│   ├── architecture/
│   ├── runbooks/
│   └── superpowers/
├── tools/
└── compose.yaml
~~~

后端模块按需包含 public.py、schemas.py、service.py、repository.py、orm.py、adapters/、jobs/。简单模块不强制创建所有文件；复杂模块按子职责拆分。

HTTP schema 转换和响应装配在 api/routes；业务校验与资源所有权在模块服务；只有跨多个模块的用例放 application。禁止 application 再为每个单模块方法建立一层转发。

前端模块按需包含 views、components、api.ts、store.ts、composables、public.ts、routes.ts。根布局通过公开选择器和路由入口消费业务，不能导入模块内部视图。

## 5. 依赖与事务规则

~~~text
单模块入口：api / worker / cli → 模块 public → 模块内部规则与 repository
跨模块入口：api / worker / cli → application → 各模块 public → 模块内部实现

bootstrap 负责把 infrastructure、供应商适配和模块协议连接起来。
shared 不反向依赖 modules；模块不依赖 api、worker 或其他模块私有实现。
~~~

单模块用例由其服务持有事务；跨模块一致性由 application 持有同一 PostgreSQL session。仓储不自行提交；网络调用在短事务之外执行。

示例：开发者调用先由 developer 校验应用和授权，再由 models 解析模型，developer 建立调用/配额事实，供应商执行后回到 developer 结算。models 不导入 developer，API 不直接操作两者 ORM。

跨模块引用可保留外键与行锁；公开接口暴露值对象和明确的只读/写入语义。模型删除仍要检查 chat、knowledge、agents、work、developer 保留的历史引用。

## 6. 运行进程与交付拓扑

~~~mermaid
flowchart LR
    Browser[浏览器] --> Web[Web / Nginx]
    External[外部 API 客户端] --> Web
    Web --> API[API]
    API --> PG[(PostgreSQL)]
    API --> Redis[(Redis)]
    PG --> Publisher[Outbox Publisher / Reconciler]
    Publisher --> Redis
    Redis --> Worker[Worker]
    Worker --> PG
    Worker --> Model[LangChain 供应商适配]
    Model --> Provider[上游模型]
    Worker --> Milvus[(Milvus)]
    Worker --> Files[受管文件存储]
    Migrator[Migrator] --> PG
~~~

- API：HTTP、鉴权、接入事务、状态查询和 SSE 订阅。
- worker：聊天/Agent/Work 长运行、知识处理、文件维护、投递与失联恢复。
- migrator：新平台业务 schema、框架持久化 schema、基础权限和后续版本升级。
- PostgreSQL：消息、运行、授权、账本、知识元数据和迁移版本的事实来源。
- Redis：首条链路用于 ARQ 和 worker 心跳；后续按实际需要增加短期事件、通知与缓存，不独占终态事实。
- 文件：文件域交付时使用独立的受管持久卷与 StoragePort；多节点前需落实共享对象存储与恢复验收。
- Milvus：使用新平台独立的索引命名空间；图谱首期采用已验证的 PostgreSQL/Milvus 路线。

API/worker 的副本数可分别调整，但必须通过租约与跨进程验收。初始默认一个 worker，不能把移出 Web 进程直接等同于已支持多节点。

新平台使用独立数据库、Redis 命名空间、文件根目录和部署项目身份，不读取旧实例的环境文件或挂载旧业务数据卷。目标编排只包含新平台所需服务，不部署 LiteLLM Proxy 或其专用 Redis。旧实例已按用户后续明确指令停止，原容器及数据保留；原前端复用不以重新启动旧后端为前提。

## 7. LangChain 1.4.7 模型接入

唯一主线是 langchain==1.4.7 的供应商直连。目标业务包、SDK 和运行编排不依赖 LiteLLM，不设置双引擎兼容层。

已核对 PyPI 元数据：langchain 1.4.7 要求 Python >=3.10,<4；依赖 langchain-core >=1.6.13,<2 和 langgraph >=1.2.12,<1.3。主包与供应商集成包版本号不是同一套，供应商包、checkpointer、ARQ 必须按解析出的兼容版本完整锁定。安装与解析只在隔离构建环境进行。

models 拥有：

| 对象 | 含义 |
| --- | --- |
| ModelConnection | 供应商类型、地址、加密凭据、连接版本 |
| ModelDefinition | 平台稳定 ID/编码、真实模型名、类型、能力与参数 |
| ModelBindingRevision | 模型与连接的不可变绑定快照 |
| ModelCapabilities | 对话、视觉、工具、结构化输出、Embedding、Rerank 支持矩阵 |
| ModelInvoker | 平台规范化的请求、流事件、响应与用量接口 |

供应商工厂使用专用 LangChain 集成包；OpenAI 协议兼容端点、Anthropic、Google 等逐个建立并验收支持矩阵。未支持的供应商或能力在连接配置和调用入口明确拒绝，不能静默降级。

首条链路仅支持 OpenAI 兼容端点的文本聊天与流式回复，其他供应商和能力明确拒绝。图片、工具调用、结构化输出、Embedding 与 Rerank 随所属业务域分别适配并验收，不能假定聊天模型对象实现全部能力。

规范化消息支持 system/user/assistant/tool、内容块、tool_call_id 与工具参数；流支持文本、推理、工具调用增量、用量和结束原因。LangChain 消息不流出 adapter 成为全平台数据模型。

当前每次调用拥有独立客户端并在结束时关闭，避免框架共享缓存被关闭后影响后续请求。后续若引入缓存，按连接版本与能力区分并明确关闭所有者；轮换密钥使缓存失效。原始密钥仅由连接所有者解密，不能进入队列 payload、LangGraph checkpoint 或普通日志。

模型使用新平台稳定 ID 与不可变绑定版本，不继承 LiteLLM 别名或 engine_type 作为核心字段。连接凭据由管理员配置并完成连接验收。连接密文有独立密钥版本与恢复材料，不能复用登录 JWT 或 API Key 摘要的秘密，也不能将掩码作为密钥保存。

所有模型调用，包括普通聊天、Agent 内部多次调用、知识抽取与公开 API，都经 application/inference。ModelInvoker 协议和消息值类型由 models/public.py 定义；原始供应商工厂只允许 bootstrap 装配访问，application 接收原始协议实现，不导入供应商私有构造器。bootstrap 把受治理的协议实现注入 Agent、chat 和 knowledge，模块不反向导入 application。

采用 create_agent 时，modules/agents/adapters 中的桥接器接收 bootstrap 注入的受治理 ModelInvoker，覆盖 invoke、stream、工具绑定及结构化输出入口；LightRAG 的模型/向量回调同样注入受治理实现。连接测试作为明确的调用来源记录；禁止以默认 SDK 或环境变量回退绕过授权、配额与记录。允许入口与 SDK 导入路径由 AST 门禁验证。

LangChain 未返回的用量以 unknown 或明确 estimated 保存；缓存、推理 Token 与上游成本分开归一，不能将估算值标成真实值。

## 8. 模型授权、配额与公开 API

developer 继续拥有应用、API Key、授权、配额、费率和 UsageLedger，供应商适配层不拥有这些业务规则。内部与外部调用使用明确的 Principal 和调用来源。

Invocation 表示一次逻辑模型调用，身份由运行/工作流 lineage + 稳定逻辑步骤实例 ID 唯一确定，请求指纹作为不可变字段校验；同身份不同指纹拒绝，不能因内容变化创建新身份。循环中有意发起的新调用使用新的步骤实例 ID，重试和审批恢复复用原身份。每次真实供应商尝试另有 InvocationAttempt ID，并关联执行 attempt。一个 AgentRun 可包含多个 Invocation；一个 WorkRun 可关联多个 AgentRun。运行次数与模型调用次数不混为一个计费单位。

预留键固定为 invocation_id + reservation_version，供应商尝试以 invocation_attempt_id 唯一，账本分录按 invocation_attempt_id + charge_kind 唯一，逻辑调用的汇总结算按 invocation_id + settlement_version 唯一。冻结模型绑定、费率和配额政策版本；重复提交同键不同指纹拒绝，重复结算返回已提交结果。

首条链路使用 PostgreSQL 账户行锁预留 Token，接入、终态与用量事实在同一数据库事务中一致提交。当前不引入 Redis 配额双写或货币计费；未来确需 Redis 协调时，必须同时实现稳定键、补偿与对账。上游已执行但响应或落账不确定时，用量标记 unknown，保留预留和关联证据，不按零费用失败处理，也不自动重放非幂等请求。供应商支持幂等时复用原供应商请求键；其余情况需对账或按明确的估算策略终结。

默认关闭不可观测的 SDK 自动重试，由受治理适配器执行有预算的重试并逐次记录 InvocationAttempt；不能关闭或不能观察重试的集成不进入首个支持矩阵。实际供应商成本与对用户计费策略分别版本化，未知上游结果下不承诺准确去重真实成本。

外部 /v1 Chat Completions、Embedding、Rerank 按公开协议提供独立 API adapter。这里的兼容指外部模型协议，不是旧平台路由。短期有界的公开 API 请求可在 API 进程执行统一 inference 用例，仍具有持久调用状态、取消与失联对账；长任务和 Agent 执行由 worker 持有。

## 9. 请求、执行、事件与恢复

聊天、Agent、Work、知识任务保留各自业务状态，共用投递、租约、取消与恢复原语。

1. 接入事务写消息、业务 Run 与 Outbox；验证客户端幂等键对应的请求指纹。
2. 事务提交后投递 ARQ；投递失败由 publisher 从 PostgreSQL 补发。
3. worker 以 Run 行版本和 attempt token 认领租约，重复投递无执行权即退出。
4. 心跳续租、取消检查使用独立短事务，不持有模型调用的长数据库事务。
5. 首条链路的 SSE 返回 PostgreSQL 中完整 RunSnapshot，以 id、attempt_id、revision、projection_seq 去重和恢复；订阅结束不取消运行。后续若引入增量事件协议与 Redis Stream，另定义 schema_version、event_id、seq 和持久快照回退契约。
6. 写终态时再次验证 attempt，把最终消息、Run 结果引用和关联账本事实一致提交；跨存储结算由可重试补偿完成。
7. reconciler 收敛过期执行和缺失投递；其启动与健康不依赖一次性迁移角色。

普通 Run 状态为 pending → running → completed/failed/cancelled/interrupted；running 可先进入 cancel_requested。终态不可被晚到事件改写。

取消先写 PostgreSQL，执行器通过独立短事务观察，网络流静默时也能关闭；Redis 加速通知按后续容量需求引入。异步 Run API 的订阅断线不自动取消运行；短期同步公开模型 API 按自身请求生命周期冻结取消策略。

失联 attempt 不能重新取得原写权限。普通模型调用和非幂等工具不自动无限重放；失败原因与是否允许恢复由 handler 定义。租约保护数据库写入，不能保证已发送的外部副作用 exactly-once。

LangGraph checkpoint 使用 PostgreSQL，以 thread/run/attempt 命名空间隔离；仅当前 attempt 的有效检查点引用可被业务 Run 认领。晚到旧 checkpoint 不覆盖当前恢复指针。框架 checkpointer 与业务 session 不假定共享事务。

审批 interrupt 先确认不可变 checkpoint 已持久化，再以验证当前租约的短事务发布 ResumeHandle、ApprovalRequest 与运行暂停事实。ResumeHandle 固定源 Run/attempt/checkpoint、冻结快照及审批版本；未发布引用的孤立 checkpoint 由对账回收，不能被恢复接口直接认领。

审批 CAS 通过后，在同一事务中认领 ResumeHandle、创建关联的新 Run 和 outbox；审批版本有唯一约束，重复请求返回同一新 Run。工具的 logical_operation_id 跨新 Run/attempt 保留，执行前核对副作用账本；outcome_unknown 不因重新构图而获得重放资格。

## 10. 业务模块与数据所有权

| 模块 | 主要资源与所有权 |
| --- | --- |
| models | 模型、连接、绑定版本、能力；提供只读解析与执行适配 |
| chat | Session、用户/助手消息、ChatRun、普通对话偏好 |
| agents | Agent 定义、发布版本、AgentRun/Attempt、审批、工具执行审计 |
| skills | 技能包、草稿、发布版本、使用权限与引用快照 |
| work | 工作任务、成员与版本快照、WorkRun、业务历史；关联 AgentRun |
| knowledge | 知识库、文档、分块/索引任务、图谱版本与检索元数据 |
| developer | 应用、Key、授权、价格、配额、Invocation、UsageLedger |
| identity | 用户、组织、角色、权限与 Principal 的可信构建 |
| files | 文件元数据、字节引用、ACL、保留期、回收与对账 |
| operations | 系统配置、审计、受控 schedule 定义与运行视图 |

Chat/Agent 的模型与工具审计不混进普通展示历史。工作任务是业务编排，队列 Task 是执行载体，两者分别建模。

跨域关键变更只有一个事务所有者。资源删除保持保守引用检查：历史保留、未知归属或查询失败均不能按无引用处理。

## 11. 知识库、文件与图谱

知识元数据直接存入 PostgreSQL，原文与解析产物进入 files 的受管字节存储。知识模块保存业务引用，files 保存对象生命周期；模型和引用检查可以使用同库事务及约束。

解析、分块、Embedding、图谱和评估是可恢复的阶段任务。每次构建尝试使用独立物理产物空间，身份包含 kb_id、index_revision、build_attempt_id；Milvus collection、图谱 workspace 与临时文件都按此身份隔离。已发布产物不可原地修改，失联 attempt 只能影响自己的未发布空间。

发布使用同一 PostgreSQL 短事务校验 attempt 并切换不可变 manifest 指针。首期采用 shadow generation，可复用经过验证的解析/向量缓存；共享可写空间只有具备存储端 fencing 并单独验收后才允许引入。垃圾回收先把无引用 generation 标为 deleting，再按完整物理身份删除；引用/发布拒绝 deleting 状态，禁止按知识库 ID 粗粒度 drop 当前集合。

向量兼容以原模型/参数、维度、模型绑定快照和索引版本校验；更换 adapter 不自动证明向量兼容。改变模型或维度时创建新索引版本并重建，不直接覆盖旧集合。

LightRAG 的 PostgreSQL 图谱/KV/doc-status 和 Milvus 映射保持可追溯。第三方存储 setup 由 migrator 的专用适配执行；runtime 不自行建表。适配必须验证第三方初始化路径符合此约束，否则不得进入目标交付。

工具和 API 通过 files 获取受管对象引用，不取得任意宿主 Path。语义搜索、受管附件与 Agent 产物有明确归属，不能共用无权限的目录遍历入口。

## 12. Agent、技能与扩展

Agent 是定义、版本和执行策略的组合。运行冻结 AgentVersion、SkillRevision、知识引用和模型绑定版本；发布修改不改变正在执行的快照。

LangChain 1.4.7 用于模型和标准 Agent 循环，LangGraph 用于需要确定分支、检查点和审批的编排。只有实际需求才引入自定义图，避免同时维护多个相同职责的运行框架。

工具使用受控注册项和有类型的输入输出；MCP 是连接适配。技能只开放其明确依赖的工具。权限变更对新调用按规定的缓存期限生效，敏感变更主动失效。

普通第三方 Python 业务插件退出核心装配。Agent 扩展不以任意宿主代码执行代替工具协议；需要命令执行时以独立沙箱能力作为后续子项目，不能在本轮随意开放宿主脚本。

## 13. 身份、导航与前端

identity 拥有新平台的用户、组织、角色和权限模型。复用经过验证的认证与授权规则，按新业务资源定义权限码和初始化种子，不以旧菜单、旧表名或插件状态作为核心授权来源。

内置路由由 app 显式注册。前端模块 routes.ts 暴露页面路由与导航元数据；服务端提供用户权限和能力状态。菜单隐藏只控制体验，API 与资源服务始终验证授权。

普通用户工作台与管理后台分开呈现，按角色授予 AI 资源权限；代码生成和插件启停不进入核心导航。

前端保留现有界面和交互。允许按职责整理目录、补齐类型及替换内部数据访问实现，但不能以迁移为由另起一套产品视觉。登录、侧栏、顶部账户区、聊天、模型目录/凭据和用量页面以原页面为基准；原品牌资源与视觉 token 直接复用，保留许可。迁移模块的 API、状态、流协议和测试一起接入，禁止同时保留两个可写 store。

新后端的 Cookie 身份、直接资源 JSON、模型绑定和 Run/SSE 由明确适配边界接入。当前后端尚未支持的旧页面能力保留来源并逐域承接，不能通过假数据、无效按钮或重启旧后端伪装成已经迁移完成。当前临时工作台已验证的取消、恢复、幂等、权限和未知用量语义必须保留。

app/runtime 的 RunStore 只拥有运行状态、订阅与游标；模块 store 拥有业务资源和消息，通过受控 reducer 应用投影，不复制第二份可写运行状态。页面切换关闭订阅与用户停止运行是不同操作。

GET RunSnapshot 返回 run_id、current_attempt_id、单调递增 attempt_no、status、revision、持久 projection、projection_seq 和终态 result_ref。事件 seq 在 attempt 内递增，事件身份是 run_id + attempt_id + seq，不能跨 attempt 直接拼接。工作进程按有界间隔写持久投影检查点，不要求每个 Token 写 PostgreSQL；终态结果先提交，再通知。

订阅从快照的同 attempt projection_seq 之后开始；已覆盖事件去重，发现序号缺口、事件窗口过期或不同 attempt 时服务端发送 reset_required，客户端重取快照，丢弃旧订阅投影后从新游标恢复。未被投影检查点或事件窗口覆盖的过程片段不承诺无损重放；终态消息以持久结果为准。临时 gap 时等待新快照，不能把缺口后的 delta 直接追加到旧文本。

## 14. 新平台数据与发布基线

首版只支持 PostgreSQL，使用独立的空库初始化与 Alembic 版本图，不维护 MySQL 平行实现或旧插件数据库版本图。

migrator 是唯一 schema 变更入口：先获取迁移锁并预检，再执行 Alembic 版本图、必要种子和框架存储 setup。API/worker 只检查新平台版本兼容并装配运行资源，不执行 create_all。

新平台拥有自己的发布记录、实例身份和兼容性指纹。旧平台发布包与新平台不属于同一快捷更新/回滚链；不通过还原旧目录名、归一化旧脚本或伪造旧指纹来制造兼容。

新平台的后续版本升级仍需验证数据和运行契约；跨契约变更需要明确升级步骤及备份恢复验收。首版不要求旧实例升级、旧账本接管或 LiteLLM 网关退役作为发布前置条件。

已有业务优化作为实现素材保留；新核心不依赖旧插件表、enabled 状态、旧管理路由或旧 Python 导入路径。维护模式由新平台明确配置表达。

未来的数据导入作为独立、离线、可重试的工具立项，明确权限、文件、模型和索引映射，不直接挂载或原地改写旧实例存储。当前不自动导入、清空或重编号旧数据。

## 15. 调度、文件维护与 generator 处理

| 当前能力 | 目标处理 |
| --- | --- |
| module_generator 及 tool/gen 页面/API | 不纳入新平台；有真实需求再做离线 tools/codegen |
| module_task.scheduler_test | 移除生产示例及对应种子 schedule |
| 文件到期提醒 | files/jobs/retention，由 worker 注册执行 |
| 回收站清理 | files/jobs/recycle，保留保留期与批量上限 |
| 文件存储对账 | files/jobs/reconcile，保留校验和历史结果 |
| 配额/账本对账 | developer/jobs/reconcile |
| APScheduler 宿主装配 | 由统一 worker 调度和 PostgreSQL schedule occurrence 接管 |
| 自定义 dotted invoke_target | 新平台只接收受控 JobDefinition ID，不执行用户输入的 Python 导入路径 |

调度注册使用稳定任务 ID 和有版本的 payload；一次触发以 schedule_id + scheduled_at 唯一化。配置明确时区、下次触发、暂停状态、积压上限与并发上限，不继承旧 Quartz 数值标记。

misfire_policy 使用明确枚举 catch_up_all、coalesce_once、skip；catch_up_all 必须有上限和积压告警。max_active_per_schedule 限制同一调度的并发执行，手动触发也共享相同互斥策略。

文件提醒按 file_id + policy_version + notice_window 幂等；回收清理和对账按任务策略禁止重叠。分别验收停机跨多个触发点、手动与定时竞争、暂停恢复，确保无双跑、漏跑或重复通知。

示例注册项：files.retention.scan.v1、files.recycle.purge.v1、files.storage.reconcile.v1、developer.usage.reconcile.v1。业务 handler 在所属模块，runtime 仅负责调度原语。

## 16. 可重复构建、质量与观测

- 后端 pyproject 声明直接依赖，requirements-core.lock 锁定完整部署依赖和哈希；CI 在干净环境验证解析与安装。
- 前端提交 package-lock.json，用 npm ci 构建；发布不借用构建机任意 node_modules。
- API、worker、migrator 同一源码快照和镜像版本；前端源码、锁文件、工具链、静态产物及镜像摘要关联。
- CI 对重构分支和目标 PR 都生效，覆盖代码规范、类型、模块边界、迁移、生产构建和关键真实链路。
- /health/live 表达进程存活；/health/ready 检查 schema、PG/Redis 和所需 worker 的新鲜健康事实。
- /health/capabilities 表达模型连接、知识检索、解析器等能力状态；供应商临时故障不靠重启整个 API 解决。
- worker 健康包含消费、心跳、publisher/reconciler 最近成功事实，不能只持有永久 ready 标记。
- 监控关注排队时间、首 Token、活跃 Run、租约过期、取消延迟、对账积压、连接池与磁盘；容量结果以实测为准。

## 17. 实施子项目及验收门槛

| 阶段 | 交付范围 | 进入下一阶段前的证据 |
| --- | --- | --- |
| A：基线与工程骨架 | 锁文件、代码规范门禁、新核心包、原有优化备份与目录整理 | 可重复安装/生产构建；基线清楚；不引入新增依赖违规 |
| B：新平台启动闭环 | 独立配置、显式 AI 装配、空库初始化、身份/文件边界及前端应用入口 | 不加载插件运行时即可启动；空库、权限和基础页面通过 |
| C：LangChain 1.4.7 模型闭环 | 连接/密钥/模型契约、授权/账本、公开 API；models/developer 前端同步实现 | 模型配置、连接验证、受治理调用与开发接入真实链路通过 |
| D：统一执行与业务闭环 | ARQ/outbox、租约/取消/恢复；chat、agents、work 的 API、store、页面和流协议 | 每域重复投递/重启/取消及构建后浏览器验收通过 |
| E：知识与调度闭环 | 知识元数据、隔离索引、文件维护；knowledge 与任务运行视图 | 权限/索引版本清楚；任务无双跑漏跑；对应生产页面通过 |
| F：新项目交付 | 新版默认入口、依赖与编排收敛、全量新代码门禁、发布和恢复演练 | 新平台可从干净源码和空库独立部署；不依赖旧代码目录和旧数据 |

按域完成行为、数据、前端、测试和观测的闭环后再扩大范围。每个阶段形成独立的详细实施计划和可评审提交；禁止以“新目录已建立”作为阶段完成标准。

## 18. 关键验收场景

1. 同版本重启、worker 异常退出后，恢复扫描仍存在，未完成 Work/Chat 状态可收敛。
2. 模型静默超过取消通知 TTL、连接不断开时，持久取消仍生效。
3. 两个 worker 重复收到同一任务，只允许当前 attempt 推进状态；旧 worker 晚到写入被拒绝。
4. 入库成功、队列投递失败后，可从 outbox 补发；Redis 丢失事件可从数据库恢复终态。
5. LangChain 的文本、图片、工具增量、结构化输出、Embedding/Rerank、异常和 usage 通过规范契约。
6. Agent 循环每次模型调用都经过授权与用量治理；供应商重试和模型步骤不重复计费或隐匿成本。
7. 密钥轮换、模型绑定版本与账本事实可追溯；未知供应商、资源引用和不兼容向量得到明确拒绝。
8. 生成器下线后，文件保留、回收、对账与业务调度持续工作。
9. 新平台接口与页面权限一致；页面切换、退出登录、SSE 重连不会串会话或错认运行。
10. 干净源码安装、空库初始化、生产产物浏览器链路及新平台自身的数据恢复有独立验收记录。

## 19. 设计来源与当前状态

- [Yuxi 架构参考](https://github.com/xerrors/Yuxi/blob/1d66405280631d30c8dd006703d9f270606d45a1/ARCHITECTURE.md)
- [LangChain 1.4.7 包元数据](https://pypi.org/pypi/langchain/1.4.7/json)
- [LangChain 模型接口](https://docs.langchain.com/oss/python/langchain/models)
- [LangGraph 持久化](https://docs.langchain.com/oss/python/langgraph/persistence)
- [ARQ 的重复执行语义](https://arq-docs.helpmanual.io/)

整体设计和代码规范作为持续实施基线。首条链路已实现独立配置与迁移、身份、LangChain 1.4.7 模型接入、聊天、持久运行与 Token 配额，并提供新平台 Compose/CI；详见[首条 AI 链路验收](../verification/2026-10-23-first-ai-slice.md)。这覆盖 B/C/D/F 的部分内容，不代表文件、公开模型 API、知识库、Agent、Work、货币计费或完整部署恢复演练已完成。旧代码与原有 55 项未提交优化保留在 `legacy/`；旧文档、设计素材和重复前端目录已移至仓库外归档，位置见[文档索引](../../README.md)。
