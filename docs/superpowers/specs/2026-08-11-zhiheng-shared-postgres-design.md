# ZhiHeng 与 LiteLLM 共享 PostgreSQL 实例设计

## 背景

LiteLLM Runtime 已使用 `litellm-db` PostgreSQL 16.14 容器。原有 `docker-compose.pg.yml` 还会启动独立的 `zhiheng-pg` PostgreSQL 14，增加了一个数据库容器和一套数据卷。当前 V0.1 单机部署选择复用 PostgreSQL 服务，同时保持数据库、用户和迁移边界隔离。

## 目标

- ZhiHeng 与 LiteLLM 复用同一个 PostgreSQL 16.14 进程。
- ZhiHeng 使用独立数据库 `zhiheng-fastapi` 和独立登录用户 `zhiheng`。
- ZhiHeng Backend 不持有 LiteLLM 数据库用户、数据库密码或 Master Key。
- 数据库初始化可重复执行，已有 ZhiHeng 数据时不重新导入基线 SQL。
- 保留独立的 ZhiHeng Redis，避免与 LiteLLM Router 状态混用。

## 架构

```text
litellm-db (PostgreSQL 16.14)
  |- database: litellm       owner: litellm
  `- database: zhiheng-fastapi owner: zhiheng

litellm-redis                LiteLLM 专用
zhiheng-redis                  ZhiHeng 专用

zhiheng-backend-pg
  |- zhiheng-network    -> zhiheng-redis
  `- litellm-network  -> litellm-db + litellm-proxy
```

共享的是 PostgreSQL 服务进程，不共享数据库、Schema、Owner 或应用凭据。

## 凭据

仓库根目录未提交的 `.env` 增加 `RUOYI_DB_PASSWORD`。初始化脚本在该值缺失时生成随机密码，并保持文件权限为 `0600`。

ZhiHeng Backend 只注入：

- `DB_HOST=litellm-db`
- `DB_PORT=5432`
- `DB_USERNAME=zhiheng`
- `DB_PASSWORD=${RUOYI_DB_PASSWORD}`
- `DB_DATABASE=zhiheng-fastapi`

LiteLLM 的 `LITELLM_DB_PASSWORD` 仍只保存在 `deploy/litellm/.env`，不进入 ZhiHeng Backend。

## 初始化

新增 `deploy/zhiheng/init-shared-postgres.sh`：

1. 检查 `litellm-db` 容器处于运行状态。
2. 在根 `.env` 中创建或读取 `RUOYI_DB_PASSWORD`。
3. 通过容器本地 PostgreSQL socket，以现有 `litellm` 管理角色创建或更新 `zhiheng` 登录角色。
4. 不存在时创建由 `zhiheng` 拥有的 `zhiheng-fastapi` 数据库。
5. 检查 `zhiheng-fastapi.public.sys_user`。
6. 表不存在时以 `zhiheng` 用户导入 `zhiheng-fastapi-backend/sql/zhiheng-fastapi-pg.sql`；存在时跳过导入。

脚本不得输出数据库密码。重复运行不删除或覆盖已有业务表。

## Compose 调整

`docker-compose.pg.yml` 删除 `zhiheng-pg` 服务和 Backend 对它的 `depends_on`。Backend 继续加入 `zhiheng-network` 与外部 `litellm-network`，并通过环境变量连接 `litellm-db`。

`zhiheng-redis` 保留。前端和 Backend 的端口保持不变：

- 前端：`http://127.0.0.1:12580`
- 后端：`http://127.0.0.1:19099`

## 启动顺序

```bash
./deploy/zhiheng/init-shared-postgres.sh
docker compose -f docker-compose.pg.yml up -d --build
```

LiteLLM Compose 必须先运行。ZhiHeng Compose 不管理 LiteLLM PostgreSQL 的生命周期，LiteLLM 停止时 ZhiHeng 数据库也会不可用，这是共享实例方案接受的故障耦合。

## 测试与验收

- 部署契约测试确认 ZhiHeng Compose 不再声明 `zhiheng-pg`。
- Backend 只注入 ZhiHeng 数据库凭据，不注入 LiteLLM 数据库凭据或 Master Key。
- 初始化脚本测试覆盖私密文件、幂等执行、已有表跳过导入和输出脱敏。
- 真实 PostgreSQL 中两个数据库 Owner 不同。
- ZhiHeng Backend 能同时访问 `litellm-db`、`zhiheng-redis` 和 `litellm-proxy`。
- 前端和后端 HTTP 服务可访问，LiteLLM Runtime 诊断返回健康状态。
