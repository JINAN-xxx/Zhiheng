# 智衡 AI 平台 · 重构开发中

智衡正在重建为以 AI 为核心的工作平台，采用模块化单体架构。模型管理、对话和用量治理直接内置于后端；模型接入使用 **LangChain 1.4.7**，前端使用 **Vue 3、TypeScript 和 Ant Design Vue**。

**当前版本为 0.1.0，已接通 AI 对话、完整模型管理与开发接入，整体目标平台仍在建设中。** 本 README 对应 `refactor/ai-core-modularization` 分支的新源码。

前端按用户确定的方向**保留原界面，允许整理内部目录和代码**。登录、侧栏、聊天、模型目录/凭据、开发接入和用量页面已复用原布局、品牌、主题与交互，并接通新后端。原页面及已完成优化完整保存在 `legacy/frontend/`；其余页面按[功能对齐计划](docs/superpowers/plans/2026-10-23-feature-parity.md)逐域接入。

## 当前功能状态

| 功能 | 当前状态 | 已实现的范围 |
| --- | --- | --- |
| 身份与会话 | 已实现并通过本地验收 | CLI 创建管理员、登录、注销、可撤销 Cookie 会话、资源归属校验 |
| 模型管理 | 已实现并通过本地验收 | 连接/模型新增、编辑、启停、发布与引用保护删除；文本、Embedding、Rerank 的真实调用与分类测试 |
| AI 对话 | 已实现并通过本地验收 | 流式回复、多轮上下文、历史记录、停止生成、断线后恢复查看 |
| 运行与用量 | 已实现并通过本地验收 | 后台执行、事务 outbox、租约、失联收敛、Token 配额预留和用量记录 |
| 知识库与文件 | 待实现 | 文档管理、解析、分块、索引和检索；**MinerU 尚未接入** |
| Agent、技能与 Work | 正在接入 | 技能提示词包、智能体配置与发布、预览和公开调用、人工选择成员的持久 Work 任务 |
| 开发接入与计费 | 已实现并通过本地验收 | 应用、一次回显 Key、授权、轮换/撤销、限额与预算；公开模型 API、手动费率版本和精确费用账本 |

本地验收使用独立 PostgreSQL / Redis 和本地模型 HTTP 服务，覆盖构建后的真实浏览器链路。具体结果与未覆盖范围见[首条 AI 链路验收](docs/superpowers/verification/2026-10-23-first-ai-slice.md)。这不等同于所有供应商、全部目标功能或生产部署均已验收。

2026-10-23 本组验收：后端 **150 项测试**、前端 **11 项行为测试与 41 项页面回归**、规则/格式/类型、模块边界与 wheel 检查均通过。独立完整 Compose 从空库启动，真实浏览器经过 Nginx、API、worker 与本地模型 HTTP 服务完成 **12 次调用**，验证聊天、取消、开发密钥生命周期、公开 JSON/SSE、Embedding/Rerank、限流和历史费用版本。详见[开发接入与模型管理验收](docs/superpowers/verification/2026-10-23-developer-models.md)。收费供应商和生产负载尚未验收。

模型连接编辑会创建不可变凭据修订，历史 Run 继续使用其原始绑定。费率只追加版本，已预留调用使用冻结价格，金额用 Decimal 计算。供应商未报告有效整数用量时保留预留、标记未知、费用为空；人工对账界面仍待建设。图片、工具执行和结构化输出仍未接入。

## 架构与目录

后端分发名为 `zhiheng-platform`，导入包为 `zhiheng`。同一份后端代码分别运行 API、worker 和 migrator，业务数据统一保存在 PostgreSQL，Redis 用于 ARQ 队列和 worker 心跳。

```mermaid
flowchart LR
    Web[Vue 3 工作台] --> API[FastAPI 接口]
    API --> PG[(PostgreSQL)]
    Worker[后台 Worker] --> PG
    Worker <--> Redis[(Redis / ARQ)]
    Worker --> Models[LangChain 1.4.7 模型适配]
    Models --> Provider[模型供应商]
    Migrator[数据库迁移进程] --> PG
```

API 将消息、运行、配额预留和 outbox 一起提交；worker 投递并执行运行，通过 attempt 与租约控制写入。最终消息、终态和用量一致落库；前端通过 SSE 读取持久快照。停止生成保存取消意图，关闭订阅只结束当前查看。

```text
backend/
├── src/zhiheng/
│   ├── api/             HTTP 入口
│   ├── application/     跨模块用例与事务编排
│   ├── modules/         identity、models、chat、developer
│   ├── runtime/         运行、租约、outbox 与恢复原语
│   ├── worker/          队列执行、取消观察与恢复
│   ├── bootstrap/       配置和资源装配
│   ├── infrastructure/  数据库连接与凭据加密
│   ├── shared/          公共值与基础错误
│   └── cli/             serve、worker、migrate、create-admin
├── migrations/          版本化数据库迁移
└── tests/               单元、契约、集成与浏览器验收
frontend/src/
├── app/                 应用、布局和显式路由
├── modules/             identity、models、chat、developer、usage
└── shared/              HTTP、SSE、校验、原公共组件与视觉样式
deploy/core/             Compose 和配置示例
docs/README.md           文档索引
tools/                   架构边界与 wheel 检查
legacy/                  旧工程归档与原有未提交优化
```

新平台使用独立配置、数据库、Redis 和持久卷。`legacy/` 供提取旧业务能力时参考，不进入新平台运行包。整体目标架构见[架构设计](docs/superpowers/specs/2026-10-23-ai-core-platform-redesign-design.md)，当前实际目录以[代码结构说明](代码结构说明.md)为准。

## 运行版本与访问入口

更新此分支的源码和 README 不会自动替换已运行的旧实例。访问旧部署地址时，页面和功能由旧部署版本决定。启动本 README 的新实例后，才会使用这里的新前端和后端。

本机使用的新实例为 `zhiheng-core`，网页地址 `http://127.0.0.1:19080`；旧 `zhiheng` 实例保持停止，旧数据保留。新实例采用独立数据卷，升级保留原 Fernet 凭据密钥和数据库。验收使用单独的临时项目与本地模型服务，不将测试模型写入正常实例。

| 使用方式 | 访问入口 |
| --- | --- |
| 本机前端开发 | `http://127.0.0.1:15190`，代理新 API 的 `19090` 端口 |
| Docker 新实例 | 默认 `http://127.0.0.1:19080`，项目名 `zhiheng-core` |

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

网页端登录后，展开“模型中心”，在“模型目录 → 凭据管理”添加 OpenAI 兼容服务连接，再在“平台模型”接入模型，进行连接测试或文字聊天。连接测试也经过持久运行与用量治理，会消耗实际供应商额度。

“开发接入”中创建应用并为 Key 授权模型。Key 只在创建或轮换成功时显示一次；公开接口使用 `Authorization: Bearer <api_key>`，支持 `/v1/models`、`/v1/chat/completions`、`/v1/embeddings` 和 `/v1/rerank`。请求中的 `model` 使用模型目录复制的平台 UUID。接口定义位于 `/docs` 和 `/openapi.json`；手动费率不代表已对接供应商价格同步或支付结算。

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

基础 CI 在 Python 3.10 / 3.13 上安装同一份完整哈希锁，检查 Ruff、Mypy、AST 依赖边界、独立单元测试及 `zhiheng_platform` wheel。平台 CI 使用真实 PostgreSQL / Redis、固定本地模型 HTTP 服务及 Chromium，检查业务集成、构建后真实页面链路和聊天/开发接入/模型管理的受控 HTTP / SSE 页面回归；普通 CI 不调用收费模型。

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
