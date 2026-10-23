# 新平台首条 AI 链路实现计划

> 使用 subagent-driven-development 实施，按 TDD 验证行为，规格审查后再做质量审查。用户已授权落实，不再询问执行方式。当前分支与工作区已由用户选定。

**目标：** 从独立空库启动新平台，完成登录、模型配置与连接测试、流式聊天、历史记录、取消和用量查看。

**架构：** 新核心位于 `backend/src/zhiheng/`，独立配置、显式路由，API/worker/migrator 分离。业务模块拥有数据和公开接口，跨模块接入由 application 编排。前端独立入口只消费新 API，使用 Vue 3、TypeScript、Ant Design Vue。

**技术栈：** Python 3.10+、FastAPI、SQLAlchemy 2、PostgreSQL、Redis/ARQ、Alembic、LangChain 1.4.7、langchain-openai；Vue 3、TypeScript、Ant Design Vue、Vite。

## 范围与保护

- 复用现有工程门禁，不依赖旧 `plugins`、`module_*`、旧 `.env` 或旧数据库。新交付依赖、命令和配置有明确入口。
- 当前原有 55 个修改文件继续保持未提交；本计划实现只暂存明确的新文件及必要工程配置，提交前后按备份清单核对。
- 本轮支持 OpenAI Chat Completions 协议的文本聊天与流式能力。视觉、工具、知识库和 Agent 在后续模块阶段实现，不在配置界面声明尚未支持的能力。
- 模型连接凭据加密存储，不进入响应、日志、Run、队列或前端持久存储。连接测试也产生授权、配额和用量事实。
- 原有运行服务保持现状。测试使用单独的 PostgreSQL/Redis 容器、动态本机端口及本地模型协议服务，不访问收费模型。
- 一个时刻最多一个实现子代理；前端任务按固定协议独立实现，主代理负责后端及集成。子代理不提交共享索引，主代理按文件清单统一提交。
- 用户进一步指出 backend 内部仍是旧结构，已将旧实现实际归档到 `legacy/backend/`、`legacy/frontend/`，新工程只保留正式核心、迁移、测试和构建配置。原有 55 项优化按归档前指纹再次核对，内容不改且不提交。

## HTTP 契约

全部接口在 `/api` 下，JSON 字段使用 snake_case，响应直接返回资源。错误为 `{"error":{"code":"...","message":"..."}}`，不回传密码或密钥输入。认证使用可撤销的 HttpOnly、SameSite=Strict 会话 Cookie；密码使用 Argon2，数据库只存会话令牌摘要。默认 Cookie Secure，开发环境显式关闭。

| 方法与路径 | 请求 | 返回 |
| --- | --- | --- |
| POST `/auth/login` | username、password | `{"user":{"id","username","is_admin"}}` 并设置 Cookie |
| GET `/auth/me` | — | 当前 user 对象 |
| POST `/auth/logout` | `{}` | 204，撤销会话 |
| GET/POST `/models/connections` | 创建：name、base_url、api_key | 列表/资源：id、name、provider、base_url、has_api_key |
| GET/POST `/models` | 创建：name、connection_id、upstream_model、max_output_tokens | 列表/资源：id、name、connection_id、upstream_model、max_output_tokens、enabled、capabilities |
| POST `/models/{id}/test` | client_request_id | 202，RunSnapshot；以固定短提示词测试，不创建用户聊天 |
| GET/POST `/chat/conversations` | 创建：title，可省略 | 列表/资源：id、title、created_at |
| GET `/chat/conversations/{id}/messages` | — | 消息列表：id、role、content、run_id、created_at |
| POST `/chat/conversations/{id}/runs` | model_id、message、client_request_id | 202，RunSnapshot |
| GET `/runs/{id}` | — | RunSnapshot |
| GET `/runs/{id}/events` | — | SSE `event: snapshot`，data 为完整 RunSnapshot；终态后结束 |
| POST `/runs/{id}/cancel` | `{}` | RunSnapshot，取消事实持久化 |
| GET `/usage` | — | limit_tokens、used_tokens、reserved_tokens、records |

模型管理和连接测试要求管理员权限；对话、运行和用量要求资源所有权。列表有上限。消息最多 16,000 字符，模型 max_output_tokens 为 1–8,192，默认 512。首轮暂不提供连接编辑与删除，绑定使用不可变快照。

RunSnapshot 字段：id、resource_type（chat/connection_test）、resource_id、status、attempt_id、attempt_no、revision、projection_seq、text、error（空或 code/message）、created_at、usage（input_tokens/output_tokens 可空，status 为 pending/known/unknown）。运行状态为 pending/running/cancel_requested/completed/failed/cancelled/interrupted。

SSE 返回完整的持久投影，前端替换当前文本，不追加全量快照；断线后重新 GET/SSE 即可恢复。订阅断线不取消运行，停止按钮显式 POST cancel。客户端切换会话、退出登录时中止旧订阅，并拒绝晚到响应覆盖新上下文。

用量 records 含 id、run_id、model_id、source、status、input_tokens、output_tokens、charged_tokens、created_at。已知用量结算；上游可能已执行而结果未知时保留预留并标记 unknown，不能计为免费失败，也不自动重发。首版使用 token 配额，不声明未实现的货币定价。

## 任务 1：独立配置、打包与 API 启动

**文件职责：**

- `backend/src/zhiheng/bootstrap/settings.py`：仅显式加载新平台配置，前缀 `ZHIHENG_`，不自动读取任何 `.env`。
- `backend/src/zhiheng/bootstrap/container.py`：资源装配和关闭。
- `backend/src/zhiheng/api/main.py`：应用工厂、生命周期和显式路由。
- `backend/src/zhiheng/api/dependencies.py`、`api/errors.py`：会话依赖和安全错误响应。
- `backend/src/zhiheng/shared/errors.py`、`shared/principal.py`：公共错误和值对象。
- `backend/src/zhiheng/cli/main.py`、`__main__.py`：serve、worker、migrate、create-admin 命令。
- `backend/pyproject.toml`、`requirements-core.in`、`requirements-core.lock`：新核心分发及完整依赖锁。
- `tools/check_core_wheel.py` 及原有 wheel 测试：验证新 CLI 和核心包，不再要求打包旧 CLI。
- `backend/tests/core/unit/test_startup.py`：导入隔离、配置错误、live/ready、关闭资源。

- [x] 先写行为测试，观察新启动工厂缺失导致失败。
- [x] 创建配置、应用工厂和显式生命周期，live 不连接外部服务，ready 检查 schema、Redis 与 worker 心跳。
- [x] 新分发仅打包 src/zhiheng，CLI 入口改为 `zhiheng.cli.main:main`；新增依赖不装入原有全局环境。
- [x] 验证包导入不加载旧运行时，配置和请求错误不泄露输入值。

~~~python
from zhiheng.api.main import create_app
from zhiheng.bootstrap.settings import Settings

app = create_app(Settings(database_url=database_url, redis_url=redis_url, credential_key=key))
assert not any(name == 'plugins' or name.startswith('plugins.') for name in sys.modules)
~~~

## 任务 2：独立数据版本与身份

**文件职责：**

- `backend/src/zhiheng/infrastructure/database.py`：DeclarativeBase、AsyncSession 和 schema 检查。
- `backend/src/zhiheng/infrastructure/credentials.py`：Fernet 凭据加解密。
- `backend/src/zhiheng/modules/identity/{public,orm,service}.py`：用户、密码、会话和认证公开接口。
- `backend/src/zhiheng/api/routes/auth.py`：登录、当前身份和注销。
- `backend/migrations/`、`bootstrap/migrations.py`：独立版本图，固定迁移内容。
- `backend/tests/core/integration/test_identity.py`：真实 PostgreSQL 认证、注销和越权行为。

- [x] 先验证空库不能被 API 自动建表；迁移后可以创建管理员并登录。
- [x] 密码从 stdin/getpass 输入，初始账号由 CLI 显式创建，不使用固定默认口令。
- [x] 会话令牌只以摘要存储，注销后旧 Cookie 失效；错误登录返回相同公开错误。
- [x] 所有业务 API 校验用户及资源归属，状态修改校验来源与 JSON 请求。

~~~bash
python -m zhiheng migrate
python -m zhiheng create-admin --username admin
python -m zhiheng serve --host 127.0.0.1 --port 19090
~~~

## 任务 3：模型与调用治理

**文件职责：**

- `backend/src/zhiheng/modules/models/{public,orm,service}.py`：连接、模型、不可变绑定与规范化消息/增量。
- `backend/src/zhiheng/modules/models/adapters/openai_chat.py`：LangChain ChatOpenAI 适配，显式地址/密钥/超时、关闭隐式重试。
- `backend/src/zhiheng/modules/developer/{public,orm,service}.py`：token 账户、预留、调用及幂等结算。
- `backend/src/zhiheng/application/inference.py`：统一模型调用和用量处理。
- `backend/src/zhiheng/api/routes/{models,usage}.py`：管理员模型配置及本人的用量查询。
- `backend/tests/core/contract/test_model_provider.py`：使用本地 HTTP 协议服务验证真实 LangChain 适配。

- [x] 先写管理员权限、密钥不可读、正常/流式调用、异常和 usage 的失败测试。
- [x] 支持 openai_compatible 文本聊天，显式声明 chat/stream 能力，不向模块外暴露 LangChain 对象。
- [x] 以 UTF-8 字节上界、消息开销与输出上限预留 token；锁定账户行，阻止并发超额预留。
- [x] 逻辑调用键与请求指纹分开，重复请求不重复预留；同键异请求拒绝。
- [x] 已知用量按唯一调用尝试结算；未知上游结果保留配额与证据。配置测试使用相同治理入口。

~~~python
assert saved_connection['has_api_key'] is True
assert 'api_key' not in saved_connection
assert cipher.decrypt(stored_ciphertext) == 'local-provider-test-key'
assert first_admission.id == repeated_admission.id
~~~

## 任务 4：聊天、持久运行与 worker

**文件职责：**

- `backend/src/zhiheng/modules/chat/{public,orm,service}.py`：会话、消息、历史上下文与所有权。
- `backend/src/zhiheng/runtime/{orm,runs,outbox}.py`：通用 Run、幂等、租约、投影及 outbox。
- `backend/src/zhiheng/application/chat_runs.py`：聊天与连接测试的原子接入。
- `backend/src/zhiheng/worker/{main,execution}.py`：ARQ、发布/恢复周期任务、执行与取消。
- `backend/src/zhiheng/api/routes/{chat,runs}.py`：聊天与持久快照订阅。
- `backend/tests/core/integration/test_chat_runs.py`、`test_worker.py`：真实 PG/Redis 的完整行为。

- [x] 写入消息、Run、配额预留和 Outbox 在同一事务，提交后才投递 ARQ。
- [x] worker 使用 attempt token 与数据库租约认领，重复投递不重复调用模型；心跳、取消和投影更新均验证执行权。
- [x] 同会话只允许一个活跃运行；消息上下文排除失败运行的未完成对话。
- [x] 取消观察独立于模型增量，静默网络仍可停止；进程关闭收敛 task。
- [x] 过期且可能已调用上游的执行标记 interrupted/unknown，保留预留，不自动重放；待投递 Run 可从 outbox 重建队列。
- [x] 终态与最终助手消息、用量结算一致提交；SSE 以持久投影恢复。

~~~python
assert duplicate_run['id'] == admitted_run['id']
assert local_provider.request_count == 1
assert (await other_user.get(f'/api/runs/{run_id}')).status_code == 404
assert terminal['status'] == 'completed'
assert terminal['usage']['status'] == 'known'
~~~

## 任务 5：新前端入口与页面

**文件职责：**

- `frontend/platform.html`、`vite.platform.config.ts`：独立 Vite 入口，只构建新 TypeScript 应用。
- `frontend/src/app/{main,router}.ts`、`App.vue`：应用和显式模块路由装配。
- `frontend/src/shared/{http,stream}.ts`、`styles.css`：有类型的请求、完整快照 SSE 解析和视觉 token。
- `frontend/src/modules/{identity,models,chat,usage}/`：按 public.ts/routes.ts/api.ts/views 划分。
- `frontend/tests/core/`：请求及流协议单元测试。
- `backend/tests/core/e2e/browser_check.py`：对真实新平台与本地模型协议服务的浏览器验收。
- `frontend/package.json`：dev:platform、build:platform、preview:platform、test:core 命令。

- [x] 先验证 SSE 跨字节边界解析、完整快照替换、取消清理和晚到请求隔离。
- [x] 新入口统一 TypeScript strict、Ant Design Vue，模块公开路由；不导入旧 router/store/plugin，也不引入 Element Plus。
- [x] 登录页、模型连接/模型配置、连接测试、会话/流式聊天、历史和用量页面按上述契约实现。
- [x] 管理员导航按用户身份呈现；API 错误在当前交互上下文展示，401 清理身份并中止旧请求。
- [x] 切换会话和退出时使旧响应失效，停止按钮独立持久取消，断线后可恢复 Run。
- [x] lint、format、vue-tsc、前端测试与临时生产构建通过；浏览器验证桌面与窄屏布局。

~~~bash
npm run dev:platform
npm run test:core
npm run lint:core
npm run typecheck
npm run build:platform -- --outDir /tmp/zhiheng-core-platform-build
~~~

## 任务 6：交付、集成与独立审查

**文件职责：** `deploy/core/compose.yaml`、`backend/Dockerfile.core`、`deploy/core/README.md`、`.github/workflows/core-platform.yml`、`docs/superpowers/verification/2026-10-23-first-ai-slice.md`。

- [x] 提交完整依赖锁，干净环境安装并验证；新镜像只复制核心包与新迁移，不携带旧插件、旧 CLI、旧环境文件或旧部署配置。
- [x] Compose 新实例名、数据库/Redis/文件卷与旧部署隔离，migrator 先完成再启动 API/worker。
- [x] 本地 PG/Redis 与 HTTP 模型服务跑通登录、模型配置、测试、聊天、取消、历史、用量、并发配额和重复投递。
- [x] 先规格审查，再质量审查；修复必须修复项后重验。
- [x] 核对原有 55 项与未跟踪优化，提交仅包含本轮实现，记录通过/跳过/未覆盖项；停止本轮临时测试服务。

验收以实际代码、数据库、模型 HTTP 请求及生产前端行为为准。后续知识库、Agent、Work、文件管理、公开 API Key 和货币计费不在本首条链路交付中提前声明完成。
