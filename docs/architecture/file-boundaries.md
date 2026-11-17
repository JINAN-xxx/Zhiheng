# 文件模块职责与生命周期

FileService保留唯一文件身份、授权、完整性读取及派生产物规则，显式组合queries、lifecycle和references。跨域继续从public.py取得FileService和公开契约，调用时访问具体职责；不通过Mixin、动态__getattr__或旧方法转发隐藏第二套实现。

## 代码归属

| 文件 | 职责 |
|---|---|
| service.py | 原文/派生产物授权、所有者与ACL、转移、元数据写入、读取票据和完整性、解析产物缓存 |
| queries.py | Console筛选、分页、统计、可委托元数据、审计和维护结果查询；原资源API游标查询与FileReadTicket仍在此 |
| lifecycle.py | 回收/恢复、保留策略、提醒、延期、到期处置和知识资料副本退役 |
| references.py | 已发送附件校验、来源快照绑定、附件引用、引用查询及释放 |
| metadata.py | 文件名校验、MIME识别、上传语义，不反向导入文件服务 |
| jobs.py | 原维护任务、attempt与会话锁、对账及物理清理，复用文件核心与生命周期规则 |
| parsing*.py、adapters/ | 通用解析配置、端口与Native/MinerU实现，沿用已有归属 |

例子：

```python
page = await files.queries.list_files(session, principal, filters)
await files.references.bind_sources(session, principal, snapshots, resource_type=kind, resource_id=run_id)
await files.lifecycle.remove(session, principal, file_id, expected_version)
```

FileService中的旧目录查询、回收、策略、提醒及引用方法已移到实际所有者，不保留旧别名。查询筛选仍是原有SQL和可见性条件，没有用另一套ACL替代核心授权。

## 授权、来源和引用

_require仍是唯一文件读取/管理授权核心。派生产物根据当前原文授权，所有权转移及uploader_access_enabled不会留下访问后门；知识库资料仍由知识模块回调验证所属知识库和当前共享范围。

绑定来源时先按业务库顺序取得锁，再按原文/派生产物顺序锁文件。来源快照、版本、哈希和图片/文本产物校验沿用原规则。业务引用存在时保守阻止回收和物理删除；释放引用仍按原文优先的顺序加锁并记录审计。

引用期限与回收期限含义不同：到期处置只释放过期引用，永久或未到期引用仍阻止删除。已回收文件的处置不会重新延长原回收期限；派生产物继续由原文生命周期管理。

## 任务、IO与事务

application/files保留上传/下载与资源授权的事务组合：存储IO之前取得授权票据，IO之后再次复核当前权限；FileObjects负责提交确认丢失时的对象登记判断与补偿。

FileJobs继续使用原会话锁与attempt nonce；清理分为持久标记purging、锁外删除物理对象、最终确认purged。中断后重复删除保持幂等，失去维护执行权后禁止更新文件或成功游标。物理清理后的开发应用计量回调继续在文件事务提交和锁释放之后执行。

生命周期和查询协作者接收调用方session，不自行提交或改变锁序。配置、ACL、引用、审计、回收与任务表均沿用原schema和版本规则。

## 附件完整上下文上限

普通Chat的图片上限为包含有效历史的16 MiB。完成checkpoint时记录其图片字节数和模型输入图片字节数，准入将本轮增量与有效历史合计后检查；模型调用前再次检查实际标准消息。摘要去除早期图片后，计数按压缩后的有效checkpoint更新。

没有计数字段的既有checkpoint继续在实际模型边界硬检查，不修改历史业务记录。超过上限的正常新请求在Run创建和费用预占之前拒绝；不能只检查本轮增量。

## 存储扫描

目录通过scandir+nsmallest限制本页内存，平铺随机键仍需O(N)目录枚举。完整校验用64KiB分块摘要，孤儿检查使用安全stat与本页登记键批量查询，IO后复核登记和一小时宽限。内部StorageStat/StorageInspection不改变HTTP DTO。FD在失败路径关闭，原对象目录、回收策略、引用和purging/fence规则保持。详情见[生命周期与有界维护](lifecycle-performance.md)。
