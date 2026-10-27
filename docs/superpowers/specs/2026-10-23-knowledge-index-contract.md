# 知识库主索引与检索实现契约

日期：2026-10-23；依据已批准整体架构第11节及文件/知识库分组计划。本组完成真实主向量索引、检索及Chat/Agent/Work组合；图谱和MinerU是后续分组。保留原Overview/Documents/Query布局与交互，内部使用新核心契约。

## 所有权与实现边界

`knowledge`拥有库、ACL、文件夹、文档、解析/分块版本、索引manifest/generation、build/retrieval intent/steps与GC事实；`application`拥有模型治理及跨模块最终接纳。`files`继续拥有原文/解析产物、访问权限、字节与业务引用。只经public能力跨模块，不导入外部ORM/私有方法。

LangChain固定1.4.7；SDK选择`pymilvus==2.6.17`，服务采用Milvus2.6稳定分支的2.6.25，完整哈希锁及Python3.10/3.13验证。官方[2.6兼容表](https://milvus.io/api-reference/pymilvus/v2.6.x/About.md)列出该SDK，服务发布见[Milvus releases](https://github.com/milvus-io/milvus/releases)。创建显式FLOAT_VECTOR/schema、HNSW/COSINE（M16/efConstruction256/query ef64）、Strong一致性；[官方API](https://milvus.io/api-reference/pymilvus/v2.6.x/MilvusClient/Collections/create_collection.md)默认是Bounded，必须显式覆盖。构建创建/index/load，读取只校验并查询，不自动创建、覆盖、load或drop。

Milvus SDK仅在所属adapter；阻塞调用在线程，取消先drain再close。新实例/验收使用独立etcd/对象存储/Milvus卷与新命名空间，不读取旧数据。只允许删除由已登记generation精确拥有的collection，校验名称、描述、schema、维度、模型指纹及manifest身份；不用库名前缀粗粒度清库。

## 权限和原文

- 认证用户可创建自己的库；owner/admin管理，private/public及显式用户allow/deny/有效期，deny优先但owner/admin不被覆盖。无角色/部门假接口。
- 库和文件权限分别生效。文档列表/预览/检索/引用按可信主体复核来源文件；有权读库不自动公开私有文件。新上传原文默认private；不因修改库ACL暗改既有文件ACL。界面明确说明并提供源文件权限入口。
- 管理该库时可经库作用域目录查询最小active用户id/username（搜索、上限100）；identity.public提供有类型能力，不跨域ORM。
- Chat使用会话owner；Agent预览/公开调用使用Agent原owner；Work使用task原owner。管理员代操作使用资源owner身份，不借操作人的权限扩大来源可见性。计费主体与操作人/资源授权主体单独记录。
- 公开Key先校验应用/Key/Agent发布关系及主模型和全部检索embedding授权，随后每步及最终接纳复核。embedding同Key/账本计费，不绕过预算/限额，不存Bearer。Key没有相应embedding授权时明确拒绝。
- 图片不能将`[Image attachment]`占位当知识文本；主索引只列真实文本格式pdf/docx/xlsx/txt/md/csv/json/html/htm，扫描PDF/图像需OCR。使用本阶段已交付原生解析端口及受管产物缓存。

## 文档与完整索引版本

每次构建有独立revision/attempt物理namespace，库同时最多一个索引修改job。现active在构建期间可查。文档源与上传FileID、DocumentID、FolderID、JobID不得混用，所有跨库/跨文件夹引用严格校验。

分块100..4000字符、overlap0..1000且小于size，默认1000/200，每文档最多2000块；保存原文片段用于引用、规范化model-only文本及不可变chunk身份。真实向量必须索引完整、维度一致、有限浮点且非零；单batch最多8段embedding、向量写入128条/批。解析/向量IO不占SQL长事务。

混合批次保留逐文档结果：成功文档可以进入新的完整manifest；失败新文档不进入检索，失败重切片保留旧active文档版本。batch显式partial，不把部分写入的collection发布。建议完整库candidate快照carryforward未替换旧chunk/vector（不重复计费），加入成功替换文档；所有候选数据、index/load与校验完成才短TX切换manifest。若写/验证/发布整体失败，不切指针。一次库查询不按每文档collection做无界fanout。

模型binding指纹和dimension写入manifest。普通query使用当前可用模型授权且必须兼容活动spec；凭据/上游embedding binding改变后返回409 `knowledge_reindex_required`。只有显式全库rebuild可以重embed全部文档并建立新spec。失败保旧pointer，但不偷偷用新模型查旧向量。

文档/文件夹/库删除是有状态job，先tombstone阻新检索，再安全更新manifest和GC；旧generation仍有检索pin时不能清理。文件夹递归处理子目录/文档。库有活跃修改job时新的修改明确409，先取消/等待；取消和失联不得发布旧attempt。源文件作为独立受管资源，删除库内文档释放本业务引用；对原文件的保留/回收按files规则处理，不能因删库而越权清除别的业务使用的原文。

## 持久流水线与付费幂等

父build/retrieval intent不创建模型Invocation；实际embedding/LLM每一步创建标准Run、配额/费用预留、outbox，由统一Executor/LangChain执行、结算、取消和失联收敛。关闭SDK内部重试，不以供应商零用量替代unknown。

每个knowledge tick只执行一个有限阶段。提交child Run+outbox+唯一step后立即归还ARQ槽，后续tick消费child终态；不能占住父槽等待子Run。需真实单槽worker覆盖完整索引/检索/主运行链。触发投递可重入，稳定step身份和lease/attempt fence拒绝旧执行；本阶段不确定/已开始的付费child不自动重发，显式重试产生新attempt。

Chat/Agent/Work检索发生在最终主Run接纳前，必须有持久RetrievalIntent：保存显式输入指纹、可信身份/计费上下文、首次资源配置/技能/manifest/bindings依赖快照、稳定child IDs、sources与主run关联。显式输入指纹和依赖指纹分开；同client_request_id重试查原intent，改变输入冲突，不能按当前依赖重建付费准备。主Run已接纳优先重放；准备失败/超时或最终接纳409都保留原拒绝，原键不重新计费。Work规范化键沿用现有**实际caller的runtime namespace和原实现UUID5**，不擅自改成billing owner；公开Key用Key身份规范化以避免互相复用。

准备锁外执行embedding/search，短TX再按既有锁序复核资源、主模型、manifest、库ACL、所有实际来源的文件version/ACL/SHA/active并原子写主Run、消息/占用、引用及intent.main_run_id。非空知识准备失败不能退为普通聊天。资料作为数据上下文，来源不是系统指令。没有命中与服务失败分开表示。

检索最多20个唯一有序KB、最终8条/24000 UTF8字节（含来源字段），query1..8000字符，top_k1..8、threshold[-1,1]。整次retrieval预算10秒；超时冻结失败并取消/排空child，迟到结果不得主接纳。API可以202返回进度供poll；关闭HTTP只停止观察，显式cancel才取消intent。query结果读取及来源下载仍复核当前权限。generation pin在未完成检索/接纳期间防GC，终态按事实释放。

## HTTP契约

所有内部API前缀`/api/knowledge`、Cookie认证、snake_case直接DTO、UUID、extra=forbid；以下为稳定最小字段，可追加不破坏字段但需同步root。版本>=1；时间UTC带时区；error为`{code,message}`或null。不回传宿主路径、供应商秘密或物理collection名。

| API | 请求及响应 |
| --- | --- |
| GET `/capabilities` | `{milvus_configured,milvus_available,native_formats,graph_available:false,mineru_available:false}`；不可用真实503或明确available=false，不伪造空库/健康 |
| GET `/bases` | 可读库数组BaseInfo；GET `/available`为可选用库数组，反映实际权限/active readiness；可选`agent_id`须先授权该已保存Agent，以其可信原owner筛选，不能接受客户端owner身份 |
| POST `/bases` | `{name,description,embed_model_id,chunk_size:1000,chunk_overlap:200,access:private,enable_knowledge_graph:false,llm_model_id:null}`→201 BaseInfo；本组不启图谱，不接受true假成功 |
| GET/PATCH/DELETE `/bases/{id}` | PATCH `{expected_version,name,description}`；DELETE `{expected_version,client_request_id}`→202 JobInfo；模型规格更改经显式rebuild，不静默编辑活动spec |
| GET/PATCH `/bases/{id}/acl` | 返回`{base,entries,builtin_permissions}`；body `{expected_version:acl_version,access,entries:[{user_id,effect,expires_at}]}` |
| GET `/bases/{id}/users?search=` | 仅库管理者，小型目录`[{id,username}]`；原File用户目录角色不放宽 |
| GET/POST `/bases/{id}/folders` | 数组FolderInfo；POST `{name,parent_id:null}`→201；PATCH/DELETE `/bases/{id}/folders/{folder_id}`带expected_version，rename改name，递归delete另带client_request_id→202 JobInfo |
| POST `/bases/{id}/uploads?filename=&folder_id=` | 原始octet body<=20MiB，受管上传；返回`{file:FileInfo,upload_id,folder_id}`，upload_id与file_id清晰区分；清理暂存上传可DELETE对应uploads/{upload_id}，仅本库/owner管理 |
| GET `/bases/{id}/documents` | `{items:[DocumentInfo],total}`；包含当前可见目录所有文档以复用原树/本地筛选，管理者可见失败/处理状态 |
| POST `/bases/{id}/documents` | `{file_ids:[1..20唯一],folder_id:null,chunk_size,chunk_overlap,client_request_id}`→202 JobInfo；同键重试返回原job/逐项结果 |
| GET `/bases/{id}/documents/{document_id}/preview` | `{document_id,file_id,text,parser_version,chunk_count}`，真实解析/原文预览，不能假返回标题；错误明确 |
| POST 同文档 `/rechunk` | `{expected_version,chunk_size,chunk_overlap,client_request_id}`→202 JobInfo；失败文档的重试入口 |
| DELETE 同文档 | `{expected_version,client_request_id}`→202 JobInfo；按前文tombstone/manifest/GC闭环，不直接越权删原文件 |
| POST `/bases/{id}/rebuild` | `{expected_version,client_request_id}`→202 JobInfo，显式全库当前embedding binding重建 |
| GET `/bases/{id}/jobs` / GET `/jobs/{id}` | 最近50数组/单JobInfo；owner/admin管理；`POST /jobs/{id}/cancel {}`幂等取消，不能要求追逐不断变化的job版本 |
| POST `/bases/{id}/query` | `{query,top_k:8,similarity_threshold:0,client_request_id}`→202 PreparationInfo；稳定子Run+query结果，poll后获得hits |
| GET `/preparations/{id}` / POST其`/cancel {}` | Cookie资源权限下读进度/结果或主run关联，显式取消；不将查状态变成新模型调用 |

BaseInfo：`id,name,description,owner_id,access,version,acl_version,embed_model_id,chunk_size,chunk_overlap,active_revision,active_job_id,index_status(empty|building|ready|failed|reindex_required),document_count,last_error,enable_knowledge_graph,llm_model_id,graph_status(disabled|pending),created_at,updated_at,can_manage`。

FolderInfo：`id,base_id,parent_id,name,version,created_at,updated_at`。DocumentInfo：`id,base_id,file_id,file_name,file_size,mime,folder_id,version,status(pending|processing|ready|failed|deleting),active_revision,job_id,chunk_count,chunk_size,chunk_overlap,error,graph_status(disabled|pending),graph_error:null,created_at,updated_at`。失败重构可status=failed且active_revision仍存在；检索由实际活动manifest决定，UI显示旧索引仍可用。

JobInfo：`id,base_id,operation(import|rechunk|rebuild|delete_document|delete_folder|delete_base|gc),status(pending|running|waiting_model|publishing|completed|partial|failed|cancel_requested|cancelled|interrupted),phase,version,attempt_id,index_revision,total_documents,completed_documents,failed_documents,chunk_count,document_results:[{document_id,file_id,status,chunk_count,error}],error,created_at,updated_at,finished_at,cancel_allowed`。未知chunk_count用null，不造进度。

PreparationInfo：`kind:'knowledge_preparation',id,resource_type,resource_id,status(queued|waiting_models|searching|ready|admitted|completed|failed|cancel_requested|cancelled|interrupted),phase,run:RunSnapshot|null,hits:[Hit],error,created_at,updated_at`。Hit：`chunk_id,document_id,file_id,source_id(文档ID),knowledge_base_id,index_revision,title,content,score`；任何命中必须匹配manifest、文档、原文/片段摘要与权限。

原有Chat/Agent预览/Work runs在无KB路径保持RunSnapshot；有KB的202可以返回PreparationInfo，前端按kind观察/恢复，主run关联后复用原SSE。Chat body新增`knowledge_base_ids:[]`，会话/工作详情要能恢复已知准备，准备期间元数据/发送/重传有一致busy约束。拒绝、取消与迟到结果不释放不确定的付费占用。

公开Agent增加可选client_request_id或Idempotency-Key；有KB时必须有稳定请求键。202准备响应提供`polling_url`，使用同Key认证的公开状态URL，不能指向Cookie专用poll。取消与主接纳竞争时，已接纳intent保持admitted并取消关联真实主Run，响应包含真实RunSnapshot。后续可继续原JSON/SSE结果；无KB原协议保持。API说明必须给出202恢复示例和embedding模型授权要求。

## 实施门禁

显式单迁移0007，0001..0006不变；schema inventory/head/CLI/wheel同时更新。先红用例，再实现，每个实际阶段独立规格后质量审查。覆盖完整混合批次、同ID并发/丢响应/最终409不重复计费、单槽无死锁、binding冲突/重建失败旧pointer、权限撤销/来源伪造、job取消/guard失联/stale publish、独立namespace GC、目录递归删除、query限时/来源字节和实际Milvus读写。

保留文件阶段327后端/24核心/91页面基线并适配新增契约。最终完整门禁、Python3.10/3.13干净安装与真实DB、空库/已有0006数据升级、原UI和真实Nginx/API/worker/LangChain/Milvus链路、配置/数据库/Redis/文件/索引/镜像备份及正常实例升级后，才标本组完成。保持55个legacy优化与2个未跟踪文件。
