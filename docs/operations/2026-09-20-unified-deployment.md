# 完整 PostgreSQL 初始化与一键部署

本次新增统一 `compose.yaml` 和 `./deploy.sh init` 入口。首次部署自动完成 PostgreSQL、两个 Redis、LiteLLM、Milvus、完整业务数据库、Runtime Key、内置插件资源及前后端启动。更新入口为 `./deploy.sh update`，前端直接使用版本库内的 dist。

## 初始化和升级

- `ruoyi-fastapi-backend/sql/platform-pg.sql` 根据基础 SQL、AI/智能体扩展的 PostgreSQL 迁移及种子生成。新库包含 53 张业务表、14 条成功迁移记录和 37 条服务商字典。后端启动可能按当前实体补充内部表，知识图谱按需初始化自身存储。
- 生成器 `scripts/build_platform_sql.py --check` 检查产物是否过时，Docker 构建也执行此检查。历史 migration 源文件与 checksum 未修改。
- 合并 SQL 拒绝非空 public schema；实际导入与完成标记处于同一事务，错误整体回滚。已有库不重新导入含 DROP 的基础 SQL。
- 每次初始化显式调用现有 MigrationRunner 校验账本并应用缺失迁移，插件版本相同也会执行。随后复用内置插件启动生命周期同步菜单、默认配置、任务、种子与安装状态，保留已停用插件的状态。
- PostgreSQL 管理凭据仅提供给初始化任务。普通后端使用独立业务角色；初始化失败时后端不会启动。
- `.env` 由脚本加锁并原子生成，权限 0600。重复执行保留密码、JWT、LiteLLM Salt、Runtime Key、Management Key 和 API Pepper；已有部署缺失这些密钥时拒绝操作，不能静默补生。

## 部署结构

主 Compose 共 8 个服务，其中 `platform-init` 是一次性任务。只开放 nginx 端口，默认 12580，可通过 `PLATFORM_PORT` 修改。应用 API 与公开 API 沿用 `/docker-api`、`/v1`。

PostgreSQL 和 Redis 使用项目独立数据卷；技能、知识库、平台附件及 Milvus 使用 `PLATFORM_DATA_DIR`。更新先构建镜像，构建成功后才暂停应用进行迁移。重复 init/start 会先停止已有应用，确保初始化门禁不会被已运行后端绕过。日志和状态查询不持部署写锁。

旧拆分 Compose 文件保留为兼容入口，旧脚本移至 `deploy/legacy/manage.sh`。现有发布目录管理的生产实例未迁移、未重启、未接管数据卷。

## 冷构建修复

首次真实冷构建成功安装 Python 依赖，但在线下载图谱词表超时。现在将已验证的 o200k_base 词表和许可随仓库提供，在镜像内复制并校验 SHA-256。

后续冷构建还复现包下载途中断开、残缺 wheel 校验失败及镜像源索引暂时不可用。镜像现固定 pip 25.2、开启缓存和下载续传，主镜像源失败后尝试官方源，保留包完整性校验；没有放宽哈希或 TLS 检查。下载续传能力参见 [pip 官方更新说明](https://pip.pypa.io/en/stable/news/)。最终从 `python:3.10` 构建成功，`pip check` 无依赖冲突。

## 验收

测试项目为 `zhiheng-acceptance-20260920`，使用独立端口 22580、独立 PostgreSQL/Redis 卷和文件目录，没有连接生产数据库。SQL 另在完全隔离的 PostgreSQL 16.14 容器验证。

- 相关后端测试 84 项通过；前端插件测试 47 组通过；SQL 生成一致性、Ruff、格式和 shell 语法检查通过。
- SQL 单独验证：空库初始化成功，非空库（包括仅有函数的 schema）拒绝，故障事务回滚至 0 表，真实 MigrationRunner 复用全部 14 条历史。
- 完整栈首次初始化与重复初始化通过；重复后用户数、菜单数、迁移数、管理员密码指纹和环境文件指纹保持一致。
- 模拟同版本旧库缺少 `ai_chat_message.attachments` 及对应第 010 项迁移记录，update 自动补齐 JSONB 列与成功历史，业务哨兵数据保持不变。
- 故意破坏已成功迁移的 checksum，update 返回非零，初始化任务失败，前后端未启动；修正账本后可重新部署恢复，数据和密钥不变。
- 冷构建镜像启动与更新通过，停止并重新启动全部服务后业务哨兵数据及迁移历史保留。真实浏览器完成管理员登录、知识库页面与权限路由检查；Milvus、知识图谱能力均可用，无页面异常。自动登录测试仅在隔离库关闭验证码，默认验证码接口已单独验证正常。

构建、启动、重复、升级、失败阻断和浏览器记录保存在 `data/agent-releases/20260920-unified-deployment/`。临时测试密钥及测试服务在验收后清理，生产仍沿用原发布入口。
