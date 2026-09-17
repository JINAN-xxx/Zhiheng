# 知识图谱实链路与补建计划

> **面向 AI 代理的工作者：** 使用 subagent-driven-development 按固定接口分工并审查，独立文件可用 dispatching-parallel-agents 并行实施。

**目标：** 新建知识库可启用图谱，已有知识库可补建、查看进度、失败重试；向量存储继续固定 Milvus。

**架构：** 固定 LightRAG 1.5.7，采用内置 PGTableGraphStorage、PGKVStorage、PGDocStatusStorage 复用平台 PostgreSQL，Milvus 保存图谱向量。图谱构建使用现有 ModelService 的已发布 Embedding/聊天模型。图谱依赖和环境别名由平台配置接入；不需要 AGE 或额外图数据库服务。当前一个 worker 的运行边界保持。

**现状证据：** 线上 graph_available=false；未安装 lightrag/neo4j，仅配置 KNOWLEDGE_MILVUS_URI；已有 1 个 Milvus 知识库、3 份文档，均未启用图谱。数据库没有 lightrag_* 表。官方 1.5.7 wheel 包含 kg/pgtable_impl.py，原生 PostgreSQL 图存储无需扩展。

## 对外合同

- `GET /ai/knowledge/databases/{db_id}/graph-build`：query 权限，返回标准 data envelope。
- `POST` 同路径：edit 权限，body `{llm_model_id?: ModelResourceId}`；首次启用必选聊天模型，已有图谱省略时沿用模型，拒绝无意切换已有模型。异步启动补建，重复请求复用当前任务。
- 状态字段：`status`（disabled/idle/queued/running/completed/failed/interrupted）、`job_id`、`total_documents`、`processed_documents`、`failed_documents`、`current_document`、`error`、`started_at`、`finished_at`。错误使用现有脱敏 KnowledgeError 风格。
- 知识库列表和详情新增 `graph_build`，保留已有 enable_knowledge_graph、llm_model_id。文档新增独立 graph_status/graph_error，图谱失败不让已成功的向量文档变为失败。
- 图谱入口对可查询知识库始终可见，未启用时展示模型选择和“启用并构建图谱”。已启用时展示进度、重试/补建和真实节点关系。新知识库原勾选功能保留。

## 工作项

- [ ] 存储适配（graph.py/stores.py/图谱依赖/专用测试）：接入 PostgreSQL 原生图、KV、状态存储；复用 DB_* 和 KNOWLEDGE_MILVUS_* 配置；验证初始化失败清理、插入完成状态、查询、删除与重启后的工作空间隔离。
- [ ] 后台补建（runtime.py/models.py/controller 与测试）：持久化任务进度，在短元数据锁下调度后台任务；构建期间保留向量读取，防止文档变更与补建竞争。失败可重试，关闭/重启产生可恢复的 interrupted 状态；不在查询时隐式触发 LLM 索引。已有文档补建只更新图谱，不重算主向量索引。
- [ ] 前端（知识库页面/API/utils/桌面测试）：始终展示图谱导航，支持选择模型、开始补建、轮询进度、失败重试；切换知识库时清理轮询并忽略旧响应。Milvus 固定字段保持，图谱存储不作为用户选项。
- [ ] 运行环境：准备图谱依赖 wheel，基于当前 chat-attachments 镜像增量构建，保持已安装依赖的版本；验证 Python 3.10 运行及 pip check。
- [ ] 实链路：使用独立测试知识库、现有真实模型及真实 PG/Milvus 验证“先向量索引→启用/补建图谱→非空节点/关系→查询→关闭重开后读取”。测试空间清理自身资源，现有用户知识库提供补建入口，不自动批量调用模型处理用户文档。
- [ ] 审查与发布：完成后端、前端回归与桌面交互验证，复制产物到新发布目录，组合 20260916-chat-attachments/manage.sh。保持 FRP 8060、Nginx 12580、私有文件和知识库持久挂载。记录实际结果与回退。

参考：[LightRAG 1.5.7 PGTableGraphStorage](https://github.com/HKUDS/LightRAG/blob/v1.5.7/lightrag/kg/pgtable_impl.py)。
