# 新平台当前功能完整验收

日期：2026-10-23。分支：`refactor/ai-core-modularization`。实现基准：`680c1d6d`。对应[实施计划](../plans/2026-10-23-first-ai-slice.md)。

本次完整验收基于 `64ee98fa`，并包含取消终态错误投影的修复。范围是当前已经实现的身份、模型、聊天、运行和用量，不代表整体目标架构中的后续业务均已完成。

## 本次交付

新后端实际位于 `backend/src/zhiheng/`，提供独立 `serve`、`worker`、`migrate`、`create-admin` 入口。业务模块为 identity、models、chat、developer；跨模块用例在 application，持久执行原语在 runtime，依赖由 bootstrap 装配。AI 核心不依赖旧插件生命周期。旧工程整体归档到 `legacy/`，不进入新包、镜像或 CI 检查范围。

从空库完成版本化迁移、显式管理员建号、Cookie 会话、模型连接及测试、LangChain 1.4.7 文本流式聊天、历史、取消和 Token 配额记录。API/worker 不自动建表，模型连接凭据加密保存，不返回密钥。队列只传稳定运行 ID。

PostgreSQL 保存消息、Run、outbox、配额和用量事实；账户行锁约束并发预留。Redis 提供 ARQ 和 worker 心跳。终态、最终消息及用量在同一数据库事务提交。SSE 读取持久完整快照，断线结束订阅，运行继续；重新订阅可恢复。上游缺少任一用量计数时保留已知部分，将整体标为 unknown 并保留额度。

新前端只使用 Vue 3、TypeScript strict、Ant Design Vue，路由和模块接口显式装配。交付包括新 Compose、构建镜像、Nginx SSE 代理、启动脚本和两条 CI。

## 本地验证证据

| 验证 | 实际结果 |
| --- | --- |
| 后端 `pytest tests/unit tests/core -q --tb=line` | **85 passed**，45.53 秒，0 失败、0 跳过；3 条第三方弃用警告来自 websockets/ARQ |
| Ruff check / format | 通过；65 个 Python 文件格式符合规则，包含源码、迁移、测试与边界工具 |
| Mypy strict | 47 个源文件通过 |
| AST 依赖边界 | 通过，包含禁止导入 legacy、旧插件/模块，以及模块私有接口和 SDK 边界 |
| 前端 `npm run test:core` | **11 passed** |
| 前端完整锁文件安装 | `npm ci` 通过，锁文件未改变 |
| ESLint / Prettier / vue-tsc / 测试类型检查 | 全部通过 |
| `npm run build:platform` | 默认 `dist` 生产构建通过；临时输出目录的生产产物也通过浏览器验收 |
| 受控 HTTP/SSE Chromium 页面回归 | **8 passed**，覆盖登录按钮/Enter/失败重试及聊天竞态、恢复、校验 |
| 补充受控页面场景 | **5 passed**，覆盖 1440/375/320 宽度、模型表单、SSE、用量失败重试及成员导航；共保存 16 张截图 |
| 构建后真实浏览器链路 | 通过 Docker Nginx → API → worker → LangChain → HTTP 模型完成登录、模型配置/测试、两轮聊天、历史、取消、用量、窄屏和注销，无业务接口拦截或页面异常 |
| 干净环境完整哈希依赖安装 | 通过，随后只安装新 wheel，API/worker 从安装包运行，不使用源码 PYTHONPATH |
| wheel 内容检查 | 通过，独立 CLI 与迁移随包安装，排除旧 CLI、插件及环境文件 |
| 后端 Docker 构建 | 默认 Python 3.12-slim 构建和真实运行通过；安装完整哈希锁及独立 wheel。此前 Python 3.10 构建、导入与迁移资源检查也通过 |
| 前端 Docker 构建 | 原 Dockerfile 的 Node 24 / npm 11.9.0 构建及 Nginx 1.28 运行通过 |
| 完整 Compose | 独立空库迁移成功；PostgreSQL、Redis、worker、API、web 健康，migrator 退出码 0 |
| Compose / 启动脚本 / CI | Compose 展开、`bash -n`、配置初始化及拒绝覆盖、actionlint 与工作流 shell 语法验证通过 |

集成测试使用本轮创建的 PostgreSQL/Redis 容器和动态回环端口，每个测试另建 UUID 数据库；不使用旧业务数据库。供应商是本地真实 HTTP 协议服务，实际经过 LangChain/供应商 SDK，浏览器主链路没有拦截业务接口。

基础浏览器工具为 `backend/tests/core/e2e/browser_check.py`，页面回归为 `frontend/tests/browser/ui_regressions.py`。运行条件和持续执行命令见根 README 与 `.github/workflows/core-platform.yml`。本轮完整验收脚本、日志、镜像摘要、截图和测试数据库快照保存于 `/home/ja/project/_local_backups/RuoYi-Vue3-FastAPI/20261023-134853-full-platform-test/`，不进入发布包。上表记录原界面适配前的完整基线；后续前端验证见下节。

## 原前端界面适配复验

用户明确保留原界面，允许整理内部实现。以 `3f493a9a` 为后端基准，复用原品牌、视觉 token、布局和消息渲染，接通新身份、模型、聊天与用量接口。后端运行源码和迁移未改动，未重复此前的 85 项后端与 10 项故障测试。

| 验证 | 本轮实际结果 |
| --- | --- |
| 锁文件与基础检查 | `npm ci`、11 项核心行为、ESLint、Prettier（含 SCSS）、TypeScript、生产构建全部通过；E2E Python 的 Ruff/format 通过 |
| 受控 Chromium 页面回归 | **22 passed**，25.354 秒；保留原 8 项业务回归，并覆盖原布局、手机表格/卡片、主题、表单、菜单、代码高亮与真实复制 |
| 原界面参照与截图 | 隔离构建原前端保存 28 张参照图，新前端保存 28 张对应图；新图无页面异常或横向溢出 |
| 独立规格审查 | 122 项核心检查及 38 项复制专项通过；四份视觉样式独立 Sass 编译结果与原文件一致 |
| 独立质量审查 | 再跑核心 11 项和页面 22 项均通过；另 **8 项**竞态、身份隔离、密钥清理及富文本安全检查通过，未发现未解决问题 |
| 真实容器浏览器链路 | 从独立空库启动，实际经过 Nginx → API → worker → LangChain → 本地 HTTP；登录、配置连接/模型、连接测试、两轮聊天、历史刷新、静默取消、4 条用量、手机导航与注销均通过，无接口拦截或页面异常 |
| 前端 Docker 镜像 | 原 Node 24 / npm 11 / Nginx Dockerfile 构建通过；生产源码 53 份文件散列冻结，镜像为 `sha256:abb1238ce65d16489befe708536c65f500e60706690002fee000af71152900ae` |

测试中修复纯代码回复内容收缩导致复制按钮被遮挡的问题；恢复模型代码复制、原智能体扩展及技能入口的位置，后两者在后端能力接通前禁用。真实 E2E 首次运行发现脚本在刷新后未展开“模型中心”就寻找用量子菜单；补齐真实点击展开后，从空测试库完整重跑通过，台账、取消和注销断言均保留。

本轮日志、审查、参照与实现截图、真实浏览器截图及测试数据库快照保存在 `/home/ja/project/_local_backups/RuoYi-Vue3-FastAPI/20261023-142845-preserve-frontend-ui/verification/`。实际模型调用 4 次，均为本地协议服务；收费供应商和 Safari/iOS 实机仍未验收。独立测试项目 `zhiheng-core-ui-audit` 已清理，正常实例模型与用量数据不来自测试库。

仅替换 `zhiheng-core` 的 web 镜像后，`http://127.0.0.1:19080` 的真实登录、原聊天空态、模型页面、手机导航及退出再次通过。正常实例仍为零模型、零用量；API、worker、PostgreSQL、Redis 及 migrator 的容器身份、镜像和启动时间与更新前一致。旧 `zhiheng` 容器仍全部停止，原 55 项优化逐项 SHA-256 保持一致且未纳入本轮提交。

## 真实容器链路与故障恢复

`zhiheng-core-audit` 使用新环境文件、新 PostgreSQL/Redis 卷和回环端口 `19081`，模型服务仅监听其 Docker bridge 地址。以下 **10 项全部通过**：

1. 浏览器通过真实 Nginx、API、worker、LangChain 和模型 HTTP 完成首条链路。
2. 重复 HTTP 请求复用同一个 Run，只调用和结算一次；同键不同请求返回冲突。
3. 上游暂停时 Nginx 已传出第一段；断开 SSE 后运行继续，重新订阅恢复完整终态快照。
4. 网络暂停期间取消仍生效；公开终态无伪失败，未知用量保留预留。
5. Redis 停止期间仍可持久接入；恢复后 outbox 补发，模型只执行一次。
6. 真实 `SIGKILL` 后失联租约收敛为 interrupted/unknown，不自动重放，也不把失败轮次加入新上下文。
7. 上游拒绝和提前 EOF 分别产生正确失败与用量事实，不泄露供应商私有错误。
8. 经 Nginx 拒绝资源越权、跨来源修改和未知请求参数。
9. 整套容器强制重建后，会话、消息、用量及加密模型凭据均有效，仍可继续调用模型。
10. 重建后注销仍撤销数据库中的会话。

另外以真实独立 PostgreSQL/Redis 补查并发接入、重复投递、Redis 清空重建、provider 准备期间取消及租约边界，**4 项通过**。这些补查脚本和原始结果保存在同一验收目录。

前期基础镜像下载失败已定位为 Docker 守护进程未使用主机代理。本轮通过临时本地转发从官方仓库预取镜像，再以主机代理构建原 Dockerfile。基础镜像和最终镜像身份记录在 `image-identities.json`；未修改全局 Docker 配置。此环境步骤不等同于对其他机器网络条件的保证。

## 审查与回归修复

后端、前端、交付均先做规格审查，再做独立质量审查，必须修复项已关闭。关键修复包括：

- 每次模型调用拥有独立客户端，防止 LangChain 缓存的客户端被提前关闭；同连接连续及并发调用通过真实 HTTP 验证。
- 屏蔽进程环境中的供应商凭据、额外 Header、代理和追踪回退，持久连接配置决定请求；中文和冲突 Header 均有回归。
- 供应商提前 EOF 不记为完成；原始协议缺少计数不会被 LangChain 合成的零值掩盖；不支持的请求参数显式拒绝。
- 失去租约的 attempt 不能提交结果；关闭后的清理 task 有期限且会取消并等待，不遗留后台写入。
- SSE 在注销、会话过期和用户禁用后停止读取；真实 TCP 断开会释放订阅与轮询，后台 Run 不受影响。
- 登录表单按钮/Enter 实际提交；聊天首次发送冻结输入，晚到列表不覆盖新会话，恢复入口不被刷新移除，历史未加载前禁止 Enter 发送。
- 窄屏导航保留可访问名称，模型选择框具备标签；生产页面没有横向溢出。
- 本轮发现 provider 准备期间取消会留下 `inference_failed` 公开错误。最终持久状态为 cancelled 时清空错误；真正 failed/interrupted 的错误保留，用量逻辑不变。新增两个竞争回归先红后绿，原始完整 worker 复现也确认零上游、零扣费；修复经独立规格与质量审查。

## 原有优化保留

归档前的 55 个修改文件与当前 `legacy/backend`、`legacy/frontend` 中对应文件逐项 SHA-256 一致，继续保持未提交。归档的暂存版本来自基准提交，未将这些优化夹带进新平台提交。

仓库外原始备份的 **42,466 个条目**在目录迁移时全部核对保留，包括未跟踪源码、文档、设计素材及原有残留依赖；先前 3 个目录引用更新保持已记录的调整。原始备份目录为 `/home/ja/project/_local_backups/RuoYi-Vue3-FastAPI/20261023-110616-before-directory-migration/`，后续旧材料清理的归档位置见[文档索引](../../README.md)。本轮再次核对原有 55 个修改文件，内容保持一致且继续未提交。

## 实例收尾与验收边界

按用户要求，旧 Compose 项目 `zhiheng` 的 7 个运行容器已停止，保留原容器和数据；旧 `12580` 入口不再提供服务。旧容器身份、挂载和重启策略保存在 `old-containers.json`。

临时 `zhiheng-core-audit` 项目在导出测试数据库快照后清理，独立测试容器、页面 preview 和镜像转发停止。默认新项目 `zhiheng-core` 由另一套空库启动于 `http://127.0.0.1:19080`，管理员显式创建；验收模型和故障数据未带入新实例。

干净新实例另验收真实 API/浏览器登录、退出、空模型列表、零用量和桌面/窄屏导航。登录凭据仅保存在本机私有验收目录的 `new-platform-login.json`，不写入仓库或日志。

未调用收费供应商，未运行远端 CI、生产压测、跨机器恢复、TLS 上线或多节点验收。Python 3.13 矩阵已配置，未在本机执行。当前验收覆盖单 worker 的本机部署，不能据此承诺所有浏览器、供应商或多节点行为。

本轮仅交付文本对话首条链路。知识库、文件、Agent、Work、外部 API Key/模型 API、货币计费、未知用量人工对账界面，以及全平台备份恢复演练仍待后续实施；整体设计中的目标能力不能视为本轮已完成。
