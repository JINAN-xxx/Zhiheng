# LightRAG 图谱实施设计

日期：2026-10-27。承接已批准整体架构第 11 节和文件/知识库第三组计划。向量知识库已在 `fdcc53b4` 交付，正常实例为 `0007_knowledge`；本设计细化既定图谱范围，不将下面的目标描述为已实现。

## 目标与选择

接通 LightRAG 1.5.7 的真实抽取、聚合、PG 图/KV/doc-status、Milvus 图向量、标签、子图与上下文查询，恢复原 Graph 页面、配置、试构建、正式构建及失败状态。模型仍通过 LangChain 1.4.7 与平台 Run/预留/outbox/账本执行。普通向量检索维持已交付行为；MinerU 属于下一组。

选择现有 worker 生命周期内有监督的图谱调度器：图任务真实等待 SDK 回调，ARQ 唯一槽执行付费子 Run。拒绝把长 SDK 父任务放进 ARQ 槽再等待同队列子任务，也不通过抛普通 Suspend、伪造立即回调或直接调用供应商规避调度。官方 pipeline 会将普通异常落为 FAILED；异常不能视为原生 checkpoint。

## 模块与存储

- `knowledge/graph/` 拥有配置、作业、attempt、不可变图版本、来源映射与读 pin；`application/graph_runs.py` 负责子调用治理和跨模块事务。
- SDK 适配留在 `knowledge/adapters/lightrag/`。只在初始化时注册独立平台存储名，拒绝名字冲突；不能反复修改官方类或全局连接配置。
- 显式迁移创建完整 8 张 KV/status 表和 2 张图表、对应主键/外键与 26 个显式索引，放入专属 schema。运行角色与迁移角色分离，图谱运行角色没有 DDL 权限；初始化实际连接并验证 catalog，缺对象、错误列/索引/版本明确失败，不自动修复。
- PG 使用显式注入的连接配置及 `enable_vector=False`，不经 SDK ClientManager 的环境/config.ini 回退和进程全局池。图存储使用小型生命周期适配；被 SDK 标记 final 的 KV/doc-status/vector 类通过组合包装保留真实 CRUD，并完整实现存储接口与能力标志。包装必须经过真实 SDK 实验，不能用空 initialize/check_tables。
- 每个 attempt 的 workspace 和三类 Milvus collection 由平台 manifest 决定，采用固定长度哈希名并保存完整身份描述。构建 provision 显式创建/index/load；活动版本的读取仅校验现有对象，缺库/集合/索引不能创建、迁移或修复。不会调用 SDK 的 drop-then-recreate 作为 GC。
- 所有 SDK 连接、缓冲和工作队列都有归属。实际 Milvus RPC 沿用已验证的绝对期限、取消和 drain 策略，不依赖高层 SDK timeout 关闭内部重试。

## 持久任务与模型回调

作业分为 build、preview、query、gc；每个 attempt 有独立 owner token、lease、绝对 deadline、workspace、状态和 callback ordinal。等待模型期间仍续租。有限 tick 只认领、投递或观察事实并返回；worker 生命周期根拥有有界 runner 任务集合，停止时等待全部退出，不留下 fire-and-forget 业务 task。

真实 SDK 回调在短事务内校验 attempt/权限/冻结 binding，按 `(attempt_id, role, operation, ordinal)` 建唯一 step、标准 Run、预留和 outbox；提交并关闭事务后等待其持久终态。相同文本出现在不同真实回调中是不同步骤，不能仅用 prompt hash 合并付费事实。未知或已启动失败调用不自动重发。进程失联使原 attempt interrupted，显式重试创建新 attempt/namespace；本组不承诺进程失联后重放同一 SDK 栈。

回调明确处理 SDK 的文本、system、history、max_tokens、response_format；内部 cache/队列参数不传供应商。`json_object` 进入有类型的模型协议并计入指纹；未知生成参数明确拒绝，不能静默忽略。输出上限不超过冻结配置和模型限制，预留包含实际完整消息及最大输出；截断抽取/摘要使候选失败，推理内容不当作抽取正文。

初始并发受全局 runner 上限和 attempt 回调信号量共同约束，配置为 1..4。每个实际模型调用都能追到 parent/attempt/step/Run 和用量；SDK 缓存命中不创建未发送的模型调用。自定义解析和供应商 SDK 路径保持关闭。

取消与关闭顺序：持久 cancel/draining → 阻止新 callback/写入 → 取消或等待已提交子 Run → 终止并等待 SDK chunk/role/embedding 队列及实际传输 → finalize storage → 释放 runner 和连接。`finalize_storages()` 本身不等于所有 SDK 队列已退出。停止和失联不得发布候选，GC 必须证明原 writer 已停止，并尊重活动指针与读 pin。

首轮独立实验进一步采用 worker 持有的专用 SDK 线程及独立 event loop：同步 Milvus 调用仍会阻塞 SDK 自己的 loop，不能因包了一层线程池就宣称主循环不阻塞。SDK 回调通过显式 attempt 上下文桥接到主 loop 建立和等待受治理 Run，数据库与 ARQ 连接只在各自所属 loop 使用；关闭时必须等待 SDK 队列、任务、传输和线程退出。最小单槽样本中，共享 loop 的运行阶段心跳最大延迟约 974 ms，独立 SDK loop 约 44 ms；这不是生产延迟保证。更完整的取消、失联、多 workspace 与查询实验仍须执行。

独立质量探针已证实实验中固定 `future.result(10)` 的 drain 仍不充分：两次等待超时后，即使 SDK 外层线程已退出，RPC executor 仍可能运行。正式 adapter 必须使用实际传输的绝对期限和取消控制，在 finally 确认 executor/future 全部终结后才能报告 writer 已停止；不能仅 join SDK 外层线程。该故障边界须先红后绿，未通过时不得把旧候选标记为可安全 GC。

## 版本与权限

图版本冻结完整活动向量 revision、所有文档/chunk/source SHA、解析版本、模型 binding、配置/prompt/SDK 版本。读取先比较当前指纹；删除、重新切片、模型 binding 或配置变化后明确要求构建新版本，禁止拼接新旧空间。构建完整成功、所有期望文档 PROCESSED、无截断且存储 flush/验证成功后，短事务切换活动指针；失败保留旧指针，但旧版本仍须满足当前指纹和权限才能读取。

图实体/关系描述可能合并多份文档。没有来源子集重算算法时，**读者必须对该版本全部来源具有当前读权**，包括标签、子图、查询与试构建结果；只过滤返回的 references 不足以授权聚合内容。按库与原文件权限交集、原 owner 身份验证，管理员代操作不扩大来源。构建前、模型调用前、发布和读取时复核；撤权后的持久结果也不能经 poll/cancel 旁路返回。

本组图谱查询使用 `aquery_data` / context-only，返回结构化实体、关系、chunk 和可追溯来源，不额外生成答案。它仍可能调用关键词 LLM 和 query Embedding，必须治理和计费；不以“context-only”宣称无模型调用。活动图 namespace 禁止写入，查询 cache 使用独立短生命周期空间或关闭，不能把 SDK 查询 cache/flush 写回活动版本。

首轮查询实验确认 SDK 构造时会将 global_config 复制到存储，运行后只设实例 `enable_llm_cache=False` 不足以关闭存储缓存。只读 query builder 必须在实例与相应存储配置上冻结一致的禁缓存策略，并保留存储拒写保护；真实关键词/Embedding 仍走治理。实验用只读 client 验证活动缓存前后相同、没有写入尝试，禁止用无声丢弃写入的 no-op 冒充只读成功。

## HTTP 与原界面

保留 `/api/knowledge/bases/{base_id}/graph/` 所属接口：`config` GET/PATCH（CAS）、`preview` POST/GET、`build` POST/GET、`jobs/{job_id}` GET 与 `/cancel` POST、`labels` GET、`subgraph` GET、`query` POST。提交模型操作使用稳定 client_request_id；返回持久图作业 DTO、明确进度/error 和原结果恢复，不把 poll 变为新调用。具体 DTO 在实施计划中按已验证 adapter 契约冻结。

配置保留原模型、超时、输出上限和 1..4 并发；推理策略只开放模型明确支持的参数，自动策略不猜测未知模型或吞掉参数。预览仅消费有限已授权片段，独立 namespace，不切换正式版本。正式构建消费平台现有受管解析/分块，不让 SDK 重新解析任意文件。

前端复制并适配原 `KnowledgeGraphPage.vue` / `KnowledgeGraph.vue`，保留 ECharts 图、布局和交互，补严格 TypeScript。Graph 目标和查询模式必须真正传到接口，不能只改变选择器文案。明确显示全集来源权限限制、stale/rebuild、失败、取消与未确定提交，前端不泄漏物理 namespace 或凭据。

## 实施门槛与验收

首先在仓库外独立环境完成 SDK 可行性实验：完整依赖求解；真实 registry/组合包装；完整 DDL 与无 DDL 角色初始化/读写；两 workspace 和冲突环境隔离；唯一 ARQ 槽下真实受治理子 Run；取消/失联/队列 drain；context-only 关键词/Embedding 计费和活动版本不写。实验未通过时修设计，不把 stub 当已接入 SDK。

实现按存储与治理、任务与 API、原 UI、真实交付分步，每步 TDD 后先规格再质量审查。最终执行完整后端与前端门禁、Python 3.10/3.13 完整哈希安装、真实 PG/Milvus/本地模型图抽取查询、单槽与失败/取消/权限/GC 回归；空库和已填充 `0007_knowledge` 升级均核对旧数据。正常实例完整备份后升级，再核账号、原页面与只读接口，保留旧工程优化。图谱与 MinerU 不互相冒充完成。

依据：官方 [LightRAG 1.5.7](https://pypi.org/project/lightrag-hku/1.5.7/) wheel `895b81d94ca447afc6290c0e049d339f6caed46fbf29e4324f6db987046c6fcb`；仓库外 `lightrag-research/{storage-design-v1,callback-design-v1}/report.md` 保存锁定源码的准确函数、schema、生命周期和未执行实验边界。
