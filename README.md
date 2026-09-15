# 智衡 AI 开放平台

智衡 AI 开放平台基于 RuoYi-Vue3-FastAPI 构建，面向企业和团队提供模型接入、统一调用、智能对话、应用开发接入和用量记录能力。平台采用插件化架构，既支持本地管理后台，也支持通过 OpenAI 兼容接口接入外部应用。

## 当前版本

- 平台版本：`v0.1`
- 后端：FastAPI、SQLAlchemy、PostgreSQL、Redis
- 前端：Vue 3、Vite、Ant Design Vue
- 模型网关：LiteLLM Proxy
- 认证方式：JWT、平台 API Key、Bearer Token
- 默认平台端口：`12580`

## 功能概览

### 平台工作台

- 智能对话：支持流式输出、历史会话、重命名、归档、恢复和删除。通过会话菜单归档后，可在侧栏“已归档”中查看历史并恢复继续对话；归档保留消息，永久删除仍需确认。
- 模型中心：统一查看和管理模型目录。
- 模型能力：支持对话模型、Embedding 模型和 Ranker 模型的能力标识。
- 开发接入：创建应用、配置模型授权、生成和轮换 API Key。
- 用量记录：查看请求状态、Token 消耗、检索单元和费用。
- API 说明：在开发接入页面查看平台 API 调用方式和示例。
- 智能体扩展：管理员统一维护平台技能，普通用户可在对话中按消息选择技能。

### 管理后台

- 用户、角色、菜单、部门、岗位和字典管理。
- 参数配置、通知公告、操作日志、登录日志和在线用户。
- 定时任务、服务监控、缓存监控和代码生成。
- 插件发现、安装、启停、升级、卸载、迁移和权限菜单管理。

## 系统架构

```text
浏览器 / 外部应用
        |
        v
RuoYi 前端（Vue 3） ---> RuoYi 后端（FastAPI） ---> PostgreSQL
                                  |
                                  +---------------> Redis
                                  |
                                  +---------------> LiteLLM Proxy ---> 上游模型服务
```

目录职责：

```text
ruoyi-fastapi-frontend/   Web 前端和 AI 工作台
ruoyi-fastapi-backend/    FastAPI 后端、插件和数据库迁移
ruoyi-fastapi-app/        uni-app 移动端
ruoyi-fastapi-test/       自动化测试
data/agent-skills/         平台技能持久文件（运行时生成，不提交 Git）
deploy/litellm/            LiteLLM 独立运行环境
deploy/ruoyi/              RuoYi PostgreSQL 初始化脚本
docs/                      架构、设计和开发文档
```

## 快速开始

### 环境要求

- Docker Engine 和 Docker Compose v2。
- Node.js `>= 20`（本地前端开发）。
- Python `>= 3.10`（本地后端开发）。
- 至少 4 GB 可用内存，首次构建模型相关依赖时建议 8 GB。

### 首次部署（推荐）

LiteLLM 和 RuoYi 使用独立的 Compose 项目。首次部署先启动 LiteLLM，再启动 RuoYi。

```bash
# 1. 启动 LiteLLM、PostgreSQL 和 Redis
cd deploy/litellm
./init-env.sh
docker compose --env-file .env -f compose.yaml up -d

# 2. 为 RuoYi 创建 Runtime Key，并写入仓库根目录 .env
LITELLM_MANAGEMENT_KEY='<LiteLLM 管理密钥>' ./init-platform-runtime-key.sh

# 3. 返回仓库根目录，一键初始化数据库并启动平台
cd ../..
./deploy.sh init
```

`init` 会生成或复用 RuoYi 的数据库密码和 JWT 密钥，初始化 `ruoyi-fastapi` 数据库，构建前后端镜像并启动服务。脚本不会删除数据库或数据卷。
初始化过程还会创建 `data/agent-skills` 持久目录，已上传的技能不会因后端容器重建而丢失。

### 日常运维

在仓库根目录执行：

```bash
./deploy.sh start      # 启动已有容器
./deploy.sh restart    # 重启服务
./deploy.sh stop       # 停止服务，保留容器和数据卷
./deploy.sh status     # 查看服务状态
./deploy.sh logs       # 查看实时日志
./deploy.sh logs --tail=100
./deploy.sh update     # 重新构建镜像并更新
```

脚本默认管理 Compose 项目 `ruoyi-vue3-fastapi`，从其他目录执行时仍会指向同一组容器。完整说明见 [`deploy/ruoyi/README.md`](./deploy/ruoyi/README.md)。

前端 `dist` 产物已随仓库发布，远端部署不需要安装 Node.js 或重新执行前端构建；Compose 会直接用 Nginx 挂载该目录。`init` 和 `update` 仍可能按需构建后端镜像。

### 访问地址

| 服务 | 地址 |
| --- | --- |
| 智衡平台 | <http://127.0.0.1:12580/> |
| Swagger | <http://127.0.0.1:12580/docker-api/docs> |
| ReDoc | <http://127.0.0.1:12580/docker-api/redoc> |
| LiteLLM 管理界面 | <http://127.0.0.1:14000/ui/> |
| LiteLLM OpenAI 兼容接口 | `http://127.0.0.1:14000/v1` |

默认管理员账号由项目初始化数据提供。生产环境请首次登录后修改密码，不要在公开环境使用默认凭据。

## 接入模型

1. 打开 LiteLLM 管理界面，配置上游模型和 Provider 凭据。
2. 在智衡平台进入「模型中心」，创建模型目录记录并填写模型编码、类型和能力。
3. 在「开发接入」创建应用，为应用授权可用模型。
4. 在「访问密钥」生成 API Key。密钥只在创建时完整展示，请妥善保存。
5. 使用平台 API 地址和 Bearer Token 发起调用。

示例：

```bash
curl http://127.0.0.1:19099/v1/chat/completions \
  -H 'Authorization: Bearer <平台 API Key>' \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "<模型编码>",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'
```

Embedding 和 Ranker 模型使用对应的模型类型和接口参数，具体请求字段以平台内「API 说明」为准。当前公开 API 由后端端口 `19099` 提供；生产环境建议通过带鉴权和 TLS 的网关转发。

## 使用平台技能

管理员进入「智能体扩展 / 技能管理」，可以上传单个 `SKILL.md` 或根目录包含 `SKILL.md` 的 ZIP 技能包。技能安装后支持查看、启用、停用和删除；启用中的技能必须先停用再删除，平台内置技能不能删除。

普通用户在智能对话输入框输入独立的 `@`，或点击“选择技能”按钮，即可为当前消息选择最多 5 个已启用技能。技能只作用于本次发送，消息发出后自动清空选择，不会改变整个会话的永久设置。

技能包不会执行脚本或自动安装依赖。生产部署应限制 `data/agent-skills` 的宿主机访问权限，并随数据库一起备份。

## 本地开发

### 后端

```bash
cd ruoyi-fastapi-backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-pg.txt
ruoyi app run --env=dev
```

后端本地开发需要 PostgreSQL、Redis 和 LiteLLM。数据库初始化和环境变量说明见 [`deploy/ruoyi/README.md`](./deploy/ruoyi/README.md) 与 [`deploy/litellm/README.md`](./deploy/litellm/README.md)。

### 前端

```bash
cd ruoyi-fastapi-frontend
npm install
npm run dev
```

开发服务器默认地址为 <http://127.0.0.1:5173/>。前端通过 Vite 代理访问本地后端。

### 移动端

```bash
cd ruoyi-fastapi-app
pnpm install
pnpm dev:h5
```

移动端详细说明见 [`ruoyi-fastapi-app/README.md`](./ruoyi-fastapi-app/README.md)。

## 测试与质量检查

```bash
# 前端插件和契约测试
cd ruoyi-fastapi-frontend
npm run test:plugin

# 后端测试
cd ../ruoyi-fastapi-backend
pytest -q

# Shell 脚本语法检查
cd ..
sh -n deploy.sh
```

端到端测试需要安装 Playwright 浏览器：

```bash
cd ruoyi-fastapi-frontend
pip install -r ../ruoyi-fastapi-test/requirements.txt
playwright install chromium
python tests/e2e/platform_shell_check.py
```

## 重要配置

### 根目录 `.env`

根目录 `.env` 由初始化脚本维护，包含 RuoYi 数据库连接、JWT 密钥、LiteLLM Runtime Key 和 Management Key。文件权限应为 `0600`，已被 Git 忽略，禁止提交。

### API Key 安全

- 平台 API Key 只保存摘要，不保存明文。
- API Key 只在创建或轮换时完整展示一次。
- 停用应用后不能调用其 API Key；重新启用应用后恢复调用。
- 轮换或撤销密钥会立即影响后续请求。

### 数据持久化

PostgreSQL 和 Redis 数据由 Docker 数据卷持久化，技能文件保存在 `data/agent-skills`。停止服务不会删除数据；执行带 `--volumes` 的 Compose 命令或清理技能目录前必须先完成备份。

## 文档索引

- [LiteLLM 部署说明](./deploy/litellm/README.md)
- [RuoYi PostgreSQL 部署说明](./deploy/ruoyi/README.md)
- [移动端说明](./ruoyi-fastapi-app/README.md)
- [测试说明](./ruoyi-fastapi-test/README.md)
- [CLI 使用说明](./ruoyi-fastapi-backend/docs/cli_usage.md)
- [技术架构演进汇报](./AI%20Open%20Platform%20技术架构演进汇报.md)
- [智能体扩展与技能管理设计](./docs/superpowers/specs/2026-09-07-agent-extension-skill-management-design.md)
- [变更日志](./CHANGELOG.md)

## 许可证

本项目采用 [MIT License](./LICENSE)。
