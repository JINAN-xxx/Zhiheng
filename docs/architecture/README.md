# 架构与模块边界

智衡采用模块化单体后端与独立 Worker。后端分发包为 zhiheng-platform，导入包为 zhiheng，命令入口为 python -m zhiheng。前端使用 Vue 3、TypeScript 和 Ant Design Vue。

## 源码结构

~~~text
backend/src/zhiheng/
├── api/             Console、推理和资源 HTTP 协议
├── application/     业务用例、事务及跨模块协作
├── modules/         领域规则、数据访问、公开端口与供应商适配
├── runtime/         持久运行、attempt、租约、投影和 Outbox
├── worker/          队列、周期调度、恢复和心跳
├── bootstrap/       配置、资源装配、迁移与适配器构造
├── infrastructure/  数据库连接、凭据加密和文件存储
└── shared/          身份、错误和基础值

frontend/src/
├── app/             入口、路由、页面外壳与模块装配
├── modules/         各业务域的 API、状态、表单和页面
└── shared/          HTTP、流式协议、校验、通用组件与样式
~~~

## 领域职责

| 模块 | 职责 |
| --- | --- |
| identity | 用户、部门、角色、会话和权限 |
| models | 服务连接、凭据修订、模型能力、容量预算与供应商适配 |
| billing | 用量事实、Token/金额/积分、价格和结算 |
| developer | 应用、Key、Scope、授权和限速策略 |
| chat | 会话、消息、回复版本、上下文和滚动摘要 |
| agents | 私人助手、最新配置、启用状态、调试、正式会话及调用审计 |
| skills | 安装预检、技能包、修订和冻结内容 |
| work | 任务、冻结成员、共享消息和单运行占用 |
| files | 文件元数据、权限、引用、回收、通用解析协议/配置与解析器 |
| knowledge | 知识资料、构建所需解析任务事实、分块、向量、重排、共享和图谱 |
| operations | 系统参数、公告、操作审计和资源异步操作 |

跨模块调用通过 public.py 或 public.ts。领域不导入其他领域的 ORM、仓储或具体供应商 SDK；HTTP 路由只做协议转换，完整用例与事务位于 application。供应商、解析器、向量与图谱实现集中在 owning module 的 adapters 内，经 bootstrap 明确构造。

模型试用与连接测试位于 `application/models/trials.py`，普通会话准入位于 `application/chat/runs.py`，它们与助手、Work、公开API和知识子调用共用原生 `RunAdmission`，身份、幂等及事务边界见[模型执行准入](execution-admission.md)。通用输入预算算法位于 `modules/models/capacity.py`，Chat和模型目录使用同一计算规则。

文件解析契约、解析配置及供应商适配器位于 `modules/files/parsing_contracts.py`、`modules/files/parsing.py` 和 `modules/files/adapters/`。知识构建专属的父任务检查、租约和解析尝试仍位于 `modules/knowledge/parsing.py`；没有为目录迁移复制第二套实现或迁移原表数据。

各领域通过独立 schema.py 提供迁移表清单，bootstrap/schema.py 聚合。表结构只由显式迁移修改；API 和 Worker 启动不执行建表。

## 运行与数据流

1. API 校验身份、当前权限与资源授权，冻结本次配置并创建持久运行。
2. 数据库事务同时记录业务事实和 Outbox，Worker 投递并消费队列。
3. 执行器按租约与 attempt token 进行供应商调用，保存增量内容、用量与终态。
4. 网页通过流式接口及状态查询观察持久投影，刷新与离开页面不丢失已接纳任务。
5. 取消先记录持久意图，再关闭上游和释放占用；未确认用量不按零计费。

PostgreSQL 保存业务与运行事实；Redis 用于队列、心跳与限速；Milvus 保存版本化向量索引；受管文件保存在共享文件卷。API、Worker 和迁移必须使用同一套配置、源码与密钥。

## 知识与解析

知识库目录、资料管理、构建准入、引用和资料迁移的代码归属见[知识模块职责](knowledge-boundaries.md)。

文档上传后，解析、切片、向量化与索引发布分阶段执行。新索引完整发布前保留原有效版本。来源权限在提交、执行和读取时复核，下载与知识检索分别授权。

解析优先级为本地 MinerU、显式启用的云端后备、基础解析。持久任务保存上游批次、源文件快照和可恢复的传输信息；创建结果未知时不重复创建任务。详见[文档解析](document-parsing.md)。

向量检索要求有效重排模型。LightRAG 图谱使用独立运行角色与图谱作业；普通知识问答使用向量链路。接口与重排规则见[知识检索](../api/knowledge-retrieval-policy.md)。

## 上下文与执行快照

普通聊天与正式助手会话共用官方create_agent、Token摘要Middleware与PostgreSQL checkpoint，分别拥有业务记忆和完整消息记录。详情见[会话上下文](chat-context-memory.md)。

智能助手只维护最新配置与启用状态，正式会话的新消息和公开 API 使用本轮受理时的配置；调试/API每请求独立。工作成员加入时冻结当时配置。技能内容、知识依赖、模型绑定和实际请求保留审计快照，编辑配置不改写已受理的运行。

## 文件与生命周期

文件身份、授权、目录查询、引用和清理任务的职责见[文件模块边界](file-boundaries.md)。通用解析继续属于文件域；知识资料使用文件引用与原文授权，不复制文件生命周期。

## 账务与计量

查询、价格版本、幂等收据和资金变更核心的职责，以及精确金额、未知用量、事务与锁序，见[账务模块边界](billing-boundaries.md)。

## 模型目录与执行准备

连接、目录、绑定、保存校验及容量依据见[模型模块边界](model-boundaries.md)。可用性表示平台配置状态，容量未知与未提供依据明确区分。

## 工程约束

- [代码规范](code-standards.md)
- [开发与隔离测试](../development.md)
- tools/check_architecture.py 检查依赖方向。
- tools/check_core_wheel.py 检查独立打包和迁移内容。
- frontend/tools/moduleBoundaries.js 检查前端模块依赖。

## 权限和容量投影

Key Scope目录返回当前身份的`grantable/reason`，签发、轮换和执行复核共用领域权限规则。读取知识库/技能分别检查read，不能推断成query/use；前端权限变化后刷新目录并清理受保护的表单与一次性密钥。

模型窗口未知时明确返回未知，不从模型名称猜窗口。Console模型响应提供默认聊天输入预算及来源；它复用Chat的输出预留与安全余量，本轮显式输出参数变化时重新计算。修改已知窗口时拒绝最大输出与安全余量占满窗口的配置；不改写历史冻结绑定。

## 源码模式启动

`compose.source.yaml`挂载只读源码和依赖清单。CLI在加载API/Worker前检查镜像中的直接依赖版本和数据库schema；迁移命令只预检依赖，允许显式升级schema。CLI help和migrate不导入API、Worker及供应商SDK。

依赖或迁移变化时，先构建镜像、显式迁移，再重建API和Worker。启动预检不替代这一升级顺序，也不替代运行中热重载的统一进程重启；Worker不自动跟随API源码重载。

前端控制器、观察命令与应用资源组合见[前端控制器与应用组合](frontend-controllers.md)。

checkpoint、退役分块、文件扫描和技能包的回收条件见[生命周期与有界维护](lifecycle-performance.md)。

退役列、具体权限与历史迁移边界见[退役字段与精确权限](schema-permissions.md)。

## 智能助手执行与记忆

[智能助手执行与记忆](assistant-langchain.md)说明最新配置、三种入口、共享技术边界、增量 checkpoint、摘要和账务约束。
