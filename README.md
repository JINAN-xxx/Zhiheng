# 智衡 AI 平台

新平台以独立的 Python 分发 `zhiheng-platform` 和 Vue 3 / TypeScript / Ant Design Vue 前端交付。首版提供管理员建号、模型连接与测试、文字聊天和流式回复、持久任务、取消恢复及 Token 配额记录。供应商未返回用量时保留预留额度并标记待核对；当前尚未完成知识库、Agent 或货币计费。

`legacy/` 是旧工程的归档来源。新平台使用自己的源码、依赖、数据库、Redis 和持久卷；启动入口不会载入归档源码或旧环境文件。原有运行实例的操作应使用其独立流程。

```text
backend/             新核心包、版本化迁移与测试
frontend/            新 platform.html 入口与业务模块
deploy/core/         新平台 Compose 与配置示例
compose.yaml         新平台 Compose 入口
deploy.sh            新平台独立部署命令
tools/               架构边界与 wheel 检查
.github/workflows/   新核心及平台 CI
legacy/              旧后端、前端、部署和 CI 归档
```

## Docker 启动

需要 Docker Engine、Compose v2.20.3+ 和用于生成配置的 Python 3。以下命令只操作 `zhiheng-core` 项目，默认网页地址为 <http://127.0.0.1:19080>。

```bash
./deploy.sh init
./deploy.sh build
./deploy.sh up
./deploy.sh exec api python -m zhiheng create-admin --username admin
```

`init` 在 `deploy/core/.env.core` 生成新的数据库随机密码和 Fernet 凭据密钥，权限为 `0600`，拒绝覆盖已有文件。初始配置明确用于本机 HTTP：`CORE_COOKIE_SECURE=false`，且允许来源为 `http://127.0.0.1:19080`。管理员命令交互读取密码，没有默认管理员或固定口令；自动化可显式使用 `--password-stdin`。

使用自定义新配置文件时，在每条命令上加 `--env-file /absolute/path/to/new-core.env`。参考 [配置示例](deploy/core/.env.core.example)。不要复用旧数据库密码或凭据密钥；当前 Compose 的 `CORE_DB_PASSWORD` 只支持字母、数字、下划线和连字符组成的 URL-safe 原始口令，请勿预先 URL 编码。`init` 生成的随机口令符合此约束。变更数据库密码不会修改已经初始化的 PostgreSQL 用户密码。Fernet 密钥用于解密已保存的供应商凭据，应与数据库一起备份并在升级中保留。

网页端登录后，在“模型”页面添加 OpenAI 兼容服务连接和模型，再进行连接测试或文字聊天。连接测试也经过持久运行与用量治理，会消耗实际供应商额度。

Compose 启动顺序为 PostgreSQL / Redis 健康 → 迁移成功 → worker 健康 → API 就绪 → 网页。`/health/live` 检查 API 进程，`/health/ready` 检查迁移版本、Redis 和 worker 持久心跳。数据库和 Redis 不向宿主机发布端口，两个新数据卷由 `zhiheng-core` 项目管理。

```bash
./deploy.sh ps
./deploy.sh logs --follow api worker
./deploy.sh down                 # 保留新平台数据卷
./deploy.sh config --quiet       # 仅校验；不加 --quiet 会输出包含密钥的配置
```

脚本显式传入新环境文件并关闭 Compose 默认环境文件发现。需要直接使用 Compose 时，同样显式指定新文件：

```bash
COMPOSE_DISABLE_ENV_FILE=1 docker compose --env-file deploy/core/.env.core -f compose.yaml config --quiet
```

对外提供 HTTPS 时，先配置入口 TLS，设置 `CORE_COOKIE_SECURE=true` 和浏览器实际访问地址的 `CORE_ALLOWED_ORIGINS` JSON 数组，再调整 `CORE_WEB_BIND`。HTTP 本机地址变化时也要同步修改允许来源。后端镜像默认 Python 3.12；可通过 `CORE_PYTHON_IMAGE`、`CORE_PIP_INDEX_URL` 设置构建基础镜像和安装源。前端使用 Node 24 / npm 11 多阶段构建，Nginx 提供 `platform.html` 路由回退和无缓冲 SSE 代理。

## 本机开发

需要 Python 3.10+、Node 24、npm 11，以及单独的 PostgreSQL 16 / Redis 7。下面的依赖容器使用独立名称与回环端口，不读取 Compose 或旧工程配置。命令中的密码只保存在当前 shell 环境。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --require-hashes -r backend/requirements-core.lock
python -m pip install -r backend/requirements-dev-core.txt
python -m pip install --no-deps --no-build-isolation -e backend

export CORE_DEV_DB_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
docker run --detach --name zhiheng-core-dev-postgres \
  --publish 127.0.0.1:15432:5432 \
  --env POSTGRES_USER=zhiheng_core --env POSTGRES_DB=zhiheng_core \
  --env POSTGRES_PASSWORD="$CORE_DEV_DB_PASSWORD" postgres:16-alpine
docker run --detach --name zhiheng-core-dev-redis \
  --publish 127.0.0.1:16379:6379 redis:7-alpine

export ZHIHENG_DATABASE_URL="postgresql+asyncpg://zhiheng_core:${CORE_DEV_DB_PASSWORD}@127.0.0.1:15432/zhiheng_core"
export ZHIHENG_REDIS_URL=redis://127.0.0.1:16379/0
export ZHIHENG_CREDENTIAL_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')"
export ZHIHENG_COOKIE_SECURE=false
export ZHIHENG_ALLOWED_ORIGINS='["http://127.0.0.1:15190"]'
```

等待独立的 PostgreSQL 和 Redis 可用，再在上述环境运行迁移与建号：

```bash
python -m zhiheng migrate
python -m zhiheng create-admin --username admin
```

分别在保留相同虚拟环境和 `ZHIHENG_*` 导出变量的终端运行 worker 和 API；关闭时在对应终端按 Ctrl+C：

```bash
python -m zhiheng worker
```

```bash
python -m zhiheng serve --host 127.0.0.1 --port 19090
```

在另一终端启动前端，访问 <http://127.0.0.1:15190>；Vite 把 `/api` 转发到新 API：

```bash
cd frontend
npm ci
npm run dev:platform
```

`Settings` 不自动读取任何 `.env`。必须提供 `ZHIHENG_DATABASE_URL`（`postgresql+asyncpg`）、`ZHIHENG_REDIS_URL` 和新的 `ZHIHENG_CREDENTIAL_KEY`。Cookie 默认仅限 HTTPS，允许来源默认空数组，Token 配额默认 `100000`，供应商超时默认 `60` 秒，队列默认 `zhiheng:core:queue`。本机开发显式覆盖 Cookie 和浏览器来源。开发依赖容器需要重新使用时应保留原有密码和凭据密钥。

## 验证

基础 CI 在 Python 3.10 / 3.13 上安装同一份完整哈希锁，检查 Ruff、Mypy、AST 依赖边界、独立单元测试及 `zhiheng_platform` wheel。平台 CI 使用真实 PostgreSQL / Redis、固定本地模型 HTTP 服务及 Chromium，检查业务集成、构建后真实页面链路和八项受控 HTTP / SSE 页面回归；普通 CI 不调用收费模型。

```bash
cd backend
python -m ruff check --config ruff.toml src tests/unit tests/core ../tools/check_architecture.py ../tools/check_core_wheel.py
python -m ruff format --check --config ruff.toml src tests/unit tests/core ../tools/check_architecture.py ../tools/check_core_wheel.py
python -m mypy --config-file pyproject.toml
python ../tools/check_architecture.py src/zhiheng
python -m pytest tests/unit -q
```

真实集成测试需显式设置 `ZHIHENG_TEST_DATABASE_URL` 和 `ZHIHENG_TEST_REDIS_URL`，再运行 `python -m pytest tests/core -q`。测试会在指定 PostgreSQL 上创建并清理 UUID 临时数据库，账号须有建库权限；Redis 也应为独立测试实例。缺少配置会跳过依赖外部服务的测试，这不等同于集成通过。

```bash
cd frontend
npm run test:core
npm run lint:core
npm run format:check
npm run typecheck
npm run build:platform
```

文档入口见 [文档索引](docs/README.md)。代码边界和数据所有者见 [代码结构说明](代码结构说明.md)，重构约定见 [代码规范](docs/architecture/2026-10-23-code-standards.md)。
