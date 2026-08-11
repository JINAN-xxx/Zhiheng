# RuoYi 共享 PostgreSQL 启动说明

RuoYi 与 LiteLLM 共用 `litellm-db` PostgreSQL 16 实例，但数据库、Owner 和密码彼此独立：

| 服务 | 数据库 | Owner |
| --- | --- | --- |
| LiteLLM | `litellm` | `litellm` |
| RuoYi | `ruoyi-fastapi` | `ruoyi` |

## 初始化

先确保 LiteLLM 的三个容器正常运行，再从仓库根目录执行：

```bash
./deploy/ruoyi/init-shared-postgres.sh
docker compose -f docker-compose.pg.yml up -d --build
```

初始化脚本会在仓库根 `.env` 中生成 `RUOYI_DB_PASSWORD`，并把文件权限设为 `0600`。脚本可以重复执行；检测到 `public.sys_user` 后不会再次导入包含删表语句的基线 SQL。

## 安全边界

- RuoYi Backend 只获得 `ruoyi` 用户的密码，无法读取 LiteLLM 的数据库密码或 Master Key。
- `litellm-db` 不映射宿主机端口，两个应用都通过 Docker 内部网络访问。
- 两个数据库共用同一个 PostgreSQL 进程、数据卷和故障域。停止或恢复 `litellm-db` 会同时影响两个应用。
- RuoYi 继续使用独立的 `ruoyi-redis`，不复用 LiteLLM Redis。

## 验证

```bash
docker exec litellm-db psql -U litellm -d postgres -Atc \
  "select datname, pg_get_userbyid(datdba) from pg_database where datname in ('litellm','ruoyi-fastapi') order by datname"
docker compose -f docker-compose.pg.yml ps
```

数据库查询应显示 `litellm|litellm` 和 `ruoyi-fastapi|ruoyi`。
