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
| models | 服务连接、凭据修订、模型能力与供应商适配 |
| billing | 用量事实、Token/金额/积分、价格和结算 |
| developer | 应用、Key、Scope、授权和限速策略 |
| chat | 会话、消息、回复版本、上下文和滚动摘要 |
| agents | 草稿、发布快照、试聊与调用审计 |
| skills | 安装预检、技能包、修订和冻结内容 |
| work | 任务、冻结成员、共享消息和单运行占用 |
| files | 文件元数据、权限、引用、回收和维护 |
| knowledge | 文档、解析、分块、向量、重排、共享和图谱 |
| operations | 系统参数、公告、操作审计和资源异步操作 |

跨模块调用通过 public.py 或 public.ts。领域不导入其他领域的 ORM、仓储或具体供应商 SDK；HTTP 路由只做协议转换，完整用例与事务位于 application。供应商、解析器、向量与图谱实现集中在 owning module 的 adapters 内，经 bootstrap 明确构造。

各领域通过独立 schema.py 提供迁移表清单，bootstrap/schema.py 聚合。表结构只由显式迁移修改；API 和 Worker 启动不执行建表。

## 运行与数据流

1. API 校验身份、当前权限与资源授权，冻结本次配置并创建持久运行。
2. 数据库事务同时记录业务事实和 Outbox，Worker 投递并消费队列。
3. 执行器按租约与 attempt token 进行供应商调用，保存增量内容、用量与终态。
4. 网页通过流式接口及状态查询观察持久投影，刷新与离开页面不丢失已接纳任务。
5. 取消先记录持久意图，再关闭上游和释放占用；未确认用量不按零计费。

PostgreSQL 保存业务与运行事实；Redis 用于队列、心跳与限速；Milvus 保存版本化向量索引；受管文件保存在共享文件卷。API、Worker 和迁移必须使用同一套配置、源码与密钥。

## 知识与解析

文档上传后，解析、切片、向量化与索引发布分阶段执行。新索引完整发布前保留原有效版本。来源权限在提交、执行和读取时复核，下载与知识检索分别授权。

解析优先级为本地 MinerU、显式启用的云端后备、基础解析。持久任务保存上游批次、源文件快照和可恢复的传输信息；创建结果未知时不重复创建任务。详见[文档解析](document-parsing.md)。

向量检索要求有效重排模型。LightRAG 图谱使用独立运行角色与图谱作业；普通知识问答使用向量链路。接口与重排规则见[知识检索](../api/knowledge-retrieval-policy.md)。

## 上下文与发布快照

普通聊天采用近期原文与滚动摘要，原始消息完整保存；智能体按配置选择最近完整轮次。详情见[会话上下文](chat-context-memory.md)。

智能体草稿与发布相互独立。公开 API 使用发布快照；工作成员加入时冻结发布版本。技能内容、知识依赖、模型绑定和实际请求保存可核对的来源，编辑草稿不改写历史运行。

## 工程约束

- [代码规范](code-standards.md)
- [开发与隔离测试](../development.md)
- tools/check_architecture.py 检查依赖方向。
- tools/check_core_wheel.py 检查独立打包和迁移内容。
- frontend/tools/moduleBoundaries.js 检查前端模块依赖。
