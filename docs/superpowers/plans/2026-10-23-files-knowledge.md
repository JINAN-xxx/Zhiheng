# 文件、知识库、图谱与 MinerU 实现计划

> 使用 `subagent-driven-development`，逐域先规格、再质量审查；当前阶段完整交付再开始下一个实现任务。原界面与 legacy 未提交优化保持原字节。

**目标：** 在内置 AI 核心接通真实受管文件、知识库索引/检索、LightRAG 图谱和 MinerU 解析，恢复普通聊天/智能体/Work 的相关入口与数据链路。不把菜单、声明能力或伪造文本作为功能交付。

**依据：** 已批准[架构设计](../specs/2026-10-23-ai-core-platform-redesign-design.md)第11节与仓库外 `inventory-knowledge.md` 实际源码审计。旧工程没有 MinerU；图谱为 LightRAG 1.5.7 的 PostgreSQL 表存储和 Milvus，不引入 Neo4j。旧知识库共享目录与旧文件 DataScope 是旧事实；新平台以可信 owner、显式用户授权和管理员管理建模，不借未实现的组织权限接口假装完成隔离。

## 分组与工程边界

1. **受管文件与确定性解析。** `modules/files` 拥有元数据、字节引用、ACL、业务引用、保留期、回收和对账；`infrastructure/storage` 实现 StoragePort，使用新独立持久卷。知识原文和解析产物必须走 files，不给业务任意宿主路径。
2. **知识主索引与检索。** `modules/knowledge` 拥有库、文件夹、文档、解析/分块产物、构建任务、索引版本/manifest和来源；`application/knowledge_jobs.py` 统一治理 Embedding/抽取调用，向量实现放所属 adapter。
3. **LightRAG 图谱。** PostgreSQL/KV/doc-status/Milvus各存储按锁定SDK适配，显式迁移框架表，运行时只连接/校验，候选与活动图版本分离。
4. **MinerU 和全链路。** 显式可配置解析连接，选用官方文档核对后的上传→异步任务→轮询→真实结果解包协议；通过相同 ParsePort/受管产物/索引主链消费，不建第二套业务流程。

每组均有增量迁移、完整后端/前端门禁、原页面、独立两阶段审查、真实容器协议验收、已有数据备份升级与局部提交。LangChain固定1.4.7，新增依赖进入完整哈希锁、wheel和干净安装检查，不引入旧插件/LiteLLM。

## 第一组：文件与解析

**主要文件：** `backend/src/zhiheng/modules/files/{contracts,orm,service,public}.py`、`infrastructure/storage.py`、文件路由与显式迁移；解析由 knowledge 原生解析边界拥有并经有类型接口供聊天和索引复用，避免各复制一套无资源限制的解析器。前端 `modules/files/` 复用原文件列表、详情、统计、回收站与授权交互。

- [x] 失败测试先覆盖 owner/授权/管理员、有效期/状态、CAS、伪造ID/路径、上传大小/空文件、SHA与MIME、字节下载/Range、业务引用和删除保护。
- [x] 上传使用服务端不透明ID/存储键，不接受宿主路径；限制20MiB并边读边校验大小/摘要。先完成字节暂存与原子提交，再提交元数据；失败/失联的孤儿由有范围的对账处理，不发布半成品。
- [x] 文件读访问与管理权限分开；ACL版本、转移归属、显式用户授权/撤销、过期/已删除/正在清理拒绝新访问，不能用文件名或静态目录URL绕过。用户ID从身份域公开目录取得，不导入其ORM。
- [x] 软删除/恢复保留字节，物理清理先事务保留purging状态并拒绝新引用，外部IO后再终结；失联可恢复，反复清理幂等。保留/到期/对账扫描有批量上限与稳定身份，不能任意导入Python任务。
- [x] 原生TXT/MD/CSV/HTML/PDF/DOCX/XLSX真实解析，空结果/扫描无文本PDF明确失败；ZIP/XML/PDF/表格大小与展开/页数/单元格/输出资源有界，产物带原文件/解析器/版本/摘要。不得默默声称OCR成功。
- [x] 普通聊天附件选择/移除/上传/历史恢复接回原paperclip/文件渲染，访问文件在接纳及历史使用时校验；真实受管图片进入模型image_url内容块，有像素/帧/字节限制及模型能力校验。历史推理不回灌，附件不能绕过Token预留。
- [x] files公开引用接口由chat/knowledge/application事务使用，删除/撤权/物理清理与引用写入有一致锁序；禁止靠“查询失败算没引用”。

## 第二组：知识库、版本化索引和检索

**主要文件：** `modules/knowledge/{contracts,orm,service,parsing,chunking,retrieval,public}.py`、`modules/knowledge/adapters/milvus.py`、`application/knowledge_jobs.py`、专用job worker调度与原Overview/Documents/Query视图。

- [ ] 红用例：库/文档/文件夹归属与ACL、上传引用、混合批次、预览解析/分块、模型类型与维度、重复提交、取消/失联、重切片/删除、无效或越权来源。
- [ ] 元数据显式迁移；库配置含真实Embedding模型、chunk参数、图谱开关及图谱chat模型。文件与文档ID不能混用，文档只能在正确库/文件夹作用域下操作。
- [ ] 可恢复阶段任务拥有稳定逻辑步骤ID；父流水线不计作模型调用。每次Embedding/LLM回调创建持久子Run、预留/outbox/统一executor，记录parent与步骤；SDK内部不可观测重试关闭。不在占满ARQ父槽位后等待无人执行的子任务。
- [ ] 每次构建独立物理空间`kb/index_revision/attempt`，租约与attempt检查写入/发布。Milvus使用新项目命名空间，collection描述/维度/字段/index与兼容指纹验证；读取不隐式创建/覆盖索引。阻塞SDK在线程内，取消后先drain再close。
- [ ] 全部文档/向量/产物成功后短事务切换不可变manifest，失败保留原活动索引可读；过期attempt不能切指针或覆盖新产物。GC先标deleting，再精确清理无引用generation，不能按库ID粗粒度drop。
- [ ] 检索使用真实受治理Embedding和当前活动manifest，校验原模型/参数/维度及chunk provenance，按文件/库授权过滤，返回可追溯来源；8条/24KiB/10秒边界和失败行为真实。
- [ ] 将知识选择与引用接到普通聊天、Agent预览/公开调用和Work成员快照。按原资源owner校验，公开Key先鉴权/授权再准备检索；检索在SQL锁事务外完成，接纳时复核引用/manifest有效性并冻结来源。非空失败不得退成普通聊天。

## 第三组：LightRAG 图谱

- [ ] 固定`lightrag-hku==1.5.7`，明确PGTableGraphStorage/PGKVStorage/PGDocStatusStorage/Milvus映射，保留中文抽取策略和规范化节点/边/来源。
- [ ] 显式迁移SDK完整PG schema与必要规范化；runtime在无DDL权限账号下能初始化、读写和恢复。禁止仅建两张图表后空monkeypatch initialize/check_tables绕过真实初始化。
- [ ] SDK LLM/Embedding回调只消费注入的受治理平台Invoker，稳定步骤身份、真实用量/未知结算和取消drain；无直接供应商凭据或环境变量回退。
- [ ] 保存配置草稿、试构建与显式构建、进度/错误、活动revision；全成功才发布，失败旧图仍读。构建/预览活跃与文档修改协调，取消/过期attempt只影响自身namespace。
- [ ] 恢复原Graph页面、labels/subgraph/query接口与来源，真实本地模型抽取→PG存图/KV/status→Milvus图向量→图查询；跨namespace删除/失败恢复有实际服务验证。

## 第四组：MinerU 与端到端交付

官方协议依据：[MinerU API](https://mineru.net/doc/docs/index_en/)。实施时再次核对选定协议版本，不凭SDK名字猜接口。本轮以可配置远程连接适配为默认方向；已有受管字节上传服务，避免把内网文件公开成URL供第三方拉取。

- [ ] 管理员配置解析连接和加密凭据，读取/日志不返回秘密；原生/MinerU显式选择，服务不可用/超时/失败/空产物真实报错，不自动切解析器。
- [ ] 测试并实现签名上传、任务身份/状态、轮询、结果下载、有限安全ZIP解包、Markdown/结构化页/图片资源保存为受管产物。上传/下载URL只能来自可信连接回应并执行受控校验；鉴权仅发给配置API，不发到签名对象地址。
- [ ] 任务取消/重试/失联保留远端job身份，不无条件重复提交；本地取消不虚构远端已取消。产物大小、文件数、路径、重名、压缩比和资源寻址有明确边界。
- [ ] 本地真实HTTP协议服务覆盖官方请求/响应及扫描PDF/图片样例产物，再接同一知识索引；外部服务凭据未配置时如实显示，未验证真实远端OCR时不能宣称供应商已验收。
- [ ] 空库/已填充前版本迁移、独立文件卷/Milvus/PG、故障恢复、ACL撤销、原UI及各域回归、文档/构建/完整哈希锁/打包全部通过。
- [ ] 正常实例先备份配置/数据库/字节存储/索引版本与镜像，升级后核对已有数据、账号、真实入口与旧实例仍停止；保护原55修改/2未跟踪文件；清理临时验收环境并更新实际状态。

第一组受管文件与原生解析已完成并升级正常实例，包含会话引用释放闭环。真实结果见[文件验收](../verification/2026-10-23-files.md)。其余分组按上文继续，未将原生解析误记为已完成知识索引/图谱/OCR。
