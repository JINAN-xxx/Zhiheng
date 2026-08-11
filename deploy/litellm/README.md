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
- 数据库中已有加密配置后，不要修改 `LITELLM_SALT_KEY`，否则原有 Provider 凭据可能无法解密。
- 需要从其他主机访问时，应通过平台网关或带 TLS 的反向代理转发，不要直接暴露数据库或 Redis。
