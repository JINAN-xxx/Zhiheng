# 知识模块职责与调用边界

知识模块公开入口为public.py和KnowledgeService。根服务负责知识库目录、基础授权、默认模型与文件夹，并显式组合materials、builds、migration、references四个纯领域协作者；这些对象复用原文件、身份与模型端口，不创建新连接或事务。

## 文件归属

| 文件 | 职责 |
|---|---|
| service.py | 目录、可见性、所有者状态、基础ACL、模型默认值与文件夹 |
| materials.py | 原文/副本授权、文档元数据、上传收据、暂存、去重及资料生命周期事实 |
| build_admission.py | 索引变更准入、配置重建、Job查询/取消、JobInfo投影 |
| migration.py | 已有资料权限迁移：预览、独立副本、入场、来源复核和完成 |
| references.py | 模型/连接/技能/助手和活跃任务引用、删除影响、私有引用计数 |
| values.py | BuildSelection和确定性指纹 |
| build.py | Worker租约、attempt、分批模型步骤与索引发布 |
| sharing.py | 邀请令牌、接受、撤销及共享成员管理 |
| retrieval.py | 受管检索准备、冻结来源、当前权限复核与结果状态 |

跨模块仍从public.py导入公开能力，应用用例显式访问所属协作者，例如：

```python
await knowledge.materials.register_upload(session, principal, base_id, file, folder_id)
await knowledge.builds.get_job_info(session, principal, job_id)
await knowledge.migration.validate(session, job_id, lock=True)
await knowledge.references.model_references(session, model_id)
```

根服务中已移除这些旧方法，没有保留转发别名、Mixin或动态__getattr__。HTTP入口、请求/响应DTO和持久化表名沿用现有协议。

## 依赖与规则

- 四个协作者共用目录核心的require、idle、version等规则；所有者与共享权限只有一个权威实现。
- 构建准入依赖资料能力，资料迁移依赖目录、资料及构建准入；协作者不反向调用迁移。
- JobInfo投影是build_admission.py中的共享函数。资料模块只依赖这个纯投影，不依赖构建准入对象，因此查询入库结果不形成服务调用环。
- 协作者通过TYPE_CHECKING引用根服务及其他协作者类型；共享选择和指纹独立于服务实现，避免运行时导入环。
- 文件授权回调指向materials；跨资源删除影响回调挂在references。构造时显式连接，状态不复制到第二份对象。
- 既有build.py、sharing.py、retrieval.py继续承担各自职责，不复制第二套Worker、邀请或检索逻辑。

## 事务与数据

application/knowledge负责外部文件IO、事务边界和补偿。领域协作者接收调用方session，只沿用原有SQL、行锁、flush和状态转移；对象存储IO不被移入知识库/文件行锁事务。

资料复制保留原文身份、哈希、授权版本与入库去重，上传收据仍支持丢失提交确认后的恢复。资料迁移仍在发布事务中复核原文和共享范围；并发迁移使用原请求锁和同一副本/Job身份。

模型子调用继续统一走RunAdmission，保存原操作人、付费人、Key和用量来源。Worker租约、取消、原有效索引及pin/generation规则沿用原实现。此结构调整没有数据库迁移、用户数据清理或费用政策变更。

## 验证原则

采用真实隔离PostgreSQL和本地HTTP/Fake供应商验证上传、复制、撤权、幂等、取消、发布、迁移及锁序。权限/所有权测试先配置显式canonical功能权限，再验证资源ACL；不通过放宽生产权限改变测试结果。缺少外部Milvus等环境的用例如实跳过。
