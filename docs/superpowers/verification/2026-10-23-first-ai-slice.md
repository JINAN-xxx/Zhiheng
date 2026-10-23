# 新平台首条 AI 链路验收

日期：2026-10-23。分支：`refactor/ai-core-modularization`。实现基准：`680c1d6d`。对应[实施计划](../plans/2026-10-23-first-ai-slice.md)。

## 本次交付

新后端实际位于 `backend/src/zhiheng/`，提供独立 `serve`、`worker`、`migrate`、`create-admin` 入口。业务模块为 identity、models、chat、developer；跨模块用例在 application，持久执行原语在 runtime，依赖由 bootstrap 装配。AI 核心不依赖旧插件生命周期。旧工程整体归档到 `legacy/`，不进入新包、镜像或 CI 检查范围。

从空库完成版本化迁移、显式管理员建号、Cookie 会话、模型连接及测试、LangChain 1.4.7 文本流式聊天、历史、取消和 Token 配额记录。API/worker 不自动建表，模型连接凭据加密保存，不返回密钥。队列只传稳定运行 ID。

PostgreSQL 保存消息、Run、outbox、配额和用量事实；账户行锁约束并发预留。Redis 提供 ARQ 和 worker 心跳。终态、最终消息及用量在同一数据库事务提交。SSE 读取持久完整快照，断线结束订阅，运行继续；重新订阅可恢复。上游缺少任一用量计数时保留已知部分，将整体标为 unknown 并保留额度。

新前端只使用 Vue 3、TypeScript strict、Ant Design Vue，路由和模块接口显式装配。交付包括新 Compose、构建镜像、Nginx SSE 代理、启动脚本和两条 CI。

## 本地验证证据

| 验证 | 实际结果 |
| --- | --- |
| 后端 `pytest tests/unit tests/core -q --tb=line` | **83 passed**，最新复验 37.77 秒，无跳过；3 条第三方弃用警告来自 websockets/ARQ |
| Ruff check / format | 通过；65 个 Python 文件格式符合规则，包含源码、迁移、测试与边界工具 |
| Mypy strict | 47 个源文件通过 |
| AST 依赖边界 | 通过，包含禁止导入 legacy、旧插件/模块，以及模块私有接口和 SDK 边界 |
| 前端 `npm run test:core` | **11 passed** |
| ESLint / Prettier / vue-tsc / 测试类型检查 | 全部通过 |
| `npm run build:platform` | 默认 `dist` 生产构建通过；临时输出目录的生产产物也通过浏览器验收 |
| 受控 HTTP/SSE Chromium 页面回归 | **8 passed**，覆盖登录按钮/Enter/失败重试及聊天竞态、恢复、校验 |
| 构建后真实浏览器链路 | 登录 → 页面创建连接与两个模型 → 连接测试 → 同连接连续两轮聊天 → 刷新历史 → 静默生成取消 → 用量 → 390px 窄屏 → 注销，全链路通过，无页面异常 |
| 干净环境完整哈希依赖安装 | 通过，随后只安装新 wheel，API/worker 从安装包运行，不使用源码 PYTHONPATH |
| wheel 内容检查 | 通过，独立 CLI 与迁移随包安装，排除旧 CLI、插件及环境文件 |
| 后端 Docker 构建 | Python 3.10 构建通过；镜像验证 LangChain 1.4.7、兼容的 httpx2、迁移安装路径和导入隔离 |
| Compose / 启动脚本 / CI | Compose 展开、`bash -n`、配置初始化及拒绝覆盖、actionlint 与工作流 shell 语法验证通过 |

集成测试使用本轮创建的 PostgreSQL/Redis 容器和动态回环端口，每个测试另建 UUID 数据库；不使用旧业务数据库。供应商是本地真实 HTTP 协议服务，实际经过 LangChain/供应商 SDK，浏览器主链路没有拦截业务接口。

真实浏览器工具为 `backend/tests/core/e2e/browser_check.py`，页面回归为 `frontend/tests/browser/ui_regressions.py`。运行条件和持续执行命令见根 README 与 `.github/workflows/core-platform.yml`。本机截图、API/worker 日志位于 `/tmp/zhiheng-core-browser-artifacts/`，不进入发布包。

## 审查与回归修复

后端、前端、交付均先做规格审查，再做独立质量审查，必须修复项已关闭。关键修复包括：

- 每次模型调用拥有独立客户端，防止 LangChain 缓存的客户端被提前关闭；同连接连续及并发调用通过真实 HTTP 验证。
- 屏蔽进程环境中的供应商凭据、额外 Header、代理和追踪回退，持久连接配置决定请求；中文和冲突 Header 均有回归。
- 供应商提前 EOF 不记为完成；原始协议缺少计数不会被 LangChain 合成的零值掩盖；不支持的请求参数显式拒绝。
- 失去租约的 attempt 不能提交结果；关闭后的清理 task 有期限且会取消并等待，不遗留后台写入。
- SSE 在注销、会话过期和用户禁用后停止读取；真实 TCP 断开会释放订阅与轮询，后台 Run 不受影响。
- 登录表单按钮/Enter 实际提交；聊天首次发送冻结输入，晚到列表不覆盖新会话，恢复入口不被刷新移除，历史未加载前禁止 Enter 发送。
- 窄屏导航保留可访问名称，模型选择框具备标签；生产页面没有横向溢出。

## 原有优化保留

归档前的 55 个修改文件与当前 `legacy/backend`、`legacy/frontend` 中对应文件逐项 SHA-256 一致，继续保持未提交。归档的暂存版本来自基准提交，未将这些优化夹带进新平台提交。

仓库外备份的 **42,466 个条目**全部核对保留，包括未跟踪源码、文档、设计素材及原有残留依赖；先前 3 个目录引用更新保持已记录的调整。备份目录为 `/home/ja/project/_local_backups/RuoYi-Vue3-FastAPI/20261023-110616-before-directory-migration/`，新增 `first-ai-slice-paths.json` 记录本轮最终路径与核对结果。备份、原始补丁和文件归档继续保留。本轮测试容器与前端 preview 已停止，原有运行服务未重启或停止。

## 验收边界

2026-10-23 对 `24efdcf0` 基准再次验收：后端 83 项测试、前端 11 项行为测试和 8 项页面回归全部通过，类型检查和默认 `dist` 生产构建通过。重新构建并安装新 wheel 后，登录、模型配置/测试、两轮聊天、历史、取消、用量、窄屏和注销的真实浏览器链路通过。测试继续使用本地模型 HTTP 服务，未调用收费供应商。

本次尝试构建前端 Docker 镜像：默认 Docker Hub 的基础镜像元数据请求长时间无进展后终止；备用镜像源拉取 Node 24 基础镜像返回 `network is unreachable`，未完成前端镜像构建和完整 Compose 启动。后端镜像构建和独立安装包的真实链路已有各自验收，不将它们作为整套容器部署通过的证据。

本次只读核对到主机 `12580` 端口仍由旧 `zhiheng` 项目提供页面，其镜像和挂载目录属于旧实例；新源码不会自动替换该实例。本轮未更换旧实例镜像、服务或数据。

未运行远端 CI、完整 Compose up、压测或生产部署。Python 3.13 矩阵已配置，未在本机执行。

本轮仅交付文本对话首条链路。知识库、文件、Agent、Work、外部 API Key/模型 API、货币计费、未知用量人工对账界面，以及全平台备份恢复演练仍待后续实施；整体设计中的目标能力不能视为本轮已完成。
