# 旧拆分部署：共享 PostgreSQL

新部署请使用 [统一一键部署入口](../README.md)。本文保留用于已有拆分部署。

ZhiHeng 与 LiteLLM 共用 `litellm-db` PostgreSQL 16 实例，但数据库、Owner 和密码彼此独立：

| 服务 | 数据库 | Owner |
| --- | --- | --- |
| LiteLLM | `litellm` | `litellm` |
| ZhiHeng | `zhiheng-fastapi` | `zhiheng` |

## 初始化

先确保 LiteLLM 的三个容器正常运行，再从仓库根目录执行：

```bash
./deploy/legacy/manage.sh init
```

日常管理使用统一脚本：

```bash
./deploy/legacy/manage.sh start      # 启动已有服务
./deploy/legacy/manage.sh restart    # 重启服务
./deploy/legacy/manage.sh stop       # 停止服务并保留数据
./deploy/legacy/manage.sh status     # 查看状态
./deploy/legacy/manage.sh logs       # 查看日志
./deploy/legacy/manage.sh update     # 重新构建并更新
```

前端构建产物位于 `zhiheng-fastapi-frontend/dist`，已随仓库提交。ZhiHeng Compose 直接用 Nginx 挂载该目录，远端启动无需安装 Node.js 或重新构建前端。

如需单独初始化数据库，仍可直接执行 `./deploy/zhiheng/init-shared-postgres.sh`。

初始化脚本依赖 `openssl` 和 `flock`，会在仓库根 `.env` 中生成 `RUOYI_DB_PASSWORD` 与持久化的 `JWT_SECRET_KEY`，并把文件权限设为 `0600`。并发执行会由 `.env.lock` 串行化，避免数据库密码或 JWT 密钥被重复生成。基线 SQL 和 `public.zhiheng_bootstrap_state` 完成标记在同一个事务中写入，任意语句失败都会整体回滚。脚本可以重复执行；检测到完成标记后不会再次导入包含删表语句的基线 SQL。

脚本还会以 `0700` 权限创建仓库根目录下的 `data/agent-skills`。该目录挂载到后端容器 `/app/data/agent-skills`，用于保存已安装的平台技能；重新构建或替换后端容器不会删除其中内容。

如果数据库已经包含业务表、但没有完成标记，脚本会拒绝继续，以免覆盖不完整或外部创建的 schema。此时需要先备份并人工确认数据库状态，不要绕过检查直接重跑基线。

## 安全边界

- ZhiHeng Backend 只获得 `zhiheng` 用户的密码，无法读取 LiteLLM 的数据库密码或 Master Key。
- `litellm-db` 不映射宿主机端口，两个应用都通过 Docker 内部网络访问。
- 两个数据库共用同一个 PostgreSQL 进程、数据卷和故障域。停止或恢复 `litellm-db` 会同时影响两个应用。
- ZhiHeng 继续使用独立的 `zhiheng-redis`，不复用 LiteLLM Redis。

## 验证

```bash
docker exec litellm-db psql -U litellm -d postgres -Atc \
  "select datname, pg_get_userbyid(datdba) from pg_database where datname in ('litellm','zhiheng-fastapi') order by datname"
docker compose ps
```

数据库查询应显示 `litellm|litellm` 和 `zhiheng-fastapi|zhiheng`。