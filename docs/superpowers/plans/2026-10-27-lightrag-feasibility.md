# LightRAG 接入可行性实验计划

> **面向 AI 代理的工作者：** 使用 `subagent-driven-development`，仅一个实现者。实验代码保存在仓库外私有目录；先规格、再质量审查。实验结论与正式平台交付分别记录。

**目标：** 用锁定真实 SDK 验证无 DDL 存储、来源隔离和单槽受治理回调，确认[图谱设计](../specs/2026-10-27-lightrag-design.md)可实施。

**架构：** 复用独立测试 PostgreSQL/Redis/Milvus 的连接，仅新建本实验登记的 UUID 数据库、schema、角色和精确 collection。实验不操作正常或 audit 实例，不修改正式依赖、迁移或旧源码。原服务和已交付知识库保持运行。

**技术栈：** `lightrag-hku==1.5.7`、Python 3.10/3.13 兼容依赖、asyncpg 0.31.0、pymilvus 2.6.17、平台已安装 wheel、ARQ、真实本地 HTTP 模型协议。

## 文件与顺序

实验目录为仓库外 `20261023-feature-parity/lightrag-spike-v1/`。创建 `environment/`（隔离安装与实际依赖清单）、`adapters/`（真实 storage 生命周期包装）、`schema/`（官方完整 DDL 与 catalog 预期）、`runner/`（SDK 回调及有监督生命周期实验）、`tests/`、`resources.jsonl`、`report.md` 和 `manifest.json`。任何秘密只进入私有运行配置，不输出到日志或报告。

### 任务 1：依赖与真实存储边界

- [ ] 核官方 wheel SHA `895b81d94ca447afc6290c0e049d339f6caed46fbf29e4324f6db987046c6fcb`，在新虚拟环境求解 core 锁与 SDK base 的联合依赖，不安装 api/offline-storage 全量 extra，不修改当前 venv。
- [ ] 保存 resolver 报告和全部 distribution/version/hash；实际导入所需 SDK 模块前确认 asyncpg/pymilvus 已安装，防止 pipmaster 动态安装。禁止通过 `--no-deps` 掩盖缺依赖。
- [ ] 先运行失败探针：原图 initialize 在无 DDL 角色下失败；原 Milvus initialize 的缺资源创建与 drop 重建行为不能冒充只读。该负例只作用于自身数据库/schema/collection。
- [ ] 显式 DDL 创建 8 张非 PG-vector 表与 2 张图表、26 个索引、10 个 PK 和 2 个 FK；创建非 owner、无 CREATE 权限的运行角色，验证实际 catalog 和 search_path/reconnect。
- [ ] 组合包装 final KV/doc-status/vector 与图生命周期适配，真实初始化/CRUD/flush/finalize；初始化连接和 catalog 校验有实际动作，不使用空 monkeypatch。缺表、错列/索引、冲突 workspace 和环境配置必须拒绝或保持显式配置，不能自动修复。
- [ ] 显式 provision 三类 Milvus collection；读取缺集合/索引不创建。两个 workspace 使用同模型短名仍隔离，GC 只删除资源登记中的精确身份，邻空间不变。

### 任务 2：单槽回调与停止

- [ ] 在实验 UUID 数据库安装现有平台 schema 和本地测试身份/模型/费率；注入 SDK 真实 LLM/Embedding callback，回调仅建立标准 Run、预留、step/outbox，释放事务后等待持久结果。
- [ ] 用生命周期根拥有 SDK runner 与 ARQ `max_jobs=1`，实际 HTTP provider 返回可解析中文实体/关系、关键词 JSON、向量和 usage。验证共享实体的两文档完整抽取、聚合、PG/KV/status 与三类向量非空，model HTTP 数与已发送 Run/usage 对齐。
- [ ] 对 callback ordinal、重复文本、并发 1/4、缓存命中记录精确行为；同一子 Run 重复观察不新增供应商调用，失败/unknown 不自动重发。
- [ ] 在 queued child、provider 已启动、merge 中取消；停止接纳并显式停止、等待 SDK role/embedding/chunk task 和传输，最后 finalize 存储。验证关闭后没有遗留 writer，候选未发布，原已完成空间不变。
- [ ] 过期 lease/worker 失联后旧 attempt 只能 interrupted；显式重试独立 namespace，旧 token 不能写入或发布。记录 event loop 心跳/取消延迟，若同进程无法有界，再评估单独 graph 进程。

### 任务 3：查询、证据与审查

- [ ] 实际 `aquery_data` 返回实体/关系/chunk/references，确认关键词和 Embedding 回调确实进入治理；最终 answer role 不执行，活动 namespace 不发生 cache/flush 写入。
- [ ] 用两份不同来源构建共享实体，撤销其中一份源权限后，平台门禁拒绝 labels/subgraph/query；不能只对返回 references 做过滤。
- [ ] 记录每条命令、真实退出码、日志 SHA、SDK 原字节摘要、实际资源身份、DDL 账号与运行账号权限证据。每个未执行项明确列出，不将 stub、skip 或纯源码阅读称作集成通过。
- [ ] 独立核对自身登记的数据库、角色、collection 和队列全部清理；不做前缀全局清理。保留报告和失败证据，经独立规格及质量审查确认后，将有效 adapter/runner 设计写入正式实施计划。

通过条件是上述真实 SDK、无 DDL 角色和唯一 ARQ 槽证据成立。若失败，先记录具体阻断与最小替代方案；不修改已交付依赖或迁移来掩盖实验问题。正式图谱配置/API/原 UI 和 `0008` 迁移在实验通过后依照验证结果实施并独立验收。
