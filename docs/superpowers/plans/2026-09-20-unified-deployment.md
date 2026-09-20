# PostgreSQL 完整初始化与一键部署实现计划

> **面向 AI 代理的工作者：** 使用 subagent-driven-development 分配独立 SQL 生成任务和审查；主代理负责部署编排与端到端验证。

**目标：** 新环境执行 `./deploy.sh init` 即可启动完整平台，已有统一部署执行 `./deploy.sh update` 自动应用插件迁移，前端使用版本库内已发布 dist。

**架构：** 根目录 `compose.yaml` 统一 PostgreSQL、平台 Redis、LiteLLM Redis、LiteLLM、Milvus、初始化任务、后端及 nginx。初始化任务完成数据库、运行密钥和插件安装/升级后才启动后端。全部持久状态放在项目数据目录/独立 Compose 卷，保留密钥与迁移记录，已有旧部署不自动接管数据卷。

**技术栈：** Docker Compose v2、POSIX shell、Python 3 标准库、现有后端 Python 依赖及 PostgreSQL 16。

## 文件边界

- `compose.yaml`：完整服务拓扑、健康依赖、持久卷、统一环境变量。
- `deploy.sh`、`deploy/platform.py`：一条命令入口、生成/复用私密环境文件、Compose 编排、启动健康检查和更新。
- `deploy/legacy/manage.sh`：保存旧拆分部署入口以兼容现有实例，旧 Compose 文件仅供兼容。
- `ruoyi-fastapi-backend/scripts/build_platform_sql.py`、`sql/platform-pg.sql`：按现有基础 SQL、插件 manifest 生成完整初始结构和迁移记录。
- `ruoyi-fastapi-backend/scripts/initialize_platform.py`：仅初始化服务持有 PostgreSQL 管理凭据，创建业务角色/库、非空库防护、复用历史记录执行插件升级、创建受限 Runtime Key。
- `deploy/platform.env.example`、`README.md`、部署说明：最短部署/更新指令，必要配置和老部署说明。
- 部署测试：真实命令编排、密钥复用、失败阻断、SQL 生成一致性及独立 Docker 栈验收。

## 实施步骤

- [x] 检查现有脚本/启动生命周期并运行部署测试基线，隔离工作区保护原有移动端删除及临时文件。
- [x] TDD 实现完整 PostgreSQL SQL 生成器，准确记录 14 项迁移 checksum/语句数，按 manifest 收集种子；`--check` 检测陈旧产物。
- [x] 独立 PostgreSQL 实测初始化、重复执行拒绝及事务回滚；保留历史增量 migration 不改 checksum。
- [x] TDD 实现统一环境初始化：使用随机密钥、0600 文件、锁与原子写入，重试不重置密码、JWT、Salt 或 API Pepper。
- [x] 编写统一 Compose 和后台初始化任务，后端必须等待初始化成功，失败不打印凭据且返回非零。
- [x] 首次初始化导入合并 SQL，已有库只执行缺失迁移；安装/升级/启用内置 AI 与智能体插件，成功后才启动后端。
- [x] 使用已发布 dist 与现有 nginx 同源代理；修复知识库、技能、文件及 Redis 数据持久化，Milvus 首次允许拉镜像。
- [x] 更新 README 与环境模板，旧入口迁至 legacy，根命令 init/start/update/stop/status/logs/restart 保持易用。
- [x] 执行相关 Python/Node 测试、Compose config、shell/lint 及独立规格/质量审查。
- [x] 使用独立项目/端口/数据目录启动完整测试栈，验证首次启动、菜单权限、上传/配置入口、初始化重复执行、更新迁移、停启数据保留及错误阻断。
- [x] 整理验证记录并提交/推送 zhiheng 分支；当前线上部署只做只读健康检查。

## 验收约束

完整首次启动允许下载镜像并构建后端，宿主机无需 Node、手工 psql 或手工获取 LiteLLM Runtime Key。模型服务商 API Key 仍由用户在平台接入，不生成或假设真实付费服务凭据。初始化脚本严禁在非空旧库运行含 DROP 的合并 SQL；升级沿用插件迁移账本，失败即停止。独立验收不得连接当前生产数据库或覆盖已有卷。

## 实测修正

- 生产 CLI 对已构建前端禁止动态安装插件，初始化改为复用内置插件启动生命周期同步菜单/配置/任务；每次先由生产 MigrationRunner 校验迁移账本，同版本缺失迁移也会执行。
- 图谱词表随仓库发布并校验摘要，冷构建不再在线下载词表。Python 镜像升级至 pip 25.2，缓存依赖下载并开启断点续传，镜像源失败后使用官方源，完整性校验保持启用。
- 既有统一部署缺少持久密钥时拒绝操作，不重新生成 JWT、Salt 或 API Pepper。日志和状态查询不持部署写锁。
