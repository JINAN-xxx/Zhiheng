# 个人资源与开放 API 验收记录

2026-11-02。个人技能、个人知识库、平台资源 API 与开发接入已完成分层验收，并发布到正常源码入口 <http://127.0.0.1:19080>，数据库为 `0016_file_purpose`。正常实例发布和本轮 QA 精确清理门禁通过；最终文档单独提交并同步至根工作树。

依据：[批准设计](../specs/2026-11-02-resource-api-design.md)、[实施计划](../plans/2026-11-02-personal-resource-api.md)、[资源接入说明](../../api/resources-v1.md)。功能提交依次为 `e4538387`、`c494de66`、`5b474a0a`、`2319bcec`、`6c08fa20`；最后 `fe499c5c506fad6b419af4de241c7c840e7c3870` 仅修正七个既有测试文件的契约。

## 已交付的功能与入口

| 入口或能力 | 已验收行为 |
| --- | --- |
| Console `/api` | 继续使用 Cookie；原界面、聊天、智能体、Work 和管理入口保留 |
| 资源 `/api/v1` | 仅接受 Bearer Key；文件、知识库、个人技能和原生异步 operation，共 23 个路径、33 个操作 |
| 文档 | `/docs/resources` 与 `/openapi/resources-v1.json` 可实际读取，规范为 OpenAPI 3.1；请求 DTO、Scope、multipart、分页、版本、响应头与错误均来自实际路由 |
| 推理 `/v1` | 保留原协议、SSE、模型/智能体授权与计费；独立规范 `/openapi/inference-v1.json` 为 7 个路径、7 个操作 |
| 个人技能与知识库 | 默认本人可见；管理员普通资源入口同样检查本人归属，主动共享按当前 ACL 处理；不同人可有同名技能 |
| 开发接入 | 可签发无模型的资源 Key，配置独立资源 RPM、显式应用资源授权和原始文件容量；同一拥有者的不同应用也不自动共享资源 |

资源访问取当前个人能力、Key Scope、应用授权和领域归属/ACL 的交集。资源 API 创建的对象归真实应用拥有者，并关联当前应用；客户端不能指定 owner 或物理路径。旧 Key 只迁入原推理 Scope，不获得资源能力。

文件支持 multipart、用途、应用隔离、游标列表、Range 下载、ETag/CAS、回收和恢复。幂等请求永久保留请求身份，响应快照到期也不会重新执行。回收与解绑不释放原始文件登记额度，原生物理清理完成后才释放；纯元数据管理不计模型积分。

知识库支持库、目录、文档、入库、检索、重新切片、重建及删除。Operation 映射原领域作业，保留 partial、取消请求、取消和中断的区别，执行阶段重新检查当前 Key、模型授权和来源 ACL。

技能支持 SKILL.md/ZIP 上传、预检、确认、状态管理、不可变版本及原包读取；删除后恢复保留固定 ID。包内版本标签、不可变 revision 和状态 CAS 版本分别处理。内置模板初始化为个人副本，旧修订、确认事实与发布冻结引用保留。

## 实际代码结构

一个 FastAPI 应用由 `api/main.py` 装配，共用一个 Container。实际目录为：

```text
backend/src/zhiheng/
├── api/common/          # 身份、传输、分页、前置条件、错误和分组规范
├── api/console/         # 原 /api 协议
├── api/resources/       # /api/v1 路由与 schemas
├── api/inference/       # 原 /v1 DTO、路由、SSE 和错误映射
├── api/compat/openai/   # 仅适配边界说明
├── application/access.py
├── application/files/       # commands、queries
├── application/knowledge/   # commands、queries、native、origin
├── application/skills/      # commands、queries
├── application/operations/  # queries、cancellation、contracts
└── modules/             # skills、knowledge、files、developer 等领域规则
```

HTTP 层负责协议和传输，应用用例负责当前授权、事务和跨域编排，领域模块持有数据与业务规则。Console 与资源 API 复用用例；未建立第二套队列、模型执行器、积分账本或业务插件框架。原有知识作业、Run/Worker、存储与 SDK 适配继续复用，未为整理目录整体重写未触及的域。

任务一至五均有独立规格、质量审查及后续差量复审；最终跨片集成和测试契约修正审查均为 PASS。Python 3.12 的 bound-classmethod DTO 兼容问题、Key 表单迟到 Scope/RPM 响应竞态已闭合；独立浏览器 2 项及 DTO 9 项复验通过。

## 完整覆盖与失败闭合

验收采用“原始完整覆盖＋环境纠正＋测试契约补充＋真实链路”的组合证据。**没有在最新测试冻结上重新进行一次 1054/366 全量零失败运行。** 原始失败、环境入口错误及修正过程保留，不用重复执行数扩大唯一覆盖。

| 证据层 | 实际结果 |
| --- | --- |
| 原完整后端，`6c08fa20` | 117 文件分片、1054 唯一 node ID；1046 PASS、8 FAIL、0 ERROR、0 SKIP；无遗漏或额外节点 |
| 原后端环境失败复验 | 原代码、原断言 5/5 PASS；源码稳定、资源清理无误 |
| 测试契约后端复验 | 六个完整文件 57/57 PASS；与原 57 节点同名，54 个原 PASS 保持，三个原 FAIL 转 PASS |
| 原完整浏览器，`6c08fa20` | 27 模块、366 上下文＝354 独立实现＋12 继承上下文；357 PASS、5 FAIL、4 ERROR、0 SKIP；无遗漏 |
| 浏览器补充复验 | 五个完整受影响模块加原三个环境节点，108/108 PASS；99 个原 PASS 保持，九个原 FAIL/ERROR 转 PASS；无改名、增删或 skip |
| 前端质量 | core 111/111 PASS、0 skip；typecheck、lint、format 均退出 0 |
| 后端静态质量 | 既有 Python 3.13 环境 mypy 220 源文件、架构检查及相应 Ruff/format 通过 |

组合覆盖闭合为后端 1054/1054 唯一节点、浏览器 366/366 上下文（354 个独立实现）；实际执行次数分别为 1116 与 474。12 个继承上下文是实际执行，不能记作 skip 或新增独立实现。

后端八个原失败中，五个是显式 `PYTHONPATH=src` 的子进程看不到私有 multipart overlay。仅在原私有 SDK 环境增加任务专属 `.pth` 后，原五个断言全部通过，未安装 host 全局包或升级 SDK。另两个仍要求本人技能具备平台管理权限，与批准的个人基础能力冲突；一个旧 quota fake 按查询次序返回错误类型。七个既有测试文件的修正保留平台 Models/Rate/Storage 的全部 403 断言，以及请求、Token、并发和费率限制断言，没有回退生产限额。

浏览器九个原失败/错误中，六个属于旧个人技能、知识库标题或父测试 helper 契约，三个属于私有 runner 缺失新 schema、截图或 scope 环境变量。修正后按原 case ID 复验。首次 fixture URL 映射错误发生在零个 case 进入之前，失败记录保留；未放宽断言、超时或强制点击来获得通过。

最终测试冻结为 645 个文件，aggregate SHA256：

```text
03d9e37959675b3a0de3ebeb545abafce586fa90855f521eb34066f5a8b0ba18
```

其中 469 个非测试文件与原完整运行的功能源逐项相同，aggregate SHA256：

```text
b7134b7243adafd0eb5930575fdab375b33db56074822a4fc3904a76d500fc5a
```

## 真实私有链路

实际链路为独立 Vite → HTTP API → PostgreSQL/Redis → 原 ARQ Worker → 原模型 SDK → 本轮本地协议 Provider → Milvus；没有 ASGI transport、浏览器 HTTP route mock 或直接调用 Worker 执行函数。未调用正常实例模型或使用其凭据。

| 场景 | 通过的真实检查 |
| --- | --- |
| 文件与额度 | multipart、Range 206、CAS、同键重放/冲突/快照到期、固定 ID 回收恢复；跨应用/拥有者拒绝；原生 purge 后额度才释放 |
| 个人技能 | SKILL.md 和 ZIP 的预检确认、原字节/SHA、版本、启停 CAS、删除恢复；本人 CRUD 与他人 404 |
| 知识库 | 原 Worker 入库、Milvus 真命中、检索、重新切片、重建；当前观察 Key 重放既有结果不重复执行或改记账归属 |
| 撤权与异步 | 排队取消、原执行 Key 撤销、来源文件归属/ACL 撤销；撤权分支无上游调用/费用；混合文档实际 partial；删除库后本人仍可观察 operation 历史 |
| 真界面 | Developer 签发仅文件读取的无模型 Key、授权后读取、取消后 404；管理员额度设置和普通成员无入口；个人 KB 命中、成员技能上传确认、普通 Chat SSE 完成 |
| 原平台 | 16 原页面、原 invoke-only Key、Agent 发布与 draft 隔离、原预览、Work 持久 turn 和旧请求重放 |

最终 11 次 Provider 请求＝11 条原生 Invocation＝11 条正向 settle。私有账本、reservation 与账户 used 精确等于 `0.000086000000000000` 点，reserved 为 0，活动模型 Run 为 0；元数据操作的 Key 无模型调用或费用。实际 Milvus 活动集合 count 与 PostgreSQL generation 行数一致。

私有 driver 的按钮定位、Select/Popconfirm 范围和额度身份错误均保留历史；仅修正私有驱动入口后完成剩余步骤，已成功的签发/授权没有为凑数重复创建。

## 正常实例备份与发布

发布前重新审计活动任务和技能归属：活动/阻塞均为 0，八个旧上传技能归属唯一。先停止入口与 Worker，再完成七个冷卷的 numeric owner、ACL、xattrs 归档；逐成员和完整解包验证通过。原 57 项 legacy 文件及额外 669 项 preview 的字节与属主、权限均保留。

PostgreSQL custom dump 为 826405 字节，SHA256：

```text
a5471a5139c32356686206a53038516a6c4d202de9f4e05444fcb60a8b0aac21
```

在原缓存 PostgreSQL 镜像、无网络/无端口的隔离实例实际完整恢复，两个 schema、81 表、1850 行原列/行 hash 相同。Catalog 的 791 列、271 约束、212 索引、两个函数、两个触发器、两个序列相同。原始 DDL 文本差异限定为随机 nonce、末尾空行和 20 个等价 CHECK cast 表达，严格归一后零剩余差异；原始 SQL、差异与最初拒绝记录均保留。

正常根工作树 `refactor/white-console-ui` 从原 `e8c6d4b2` 快进到 `fe499c5c`，功能工作树及分支保留。两份运行环境文件仅变更 `CORE_BACKEND_IMAGE`；实际源码代理补齐 `/openapi/`，保留 HMR、SSE 和分享日志保护。原五个数据基础服务容器 ID 保持。

后端仅离线构建一次 multipart 派生镜像 `sha256:b15a8f7963c616f6d44590fbb2e4d872f5115bdb28bc93f4d48dc8c3775af68f`。Python 3.12.15、User `10001:10001`、原 111 个 distribution 原版本、LangChain 1.4.7 与 pip 25.0.1 均保持，仅增加 `python-multipart==0.0.33`。同镜像 CLI、25 个 Native DTO 等价案例、190-path OpenAPI 与 multipart schema 门禁通过；没有前端 build 或 SDK 升级。

一次私有脚本的 `umask 077` 使同步源码为 0600，UID 10001 无法导入，迁移 CLI 尚未开始即失败。精确修复本轮 tracked 源文件/目录为 644/755、确认内容未变、源读取及 CLI import 通过后，以同镜像重试成功；首次失败保留。实际迁移 `0013 → 0014 → 0015 → 0016` 退出 0，随后才启动应用与 Worker。

迁移前后 1849 条非 Alembic 旧行的原列全部相同，Alembic marker 单列明确变化。81 表/1850 行变为 86 表/1862 行，仅增加四个个人模板、四个修订、四个旧标识关联；四张新资源事实表均为空。原模型、Key、费率、积分、历史修订/确认、冻结配置及文件字节保全。

发布后机器门禁确认九个正常服务运行健康，migrator 退出 0，API/Worker 使用上述镜像与 UID 10001。19080 readiness 为实际 JSON `{"status":"ok"}`；资源文档 HTML 与正确 schema URL、23 路径/33 操作资源规范和 7/7 推理规范均可读取。

原 16 页加四个附加只读页通过；页面错误、5xx、业务 mutation、新建 Key/资源和模型调用均为 0，375px 无横向溢出。原 Key 本来已 revoked，升级后保持该状态及两个推理 Scope、无资源 Scope；只读授权检查 401 符合原状态。原始 Key 不可得，未声称在正常实例做过旧 Key HTTP 活调用。

只读 smoke 后，财务、模型、Key、技能历史、Agent/Work/Chat/Run 和文件等 39 张受保护表无变化，57 legacy＋669 preview 及文件字节/UID/GID/mode 保持。登录与维护的合法变化单列为 `file_jobs` 1556→1560、`identity_sessions` 37→38、`operations_logs` 71→72；一行 `knowledge_graph_generations` 逐列对比原已验证 dump，仅 `gc_after` 改变，frozen、manifest、fingerprint、status 和来源 ID 保持。不能写成发布后全数据库不变。

## 证据索引与清理

私有证据留在仓库外。本机证据根为 `_local_backups/RuoYi-Vue3-FastAPI/20261023-feature-parity/resource-api/20261102/`（位于项目父目录）；以下路径均相对此根。仓库仅记录必要计数、hash 与结论，不纳入环境文件、凭据、数据库包或私人消息内容。

| 证据 | 用途 |
| --- | --- |
| `PRIVATE-ACCEPTANCE-REPORT.md`、`final-suite/PRIVATE-GATE-PROOF.json`、`final-suite/acceptance-closure.json` | 原全套、补充闭合、真实账务和私有门禁；报告与 closure 的 SHA 已核对匹配 gate |
| `final-fixes/{HANDOFF.md,REVIEW.md,verification.json}` 与 `checks/*-node-parity.json` | 七文件测试差量、源码身份、57/108 精确节点对应及独立审查 |
| `personal-domains/`、`access/`、`files/`、`resources/`、`protocol-ui/`、`final-integration/` | 各切片规格、质量复审及集成审查 |
| `release-image/{README.md,manifest.json,source-validation-proof.json}` | 单依赖镜像差异、原版本和 Python 3.12 真实门禁；其中 ready-not-deployed 是当时阶段快照 |
| `normal-release/20261102T125409Z/{STAGE-A.md,backup-proof.json}` | 新鲜冷备、完整恢复和 schema/catalog 不变量 |
| 同目录 `stage-B-postmigration-gate.json`、`stage-B-migration-proof.json`、`stage-B-ready-proxy-proof.json` | 实际迁移、旧行保护和正常入口协议验证 |
| 同目录 `RELEASE-REPORT.md`、`normal-ui-readonly-proof.json`、`normal-old-key-readonly-proof.json`、`release-machine-proof.json`、`post-smoke-graph-GC-delta.json` | 20 页只读检查、旧 Key 原状态、发布后最终机器门禁及图谱单列维护变化 |
| `cleanup/{cleanup-proof.json,base-cleanup.json,external-resource-audit.json,root-independent-check.json}` | 本轮资源精确清理、租约审计、正常/审计实例保护及 root 独立复核 |

PRIVATE gate 中的“未部署正常实例”是其阶段边界；当前发布状态以较后的 NORMAL `release-machine-proof.json` 为准。

最终 `cleanup/cleanup-proof.json` 为 `PRIVATE-CLEANUP-PASSED`。四个 owned live 角色及 esbuild helper 已停止，原 PID/startticks 对应进程树无残留；私有 live PostgreSQL/Milvus 数据库、Redis 逻辑库及租约已清理，held 升级副本随本轮 QA 基础资源删除。248 份外部资源 journal/lease 均结束，无残留数据库、角色、集合或锁。

五个 owned 容器、五个命名卷、原 MinIO 容器唯一挂载的匿名卷及一个网络均在身份核验后精确移除并确认不存在；没有全局 prune。任务专属 SDK `.pth` 与工作树 `node_modules` 链接已移除，原根目录依赖、SDK 版本、功能工作树/分支、派生镜像和私有备份/快照/日志继续保留，未安装或升级 SDK/host 全局包。

清理前后正常与审计实例共 19 个组件的 ID、image、启动时间、状态与健康不变；root 独立复核 12 个 owned Docker 对象均不存在，19080/19081 readiness 均为 200 JSON `{"status":"ok"}`。正常/审计数据库、备份、源码代理和环境文件未因本次清理修改。最终文档单独提交并快进同步至根工作树，已验收功能源码保持；最终提交、两个工作树状态、入口及 726 项原文件保护结果记录在仓库外 `root-finalization-proof.json`。

OpenAI Files/Vector Stores/Skills 兼容资源路由仅预留，未注册，不能用其资源 SDK 直接调用本平台接口。MinerU 仍按既定安排保留本机准备，模型未下载、服务未部署、实际 OCR 未验收。脚本上传不授予执行能力；本地协议 Provider 验收不等于收费供应商或生产负载验收。本轮没有前端打包。
