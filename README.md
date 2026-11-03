# 智衡 AI 平台

智衡正在重建为以 AI 为核心的工作平台，采用模块化单体架构。模型管理、对话和用量治理直接内置于后端；模型接入使用 **LangChain 1.4.7**，前端使用 **Vue 3、TypeScript 和 Ant Design Vue**。

**当前版本为 0.1.0，已接通 AI 对话、后台管理、完整模型管理、平台积分、开发接入、技能、智能体、Work、受管文件、聊天附件、知识库向量检索和 LightRAG 图谱。MinerU 已完成本机部署准备，暂缓接入。** 本 README 对应根目录 `backend/`、`frontend/` 的当前源码。

普通对话采用滚动摘要：未压缩的完整问答累计 **30 轮**时整理更早内容，保留最近 **15 轮**原文；长文本按输入预算提前整理。摘要后台复用原模型、Worker 和积分账本，用量标记“会话摘要”，失败后可继续聊天并手动重试整理。原始记录完整保存，模型目录可配置上下文窗口。策略、配置和快照边界见[会话上下文说明](docs/architecture/chat-context-memory.md)。当前迁移版本为 `0017_chat_context_memory`。 实际验证与交付范围见[上下文验收记录](docs/superpowers/verification/2026-11-03-chat-context-memory.md)。

平台积分由内置 `billing` 模块统一管理。“用量与调用 → 我的积分”展示可用、预占、已消费及明细；拥有积分权限的管理员从“用户积分”发放、回收、暂停账户并核对未知用量。新用户默认零积分，积分长期有效。模型目录的“费率配置 → 积分计价”按百万 Token 或检索单元创建价格版本，调用先预占、后按实际用量结算，API Key 共用应用所有者积分并可单独配置月积分上限。历史 Token、金额费率和金额预算保留原单位。

积分计费安装时默认关闭，由管理员控制启停；后续升级保留现有模式、价格和用户积分。启用顺序为：配置所有已发布模型的积分价格（免费也需明确标记）、向用户发放积分、超级管理员在“用户积分”显式启用。启停需等待在途运行与知识任务完成；缺价、余额不足或账户暂停均阻止新的积分调用。定价和全局启停的备注均可留空，操作人、时间和版本自动记录。实现与验证见[平台积分验收](docs/superpowers/verification/2026-10-29-platform-credits.md)。

资源管理采用软删除：内置及上传技能、模型、凭据、智能体和应用可一次确认移除，历史聊天、账单、内容修订及冻结配置继续保留。仍有有效依赖或在途任务时，页面列出具体资源和处理入口；旧版本、已完成调用和已移除成员不再永久挡住删除。同一标识的技能包支持预检后更新或恢复，旧确认请求保持原结果。停用应用可修改名称，重新启用应用或重开工作时会复核依赖。生命周期原验收版本为 `0013_skill_revision_history`；个人资源 API 阶段新增至 `0016_file_purpose`，本轮会话记忆继续增量升级至 `0017_chat_context_memory`；相关数据保护见各轮发布验收，详见[资源生命周期验收](docs/superpowers/verification/2026-10-30-resource-lifecycle.md)。

文档上传接纳后，解析、切片和索引在后台继续；页面只更新任务进度，完成后静默同步列表，保留输入、选择、滚动和预览。隐藏标签暂停观察，返回后同步，离开页面不取消已接纳任务。存储管理默认使用简洁文件列表和回收站，统计、策略、提醒、对账等从高级管理进入，详见[静默观察与存储精简验收](docs/superpowers/verification/2026-10-30-quiet-background-ui.md)。

模型和智能体复用统一资源卡片：名称、状态、核心信息与底部操作位置一致，默认收起 UUID，保留复制与完整 ID 查看。测试、试聊作为常用操作，编辑、费率和 API 等通过更多菜单进入，完整配置与权限保护继续生效。工作记录在原有居中弹窗内使用同一套紧凑卡片，模型手机端保留紧凑列表。详见[资源卡片验收](docs/superpowers/verification/2026-10-30-resource-card-ui.md)。

全平台验收覆盖 906 项后端基础用例、107 项前端核心和 360 次页面回归。实测发现的图谱死锁已统一模型/知识库锁顺序，并通过 14 项新增回归、248 项受影响复验及实际构建/查询链路；原模型、积分与未提交优化保留。覆盖范围和未验项目见[全平台验收记录](docs/superpowers/verification/2026-11-02-full-platform-verification.md)。

本机入口为 <http://127.0.0.1:19080>，当前公网入口为 <http://115.190.121.37:8060>。当前实例已备份并升级至 `0017_chat_context_memory`，原模型、凭据、价格、积分和聊天历史保留。2026-10-27 的模型恢复与 `0008_graph` 图谱接入属于较早阶段，见[模型服务恢复记录](docs/superpowers/verification/2026-10-27-model-service-restoration.md)和[图谱验收](docs/superpowers/verification/2026-10-27-lightrag.md)。

前端固定使用浅色，顶栏、侧栏与主内容共用连续白色底色、蓝紫主色和系统字体；页面外壳移除分隔边线和重复页名，资源卡片、创建配置面板及数据表格保留对应的模块边界。主题切换已移除，旧深色偏好与系统深色模式不影响当前界面。工作台与资源搜索读取真实接口；对话和工作各自通过居中弹窗切换历史记录，智能体使用完整配置页与右侧试聊，技能继续通过 `SKILL.md` 或 ZIP 包解析并确认安装。各业务页面复用原有权限、运行状态和接口，保留完整配置与治理操作。源码位于 `frontend/src/`；`frontend/design-previews/bailian/` 保留为设计参考，示例状态不进入正式业务。当前样式见[白色界面验收](docs/superpowers/verification/2026-10-29-white-console.md)，原界面接入范围见[控制台 UI 计划](docs/superpowers/plans/2026-10-28-console-ui.md)。原页面及未提交优化保存在 `legacy/frontend/`。

登录页采用左侧品牌图形、右侧表单的白色布局，手机自动切换为单列，仅保留“登录”标题。“保存密码”在认证成功后请求浏览器密码管理器保存，“忘记密码”提供管理员重置指引。账号密码认证、回车提交、错误重试和密码显隐保持可用，详情见[登录页验收](docs/superpowers/verification/2026-10-29-login-ui.md)。

左下角账号菜单提供“个人资料”，支持本人显示名称、联系方式及密码修改，角色、部门和岗位只读；密码修改成功后全部会话需要重新登录。凭据名称、共享表格与配置浮层按当前主题核对，详情见[个人资料与界面一致性验收](docs/superpowers/verification/2026-10-29-profile-and-ui-consistency.md)。

新角色编码由后端自动生成，既有编码保持稳定。角色目录包含系统管理和 AI 平台管理：普通账号保留基础使用与本人资源能力，模型/凭据、连接测试、费率、共享技能和存储治理分别授权；给新用户绑定拥有模型维护权限的角色后，刷新即可接入模型，详见[AI 管理权限验收](docs/superpowers/verification/2026-10-29-ai-management-permissions.md)。

对话体验通过工具栏“历史记录”弹窗查找最近或已归档会话，选中后关闭，聊天区不再被常驻历史栏挤压；“新建对话”可直接使用。模型选择位于输入框下方，已移除文本/图片能力标签。右上角“对话设置”支持系统提示词、温度和输出长度，发送时实际进入受治理的模型请求；输出长度受当前模型目录上限约束，重新生成沿用原消息选择。提示词不保存在浏览器本地，切回会话时从有权读取的消息快照恢复，详情见[历史与生成设置验收](docs/superpowers/verification/2026-10-29-chat-history-settings.md)。

2026-10-28 已在正常入口启用这套控制台 UI，保留现有模型配置与业务接口。34 项核心测试和 182 个唯一浏览器用例完成验证，真实登录及十个页面只读检查通过；执行次数、重试记录和明确限制见[控制台 UI 验收](docs/superpowers/verification/2026-10-28-console-ui.md)。

## 当前源码开发运行

当前本机使用源码模式，入口仍为 <http://127.0.0.1:19080>。前端 Vite 热更新、API 自动重载，普通代码修改无需重新打包；Worker 代码修改后重启 Worker。`dev.sh` 默认沿用正在运行的 Core 配置，也支持 `--env-file` 显式选择已有配置。

```bash
./dev.sh up
./dev.sh restart-worker
```

聊天已接通重新生成与回复版本、持久赞踩、浏览器朗读、可撤销文本分享，并移除欢迎页中的模式切换。数据库已备份升级为 `0009_message_actions`，60 张原表的原始字段/数据指纹保留，详见[消息操作与源码运行验收](docs/superpowers/verification/2026-10-28-message-actions.md)。镜像发布流程保留在后文，当前开发使用 `dev.sh`。

## 当前功能状态

| 功能           | 当前状态               | 已实现的范围                                                                                                                                                                  |
| -------------- | ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 身份与会话     | 已实现并通过本地验收   | CLI 创建管理员、登录、注销、可撤销 Cookie 会话、资源归属校验                                                                                                                  |
| 基础后台管理   | 已接通并通过本地验收   | 独立后台入口；用户、角色权限、部门、岗位、菜单、字典、参数、公告、登录/操作日志、在线会话和系统状态                                                                           |
| 模型管理       | 已实现并通过本地验收   | 连接/模型新增、编辑、启停、发布与引用保护删除；文本、图片（需启用视觉）、Embedding、Rerank 的真实调用与分类测试                                                               |
| AI 对话        | 已实现并通过本地验收   | 流式回复、多轮上下文、附件/图片、重命名/归档/删除、停止与断线恢复                                                                                                             |
| 运行与用量     | 已实现并通过本地验收   | 后台执行、事务 outbox、租约、失联收敛、积分账户/预占/结算/流水与历史 Token 用量                                                                                                  |
| 受管文件       | 已实现并通过本地验收   | 知识库内的存储治理、权限/引用保护、回收/审计/策略/提醒/对账；真实原生解析与聊天附件                                                                                           |
| 知识库与图谱   | 向量与图谱已验收并部署 | 库/文件夹/文档管理、原生解析分块、Milvus 索引/检索、原文权限交集与 Chat/Agent/Work/API 向量组合；LightRAG 配置、试构建、发布、画布及带来源的上下文查询；MinerU 仅完成部署准备 |
| 技能           | 已实现并通过本地验收   | 原技能管理界面、ZIP/主文件预检和确认安装、内容/启停/删除、聊天选择及历史快照                                                                                                  |
| 智能体         | 已实现并通过本地验收   | 原配置/发布/试聊/API界面、不可变发布与技能快照、推理和停止、应用Key公开JSON/SSE调用；知识检索、来源与原 Key 恢复                                                              |
| Work           | 已实现并通过本地验收   | 原工作模式与最近工作、任务/成员、冻结发布版本、人工单成员执行、署名共享历史、停止与失联收敛                                                                                   |
| 开发接入与计费 | 已实现并通过本地验收   | 应用、一次回显 Key、授权、轮换/撤销、限额与预算；公开模型 API、手动费率版本和精确费用账本                                                                                     |

本地验收使用独立 PostgreSQL / Redis 和本地模型 HTTP 服务，覆盖构建后的真实浏览器链路。具体结果与未覆盖范围见[首条 AI 链路验收](docs/superpowers/verification/2026-10-23-first-ai-slice.md)。这不等同于所有供应商、全部目标功能或生产部署均已验收。

2026-10-26 运行恢复：Redis AOF 无效零尾在完整备份和副本校验后修复，正常实例 33 张业务表指纹一致；原界面、管理员登录、11 个接口和六个页面复测通过。健康检查地址已代理到实际 API，避免网页 HTML 200 掩盖后台故障，详见[服务恢复验收](docs/superpowers/verification/2026-10-26-service-recovery.md)。该记录对应升级前的 `0006_files`；2026-10-27 已按下述验收升级到知识库版本。

2026-10-27 知识库验收：最终后端 386 项完整回归、Python 3.10/3.13 各 79 项安装后测试、四条真实 API/worker 进程链和独立复审通过。真实浏览器覆盖上传、解析、Milvus、来源权限及四种 AI 调用；资料只进入 user 数据消息。正常实例已备份并升级至 `0007_knowledge`，33 张原表的原列/原行一致，原管理员、12 个接口和七个页面验证通过。详见[知识库验收](docs/superpowers/verification/2026-10-27-knowledge.md)。

2026-10-27 图谱交付：LightRAG 1.5.7 已进入内置知识域，独立规格和质量审查关闭初始化、锁序及连续取消收尾问题。实际镜像通过原图谱界面、真实 SDK/PG/Milvus、来源权限与调用方费用验证；正常实例升级保留 44 张原表原列原行和全部持久卷身份。管理员、12 个接口、七个页面和实际 JSON 就绪检查通过。回归结果及明确限制见[图谱验收](docs/superpowers/verification/2026-10-27-lightrag.md)。

MinerU 按当前安排保留本机自托管路线：已核对硬件与临时容器 GPU 可用性，后续采用独立服务环境。模型尚未下载，服务尚未部署，实际 OCR 尚未验收；本轮暂时跳过。准备范围与后续待办见[文件与知识库计划](docs/superpowers/plans/2026-10-23-files-knowledge.md)。

2026-10-23 文件版本验收：最终后端327项完整回归，前端24项行为和91项页面回归、类型/格式/构建及独立规格/质量审查通过。真实容器覆盖受管上传、原生解析、图片模型调用、文件回收、提醒和已有数据升级，原24张表的原列/原行核对一致。详见[文件与聊天附件验收](docs/superpowers/verification/2026-10-23-files.md)；此前[开发接入与模型管理](docs/superpowers/verification/2026-10-23-developer-models.md)、[技能](docs/superpowers/verification/2026-10-23-skills.md)、[智能体](docs/superpowers/verification/2026-10-23-agents.md)和[Work](docs/superpowers/verification/2026-10-23-work.md)记录保留。上述早期验收使用本地模型协议服务；当前四个模型的真实供应商连通测试见模型服务恢复记录，生产负载尚未验收。

模型连接编辑会创建不可变凭据修订，历史 Run 继续使用其原始绑定。费率只追加版本，已预留调用使用冻结价格，金额和积分用 Decimal 计算。供应商未报告有效计量时保留预占、标记未知；积分管理员可核对未知消费，只补充尚未确认的计量，不能覆盖已有事实。图片已接到普通聊天，模型须启用视觉能力。工具执行和结构化输出仍未接入。

## 个人资源与开放 API

当前源码提供 `/api/v1` 的文件、个人知识库、个人技能与原生异步操作接口。开发接入包含不可变 Scope、独立资源 RPM、应用资源授权、当前源文件用量和平台额度；资源专用 Key 可不选模型，索引和检索的模型步骤仍沿用允许列表及积分规则。管理员普通资源入口也遵守个人归属，其他人的资源须明确允许委托；同 Owner 的两个应用不自动共用资源。

[资源 API 接入说明](docs/api/resources-v1.md)说明真实文档入口、授权、版本、幂等、费用及迁移边界。运行本分支源码后可访问 `/docs/resources` 与 `/openapi/resources-v1.json`；原 `/api` Cookie Console、`/v1` Bearer 推理路径保留。OpenAI 资源 SDK 兼容仍仅预留，不提供假的 `/v1/files` 等端点。分支验收与正常实例发布分别记录，不能将本说明视为源实例已升级。

## 架构与目录

后端分发名为 `zhiheng-platform`，导入包为 `zhiheng`。同一份后端代码分别运行 API、worker 和 migrator，业务数据统一保存在 PostgreSQL，Redis 用于 ARQ 队列和 worker 心跳。

```mermaid
flowchart LR
    Web[Vue 3 工作台] --> API[FastAPI 接口]
    API --> Files[(受管文件卷)]
    Worker --> Files
    API --> PG[(PostgreSQL)]
    Worker[后台 Worker] --> PG
    Worker <--> Redis[(Redis / ARQ)]
    Worker --> Inference[统一 Run 执行器与用量治理]
    Inference --> Models[LangChain 1.4.7 模型适配]
    Models --> Provider[模型供应商]
    API --> Vector[(Milvus)]
    Worker --> Vector
    Worker --> Graph[LightRAG 适配与独立 SDK loop]
    Graph --> PG
    Graph --> Vector
    Graph --> Inference
    Migrator[数据库迁移进程] --> PG
```

API 将消息、运行、配额预留和 outbox 一起提交；worker 投递并执行运行，通过 attempt 与租约控制写入。最终消息、终态和用量一致落库；前端通过 SSE 读取持久快照。停止生成保存取消意图，关闭订阅只结束当前查看。

```text
backend/
├── src/zhiheng/
│   ├── api/             main.py 统一装配 common/console/resources/inference 协议组
│   ├── application/     跨模块用例与事务编排
│   ├── modules/         identity、models、billing、chat、developer、skills、agents、work、files、knowledge
│   ├── runtime/         运行、租约、outbox 与恢复原语
│   ├── worker/          队列执行、取消观察与恢复
│   ├── bootstrap/       配置和资源装配
│   ├── infrastructure/  数据库连接、凭据加密与受管存储
│   ├── shared/          公共值与基础错误
│   └── cli/             serve、worker、migrate、create-admin
├── migrations/          版本化数据库迁移
└── tests/               单元、契约、集成与浏览器验收
frontend/src/
├── app/                 应用、布局和显式路由
├── modules/             identity、models、billing、chat、developer、skills、agents、work、files、knowledge、usage
└── shared/              HTTP、SSE、校验、原公共组件与视觉样式
deploy/core/             Compose 和配置示例
docs/README.md           文档索引
tools/                   架构边界与 wheel 检查
legacy/                  旧工程归档与原有未提交优化
```

新平台使用独立配置、数据库、Redis 和持久卷。`legacy/` 供提取旧业务能力时参考，不进入新平台运行包。整体目标架构见[架构设计](docs/superpowers/specs/2026-10-23-ai-core-platform-redesign-design.md)，当前实际目录以[代码结构说明](代码结构说明.md)为准。

## 运行版本与访问入口

当前源码模式实例从本工作目录加载 `backend/src` 与 `frontend/src`，前端热更新、API 自动重载；Worker 代码变更后执行 `./dev.sh restart-worker`。镜像发布实例仍需显式更新镜像和服务，参照下面的部署流程。

本机使用的新实例为 `zhiheng-core`，网页地址 `http://127.0.0.1:19080`；旧 `zhiheng` 实例保持停止，旧数据保留。新实例采用独立数据卷，升级保留原 Fernet 凭据密钥和数据库。验收使用单独的临时项目与本地模型服务，不将测试模型写入正常实例。

| 使用方式      | 访问入口                                             |
| ------------- | ---------------------------------------------------- |
| 本机前端开发  | `http://127.0.0.1:15190`，代理新 API 的 `19090` 端口 |
| Docker 新实例 | 默认 `http://127.0.0.1:19080`，项目名 `zhiheng-core` |

## Docker 启动

需要 Docker Engine、Compose v2.20.3+ 和用于生成配置的 Python 3。以下命令只操作 `zhiheng-core` 项目，默认网页地址为 <http://127.0.0.1:19080>。

```bash
./deploy.sh init
./deploy.sh build
./deploy.sh up
./deploy.sh exec api python -m zhiheng create-admin --username admin
```

`init` 在 `deploy/core/.env.core` 生成新的数据库随机密码和 Fernet 凭据密钥，权限为 `0600`，拒绝覆盖已有文件。初始配置明确用于本机 HTTP：`CORE_COOKIE_SECURE=false`，且允许来源为 `http://127.0.0.1:19080`。管理员命令交互读取密码，没有默认管理员或固定口令；自动化可显式使用 `--password-stdin`。

使用自定义新配置文件时，在每条命令上加 `--env-file /absolute/path/to/new-core.env`。参考 [配置示例](deploy/core/.env.core.example)。不要复用旧数据库密码或凭据密钥；当前 Compose 的 `CORE_DB_PASSWORD` 只支持字母、数字、下划线和连字符组成的 URL-safe 原始口令，请勿预先 URL 编码。`init` 生成的随机口令符合此约束。变更数据库密码不会修改已经初始化的 PostgreSQL 用户密码。Fernet 密钥用于解密已保存的供应商凭据，应与数据库一起备份并在升级中保留。

网页端登录后，展开“模型中心”，在“模型目录 → 凭据管理”添加 OpenAI 兼容服务连接，再在“平台模型”接入模型，进行连接测试或文字聊天。连接测试也经过持久运行与用量治理，会消耗实际供应商额度。

“开发接入”中创建应用并为 Key 授权模型。Key 只在创建或轮换成功时显示一次；公开接口使用 `Authorization: Bearer <api_key>`，支持 `/v1/models`、`/v1/chat/completions`、`/v1/embeddings` 和 `/v1/rerank`。请求中的 `model` 使用模型目录复制的平台 UUID。接口定义位于 `/docs` 和 `/openapi.json`；手动费率不代表已对接供应商价格同步或支付结算。

知识资料统一在“知识库 → 管理文档”完成上传、文件夹管理、预览、原文权限、删除和索引任务。管理员可从知识库首页进入“存储管理”，查看知识文档与对话附件的存储、引用、回收站、审计和保留策略；主导航不再单列文件管理。旧地址 `/files` 和 `/system/file` 仍可访问管理员存储页面。

对话中的纸夹用于图片/文档上传，每条消息最多10张图片、5个文档，单件最大20MiB；图片须选择已启用视觉能力的模型。文件的“公开”仅允许已登录平台成员读取。会话删除解除附件引用；文件仍被业务引用时不能删除或物理清理。底层 `files` 内置模块继续统一管理存储和权限，知识文档与对话附件的业务归属保持独立。

“技能管理”默认展示我的技能；每位用户可上传根目录包含 `SKILL.md` 的 ZIP 或单独主文件，预检后确认安装、更新或恢复自己的副本。普通账号可管理并选用本人启用技能；聊天输入末尾 `@` 或点击技能按钮，最多选择 5 项。技能主文件作为本次系统上下文并计入预留，历史记录保留内容版本摘要。包内附件只保存，不执行。

“智能体”中，可保存草稿并调试，绑定应用后发布。上下文轮数可选 **0–20**，默认 **20**：仅携带最近 N 轮历史问答和当前问题，0 仅发送当前问题；系统提示词、技能、知识资料及工作空间任务目标仍生效。保存草稿后用于调试，公开 API 使用发布配置，工作空间使用成员冻结配置；调整轮数保留页面完整对话，不生成普通对话的滚动摘要。公开端点为 `/v1/agents/{id}/chat/completions`，API抽屉按发布快照创建模型授权Key；对外调用前需在模型目录配置费率。草稿修改不影响已发布版本，重新发布才生效。欢迎语仅展示，技能和知识库绑定随发布冻结。知识准备使用原智能体所有者的权限，检索失败不退回无知识调用。

从侧栏“工作空间”独立进入工作，填写目标并选择已发布成员，即可每轮人工指定一个成员接力。工具栏常驻“工作记录”和“新建工作”；记录在居中弹窗中搜索、筛选与切换，选中后关闭，不挤压主工作区，打开或关闭弹窗保留当前目标和消息草稿。最近/已归档、重命名、删除及运行锁继续生效，见[工作记录弹窗验收](docs/superpowers/verification/2026-10-29-work-history-modal.md)。工作空间使用自己的任务、成员和历史记录，与“对话体验”分别导航，页面内不再提供聊天/工作切换；直接打开工作空间不会加载普通聊天历史。成员加入时冻结发布版本，更新须显式确认；重新进入可查看持久历史和运行状态。停止会等待后台关闭供应商并释放任务，失联运行收敛为中断，明确重试才发起新调用。

点击左下角头像，在账号菜单中进入“后台管理”（`/admin`）；入口按账号权限显示。超级管理员可管理全部后台功能，普通成员按启用角色的具体权限进入；权限在每次后端请求时重新检查。用户管理支持组织归属、角色分配、停用、密码重置和保留历史业务的账号删除，保护本人及最后一个管理员。新建与重置密码至少 12 个字符，原账号和原密码继续有效。

角色列表默认包含受保护的“超级管理员”内置角色，现有与新建的超级管理员账号自动建立真实关联；该角色可查看全部管理权限和成员数，由系统维护。用户管理中的“超级管理员”身份开关会同步角色关联，普通角色授权由角色管理维护。

后台提供部门树、岗位、12 个已接通管理页的导航配置、字典及条目、参数和公告、登录/操作日志、在线会话强制退出及真实基础服务状态。参数目前支持公告展示开关 `console.notice_enabled`；发布公告通过右上角通知按钮展示给已登录用户。写入使用版本检查并记录操作摘要，日志不保存密码和令牌；旧账号与会话原本未知的创建时间显示为 `—`。数据库版本为 `0011_builtin_admin_role`，详见[后台管理验收](docs/superpowers/verification/2026-10-28-admin-console.md)与[内置角色和入口调整](docs/superpowers/verification/2026-10-29-builtin-role.md)。

侧栏“知识库”可创建向量库、管理文件夹与文档、上传原文、重新切片、重建索引和测试检索。库与原文件权限分别生效；共享库不会公开私有原文。聊天可选择知识库，智能体发布和 Work 成员快照保留知识绑定。公开智能体调用须为 Key 授权主模型及全部向量模型，并提供稳定 `client_request_id` 或 `Idempotency-Key`；202 响应使用同 Key 轮询 `polling_url`，再按原请求重放结果。图谱页面支持配置、试构建、正式构建、画布和带来源的上下文查询；聊天、智能体、Work 与公开调用的知识组合继续使用向量检索。OCR 暂缓接入。

页面跳转或刷新后可恢复未完成的上传批次，已确认未导入的暂存可在弹窗取消时清理。完全退出浏览器后，界面还不能恢复未导入批次，原文及其暂存引用仍会保留。退出前请完成导入或确认取消上传，服务端暂存发现与恢复列为后续改进。

Compose 启动顺序为 PostgreSQL / Redis 及 Milvus 依赖健康 → 迁移成功 → worker 健康 → API 就绪 → 网页。`/health/live` 检查 API 进程，`/health/ready` 检查迁移版本、Redis 和 worker 持久心跳。数据库、Redis 和向量服务不向宿主机发布端口，Compose 声明的六个持久卷由 `zhiheng-core` 项目管理；备份同时保存数据库、Redis、文件、三份向量数据、配置密钥及对应镜像。MinIO 镜像还会为 `/data` 创建匿名卷，备份时须按实际容器挂载清单额外保存，并核对升级前后的卷身份；API 与 worker 必须使用同一文件卷。

```bash
./deploy.sh ps
./deploy.sh logs --follow api worker
./deploy.sh down                 # 保留新平台数据卷
./deploy.sh config --quiet       # 仅校验；不加 --quiet 会输出包含密钥的配置
```

脚本显式传入新环境文件并关闭 Compose 默认环境文件发现。需要直接使用 Compose 时，同样显式指定新文件：

```bash
COMPOSE_DISABLE_ENV_FILE=1 docker compose --env-file deploy/core/.env.core -f compose.yaml config --quiet
```

对外提供 HTTPS 时，先配置入口 TLS，设置 `CORE_COOKIE_SECURE=true` 和浏览器实际访问地址的 `CORE_ALLOWED_ORIGINS` JSON 数组，再调整 `CORE_WEB_BIND`。HTTP 本机地址变化时也要同步修改允许来源。后端镜像默认 Python 3.12；可通过 `CORE_PYTHON_IMAGE`、`CORE_PIP_INDEX_URL` 设置构建基础镜像和安装源。前端使用 Node 24 / npm 11 多阶段构建，Nginx 提供 `platform.html` 路由回退和无缓冲 SSE 代理。

## 本机开发

需要 Python 3.10+、Node 24、npm 11，以及单独的 PostgreSQL 16 / Redis 7。下面的依赖容器使用独立名称与回环端口，不读取 Compose 或旧工程配置。命令中的密码只保存在当前 shell 环境。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r backend/requirements-core.lock
python -m pip install -r backend/requirements-dev-core.txt
python -m pip install --no-deps --no-build-isolation -e backend

export CORE_DEV_DB_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
docker run --detach --name zhiheng-core-dev-postgres \
  --publish 127.0.0.1:15432:5432 \
  --env POSTGRES_USER=zhiheng_core --env POSTGRES_DB=zhiheng_core \
  --env POSTGRES_PASSWORD="$CORE_DEV_DB_PASSWORD" postgres:16-alpine
docker run --detach --name zhiheng-core-dev-redis \
  --publish 127.0.0.1:16379:6379 redis:7-alpine

export ZHIHENG_DATABASE_URL="postgresql+asyncpg://zhiheng_core:${CORE_DEV_DB_PASSWORD}@127.0.0.1:15432/zhiheng_core"
export ZHIHENG_REDIS_URL=redis://127.0.0.1:16379/0
export ZHIHENG_FILES_ROOT="$PWD/.runtime/files"
export ZHIHENG_CREDENTIAL_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
export ZHIHENG_COOKIE_SECURE=false
export ZHIHENG_ALLOWED_ORIGINS='["http://127.0.0.1:15190"]'
```

等待独立的 PostgreSQL 和 Redis 可用，再在上述环境运行迁移与建号：

```bash
python -m zhiheng migrate
python -m zhiheng create-admin --username admin
```

分别在保留相同虚拟环境和 `ZHIHENG_*` 导出变量的终端运行 worker 和 API；关闭时在对应终端按 Ctrl+C：

```bash
python -m zhiheng worker
```

```bash
python -m zhiheng serve --host 127.0.0.1 --port 19090
```

在另一终端启动前端，访问 <http://127.0.0.1:15190>；Vite 把 `/api` 转发到新 API：

```bash
cd frontend
npm ci
npm run dev:platform
```

`Settings` 不自动读取任何 `.env`。必须提供 `ZHIHENG_DATABASE_URL`（`postgresql+asyncpg`）、`ZHIHENG_REDIS_URL` 和新的 `ZHIHENG_CREDENTIAL_KEY`。Cookie 默认仅限 HTTPS，允许来源默认空数组，Token 配额默认 `100000`，供应商超时默认 `60` 秒，队列默认 `zhiheng:core:queue`。本机开发显式覆盖 Cookie 和浏览器来源。开发依赖容器需要重新使用时应保留原有密码和凭据密钥。

## 验证

基础 CI 在 Python 3.10 / 3.13 上安装同一份完整哈希锁，检查 Ruff、Mypy、AST 依赖边界、独立单元测试及 `zhiheng_platform` wheel。平台 CI 使用真实 PostgreSQL / Redis、固定本地模型 HTTP 服务及 Chromium，检查业务集成、构建后真实页面链路和聊天/开发接入/模型管理的受控 HTTP / SSE 页面回归；普通 CI 不调用收费模型。

```bash
cd backend
python -m ruff check --config ruff.toml src tests/unit tests/core ../tools/check_architecture.py ../tools/check_core_wheel.py
python -m ruff format --check --config ruff.toml src tests/unit tests/core ../tools/check_architecture.py ../tools/check_core_wheel.py
python -m mypy --config-file pyproject.toml
python ../tools/check_architecture.py src/zhiheng
python -m pytest tests/unit -q
```

真实集成测试需显式设置 `ZHIHENG_TEST_DATABASE_URL` 和 `ZHIHENG_TEST_REDIS_URL`，再运行 `python -m pytest tests/core -q`。测试会在指定 PostgreSQL 上创建并清理 UUID 临时数据库，账号须有建库权限；Redis 也应为独立测试实例。缺少配置会跳过依赖外部服务的测试，这不等同于集成通过。

```bash
cd frontend
npm run test:core
npm run lint:core
npm run format:check
npm run typecheck
npm run build:platform
```

文档入口见 [文档索引](docs/README.md)。代码边界和数据所有者见 [代码结构说明](代码结构说明.md)，重构约定见 [代码规范](docs/architecture/2026-10-23-code-standards.md)。
