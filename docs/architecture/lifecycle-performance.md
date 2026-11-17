# 生命周期与有界维护

物理回收以业务生命周期、真实引用和执行归属为依据。保留不可变调用、版本和回执；取消或IO失败不冒充已经清理成功。

## 对话 checkpoint

ChatCheckpointStore 使用固定版本官方 PostgreSQL saver；被删除会话通过 aprune(strategy="delete") 在SDK事务中删除 checkpoint、write、blob。清理前在会话锁内再次验证 owner、tombstone、无活动父对话及到期时间，先登记一分钟重试时间，再于业务事务外执行SDK IO。整轮使用单调时钟共享30秒预算，耗尽后未处理候选留到后续；立即失败可继续检查后续候选。失败/取消保留业务头，成功后才清理删除会话的有效头并一小时复扫，以接住旧进程迟到写入。

ContextSnapshot、SummaryTask、source/file refs 和调用事实不随SDK物理清理删除。活跃会话保护已接受头、重生成输入检查点、历史base，以及在途对话/摘要依赖。官方 keep_latest 只保留物理最新项，不能表达这组保护身份，因此本实现不把它应用于活跃会话。会话显式删除仍是当前历史物理释放边界。

## 退役知识分块

GenerationStore.cleanup_chunks 只处理已完成外部GC、状态 deleted、非当前 active_revision、无 GenerationPin 的一个版本。在 generation 行锁内重新验证条件，generation 与 chunk 均使用 SKIP LOCKED；先锁定并物化最多1000个chunk ID，再按这份固定清单删除。锁顺序是 generation→chunk，不反向获取base/job/file锁。

KnowledgeJobs.cleanup_retired_chunks 使用短事务，接入原15分钟文件维护。generation/job/step/parse/retrieval及图冻结元数据保留，原文件引用不释放；每小时对已登记远端namespace重复drop继续处理迟到RPC。当前活动、candidate/retired/deleting、被pin的版本和没有writer停止证据的图资源均维持原保护。

## 文件扫描

StoragePort 明确区分 StorageInspection(size,sha256) 和 StorageStat(size,modified_at)。S3FileStorage 用64KiB块计算摘要，stat仅读取对象元信息，拒绝空对象、超限对象及不完整读取；响应连接在成功和失败路径均关闭。受管文件统一使用私有业务 MinIO，API 与 Worker 不使用本地文件后端。

对象目录使用 scandir+nsmallest，内存只保留本页，排序成本 O(N log limit)。平铺随机键目录仍需 O(N)枚举，不声称是物理目录keyset。DB记录继续UUID分页；孤儿检测按页批量查询已登记键，IO后复核登记和一小时宽限。真实引用、原文→派生物锁序、purging两阶段、审计、会话维护锁与attempt nonce保持原规则；失联或取消不能推进成功游标。

## 技能包与解析

ZIP/YAML解析通过 asyncio.to_thread 执行，解析函数只处理输入bytes，无session或外部副作用。10MiB上传、30MiB解压、200文件、128KiB主文件、64层YAML和20000事件等限制保持；确认仍重新检查草稿SHA与parsed_hash，解析在应用/文件行锁外进行。

SkillRevision保留原始包、内容、hash与版本。确认草稿保留原ID、SHA、parsed_hash、目标版本、时间、confirmed引用及完整回执，包体在同一确认事务中置空；回滚保留原上传，重放直接使用原回执。0038只释放已有、身份匹配且具备完整回执字段的确认草稿包体；未确认或旧格式回执保留，迁移重复执行无额外影响。重复上传的原始压缩bytes不重建，修订下载仍返回原不可变包。

技能资源列表直接复用一次join得到的元数据投影，保持所有权、分页和历史状态，避免逐条重读及二进制包体加载。当前不增加全局解析缓存或内容寻址存储层。

## 验证

使用隔离PostgreSQL/Redis、本地Fake向量及纯解析验证：SDK删除中途失败整体回滚、活跃/引用保护、超时退避、知识批量与锁边界、文件FD/同尺寸篡改/purge/fence，以及技能确认重放、旧回执保护、迁移幂等与异步响应。真实供应商推理独立于这些维护检查。
