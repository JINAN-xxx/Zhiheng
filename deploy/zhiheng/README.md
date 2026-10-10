# 拆分部署：共享 PostgreSQL

新部署请使用 [统一一键部署入口](../README.md)。本文说明平台与 LiteLLM 分开编排的部署方式；管理入口路径中的 `legacy` 表示保留这一拆分方式。原 `ruoyi` 实例应先沿用原发布入口管理并完成迁移，不能直接通过当前脚本接管，迁移要求见 [拆分部署兼容入口](../README.md#拆分部署兼容入口)。

ZhiHeng 与 LiteLLM 共用 `litellm-db` PostgreSQL 16 实例，但数据库、Owner 和密码彼此独立：

| 服务 | 数据库 | Owner |
| --- | --- | --- |
| LiteLLM | `litellm` | `litellm` |
| 智衡平台 | `zhiheng-fastapi` | `zhiheng` |

上表为新数据库的默认值。源码路径为 `zhiheng-fastapi-backend/`，平台服务和容器为 `zhiheng-backend-pg`、`zhiheng-frontend`、`zhiheng-redis`，平台网络为 `zhiheng-network`。拆分部署使用根目录 `docker-compose.pg.yml`、默认项目名 `zhiheng-vue3-fastapi` 和外部 `litellm-network`。

使用 `ZHIHENG_COMPOSE_PROJECT`、`ZHIHENG_COMPOSE_FILE`、`ZHIHENG_ENV_FILE` 可指定项目、编排文件和环境文件；`ZHIHENG_SHARED_POSTGRES_CONTAINER` 可指定共享 PostgreSQL 容器，默认 `litellm-db`。对应 `RUOYI_*` 变量仅作只读回退兼容。已有数据库可通过 `.env` 中的 `PLATFORM_DB_NAME` / `PLATFORM_DB_USER` 显式选择；这些配置不会自动重命名数据库或迁移生产数据，详见 [数据库入口](../README.md#数据库入口)。

## 初始化

以下 `init` 用于新数据库。先确保 LiteLLM 的三个容器正常运行，再从仓库根目录执行：

```bash
./deploy/legacy/manage.sh init
```

已有实例应执行更新；仅存在旧默认数据库 `ruoyi-fastapi` 时，初始化脚本拒绝自动创建空的 `zhiheng-fastapi` 库。保留旧库时，完成容器与数据挂载迁移并显式配置 `PLATFORM_DB_NAME` / `PLATFORM_DB_USER` 后再更新。若要修改实体库名或账号，需要另行完成数据库迁移。

拆分 Redis 的 `/data` 使用镜像声明的匿名卷。服务更名时，仅用不带 `-v` 的 `docker rm` 保留旧卷不会自动接回；替换前必须用 `docker inspect` 确认 `/data` 对应的卷并备份，再通过 Compose 覆盖文件将 `zhiheng-redis:/data` 显式挂载到该外部旧卷。统一栈的 Redis 使用项目专属命名卷，保留原项目名即可复用，两种迁移方式不同。完整 YAML 示例、原项目启动命令及验证步骤见 [拆分 Redis 旧卷迁移](../README.md#拆分-redis-旧卷迁移)。

使用旧卷覆盖文件的实例，部署和后续更新必须始终加载 `docker-compose.pg.yml` 与 `redis-migration.yaml`。直接 Compose 前，将 `.env` 中的旧密码键原值迁移到 `ZHIHENG_DB_PASSWORD` / `ZHIHENG_REDIS_PASSWORD`，不输出密钥；脚本对旧变量的内存兼容不会写入直接 Compose 的配置。不要直接执行下面的默认 `update`，否则不会加载旧卷覆盖配置。

已采用当前命名、且无需额外覆盖配置的拆分实例，日常管理使用：

```bash
./deploy/legacy/manage.sh start      # 启动已有服务
./deploy/legacy/manage.sh restart    # 重启服务
./deploy/legacy/manage.sh stop       # 停止服务并保留数据
./deploy/legacy/manage.sh status     # 查看状态
./deploy/legacy/manage.sh logs       # 查看日志
./deploy/legacy/manage.sh update     # 重新构建并更新
```

前端构建产物位于 `zhiheng-fastapi-frontend/dist`，已随仓库提交。ZhiHeng Compose 直接用 Nginx 挂载该目录，远端启动无需安装 Node.js 或重新构建前端。

如需单独初始化新数据库，可执行 `./deploy/zhiheng/init-shared-postgres.sh`。

初始化脚本依赖 `openssl` 和 `flock`，会在仓库根 `.env` 中生成 `ZHIHENG_DB_PASSWORD`、`ZHIHENG_REDIS_PASSWORD` 与持久化的 `JWT_SECRET_KEY`，并把文件权限设为 `0600`。旧配置中的 `RUOYI_DB_PASSWORD` / `RUOYI_REDIS_PASSWORD` 只用于读取兼容；初始化写回新名称并保留原值，新旧值冲突时拒绝继续。并发执行会由 `.env.lock` 串行化，避免数据库密码或 JWT 密钥被重复生成。基线 SQL 和新完成标记 `public.zhiheng_bootstrap_state` 在同一个事务中写入，任意语句失败都会整体回滚。脚本可以重复执行，并识别历史 `public.ruoyi_bootstrap_state`；检测到完成标记后不会再次导入包含删表语句的基线 SQL。

脚本还会以 `0700` 权限创建仓库根目录下的 `data/agent-skills`。该目录挂载到后端容器 `/app/data/agent-skills`，用于保存已安装的平台技能；重新构建或替换后端容器不会删除其中内容。

如果数据库已经包含业务表、但没有完成标记，脚本会拒绝继续，以免覆盖不完整或外部创建的 schema。此时需要先备份并人工确认数据库状态，不要绕过检查直接重跑基线。

## 安全边界

- ZhiHeng Backend 只获得平台数据库用户的密码，默认用户为 `zhiheng`，无法读取 LiteLLM 的数据库密码或 Master Key。
- `litellm-db` 不映射宿主机端口，两个应用都通过 Docker 内部网络访问。
- 两个数据库共用同一个 PostgreSQL 进程、数据卷和故障域。停止或恢复 `litellm-db` 会同时影响两个应用。
- ZhiHeng 使用独立的 `zhiheng-redis`，不复用 LiteLLM Redis。

## 验证

```bash
docker exec litellm-db psql -U litellm -d postgres -Atc \
  "select datname, pg_get_userbyid(datdba) from pg_database where datname in ('litellm','zhiheng-fastapi') order by datname"
./deploy/legacy/manage.sh status
```

默认新部署的数据库查询应显示 `litellm|litellm` 和 `zhiheng-fastapi|zhiheng`；显式配置已有数据库的实例应按实际库名和 Owner 验证。
