# 平台结构与工程合理性评估

审查日期：2026-10-23。审查对象为当前工作区，包含已有未提交修改；基准提交为 1cd3307b。本文是重构前的基线清单，不代表线上实例已发生这些故障，也不代表此前讨论的目录草案或 LangChain 选型已经获准实施。

后续设计决策：用户已明确 AI 核心内置，并指定用 langchain==1.4.7 替换模型接入。当前目标方案见[整体重构设计](../superpowers/specs/2026-10-23-ai-core-platform-redesign-design.md)，本文保留原审查时点的事实与风险描述。

优先顺序：运行恢复与交付一致性 → 业务边界与升级机制 → 模型接入及 Agent 能力演进。单体部署、LiteLLM、双 UI 框架和业务分层本身均不能单独作为“不合理”的依据。

优先级：P1 表示应先处理的运行或交付风险；P2 表示有具体触发条件的可靠性问题或影响演进的结构债；P3 表示可接受的过渡成本，应结合产品范围治理。每项结论区分代码事实、隔离验证和条件判断。

## 整体判断：业务边界与运行规则的一致性不足

目前可以看到几条演进路线同时保留：若依后台基础、通用插件框架、LiteLLM 模型控制面、抽取的知识库能力，以及后来加入的 Agent 和工作任务。各部分有局部的接口、状态与防护，但共同的业务划分和运行职责尚未完全收敛。这使一次业务修改往往需要同时理解插件状态、后台权限、模型引擎状态和本地任务状态。

与前面核对的 Yuxi 提交 1d66405280631d30c8dd006703d9f270606d45a1 相比，主要差距在整体规则的可解释性：

| 方面 | Yuxi 中可以明确定位的责任 | 当前平台的分散表现 |
| --- | --- | --- |
| 产品核心 | agents、knowledge、models 构成内置业务主体 | 核心 AI 受通用插件生命周期控制，管理模块同时承载多类基础业务 |
| 代码入口 | Web/worker 入口与核心 Python 包区分 | 内置模块扫描、插件扫描、前端固定路由和清单菜单共同装配 |
| 执行职责 | 请求接入、持久任务、worker 执行的责任有明确说明 | 普通聊天、Work、图谱分别实现执行和恢复，恢复循环又依赖插件启动角色 |
| 数据升级 | storage-migrator 负责 schema 变更 | 初始化 SQL、插件迁移、启动建表与运维迁移入口需要共同追踪 |
| 模型接入 | 核心模型包集中处理供应商适配 | 统一运行协议、LiteLLM 控制面和资源绑定约束之间的可替换边界不一致 |

参考：[该版本的 Yuxi 架构说明](https://github.com/xerrors/Yuxi/blob/1d66405280631d30c8dd006703d9f270606d45a1/ARCHITECTURE.md)。这是组织与职责的比较，不构成 Yuxi 全面正确或必须复制其技术栈的判断。

因此重构需要先明确：业务模块和数据所有者、跨模块公开接口、执行所有权与恢复责任、schema 变更唯一入口。聊天、工作任务和索引仍可保留各自的业务状态模型，共用租约、取消与恢复等基础机制；无需强制合并所有任务状态。

后文第 1 项就是这种职责交叉的实际后果：一次性全局初始化 writer 的标记被同时用于决定持续运行的恢复执行器是否启动。单纯移动文件不能改变这个运行规则。

## 1. P1：Work 恢复执行器依赖一次性插件启动 writer

当同代际 Redis ready 标记仍有效、数据库插件安装状态完整、旧 writer 已全部退出时，新进程会复用 ready 并作为 reader 激活。Agent hook 却只在 startup_write_enabled 为真时扫描过期运行并创建周期恢复任务。默认单 worker 的同版本重启可以进入这个分支，导致遗留 current_run_id 没有恢复扫描，后续执行持续被阻止。

已有 heartbeat、过期阈值和恢复时的条件更新；缺口在恢复执行器是否存在，而不是完全没有恢复算法。应将持续运行职责与一次性初始化职责分离，明确恢复执行器的生命周期和所有权。

证据：[Agent hook](../../backend/plugins/agent_extension/hooks.py) 第 59–62 行；[ready 复用与 reader 激活](../../backend/plugins/core/runtime/application.py) 第 195–222、284–299 行；[ready 有效期](../../backend/common/constant.py) 第 147 行；[运行占用检查](../../backend/plugins/agent_extension/service/work_task_run_service.py) 第 191 行。

验证：已隔离执行实际 ready-success 分支、reader 激活方法和 Agent on_startup，确认未执行恢复、未创建周期任务。未执行真实容器重启。

## 2. P1/P2：普通聊天运行生命周期缺少完整的故障恢复

- P1：pending/running 已提交后进程强杀或终态保存失败，当前没有普通聊天孤儿运行扫描。遗留运行会长期保持活动状态，归档、删除仍会因此被拒绝。正常取消与断连有受保护的收尾，但不能覆盖硬退出。
- P2：会话锁固定 900 秒且没有续租。实际流超过租期时，另一个请求可以获取同会话锁。旧 owner 比较 token 后释放锁的保护是有效的，但不能阻止超租期后的并行运行。默认网关配置还有 600 秒超时，因此此项是条件风险，不是已复现的默认部署端到端故障。
- P2：取消接口仅写入有效 60 秒的 Redis 标记，生成端只在收到上游事件后检查。若只调用取消接口、原 SSE 仍连接且上游静默超过 60 秒，取消意图可能在被读取前消失。常规页面停止按钮还会主动 abort 原连接，不能将此项描述成页面停止必定失效。

应统一定义运行所有权、续租、持久取消意图和异常运行收敛规则，分别验收正常停止、超租期、进程退出和存储中断。

证据：[聊天协调器](../../backend/plugins/ai/service/chat_runtime_coordinator.py) 第 9–10、168–180、221–238 行；[聊天服务](../../backend/plugins/ai/service/platform_chat_service.py) 第 525、553、570–571、670–687、1006–1024 行；[普通聊天 DAO](../../backend/plugins/ai/dao/platform_chat_dao.py) 第 234 行；[页面停止行为](../../frontend/plugins/ai/views/chat/index.vue) 第 415–426 行。

验证：实际协调器配合内存 Redis 和逻辑时钟，确认 901 秒后旧 owner 未退出时第二 owner 可取锁，且旧 owner 不会误删新锁；实际事件转发函数确认标记过期后 late event 仍会被转发。未进行真实模型长流、强杀或存储故障实验。

## 3. P1：前端依赖没有可重复的安装基线

前端忽略 package-lock.json 和 yarn.lock，CI 使用 npm install --no-package-lock，多项依赖使用宽版本范围。镜像发布虽会重新构建前端，但将构建机现有 node_modules 链接到临时源码快照，源码哈希不覆盖实际依赖环境。同一源码可能因安装时间或构建机不同产生不同结果。

应提交一种包管理器的锁文件，以冻结安装命令构建，并将依赖基线纳入发布记录。现有 revision、dirty、源码摘要和镜像 ID 记录仍然有价值，这不是“完全没有版本溯源”。

证据：[前端忽略规则](../../frontend/.gitignore) 第 24–25 行；[依赖范围](../../frontend/package.json)；[CI 安装](../../.github/workflows/playwright.yml) 第 35 行；[发布快照与构建](../../deploy/release/build.py) 第 32–37、265–270 行。

## 4. P1：开发态验证与源码部署实际交付物之间有断点

浏览器 CI 启动 Vite 开发服务器，当前工作流没有生产构建步骤。源码部署却直接挂载仓库 dist，前端 Dockerfile 也直接复制 dist。源码检查通过不能证明交付的静态文件对应这些源码，也不能发现只在生产打包时出现的问题。

应让生产构建和对构建产物的冒烟检查进入自动验收，并明确 dist 的生成与发布责任。镜像发布链路已有从源码快照重新构建的保护；本次没有判定当前 dist 已过期。

证据：[前端工作流](../../.github/workflows/playwright.yml) 第 45–65 行；[源码部署](../../compose.yaml) 第 175 行；[前端镜像](../../frontend/Dockerfile) 第 4 行；[镜像发布重建](../../deploy/release/build.py) 第 270 行。

## 5. P2：AI 的核心地位与可选插件生命周期不一致

AI 和 Agent 随平台默认启用，模型、对话、知识库和开发接入等核心功能却仍受通用插件安装、启停、版本、菜单同步和启动角色机制管理。对“AI 是产品核心”的定位，这增加了核心能力装配的状态组合和排障成本。

应将核心 AI 的启动与升级纳入平台必需流程，保留业务模块边界。第三方业务插件框架是否保留仍待产品需求明确，不能据此直接删除全部插件基础设施。

证据：[默认启用列表](../../backend/config/env.py) 第 34 行；[AI 清单](../../backend/plugins/ai/plugin.yaml) 第 15–66 行；[应用启动](../../backend/server.py) 第 163–182 行。

## 6. P2：目录边界与实际依赖不完全一致

Agent 已有公开接口可用，但控制器仍直接依赖 AI 内部配置、服务、DTO 和运行时装配；公共请求上下文又依赖管理模块的用户类型。前端宿主维护 Work 路由、权限及内部视图导入，同时插件清单也持有菜单声明，修改业务入口常需跨多个位置同步。

知识解析与知识业务分别位于 module_Knowledge/src/knowledge 和 plugins/ai/knowledge，分工可以成立，但现有命名不容易表达。应按业务所有权明确入口和依赖方向；目录名称、多种分层方式并存本身不是 bug。

证据：[Agent 控制器](../../backend/plugins/agent_extension/controller/agent_invocation_controller.py) 第 26–37 行；[公共上下文](../../backend/common/context.py) 第 6 行；[知识解析依赖](../../backend/plugins/ai/knowledge/runtime.py) 第 18–19 行；[宿主 Work 路由](../../frontend/src/router/index.js) 第 100–114 行；[导航规则](../../frontend/src/components/Workspace/workspaceTransforms.js)。

## 7. P2：模型运行接口可替换，管理与持久化层却锁定 LiteLLM

ModelRuntimeEngine 已抽象聊天、流式、Embedding、Rerank；但资源绑定的数据库约束、绑定创建、运行时装配以及凭据/部署管理仍固定 LiteLLM。替换供应商接入层时，抽象不能直接覆盖整个模型子系统。

应明确平台模型、供应商连接和实际执行适配器各自的契约。LiteLLM 与 LangChain 都是可行路线；需要治理的是业务契约与具体引擎绑定过紧的问题，而不是仅因使用 LiteLLM 就要求替换。

证据：[运行协议](../../backend/plugins/ai/domain/model_runtime.py) 第 346–357 行；[数据库约束](../../backend/plugins/ai/entity/do/ai_resource_binding_do.py) 第 20 行；[绑定创建](../../backend/plugins/ai/service/model_catalog_service.py) 第 210 行；[运行时装配](../../backend/plugins/ai/infrastructure/model_runtime_provider.py) 第 77–85 行；[引擎管理](../../backend/plugins/ai/service/model_engine_service.py)。

## 8. P2：Agent 产品能力边界需要明确

当前主要调用流程是组装提示词、技能文本和知识片段后发起模型请求。统一模型消息不支持 tool 角色，响应和事件也没有完整的工具调用结构。现有能力可以承担配置式助手，但尚不足以支撑此前讨论的多步工具执行、MCP、人工介入与持久恢复。

这属于产品目标与基础协议之间的差距，不代表当前配置式助手实现错误。应先明确 Agent 目标范围，再建设执行协议与运行时，避免先引入框架却让新链路绕过授权、配额和用量记录。

证据：[Agent 请求组装](../../backend/plugins/agent_extension/service/agent_invocation_service.py) 第 18–75 行；[模型角色与响应事件](../../backend/plugins/ai/domain/model_runtime.py) 第 9、119–133 行。

## 9. P1（交付 MySQL 时）/P2：数据库升级责任分散，支持矩阵存在实际漂移

平台同时存在完整初始化 SQL、插件迁移、平台增量 SQL、启动 create_all 和 Alembic 运维入口，修改表结构时需要追踪多个入口。create_all 只能创建缺失表，不能替代已有表的字段升级。

当前具体例子：ORM 已声明 ai_chat_message.attachments，PostgreSQL 有 010_chat_attachments.sql，AI 清单仍声明支持 MySQL，但未找到相应 MySQL 字段升级。已存在旧聊天表的 MySQL 实例升级后可能因缺列而失败。默认 PostgreSQL 部署不因此自动受影响，新建环境是否被 ORM 补表也需与已有表升级区别验收。

应统一版本化升级责任，保留历史迁移执行证据，并明确 PostgreSQL 主线与 MySQL 的实际支持范围。

证据：[启动建表](../../backend/config/get_db.py) 第 20–36 行；[迁移与支持声明](../../backend/plugins/ai/plugin.yaml) 第 24–43、206–209 行；[附件 ORM](../../backend/plugins/ai/entity/do/ai_chat_platform_do.py) 第 88–93 行；[PostgreSQL 附件迁移](../../backend/plugins/ai/migrations/postgresql/010_chat_attachments.sql)；[MySQL 聊天表](../../backend/plugins/ai/migrations/mysql/003_platform_chat.sql)。本次为静态确认的迁移缺口，未运行 MySQL 升级实验。

## 10. P2（扩容或长任务隔离时）：后台任务所有权仍依赖 Web 进程

图谱构建使用进程内 asyncio.Task 和所有权表；另一个进程看不到本进程任务时会执行中断判定。两套 Compose 固定单 worker 是当前实现的合理约束，但不能直接调高 worker 数量实现扩容。Web 重启也会影响仍在执行的后台任务。

应先定义任务提交、持久状态、执行所有权、续租和恢复规则，再按实际负载评估独立 worker。已有原子元数据提交、文件锁和 PostgreSQL/Milvus 图谱存储，不能误称为完全没有持久化或图谱主体仅存 JSON。

证据：[图谱任务所有权与创建](../../backend/plugins/ai/knowledge/runtime.py) 第 43–45、109、259–290、428–437 行；[源码 worker 配置](../../compose.yaml) 第 6 行；[发布 worker 配置](../../deploy/compose.release.yaml) 第 14 行。

## 11. P2：业务验收与持续 readiness 覆盖不完整

前端有可执行 store/页面 action 测试、源码契约测试和 Mock API 浏览器交互；后端 CI 也有 PostgreSQL。这些测试各有价值，但当前浏览器工作流没有证明真实鉴权、菜单、上传、知识检索、Agent 和 SSE 的端到端联通。部分已有浏览器验收脚本未接入该工作流。

后端容器探针只访问 /docs，不能识别依赖失效或 Work 恢复循环缺席。发布时另有版本、路由和插件检查，但不等于持续运行中的业务 readiness。应分别建立生产构建冒烟、关键真实服务链路和持续就绪/后台执行器状态检查。

证据：[浏览器 CI](../../.github/workflows/playwright.yml) 第 43–65 行；[Mock 拦截](../../frontend/tests/e2e/platform_shell_check.py) 第 559 行；[后端 CI](../../.github/workflows/backend.yml)；[源码探针](../../compose.yaml) 第 163 行；[发布探针](../../deploy/compose.release.yaml) 第 184 行。

## 12. P2：依赖与局部文档仍保留历史技术路线

PostgreSQL 运行依赖和后端 CI 仍显式安装 Agno 及多种供应商 SDK；当前 AI 清单只声明 httpx、litellm。AI README 还列出旧依赖和启动安装行为，与当前清单及开发手册不一致。新开发者容易误判真实运行链路，依赖升级也需要核对多处清单。

应依据实际运行和传递依赖生成安装基线，统一依赖声明来源并校准文档。生产代码静态扫描未见 Agno 的直接导入，不足以单独证明所有历史 SDK 都能卸载，精简前仍需核实传递依赖。

证据：[PG 依赖](../../backend/requirements-pg.txt) 第 1–25 行；[后端 CI 依赖](../../.github/workflows/backend.yml) 第 67 行起；[AI 当前清单](../../backend/plugins/ai/plugin.yaml) 第 188–191 行；[AI README](../../backend/plugins/ai/README.md) 第 84–99 行。

## 13. P3：双 UI 与集中组件带来持续治理成本

前端同时使用 Ant Design Vue 和 Element Plus，需要维护主题映射、兼容样式与行为一致性。知识库页面又集中承担上传、检索、图谱、轮询和页面切换等职责。它们增加修改时需要同时理解和回归的范围，但不能仅凭框架数量或文件行数判定存在功能 bug。

已有 Element Plus 迁移基线约束（42 个文件），不宜无范围地重写所有管理页面。应按业务修改频率逐步收敛组件体系，并按上传、检索、图谱等职责拆分高负担页面。

证据：[前端依赖](../../frontend/package.json)；[主题桥接](../../frontend/src/assets/styles/workspace.scss) 第 57 行起；[迁移基线测试](../../frontend/tests/plugins/elementPlusRemovalContract.test.js) 第 38 行；[知识库页面](../../frontend/plugins/ai/views/knowledge/index.vue)。

## 验证范围与建议顺序

本次实际执行了只读静态检查、实际函数的内存替身隔离验证，以及以下三个已有前端测试，均退出码为 0：elementPlusRemovalContract.test.js、knowledgeGraphWorkspace.test.js、workTaskStoreIntegration.test.js。它们不能代表全量测试、真实供应商、生产构建、容器重启、压测或灾备恢复已经通过。

建议分批推进：

1. 先解决 Work 恢复执行器、普通聊天异常运行恢复和条件取消/租期语义；需要交付 MySQL 时同步补齐字段迁移。
2. 固定前端依赖，补生产构建与静态产物门禁，明确源码部署和镜像发布两条链路的责任。
3. 收拢核心 AI 装配与升级入口，明确模块数据所有权及公开接口，再实施目录迁移。
4. 根据 Agent 与开放 API 的产品优先级验证 LangChain/LiteLLM 路线，随后按负载评估后台 worker、单进程约束和 UI 收敛。

备份恢复目标、容量阈值和多租户支持边界仍应与实际部署及产品承诺核对；本次未把缺少现场验收材料直接当作线上缺陷。
