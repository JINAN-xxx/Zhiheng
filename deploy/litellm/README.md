# LiteLLM Runtime

本目录提供 AI Open Platform V0.1 使用的 LiteLLM 运行基线，包括独立的 PostgreSQL、Redis 和 LiteLLM Proxy。

## 启动

在本目录执行：

```bash
./init-env.sh && docker compose --env-file .env -f compose.yaml up -d
```

首次执行会创建权限为 `0600` 的 `.env`，生成数据库密码、Redis 密码、LiteLLM Master Key 和 Salt Key。再次执行会保留已有 `.env` 和数据卷。

查看服务状态：

```bash
docker compose --env-file .env -f compose.yaml ps
```

三个服务都应显示为 `healthy`。首次启动需要初始化数据库，LiteLLM Proxy 可能需要等待几十秒。

LiteLLM 健康后，为 RuoYi Backend 创建独立的 Runtime Key，并显式配置 Management Key：

```bash
LITELLM_MANAGEMENT_KEY='<proxy-admin-key>' ./init-platform-runtime-key.sh
```

脚本使用部署侧 Master Key 创建别名为 `platform-runtime` 的 `llm_api` Virtual Key，并将 Runtime Key 和 Management Key 写入仓库根目录未提交的 `.env`。已有非空 Runtime Key 时不会重复创建，但仍会同步 Management Key；已有非空 Management Key 不会被调用环境覆盖。脚本不输出任何 Key，也不会从 Master Key 自动复制 Management Key。

脚本只把部署 dotenv 当作数据解析，不会作为 Shell 脚本执行。Key 中常见的 `$`、`#` 和空格会使用 Compose 字面量编码保真；换行、Shell 控制符及无法安全编码的单引号或反斜杠会在请求 LiteLLM 前被拒绝。创建远程 Runtime Key 前，脚本会先在目标目录完成权限为 `0600` 的本地 staging；最终替换失败时保留原文件并清理临时凭据。

首期 LiteLLM 未提供独立管理角色时，运维可以显式将具有 Proxy Admin 权限的值配置为 `LITELLM_MANAGEMENT_KEY`（包括当前 Master Key 的值）。该兼容做法只属于运维配置；Platform 代码、环境变量名和 HTTP 客户端始终保持 Management 凭据语义。本地开发不使用控制面时可留空，此时仅能查看本地 Model Catalog。

随后在仓库根目录启动 RuoYi PostgreSQL 版本：

```bash
docker compose -f docker-compose.pg.yml up -d
```

RuoYi Backend 通过外部 `litellm-network` 访问 `http://litellm-proxy:4000`。Docker Compose 会要求仓库根 `.env` 同时提供 `LITELLM_RUNTIME_KEY` 和 `LITELLM_MANAGEMENT_KEY`；缺失任一变量都会在启动前失败。两个 Compose 项目生命周期独立，启动 RuoYi 前应先确保 LiteLLM 三个容器健康。

## 访问

- 管理界面：<http://127.0.0.1:14000/ui/>
- OpenAI 兼容 API：`http://127.0.0.1:14000/v1`
- 健康检查：<http://127.0.0.1:14000/health/liveliness>

管理界面的默认用户名是 `admin`，密码是 `.env` 中的 `LITELLM_MASTER_KEY`。

初始模型列表为空。登录管理界面添加模型后，模型配置会保存到 PostgreSQL，不需要修改 `config.yaml`。

## 验证

```bash
curl --fail http://127.0.0.1:14000/health/liveliness

set -a
. ./.env
set +a
curl --fail \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  http://127.0.0.1:14000/v1/models
```

未携带 Master Key 或有效 Virtual Key 访问受保护接口时应返回 `401`。

## 日志

```bash
docker compose --env-file .env -f compose.yaml logs -f litellm-proxy
docker compose --env-file .env -f compose.yaml logs -f litellm-db litellm-redis
```

## 停止与重启

停止容器并保留数据：

```bash
docker compose --env-file .env -f compose.yaml down
```

重新启动：

```bash
docker compose --env-file .env -f compose.yaml up -d
```

只有明确需要清空全部 LiteLLM 数据时，才执行：

```bash
docker compose --env-file .env -f compose.yaml down --volumes
```

`down --volumes` 会永久删除 LiteLLM 的 PostgreSQL 和 Redis 数据卷。

## 安全约束

- Proxy 只绑定 `127.0.0.1:14000`，PostgreSQL 和 Redis 不暴露宿主机端口。
- `.env` 已被 Git 忽略，不要提交或通过日志输出其中的值。
- RuoYi Backend 只持有职责分离的 `LITELLM_RUNTIME_KEY` 和 `LITELLM_MANAGEMENT_KEY`，不要把 `LITELLM_MASTER_KEY`、`LITELLM_DB_PASSWORD` 或 `LITELLM_REDIS_PASSWORD` 注入 Platform 容器。
- Frontend 不持有任何 LiteLLM Key，所有运行面和管理面请求都由 Backend 代理。
- 数据库中已有加密配置后，不要修改 `LITELLM_SALT_KEY`，否则原有 Provider 凭据可能无法解密。
- 需要从其他主机访问时，应通过平台网关或带 TLS 的反向代理转发，不要直接暴露数据库或 Redis。
