# LiteLLM Runtime 基线实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 提供一套可用单条 Docker Compose 命令启动并完成健康与认证验证的 LiteLLM Proxy、PostgreSQL 和 Redis 运行基线。

**架构：** LiteLLM 使用独立 PostgreSQL 保存动态模型和原始 Spend，使用独立 Redis 保存 Router 协调状态。数据库和 Redis 仅暴露在 Compose 网络内，Proxy 只绑定宿主机 `127.0.0.1:14000`，所有 Secret 由未提交的 `.env` 注入。

**技术栈：** Docker Compose v2、LiteLLM Database v1.95.0、PostgreSQL 16.14、Redis 7.4.7、Pytest、PyYAML、POSIX shell

---

## 文件结构

- 创建 `deploy/litellm/compose.yaml`：定义 LiteLLM Proxy、PostgreSQL、Redis、网络、卷和健康检查。
- 创建 `deploy/litellm/config.yaml`：定义 LiteLLM 数据库模式、Router Redis 和生产日志配置。
- 创建 `deploy/litellm/.env.example`：列出镜像、端口和 Secret 环境变量契约。
- 创建 `deploy/litellm/init-env.sh`：生成权限为 `0600` 的本地 `.env`，且不覆盖已有文件。
- 创建 `deploy/litellm/README.md`：记录启动、检查、日志、停止和数据清理命令。
- 创建 `backend/tests/deploy/test_litellm_runtime.py`：验证部署文件的安全和可重复性契约。
- 修改 `.gitignore`：忽略 `deploy/litellm/.env`。

### 任务 1：建立 Compose 部署契约测试

**文件：**
- 创建：`backend/tests/deploy/test_litellm_runtime.py`

- [ ] **步骤 1：编写缺失部署文件时失败的测试**

```python
from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEPLOYMENT_ROOT = REPOSITORY_ROOT / 'deploy' / 'litellm'


def load_yaml(name: str) -> dict[str, object]:
    with (DEPLOYMENT_ROOT / name).open(encoding='utf-8') as file:
        return yaml.safe_load(file)


def test_litellm_compose_uses_pinned_local_images_and_private_data_services() -> None:
    compose = load_yaml('compose.yaml')
    services = compose['services']

    assert set(services) == {'litellm-proxy', 'litellm-db', 'litellm-redis'}
    assert {name: service['container_name'] for name, service in services.items()} == {
        'litellm-proxy': 'litellm-proxy',
        'litellm-db': 'litellm-db',
        'litellm-redis': 'litellm-redis',
    }
    assert services['litellm-proxy']['image'] == '${LITELLM_IMAGE:-ghcr.io/berriai/litellm-database:v1.95.0}'
    assert services['litellm-db']['image'].endswith('postgres:16.14-alpine3.24}')
    assert services['litellm-redis']['image'].endswith('redis:7.4.7}')
    assert services['litellm-proxy']['ports'] == ['127.0.0.1:${LITELLM_PORT:-14000}:4000']
    assert 'ports' not in services['litellm-db']
    assert 'ports' not in services['litellm-redis']


def test_litellm_compose_waits_for_stateful_dependencies() -> None:
    services = load_yaml('compose.yaml')['services']

    assert services['litellm-proxy']['depends_on'] == {
        'litellm-db': {'condition': 'service_healthy'},
        'litellm-redis': {'condition': 'service_healthy'},
    }
    assert services['litellm-db']['healthcheck']
    assert services['litellm-redis']['healthcheck']
    assert services['litellm-proxy']['healthcheck']


def test_litellm_config_uses_database_redis_and_environment_secrets() -> None:
    config = load_yaml('config.yaml')

    assert config['model_list'] == []
    assert config['general_settings']['store_model_in_db'] is True
    assert config['general_settings']['database_url'] == 'os.environ/DATABASE_URL'
    assert config['general_settings']['master_key'] == 'os.environ/LITELLM_MASTER_KEY'
    assert config['router_settings']['redis_password'] == 'os.environ/LITELLM_REDIS_PASSWORD'
    assert config['litellm_settings']['json_logs'] is True


def test_litellm_local_environment_is_ignored() -> None:
    gitignore = (REPOSITORY_ROOT / '.gitignore').read_text(encoding='utf-8').splitlines()
    env_example = (DEPLOYMENT_ROOT / '.env.example').read_text(encoding='utf-8')

    assert '/deploy/litellm/.env' in gitignore
    for key in (
        'LITELLM_DB_PASSWORD',
        'LITELLM_REDIS_PASSWORD',
        'LITELLM_MASTER_KEY',
        'LITELLM_SALT_KEY',
    ):
        assert f'{key}=' in env_example
```

- [ ] **步骤 2：运行测试验证正确失败**

运行：

```bash
cd backend
pytest tests/deploy/test_litellm_runtime.py -v
```

预期：测试因 `deploy/litellm/compose.yaml` 不存在而失败。

- [ ] **步骤 3：提交红灯测试**

```bash
git add backend/tests/deploy/test_litellm_runtime.py
git commit -m "test: 定义 LiteLLM 部署契约"
```

### 任务 2：实现 Compose 和 LiteLLM 配置

**文件：**
- 创建：`deploy/litellm/compose.yaml`
- 创建：`deploy/litellm/config.yaml`
- 创建：`deploy/litellm/.env.example`
- 修改：`.gitignore`
- 测试：`backend/tests/deploy/test_litellm_runtime.py`

- [ ] **步骤 1：实现三服务 Compose**

```yaml
services:
  litellm-db:
    image: ${LITELLM_POSTGRES_IMAGE:-swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/postgres:16.14-alpine3.24}
    container_name: litellm-db
    restart: unless-stopped
    environment:
      POSTGRES_DB: litellm
      POSTGRES_USER: litellm
      POSTGRES_PASSWORD: ${LITELLM_DB_PASSWORD:?set LITELLM_DB_PASSWORD in .env}
      POSTGRES_INITDB_ARGS: --encoding=UTF8 --lc-collate=C --lc-ctype=C
    volumes:
      - litellm-postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: [CMD-SHELL, pg_isready -U litellm -d litellm]
      interval: 5s
      timeout: 5s
      retries: 30
    networks: [litellm-network]

  litellm-redis:
    image: ${LITELLM_REDIS_IMAGE:-swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/redis:7.4.7}
    container_name: litellm-redis
    restart: unless-stopped
    environment:
      LITELLM_REDIS_PASSWORD: ${LITELLM_REDIS_PASSWORD:?set LITELLM_REDIS_PASSWORD in .env}
    command:
      - sh
      - -c
      - exec redis-server --appendonly yes --requirepass "$${LITELLM_REDIS_PASSWORD}"
    volumes:
      - litellm-redis-data:/data
    healthcheck:
      test:
        - CMD-SHELL
        - REDISCLI_AUTH="$${LITELLM_REDIS_PASSWORD}" redis-cli ping
      interval: 5s
      timeout: 5s
      retries: 30
    networks: [litellm-network]

  litellm-proxy:
    image: ${LITELLM_IMAGE:-ghcr.io/berriai/litellm-database:v1.95.0}
    container_name: litellm-proxy
    restart: unless-stopped
    init: true
    environment:
      DATABASE_URL: postgresql://litellm:${LITELLM_DB_PASSWORD}@litellm-db:5432/litellm
      LITELLM_MASTER_KEY: ${LITELLM_MASTER_KEY:?set LITELLM_MASTER_KEY in .env}
      LITELLM_SALT_KEY: ${LITELLM_SALT_KEY:?set LITELLM_SALT_KEY in .env}
      LITELLM_MODE: PRODUCTION
      STORE_MODEL_IN_DB: "True"
      DISABLE_SCHEMA_UPDATE: "false"
      LITELLM_REDIS_HOST: litellm-redis
      LITELLM_REDIS_PORT: "6379"
      LITELLM_REDIS_PASSWORD: ${LITELLM_REDIS_PASSWORD:?set LITELLM_REDIS_PASSWORD in .env}
    command: [--config, /app/config.yaml, --port, "4000", --num_workers, "1"]
    volumes:
      - ./config.yaml:/app/config.yaml:ro
    ports:
      - 127.0.0.1:${LITELLM_PORT:-14000}:4000
    depends_on:
      litellm-db:
        condition: service_healthy
      litellm-redis:
        condition: service_healthy
    healthcheck:
      test:
        - CMD-SHELL
        - python -c "import urllib.request; urllib.request.urlopen('http://localhost:4000/health/liveliness')"
      interval: 10s
      timeout: 5s
      retries: 30
      start_period: 120s
    networks: [litellm-network]

networks:
  litellm-network:
    name: litellm-network
    driver: bridge

volumes:
  litellm-postgres-data:
    name: litellm-postgres-data
  litellm-redis-data:
    name: litellm-redis-data
```

- [ ] **步骤 2：实现 LiteLLM 配置**

```yaml
model_list: []

router_settings:
  routing_strategy: simple-shuffle
  redis_host: os.environ/LITELLM_REDIS_HOST
  redis_port: os.environ/LITELLM_REDIS_PORT
  redis_password: os.environ/LITELLM_REDIS_PASSWORD

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY
  database_url: os.environ/DATABASE_URL
  store_model_in_db: true
  proxy_batch_write_at: 60

litellm_settings:
  request_timeout: 600
  set_verbose: false
  json_logs: true
  telemetry: false
```

- [ ] **步骤 3：添加环境变量模板和忽略规则**

```dotenv
LITELLM_IMAGE=ghcr.io/berriai/litellm-database:v1.95.0
LITELLM_POSTGRES_IMAGE=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/postgres:16.14-alpine3.24
LITELLM_REDIS_IMAGE=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/redis:7.4.7
LITELLM_PORT=14000
LITELLM_DB_PASSWORD=replace-with-url-safe-random-value
LITELLM_REDIS_PASSWORD=replace-with-random-value
LITELLM_MASTER_KEY=sk-replace-with-random-value
LITELLM_SALT_KEY=sk-replace-once-and-never-rotate
```

在 `.gitignore` 增加：

```gitignore
/deploy/litellm/.env
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend
pytest tests/deploy/test_litellm_runtime.py -v
```

预期：4 个测试全部通过。

- [ ] **步骤 5：验证 Compose 渲染**

运行：

```bash
cd deploy/litellm
docker compose --env-file .env.example -f compose.yaml config --quiet
```

预期：退出码为 0。

- [ ] **步骤 6：提交配置实现**

```bash
git add .gitignore deploy/litellm/compose.yaml deploy/litellm/config.yaml deploy/litellm/.env.example
git commit -m "feat: 添加 LiteLLM 数据库部署基线"
```

### 任务 3：实现安全的本地环境初始化

**文件：**
- 创建：`deploy/litellm/init-env.sh`
- 修改：`backend/tests/deploy/test_litellm_runtime.py`

- [ ] **步骤 1：编写环境初始化失败测试**

```python
import subprocess


def test_init_env_generates_private_idempotent_secrets(tmp_path: Path) -> None:
    target = tmp_path / '.env'
    script = DEPLOYMENT_ROOT / 'init-env.sh'

    subprocess.run([str(script), str(target)], check=True)
    first_content = target.read_text(encoding='utf-8')
    subprocess.run([str(script), str(target)], check=True)

    assert target.read_text(encoding='utf-8') == first_content
    assert target.stat().st_mode & 0o777 == 0o600
    assert 'LITELLM_DB_PASSWORD=' in first_content
    assert 'LITELLM_REDIS_PASSWORD=' in first_content
    assert 'LITELLM_MASTER_KEY=sk-' in first_content
    assert 'LITELLM_SALT_KEY=sk-' in first_content
```

- [ ] **步骤 2：运行测试验证正确失败**

运行：

```bash
cd backend
pytest tests/deploy/test_litellm_runtime.py::test_init_env_generates_private_idempotent_secrets -v
```

预期：因 `init-env.sh` 不存在而失败。

- [ ] **步骤 3：实现初始化脚本**

```sh
#!/bin/sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
env_file=${1:-"$script_dir/.env"}

if [ -e "$env_file" ]; then
    echo "保留已有环境文件: $env_file"
    exit 0
fi

umask 077
db_password=$(openssl rand -hex 24)
redis_password=$(openssl rand -hex 24)
master_key="sk-$(openssl rand -hex 32)"
salt_key="sk-$(openssl rand -hex 32)"

{
    printf '%s\n' 'LITELLM_IMAGE=ghcr.io/berriai/litellm-database:v1.95.0'
    printf '%s\n' 'LITELLM_POSTGRES_IMAGE=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/postgres:16.14-alpine3.24'
    printf '%s\n' 'LITELLM_REDIS_IMAGE=swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/redis:7.4.7'
    printf '%s\n' 'LITELLM_PORT=14000'
    printf 'LITELLM_DB_PASSWORD=%s\n' "$db_password"
    printf 'LITELLM_REDIS_PASSWORD=%s\n' "$redis_password"
    printf 'LITELLM_MASTER_KEY=%s\n' "$master_key"
    printf 'LITELLM_SALT_KEY=%s\n' "$salt_key"
} > "$env_file"

echo "已创建环境文件: $env_file"
```

- [ ] **步骤 4：运行部署测试验证通过**

运行：

```bash
cd backend
pytest tests/deploy/test_litellm_runtime.py -v
```

预期：5 个测试全部通过。

- [ ] **步骤 5：提交初始化脚本**

```bash
git add deploy/litellm/init-env.sh backend/tests/deploy/test_litellm_runtime.py
git commit -m "feat: 添加 LiteLLM 本地密钥初始化"
```

### 任务 4：启动并验证运行时

**文件：**
- 创建：`deploy/litellm/README.md`
- 生成但不提交：`deploy/litellm/.env`

- [ ] **步骤 1：生成本地密钥并校验 Compose**

```bash
cd deploy/litellm
./init-env.sh
docker compose --env-file .env -f compose.yaml config --quiet
```

预期：生成权限为 `0600` 的 `.env`，Compose 校验退出码为 0。

- [ ] **步骤 2：启动三个容器**

```bash
docker compose --env-file .env -f compose.yaml up -d
docker compose --env-file .env -f compose.yaml ps
```

预期：`litellm-db`、`litellm-redis`、`litellm-proxy` 最终均为 `Up (healthy)`。

- [ ] **步骤 3：验证健康和认证边界**

```bash
curl --fail --silent http://127.0.0.1:14000/health/liveliness
curl --silent --output /dev/null --write-out '%{http_code}\n' http://127.0.0.1:14000/v1/models
set -a
. ./.env
set +a
curl --fail --silent -H "Authorization: Bearer $LITELLM_MASTER_KEY" http://127.0.0.1:14000/v1/models
```

预期：健康检查成功；未认证请求返回 `401`；认证后的模型列表请求返回成功。

- [ ] **步骤 4：验证数据库与 Redis 没有宿主机端口**

```bash
docker inspect litellm-db --format '{{json .HostConfig.PortBindings}}'
docker inspect litellm-redis --format '{{json .HostConfig.PortBindings}}'
```

预期：两条命令均输出 `null` 或 `{}`。

- [ ] **步骤 5：编写运行手册**

````markdown
# LiteLLM Runtime

## 启动

```bash
./init-env.sh
docker compose --env-file .env -f compose.yaml up -d
```

## 状态与日志

```bash
docker compose --env-file .env -f compose.yaml ps
docker compose --env-file .env -f compose.yaml logs -f litellm-proxy
```

## 停止

```bash
docker compose --env-file .env -f compose.yaml down
```

`down` 保留数据库和 Redis 数据卷。只有明确需要清空全部 LiteLLM 数据时才使用 `down --volumes`。
````

- [ ] **步骤 6：运行最终验证**

运行：

```bash
cd backend
pytest tests/deploy/test_litellm_runtime.py -v
cd ../deploy/litellm
docker compose --env-file .env -f compose.yaml config --quiet
docker compose --env-file .env -f compose.yaml ps
```

预期：全部测试通过，Compose 校验成功，三个服务均健康。

- [ ] **步骤 7：提交运行手册**

```bash
git add deploy/litellm/README.md
git commit -m "docs: 添加 LiteLLM 运行手册"
```
