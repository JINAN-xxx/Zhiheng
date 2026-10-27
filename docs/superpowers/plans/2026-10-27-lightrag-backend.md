# LightRAG 后端实施计划

> **面向 AI 代理的工作者：** 使用 `subagent-driven-development`，一个后端实现者，完成后先独立规格、再质量审查。根任务拥有前端、部署和最终交付；不修改 legacy，不触正常或 audit 数据。

**目标：** 将独立实验已验证的真实 SDK 接入内置 knowledge，完成图谱配置、预览、构建、标签、子图和受治理上下文查询。

**架构：** 版本化 PG 图/KV/status 与三类 Milvus collection；有持久身份的图谱作业及 attempt；worker 生命周期持有 SDK 线程/独立 loop，模型回调经主 loop 标准 Run/outbox/账本执行，唯一 ARQ 槽不等待自己的子任务。

**技术栈：** LightRAG 1.5.7、LangChain 1.4.7、asyncpg、pymilvus、PostgreSQL、现有 Run/ARQ。使用[图谱设计](../specs/2026-10-27-lightrag-design.md)。实验报告仅证明基础可行性，不免除下面的产品门禁。

## 文件与职责

| 文件 | 职责 |
| --- | --- |
| `modules/knowledge/graph/contracts.py` / `orm.py` / `service.py` | 严格 DTO、配置/作业/attempt/版本/pin、CAS 和全集来源授权 |
| `modules/knowledge/graph/store.py` / `generation.py` | 认领、租约、步骤身份、状态、发布与精确 GC |
| `modules/knowledge/adapters/lightrag/{schema,postgres,vector,factory,sdk}.py` | 锁版真实 SDK、完整 catalog 校验、组合包装、registry、独立 SDK loop |
| `application/graph_runs.py` / `graph_jobs.py` | 模型步骤治理、参数转换、作业与查询编排 |
| `worker/graph_runner.py` / `worker/main.py` | 有界监督、SDK 线程收尾、恢复和心跳 |
| `api/routes/knowledge_graph.py` | 配置、持久作业、图展示和查询协议 |
| `modules/models/{contracts,service}.py` 及模型 adapter | 有类型 JSON/推理参数、实际用量归一与参数能力拒绝 |
| `migrations/versions/0008_graph.py` / schema inventory | 显式业务表、完整 SDK 专属 schema、版本升级 |
| `tests/core/integration/test_graph_*.py` / `tests/unit/test_graph_*.py` | 真实 PG/Milvus/模型及纯边界回归 |

根据实际职责可调整小文件名，禁止单文件堆积所有业务或为了命名机械拆转发层。所有跨域访问仅经公开接口，SDK 仅在 adapter。

## 1. 先修底层故障边界

- [ ] 使用独立质量探针的真实 RPC 延迟条件复现：两次固定等待超时后 RPC thread 仍存活，旧 writer 不得标可 GC。
- [ ] 复用现有 `VectorIOControl` 的实际 gRPC 期限/取消，新增 SDK async 方法到受控执行的边界；finally 排空每个实际 future、关闭 executor，不能仅 join 外层 SDK 线程。
- [ ] 在取消、过期 lease、实际 RPC 异常、关闭再次取消时确认 future/thread 归零，旧 namespace 无迟到写入；固定时间等待不能替代真实传输终结。

## 2. 依赖、完整迁移与模型控制

- [ ] 为 LightRAG base 联合求解完整哈希锁。实验诊断使用 tenacity9.1.4/google-genai1.73.0；正式选择必须兼容现有 LangChain/OpenAI，执行 Python3.10/3.13 实装及已有平台完整回归，不能沿用诊断锁冒充跨版本锁。
- [ ] `0008_graph` 新建配置/作业/attempt/generation/pin 和 SDK 全部 10 表、26 显式索引、10 PK、2 FK；0001..0007 同字节。SDK schema 由迁移角色拥有，显式运行连接无 DDL；运行时只验证，不自行建角色或修表。
- [ ] 在 `Settings` 明确图谱运行连接，默认能力不可用；部署由根任务配置非 owner 角色和密码。缺连接、缺表、错列/索引明确报错，不能 fallback 到主连接或 SDK 环境。
- [ ] 将 `json_object`、输出上限和实际支持的推理参数纳入有类型 ModelRequest/Options、冻结指纹与供应商转换；未知能力拒绝。JSON 回调真实 input/output/缓存用量捕获，未报告时保留 unknown 与预留；不得把关键词用量写成零。

## 3. 真实存储与版本

- [ ] 从锁定 wheel 提取 catalog 预期并验证 SDK 原字节；PG 显式 pool、组合包装与图生命周期实际 init/reconnect/finalize，不调用 ClientManager 的环境读取和全局 DDL。
- [ ] 三类 Milvus collection 显式 provision，名称固定长度、完整 manifest 描述；读路径禁止 create/repair/load/drop；缺资源失败。每个方法接入实际传输控制，活动图拒写。
- [ ] 两个并发 workspace、相同模型名、冲突 SDK 环境和重连实际验证隔离；缺列/索引、长度/碰撞、邻 namespace 保护均有真实服务测试。
- [ ] 受管 chunk 到 SDK ID 的映射保存文档/文件/索引 revision/ordinal/hash，不能直接采用 deprecated custom-chunks 的 unknown_source 或内容去重作为来源契约；相同文本的两个文档仍可追溯。
- [ ] 完整成功才切换图活动指针；配置/binding/源/index_revision 变化使旧图 stale。读 pin、模型回调和候选 writer 完成后才能精确 GC，无前缀删除。

## 4. 持久回调与监督

- [ ] 图模型资源类型加入严格 RunSnapshot/执行 guard，不能借 connection_test 作为产品资源。标准 step+Run/reserve/outbox 同事务，SDK 缓存命中不造调用；每个 outbox ID 可被测试精确登记。
- [ ] 独立 graph attempt lease/epoch/deadline，主 loop 监督有界 SDK 线程，跨 loop 只传可信值与明确 attempt 身份；DB/Redis client 不跨 loop 使用。
- [ ] SDK role/embedding 队列和 chunk task 有明确句柄、停止与 drain，关闭后连接/线程全退出。ARQ max_jobs=1 完成真实抽取/gleaning/强制摘要及三类向量；并发1/4均计费正确。
- [ ] queued、provider-started、merge 中途取消；失联重启旧 attempt interrupted，新显式 attempt 独立 namespace。旧 epoch 不能写入或发布；未知子调用不自动重发，取消响应仍执行当前读授权。

## 5. HTTP 契约

所有路径为 `/api/knowledge/bases/{base_id}/graph` 下的 Cookie 入口，UUID、snake_case、extra=forbid。GraphConfig：`version,llm_model_id,reasoning_strategy,timeout_seconds,max_output_tokens,concurrency`；策略为 auto/default/low/disabled，能力未知时不猜测供应商参数。

| 方法和子路径 | 请求与响应 |
| --- | --- |
| GET/PATCH `/config` | GET `{config,available,active_revision,requires_rebuild,can_manage}`；PATCH `{expected_version,llm_model_id,reasoning_strategy,timeout_seconds,max_output_tokens,concurrency}` |
| POST `/preview` | `{client_request_id,expected_version,document_id:null|UUID}` →202 GraphJobInfo，有限已授权片段，不切正式版本 |
| POST `/build` | `{client_request_id,expected_version,rebuild:false}` →202 GraphJobInfo；同键重试原 job，不重复付费 |
| GET `/jobs` | 最近50 GraphJobInfo 数组 |
| GET `/jobs/{job_id}` / POST其`/cancel {}` | 原作业进度、错误、结果与幂等取消；结果仍按当前全集来源授权 |
| GET `/labels` | 当前可读活动版本 `{items:[string],truncated}`，上限200，不能无界返回 |
| GET `/subgraph` | `node_label=*&max_depth=1..5&max_nodes=1..200` →`{revision,nodes,edges,is_truncated}`；节点含id/entity_name/entity_type/description，边含id/source/target/description |
| POST `/query` | `{client_request_id,query,mode:mix|local|global|hybrid|naive,top_k:1..8}` →202 GraphJobInfo；结果 `{context,entities,relationships,sources}`，上下文<=24000 UTF8字节 |

GraphJobInfo：`id,base_id,operation,status,phase,version,attempt_id,graph_revision,processed_documents,total_documents,progress,error,result,cancel_allowed,created_at,updated_at,finished_at`。终态为 completed/failed/cancelled/interrupted，活动态 pending/running/waiting_model/draining；只返回真实已知进度，未知数为 null。错误为 `{code,message}`。

labels/subgraph/query/preview 全部先按可信 owner/读者校验库和该图版本全集原文权限；一份源被撤权时不返回聚合图。query 用真实关键词/Embedding，只取 SDK context，不执行答案生成；cache 禁用必须覆盖复制配置，存储拒写保护保留，完整活动 PG/Milvus 读前后摘要核对。

## 6. 门禁与交接

- [ ] 先红后绿、完整后端回归/ruff/format/mypy/AST、wheel资源、哈希安装、3.10/3.13、0007已有数据迁移、新 graph 正常/失败/权限/费用/取消/失联/单槽/GC 全通过。
- [ ] 冻结后端和测试完整 source map；每个最终命令退出码、实际非 NUL 日志 SHA、source before/after 一致，失败尝试保留。
- [ ] 独立规格审查后独立质量审查，实质问题修复后重新绑定候选。交接精确 API/DTO、能力状态、迁移/角色 provisioning、安装包和已验证限制给根任务。
- [ ] 此后由根任务恢复原 ECharts 图谱页面、完成前端双审、真实容器链、完整备份与正常实例升级；在此之前不得标记整组已交付。
