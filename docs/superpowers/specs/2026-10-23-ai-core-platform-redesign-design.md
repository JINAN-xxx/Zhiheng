# AI 核心平台整体重构设计

日期：2026-10-23。分支：refactor/ai-core-modularization。状态：用户已确认并授权实施，当前进入阶段 A；本稿不表示全部业务重构已完成。

## 1. 已确认的目标与本稿范围

用户已确认：AI 是产品核心；核心业务内置；参考 Yuxi 的职责划分；重视代码规范；模型接入替换为 Python langchain==1.4.7。

本稿覆盖产品能力、模块职责、运行进程、模型接入、Agent 执行、数据所有权、前端工程、交付规范及迁移顺序。实施按子项目分别落地，不能用一次大规模移动文件替代行为迁移。

现状依据：[平台评估清单](../../architecture/2026-10-23-platform-assessment.md)。强制规范：[代码规范草案](../../architecture/2026-10-23-code-standards.md)。

本稿建议以单实例、组织内多用户的私有部署产品作为首个交付范围。现有 tenant_id/project_id 预留字段不构成 SaaS 多租户能力；保留历史值，不在本轮赋予新的隔离语义。

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

generator 生成的是旧 controller/service/dao 模板，默认从线上产品移除。若团队确有持续使用需求，另行作为 tools/codegen 离线工具维护；保留旧生成配置的归档证据，目录迁移阶段不自动删除历史表。

module_task 不整体删除：scheduler_test 演示代码和种子任务退出生产；file_task 中到期提醒、回收站清理、存储对账迁入 files/jobs；通用调度、租约和执行移入 runtime 与 worker。

第三方完整业务插件体系的产品需求尚未确定。本轮核心业务退出其生命周期；历史迁移、资源引用责任与审计记录仍需迁移保留。

## 4. 目标目录

~~~text
zhiheng/
├── backend/
│   ├── src/zhiheng/
│   │   ├── bootstrap/                 # 配置、依赖装配、进程生命周期
│   │   ├── api/
│   │   │   ├── main.py                # FastAPI 入口
│   │   │   ├── routes/                # 显式注册，HTTP 请求/响应适配
│   │   │   ├── dependencies/
│   │   │   └── middleware/
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
│   ├── migrations/                    # 单一版本图及历史导入适配
│   ├── tests/
│   │   ├── unit/
│   │   ├── integration/
│   │   ├── contract/
│   │   └── e2e/
│   ├── pyproject.toml
│   └── requirements.lock              # 固定版本与哈希的部署依赖
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
- migrator：业务 schema、框架持久化 schema、基础权限与历史数据升级。
- PostgreSQL：消息、运行、授权、账本、知识元数据和迁移版本的事实来源。
- Redis：ARQ、短期流事件、快速取消通知、配额协调及缓存；不独占终态事实。
- 文件：首个交付使用现有受管持久卷与 StoragePort；多节点前需落实共享对象存储与恢复验收。
- Milvus：保留现有向量索引；图谱首期沿用已验证的 PostgreSQL/Milvus 路线。

API/worker 的副本数可分别调整，但必须通过租约与跨进程验收。初始默认一个 worker，不能把移出 Web 进程直接等同于已支持多节点。

目标运行不部署 LiteLLM Proxy 或其专用 Redis。当前名为 litellm-db 的 PostgreSQL 同时存放平台业务库，切换服务命名时保留业务数据卷，先迁移凭据和绑定，再退役网关数据库。

## 7. LangChain 1.4.7 模型接入

唯一主线是 langchain==1.4.7 的供应商直连。LiteLLM 只作为历史配置迁移来源，目标业务包、SDK 和运行编排不依赖它。

已核对 PyPI 元数据：langchain 1.4.7 要求 Python >=3.10,<4；依赖 langchain-core >=1.6.13,<2 和 langgraph >=1.2.12,<1.3。主包与供应商集成包版本号不是同一套，供应商包、checkpointer、ARQ 必须按解析出的兼容版本完整锁定。安装与解析只在隔离构建环境进行。

models 拥有：

| 对象 | 含义 |
| --- | --- |
| ModelConnection | 供应商类型、地址、加密凭据、连接版本 |
| ModelDefinition | 平台稳定 ID/编码、真实模型名、类型、能力与参数 |
| ModelBindingRevision | 模型与连接的不可变绑定快照 |
| ModelCapabilities | 对话、视觉、工具、结构化输出、Embedding、Rerank 支持矩阵 |
| ModelInvoker | 平台规范化的请求、流事件、响应与用量接口 |

供应商工厂使用专用 LangChain 集成包；OpenAI 协议兼容端点、Anthropic、Google 等按实际存量连接建立支持矩阵。未支持的供应商或能力在升级预检中列出资源 ID 并阻止切换，不能静默降级。

初始适配覆盖聊天、流式、图片、工具调用、结构化输出、Embedding 与 Rerank。Embedding 和 Rerank 分别适配；不能假定聊天模型对象实现全部能力。

规范化消息支持 system/user/assistant/tool、内容块、tool_call_id 与工具参数；流支持文本、推理、工具调用增量、用量和结束原因。LangChain 消息不流出 adapter 成为全平台数据模型。

客户端按连接版本与能力缓存；轮换密钥使缓存失效。原始密钥仅由连接所有者解密，不能进入队列 payload、LangGraph checkpoint 或普通日志。

迁移使用平台模型稳定 ID 对齐旧别名与真实供应商型号，历史 Run 和旧绑定快照保留 engine_type=litellm 的事实；为后续调用创建 LangChain 绑定版本，不能覆盖历史快照。旧网关无法导出可用密钥时要求重新录入/轮换并完成连接验收，不能将掩码作为密钥导入。新连接密文有独立密钥版本与恢复材料，不能复用登录 JWT 或 API Key 摘要的秘密。

所有模型调用，包括普通聊天、Agent 内部多次调用、知识抽取与公开 API，都经 application/inference。ModelInvoker 协议和消息值类型由 models/public.py 定义；原始供应商工厂只允许 bootstrap 装配访问，application 接收原始协议实现，不导入供应商私有构造器。bootstrap 把受治理的协议实现注入 Agent、chat 和 knowledge，模块不反向导入 application。

采用 create_agent 时，modules/agents/adapters 中的桥接器接收 bootstrap 注入的受治理 ModelInvoker，覆盖 invoke、stream、工具绑定及结构化输出入口；LightRAG 的模型/向量回调同样注入受治理实现。连接测试作为明确的调用来源记录；禁止以默认 SDK 或环境变量回退绕过授权、配额与记录。允许入口与 SDK 导入路径由 AST 门禁验证。

LangChain 未返回的用量以 unknown 或明确 estimated 保存；缓存、推理 Token 与上游成本分开归一，不能将估算值标成真实值。

## 8. 模型授权、配额与公开 API

developer 继续拥有应用、API Key、授权、配额、费率和 UsageLedger，供应商适配层不拥有这些业务规则。内部与外部调用使用明确的 Principal 和调用来源。

Invocation 表示一次逻辑模型调用，身份由运行/工作流 lineage + 稳定逻辑步骤实例 ID 唯一确定，请求指纹作为不可变字段校验；同身份不同指纹拒绝，不能因内容变化创建新身份。循环中有意发起的新调用使用新的步骤实例 ID，重试和审批恢复复用原身份。每次真实供应商尝试另有 InvocationAttempt ID，并关联执行 attempt。一个 AgentRun 可包含多个 Invocation；一个 WorkRun 可关联多个 AgentRun。运行次数与模型调用次数不混为一个计费单位。

预留键固定为 invocation_id + reservation_version，供应商尝试以 invocation_attempt_id 唯一，账本分录按 invocation_attempt_id + charge_kind 唯一，逻辑调用的汇总结算按 invocation_id + settlement_version 唯一。冻结模型绑定、费率和配额政策版本；重复提交同键不同指纹拒绝，重复结算返回已提交结果。

Redis Lua 继续负责快速预留/结算协调，PostgreSQL 保留可追溯事实；两者之间使用具备上述稳定键的补偿与对账。上游已执行但响应或落账不确定时进入 outcome_unknown，保留预留和关联证据，不按零费用失败处理，也不自动重放非幂等请求。供应商支持幂等时复用原供应商请求键；其余情况需对账或按明确的估算策略终结。

默认关闭不可观测的 SDK 自动重试，由受治理适配器执行有预算的重试并逐次记录 InvocationAttempt；不能关闭或不能观察重试的集成不进入首个支持矩阵。实际供应商成本与对用户计费策略分别版本化，未知上游结果下不承诺准确去重真实成本。

外部 /v1 Chat Completions、Embedding、Rerank 等既有契约由兼容 adapter 提供。短期有界的公开 API 请求可在 API 进程执行统一 inference 用例，仍具有持久调用状态、取消与失联对账；长任务和 Agent 执行由 worker 持有。不存在跳过平台业务控制的第二条直连路线。

## 9. 请求、执行、事件与恢复

聊天、Agent、Work、知识任务保留各自业务状态，共用投递、租约、取消与恢复原语。

1. 接入事务写消息、业务 Run 与 Outbox；验证客户端幂等键对应的请求指纹。
2. 事务提交后投递 ARQ；投递失败由 publisher 从 PostgreSQL 补发。
3. worker 以 Run 行版本和 attempt token 认领租约，重复投递无执行权即退出。
4. 心跳续租、取消检查使用独立短事务，不持有模型调用的长数据库事务。
5. 外部事件带 schema_version、run_id、attempt_id、event_id、seq；Redis Stream 是短期投影。
6. 写终态时再次验证 attempt，把最终消息、Run 结果引用和关联账本事实一致提交；跨存储结算由可重试补偿完成。
7. reconciler 收敛过期执行和缺失投递；其启动与健康不依赖一次性迁移角色。

普通 Run 状态为 pending → running → completed/failed/cancelled/interrupted；running 可先进入 cancel_requested。终态不可被晚到事件改写。

取消先写 PostgreSQL；Redis 用作加速信号。执行器独立观察取消，网络流静默时也能关闭。新异步 Run API 的断线不自动取消；旧同步 SSE 兼容入口按原语义显式写取消，策略随请求冻结。

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

知识元数据从 metadata.json 迁入 PostgreSQL，原文与解析产物进入 files 的受管字节存储。知识模块保存业务引用，files 保存对象生命周期；模型和引用检查可以使用同库事务及约束。

解析、分块、Embedding、图谱和评估是可恢复的阶段任务。每次构建尝试使用独立物理产物空间，身份包含 kb_id、index_revision、build_attempt_id；Milvus collection、图谱 workspace 与临时文件都按此身份隔离。已发布产物不可原地修改，失联 attempt 只能影响自己的未发布空间。

发布使用同一 PostgreSQL 短事务校验 attempt 并切换不可变 manifest 指针。首期采用 shadow generation，可复用经过验证的解析/向量缓存；共享可写空间只有具备存储端 fencing 并单独验收后才允许引入。垃圾回收先把无引用 generation 标为 deleting，再按完整物理身份删除；引用/发布拒绝 deleting 状态，禁止按知识库 ID 粗粒度 drop 当前集合。

向量兼容以原模型/参数、维度、模型绑定快照和索引版本校验；更换 adapter 不自动证明向量兼容。改变模型或维度时创建新索引版本并重建，不直接覆盖旧集合。

LightRAG 的 PostgreSQL 图谱/KV/doc-status 和 Milvus 映射保持可追溯。第三方存储 setup 由 migrator 的专用适配执行；runtime 不自行建表。适配必须验证第三方初始化路径符合此约束，否则不得进入目标交付。

已有本地路径和文件 ID 通过显式映射迁移；工具和 API 获取受管对象引用，不取得任意宿主 Path。语义搜索、受管附件与 Agent 产物有明确归属，不能共用无权限的目录遍历入口。

## 12. Agent、技能与扩展

Agent 是定义、版本和执行策略的组合。运行冻结 AgentVersion、SkillRevision、知识引用和模型绑定版本；发布修改不改变正在执行的快照。

LangChain 1.4.7 用于模型和标准 Agent 循环，LangGraph 用于需要确定分支、检查点和审批的编排。只有实际需求才引入自定义图，避免同时维护多个相同职责的运行框架。

工具使用受控注册项和有类型的输入输出；MCP 是连接适配。技能只开放其明确依赖的工具。权限变更对新调用按规定的缓存期限生效，敏感变更主动失效。

普通第三方 Python 业务插件退出核心装配。Agent 扩展不以任意宿主代码执行代替工具协议；需要命令执行时以独立沙箱能力作为后续子项目，不能在本轮随意开放宿主脚本。

## 13. 身份、导航与前端

身份和权限继续复用有效数据与规则，拆出 identity 所有权。既有权限码、资源 ID 和模型/应用状态先保持契约语义，不与目录改名同时重编号。

内置路由由 app 显式注册。前端模块 routes.ts 暴露页面路由与导航元数据；服务端提供用户权限和能力状态。菜单隐藏只控制体验，API 与资源服务始终验证授权。

历史菜单/角色映射迁移成内置资源授权；不因移除插件安装就重新授予所有角色 AI 权限。普通用户工作台与管理后台分开呈现，代码生成和插件启停不进入核心导航。

前端目标为 Vue 3 + TypeScript + Ant Design Vue，按模块迁移；保留现有视觉 token 与许可。迁移模块的页面、API、store、流协议和测试一起迁移，禁止同时保留两个可写 store。

app/runtime 的 RunStore 只拥有运行状态、订阅与游标；模块 store 拥有业务资源和消息，通过受控 reducer 应用投影，不复制第二份可写运行状态。页面切换关闭订阅与用户停止运行是不同操作；旧同步 SSE 的断线策略由兼容入口明确处理。

GET RunSnapshot 返回 run_id、current_attempt_id、单调递增 attempt_no、status、revision、持久 projection、projection_seq 和终态 result_ref。事件 seq 在 attempt 内递增，事件身份是 run_id + attempt_id + seq，不能跨 attempt 直接拼接。工作进程按有界间隔写持久投影检查点，不要求每个 Token 写 PostgreSQL；终态结果先提交，再通知。

订阅从快照的同 attempt projection_seq 之后开始；已覆盖事件去重，发现序号缺口、事件窗口过期或不同 attempt 时服务端发送 reset_required，客户端重取快照，丢弃旧订阅投影后从新游标恢复。未被投影检查点或事件窗口覆盖的过程片段不承诺无损重放；终态消息以持久结果为准。临时 gap 时等待新快照，不能把缺口后的 delta 直接追加到旧文本。

## 14. 数据升级与兼容

PostgreSQL 是主线交付数据库。MySQL 现有缺口纳入历史兼容评估；旧实例迁移支持单独声明，不能把预留代码当作全量兼容承诺。

migrator 是唯一 schema 变更入口：先做维护/迁移锁与版本预检，再执行 Alembic 版本图、数据导入和框架存储 setup。API/worker 只检查版本兼容并装配运行资源，不执行 create_all。

每个域切换前建立写入屏障：阻止旧请求/调度投递，盘点并收敛排队、运行、取消和待结算事项，确认旧执行器及补偿器退出，再导入与核对，最后启用新写入者。同一资源在任一时刻只有一个可写实现；兼容路由只转入该实现，不同时维护新旧账本或 store。

LangChain 接入切换与网关退役分别验收。旧凭据/绑定已迁移、旧调用/补偿无在途写入且待结算或 outcome_unknown 已有可追溯处置后，才退役 LiteLLM。未满足条件时不得只依据阶段编号删除旧服务或数据库。

导入保留现有表、ID、业务版本、计费记录和迁移执行证据。插件 enabled 状态不再决定核心业务是否存在；维护模式由明确平台配置表达。旧模型/应用停用状态与权限仍按业务语义保留。

sys_plugin_resource_ownership 等持久引用责任必须映射到新模块资源所有者，保留 retained/unknown 与历史回调证据；不能为了删除插件框架直接清成 none。新资源引用 registry 由 bootstrap 显式注册，长期不依赖历史 Python 导入路径。

知识迁移按 dry-run → 停写/任务收敛 → 导入元数据/文件映射 → 核对数量/校验值/权限/索引 → 切换读写所有者执行。原始存储保留到验收完成，避免无期限双写。

历史无时区时间通过显式 legacy_timezone 转换并记录；预检缺少该配置时拒绝有歧义的时间转换。

公开 /v1 保持协议兼容；旧管理和工作台接口通过有期限的路由 adapter 迁移，不能依赖 SSE 重定向。generator 的退出作为明确产品变化记录。

应用回退只用于已声明兼容的数据契约；不承诺逆转 schema、第三方图谱或文件格式变更。跨契约回退使用经演练的数据备份与恢复流程。

## 15. 调度、文件维护与 generator 处理

| 当前能力 | 目标处理 |
| --- | --- |
| module_generator 及 tool/gen 页面/API | 从线上产品下线，历史配置归档；有真实需求再做离线 tools/codegen |
| module_task.scheduler_test | 移除生产示例及对应种子 schedule |
| 文件到期提醒 | files/jobs/retention，由 worker 注册执行 |
| 回收站清理 | files/jobs/recycle，保留保留期与批量上限 |
| 文件存储对账 | files/jobs/reconcile，保留校验和历史结果 |
| 配额/账本对账 | developer/jobs/reconcile |
| APScheduler 宿主装配 | 由统一 worker 调度和 PostgreSQL schedule occurrence 接管 |
| 自定义 dotted invoke_target | 映射为受控 JobDefinition ID；未识别项阻止迁移并报告 |

调度注册使用稳定任务 ID 和有版本的 payload；一次触发以 schedule_id + scheduled_at 唯一化。旧 Quartz 表达式做显式转换并验证时区、下次触发和暂停状态，不能静默改变执行频率。

迁移逐项映射 misfire_policy：旧补跑全部映射 catch_up_all（有上限和积压告警）、合并映射 coalesce_once、跳过映射 skip；先按旧调度实际实现解释原值，不凭字段名猜测。concurrent 映射为 max_active_per_schedule；不允许并发的任务通过 schedule 级租约约束不同触发时间，手动触发也共享互斥策略。

当前实现需特别处理：misfire_policy 的 1/3 都是 coalesce=False，grace 分别为 None/极大值；2 是 coalesce=True。不能将旧标记 3 因 UI 文案而直接转成 skip。concurrent=0 的现有上限实际为 3，不能迁成无限并发。迁移报告保存标记与实际计算行为；默认保持已执行语义，改为新的跳过/积压政策属于显式策略变更并记录审计。

切换时停止旧调度、交接未完成 occurrence 后再启用新调度。文件提醒按 file_id + policy_version + notice_window 幂等；回收清理和对账按任务策略禁止重叠。分别验收停机跨多个触发点、手动与定时竞争、暂停恢复，确保无双跑、漏跑或重复通知。

示例注册项：files.retention.scan.v1、files.recycle.purge.v1、files.storage.reconcile.v1、developer.usage.reconcile.v1。业务 handler 在所属模块，runtime 仅负责调度原语。

## 16. 可重复构建、质量与观测

- 后端 pyproject 声明直接依赖，requirements.lock 锁定完整部署依赖和哈希；CI 在干净环境验证解析与安装。
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
| A：基线与工程骨架 | 锁文件、代码规范门禁、新核心包、旧接口兼容测试、现有恢复缺口测试 | 可重复安装/生产构建；基线清楚；不引入新增依赖违规 |
| B：核心装配与迁移入口 | AI 内置、显式路由、迁移独立、身份/文件边界与对应前端导航/API/store 切片 | 核心不依赖插件状态；旧版升级、权限和身份/文件生产页面通过 |
| C：LangChain 1.4.7 替换 | 连接/密钥/模型契约、授权/账本、公开 API；models/developer 前端同步迁移 | 模型中心与开发接入真实链路通过；满足网关退役屏障 |
| D：统一执行与业务迁移 | ARQ/outbox、租约/取消/恢复；chat、agents、work 每域同步迁移 API、store、页面和流协议 | 每域重复投递/重启/取消及构建后浏览器验收通过 |
| E：知识与调度迁移 | 知识元数据、隔离索引、文件维护、旧调度转换；knowledge 与任务运行视图同步迁移 | 旧知识/权限/向量兼容；任务无双跑漏跑；对应生产页面通过 |
| F：兼容与交付收敛 | 剩余旧入口/store 退役、UI 兼容清单收敛、发布和数据恢复演练 | 核心域已在前序阶段闭环；发布/恢复与遗留清单验收完成 |

迁移按域完成行为、数据、前端、测试和观测的闭环后再扩大范围。每个阶段形成独立的详细实施计划和可评审提交；禁止以“新目录已建立”作为阶段完成标准。

## 18. 关键验收场景

1. 同版本重启、旧 worker 退出后，恢复扫描仍存在，遗留 Work/Chat 状态可收敛。
2. 模型静默超过取消通知 TTL、连接不断开时，持久取消仍生效。
3. 两个 worker 重复收到同一任务，只允许当前 attempt 推进状态；旧 worker 晚到写入被拒绝。
4. 入库成功、队列投递失败后，可从 outbox 补发；Redis 丢失事件可从数据库恢复终态。
5. LangChain 的文本、图片、工具增量、结构化输出、Embedding/Rerank、异常和 usage 通过规范契约。
6. Agent 循环每次模型调用都经过授权与用量治理；供应商重试和模型步骤不重复计费或隐匿成本。
7. 密钥、模型绑定与历史账本迁移可追溯；未知供应商、引用责任和不兼容向量阻止切换。
8. 生成器下线后，文件保留、回收、对账与业务调度持续工作。
9. 新旧关键接口及权限一致；页面切换、退出登录、SSE 重连不会串会话或错认运行。
10. 空库和旧实例升级、生产产物浏览器链路、兼容回退与数据恢复有独立验收记录。

## 19. 设计来源与当前状态

- [Yuxi 架构参考](https://github.com/xerrors/Yuxi/blob/1d66405280631d30c8dd006703d9f270606d45a1/ARCHITECTURE.md)
- [LangChain 1.4.7 包元数据](https://pypi.org/pypi/langchain/1.4.7/json)
- [LangChain 模型接口](https://docs.langchain.com/oss/python/langchain/models)
- [LangGraph 持久化](https://docs.langchain.com/oss/python/langgraph/persistence)
- [ARQ 的重复执行语义](https://arq-docs.helpmanual.io/)

整体设计和代码规范已作为实施基线；阶段 A 的工程基础与恢复修复以[验证记录](../verification/2026-10-23-ai-core-foundation.md)为准。业务域迁移、LangChain 接入替换与生产切换仍按后续阶段验收。
