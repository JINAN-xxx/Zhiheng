# LiteLLM Runtime 基线设计

## 目标

在现有 AI Open Platform 仓库中提供一套可用单条 Docker Compose 命令启动的 LiteLLM Runtime。第一阶段包含独立 LiteLLM Proxy、PostgreSQL 和 Redis，支持数据库迁移、动态模型管理、原始用量持久化，并为后续 Platform OpenAPI 集成保留稳定的内部访问边界。

## 范围

本阶段负责：

- 独立部署 LiteLLM Proxy、LiteLLM PostgreSQL 和 LiteLLM Redis。
- 固定容器镜像版本，不使用 `latest` 或移动标签。
- 为 PostgreSQL 和 Redis 配置健康检查及持久化卷。
- 通过未提交的环境文件注入数据库密码、Redis 密码、Master Key 和 Salt Key。
- 启用 LiteLLM 数据库模型管理和原始 Spend 持久化。
- 仅把 LiteLLM 端口绑定到宿主机回环地址。
- 提供配置校验、健康检查和认证检查命令。

本阶段不负责：

- Platform Tenant、Project、Application 和 Customer API Key。
- Platform OpenAI 兼容代理接口。
- Platform Model Catalog 到 LiteLLM Deployment 的同步。
- Platform Usage Ledger、Quota、Pricing 和 Billing。
- BISHENG、vLLM、Prometheus、Grafana 或多实例 LiteLLM。
- 真实 Provider Credential 和生产模型 Deployment。

## 容器架构

```text
Host 127.0.0.1:14000
          |
          v
  litellm-proxy:4000
       |       |
       |       +---- litellm-redis:6379
       |
       +------------ litellm-db:5432
```

三个服务加入独立的 `litellm-network`。PostgreSQL 和 Redis 不发布宿主机端口。LiteLLM Proxy 只发布到 `127.0.0.1`，后续接入 Platform 时再把 Platform Backend 加入同一内部网络或合并 Compose 网络定义。

## 镜像

| 服务 | 镜像 |
|---|---|
| LiteLLM Proxy | `ghcr.io/berriai/litellm-database:v1.95.0` |
| PostgreSQL | `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/postgres:16.14-alpine3.24` |
| Redis | `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/redis:7.4.7` |

Compose 允许通过环境变量覆盖完整镜像地址，但默认值必须是上述已经存在于本机的固定标签。

## 持久化

- `litellm-postgres-data` 挂载到 `/var/lib/postgresql/data`。
- `litellm-redis-data` 挂载到 `/data`，Redis 启用 AOF。
- LiteLLM Proxy 自身不保存业务文件。
- 单实例阶段由 LiteLLM 启动流程执行数据库迁移。
- 后续扩展到多实例时，迁移必须拆分为独立的一次性 Job，并为 Proxy 设置 `DISABLE_SCHEMA_UPDATE=true`。

## 配置

`config.yaml` 使用以下边界：

- `model_list` 初始为空，避免提交假模型或 Provider Secret。
- `general_settings.store_model_in_db` 为 `true`。
- `general_settings.database_url` 从 `DATABASE_URL` 环境变量读取。
- `general_settings.master_key` 从 `LITELLM_MASTER_KEY` 读取。
- `router_settings` 连接独立 Redis，并使用 `simple-shuffle`。
- `litellm_settings.request_timeout` 设置为 600 秒。
- 关闭详细调试日志，启用结构化 JSON 日志。

初始启动完成后可通过 Admin UI 或管理 API 创建数据库模型。Platform Model Catalog 实现后，Platform 是模型期望状态的唯一业务主数据，LiteLLM 数据库仅保存已经应用的 Runtime Deployment。

## 密钥

仓库只提交 `.env.example`，实际 `.env` 必须被 Git 忽略。运行所需密钥包括：

- `LITELLM_DB_PASSWORD`
- `LITELLM_REDIS_PASSWORD`
- `LITELLM_MASTER_KEY`
- `LITELLM_SALT_KEY`

数据库密码使用 URL 安全字符。Master Key 和 Salt Key 必须以 `sk-` 开头。Salt Key 初始化后不能随意更换，否则数据库中加密保存的 Provider Credential 将无法解密。

## 启动顺序与故障处理

1. PostgreSQL 启动并通过 `pg_isready`。
2. Redis 启动并通过带认证的 `PING`。
3. LiteLLM Proxy 启动、执行数据库迁移并通过 `/health/liveliness`。

PostgreSQL 或 Redis 未就绪时，Compose 不启动 LiteLLM。LiteLLM 退出后由 `restart: unless-stopped` 重启。健康检查保留足够的启动宽限时间以覆盖首次迁移。

## 验收

必须验证：

1. `docker compose config` 成功解析，且渲染结果不包含未展开变量。
2. 三个容器均处于运行状态，健康检查通过。
3. `GET /health/liveliness` 返回成功。
4. 不携带认证访问受保护接口失败。
5. 使用 Master Key 访问 `/v1/models` 成功。
6. PostgreSQL 和 Redis 不存在宿主机端口映射。
7. 重启 Compose 后 PostgreSQL 和 Redis 数据卷仍然存在。

## 后续阶段

下一阶段在此 Runtime 基线上实现 Platform Model Catalog、Deployment 同步和内部服务凭据。Customer API Key、客户身份、业务配额和最终 Usage Ledger 始终归 Platform 管理，不下沉到 LiteLLM。
