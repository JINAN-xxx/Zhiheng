# ZhiHeng 共享 PostgreSQL 实例实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让 ZhiHeng 与 LiteLLM 复用现有 PostgreSQL 16.14 容器，同时使用独立数据库、用户和凭据，并启动完整 ZhiHeng 平台。

**架构：** `litellm-db` 同时承载 `litellm` 与 `zhiheng-fastapi` 两个数据库，Owner 分别为 `litellm` 与 `zhiheng`。初始化脚本通过容器本地 socket 创建 ZhiHeng 角色和数据库，Backend 只获得 `ZHIHENG_DB_PASSWORD`，ZhiHeng Redis 继续独立运行。

**技术栈：** Docker Compose、PostgreSQL 16.14、POSIX shell、Pytest、PyYAML

---

## 文件结构

- 创建 `deploy/zhiheng/init-shared-postgres.sh`：生成 ZhiHeng 数据库密码、创建角色和数据库、按需导入 SQL。
- 创建 `deploy/zhiheng/README.md`：记录共享数据库启动、验证和故障边界。
- 修改 `docker-compose.pg.yml`：删除 `zhiheng-pg`，向 Backend 注入独立数据库连接。
- 修改 `backend/.env.dockerpg`：同步容器内数据库主机、用户和空密码模板。
- 修改 `backend/tests/deploy/test_litellm_runtime.py`：增加共享 PostgreSQL 部署契约。

### 任务 1：定义共享 PostgreSQL 部署契约

**文件：**

- 修改：`backend/tests/deploy/test_litellm_runtime.py`

- [ ] **步骤 1：编写失败测试**

新增测试：

```python
def test_zhiheng_compose_reuses_litellm_postgres_with_separate_credentials() -> None:
    compose = load_repository_yaml('docker-compose.pg.yml')
    services = compose['services']
    backend = services['zhiheng-backend-pg']

    assert set(services) == {'zhiheng-frontend', 'zhiheng-backend-pg', 'zhiheng-redis'}
    assert backend['depends_on'] == {'zhiheng-redis': {'condition': 'service_healthy'}}
    assert backend['environment']['DB_HOST'] == 'litellm-db'
    assert backend['environment']['DB_USERNAME'] == 'zhiheng'
    assert backend['environment']['DB_DATABASE'] == 'zhiheng-fastapi'
    assert backend['environment']['DB_PASSWORD'].startswith('${ZHIHENG_DB_PASSWORD:')
    assert 'LITELLM_DB_PASSWORD' not in backend['environment']
    assert 'LITELLM_MASTER_KEY' not in backend['environment']


def test_shared_postgres_initializer_has_non_destructive_guard() -> None:
    script = (REPOSITORY_ROOT / 'deploy/zhiheng/init-shared-postgres.sh').read_text(encoding='utf-8')

    assert "to_regclass('public.sys_user')" in script
    assert 'backend/sql/zhiheng-fastapi-pg.sql' in script
    assert 'ZHIHENG_DB_PASSWORD' in script
    assert 'LITELLM_DB_PASSWORD' not in script
```

- [ ] **步骤 2：运行红灯**

```bash
cd backend
pytest tests/deploy/test_litellm_runtime.py -v
```

预期：Compose 仍包含 `zhiheng-pg`，初始化脚本不存在。

- [ ] **步骤 3：提交红灯测试**

```bash
git add backend/tests/deploy/test_litellm_runtime.py
git commit -m "test: 定义 ZhiHeng 共享 PostgreSQL 契约"
```

### 任务 2：实现私密且幂等的数据库初始化

**文件：**

- 创建：`deploy/zhiheng/init-shared-postgres.sh`
- 创建：`deploy/zhiheng/README.md`
- 修改：`backend/tests/deploy/test_litellm_runtime.py`

- [ ] **步骤 1：扩展脚本行为测试**

使用临时根 `.env` 和假的 `docker` 可执行文件，断言：

```python
def test_shared_postgres_initializer_generates_private_password_without_leaking(tmp_path: Path) -> None:
    # fake docker 对 inspect 返回 true，对 to_regclass 返回空，其余命令记录 stdin。
    # 执行脚本后断言环境文件为 0600、保留 LITELLM_RUNTIME_KEY、增加 ZHIHENG_DB_PASSWORD。
    # stdout/stderr 不得包含生成的测试密码。


def test_shared_postgres_initializer_skips_seed_when_schema_exists(tmp_path: Path) -> None:
    # fake docker 对 to_regclass 返回 sys_user。
    # 断言捕获内容不包含基线 SQL 的部门表注释。
```

- [ ] **步骤 2：运行测试确认因脚本缺失而失败**

```bash
cd backend
pytest tests/deploy/test_litellm_runtime.py -v
```

- [ ] **步骤 3：实现初始化脚本**

脚本必须：

```sh
#!/bin/sh
set -eu

# 默认目标为仓库根 .env，可通过第一个参数覆盖以便测试。
# 检查 litellm-db 正在运行。
# 缺少 ZHIHENG_DB_PASSWORD 时用 openssl rand -hex 24 生成并原子写回 0600 文件。
# 通过 psql \gexec 幂等创建 zhiheng 角色和 "zhiheng-fastapi" 数据库。
# 用 to_regclass('public.sys_user') 判断是否需要导入基线 SQL。
# 所有输出都不得包含密码或 SQL 响应正文。
```

角色与数据库 SQL：

```sql
\set db_password '<generated-hex>'
SELECT format('CREATE ROLE zhiheng LOGIN PASSWORD %L', :'db_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'zhiheng')
\gexec
ALTER ROLE zhiheng LOGIN PASSWORD :'db_password';
SELECT 'CREATE DATABASE "zhiheng-fastapi" OWNER zhiheng'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'zhiheng-fastapi')
\gexec
```

- [ ] **步骤 4：运行测试、Shell 语法检查并提交**

```bash
cd backend
pytest tests/deploy/test_litellm_runtime.py -v
cd ..
sh -n deploy/zhiheng/init-shared-postgres.sh
git add deploy/zhiheng backend/tests/deploy/test_litellm_runtime.py
git commit -m "feat: 添加 ZhiHeng 共享数据库初始化"
```

### 任务 3：调整 Compose 与 Backend 环境

**文件：**

- 修改：`docker-compose.pg.yml`
- 修改：`backend/.env.dockerpg`
- 测试：`backend/tests/deploy/test_litellm_runtime.py`

- [ ] **步骤 1：删除 `zhiheng-pg` 服务并注入连接配置**

Backend 环境：

```yaml
environment:
  DB_HOST: litellm-db
  DB_PORT: "5432"
  DB_USERNAME: zhiheng
  DB_PASSWORD: "${ZHIHENG_DB_PASSWORD:?run deploy/zhiheng/init-shared-postgres.sh first}"
  DB_DATABASE: zhiheng-fastapi
  LITELLM_BASE_URL: http://litellm-proxy:4000
  LITELLM_RUNTIME_KEY: "${LITELLM_RUNTIME_KEY:?set LITELLM_RUNTIME_KEY in repository .env}"
depends_on:
  zhiheng-redis:
    condition: service_healthy
```

完整删除 `zhiheng-pg` 服务。`zhiheng-redis` 只加入 `zhiheng-network`，Backend 继续加入两个网络。

- [ ] **步骤 2：同步 `.env.dockerpg` 非敏感模板**

```dotenv
DB_HOST = 'litellm-db'
DB_USERNAME = 'zhiheng'
DB_PASSWORD = ''
DB_DATABASE = 'zhiheng-fastapi'
```

- [ ] **步骤 3：验证并提交**

```bash
cd backend
pytest tests/deploy/test_litellm_runtime.py -v
cd ..
ZHIHENG_DB_PASSWORD=test LITELLM_RUNTIME_KEY=test docker compose -f docker-compose.pg.yml config --quiet
git add docker-compose.pg.yml backend/.env.dockerpg
git commit -m "feat: 复用 LiteLLM PostgreSQL 实例"
```

### 任务 4：真实初始化、启动和验收

**文件：**

- 可能修改：仅限前述文件中联调发现的缺陷。

- [ ] **步骤 1：运行真实初始化**

```bash
./deploy/zhiheng/init-shared-postgres.sh
stat -c '%a' .env
```

预期 `.env` 为 `600`，脚本不输出密码。

- [ ] **步骤 2：验证数据库隔离**

```bash
docker exec litellm-db psql -U litellm -d postgres -Atc \
  "select datname, pg_get_userbyid(datdba) from pg_database where datname in ('litellm','zhiheng-fastapi') order by datname"
```

预期 `litellm|litellm` 与 `zhiheng-fastapi|zhiheng`。

- [ ] **步骤 3：构建并启动 ZhiHeng**

```bash
docker compose -f docker-compose.pg.yml up -d --build
```

- [ ] **步骤 4：等待服务并验证**

```bash
docker compose -f docker-compose.pg.yml ps
curl --fail http://127.0.0.1:12580/
curl --fail http://127.0.0.1:19099/docs
docker exec zhiheng-backend-pg python -c \
  "import urllib.request; print(urllib.request.urlopen('http://litellm-proxy:4000/health/liveliness').status)"
```

- [ ] **步骤 5：运行回归与安全检查**

```bash
cd backend
pytest tests/deploy/test_litellm_runtime.py tests/plugins/ai -q
ruff check tests/deploy/test_litellm_runtime.py plugins/ai
cd ..
git diff --check
git status --short
```

## 规格覆盖自检

- [ ] 同一 PostgreSQL 进程、两个独立数据库和 Owner。
- [ ] Backend 不持有 LiteLLM 数据库凭据或 Master Key。
- [ ] 初始化已有数据时不执行基线 SQL。
- [ ] ZhiHeng Redis 保持独立。
- [ ] 前端、后端、数据库、Redis 和 LiteLLM Runtime 均有真实运行验证。
