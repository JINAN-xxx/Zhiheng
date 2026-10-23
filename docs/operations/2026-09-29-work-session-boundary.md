# Work 状态与会话清理模块化记录

日期：2026-09-29。工作区：`/home/ja/project/zhiheng`，分支：`fix/platform-integration-review`。前序命名、部署修复和三个模块边界试点均保留。

## 已完成的边界

Work 的实现迁至 [插件 store](../../frontend/plugins/agent_extension/stores/workTask.js)，仍使用原 Pinia ID。原宿主 store 文件仅转发到 [Work 公共入口](../../frontend/plugins/agent_extension/public/workTasks.js)，旧导入与新导入取得同一实例。

侧栏使用 `useWorkTaskNavigation`，只读取任务摘要、分页、查询条件、加载与错误状态，并调用列表刷新、重命名和删除。Work 页面使用模块自己的 store，执行、成员、草稿、重试和轮询逻辑仍归模块管理。此次迁移没有改变任务接口地址、消息格式或权限能力规则。

[会话生命周期插件](../../frontend/src/store/sessionLifecycle.js) 由平台 Pinia 安装，各模块通过 `sessionCleanup` 指定同步清理动作。清理仅作用于调用者所在的 Pinia、只涉及已创建的 store；store dispose 时注销。单个清理动作失败不会阻止其他模块，也不会阻止用户令牌被清除。用户模块已不再直接导入 Work 的专用清理函数。

Work 清理会使旧请求代次失效，停止本地流与轮询，清空任务、智能体目录和草稿。回归还发现原 reset 遗漏列表/详情的加载与错误标记及分页；已补齐，避免退出后仍显示旧的“加载中”状态。

原退出和 401 路径继续调用 `clearSession`，成功登录也先清理旧模块状态，再保存新令牌。失败的登录 action 不主动清理原会话。该生命周期机制目前仅由 Work 登记业务清理动作，不代表聊天及所有其他 store 已全部接入。

## 迟到 401 的既有竞态修正

独立审查使用真实 Axios 和 Pinia 复现：旧账号请求发出后登录新账号，旧请求随后返回 HTTP 401 或业务 `code=401`，原拦截器会清空新账号令牌和 Work 状态。它发生在 Work 的请求代次检查之前；本轮开始时已有该问题。

[共享请求处理](../../frontend/src/utils/request.js) 现在将响应关联的请求配置传入过期处理，仅当请求携带的 Authorization 与当前令牌匹配时才清理并跳转。旧身份或匿名请求仍然失败，但不会影响当前身份。当前身份两类 401 的清理、错误与跳转行为保留。

此修正限定于 401 的会话清理副作用，不代表全部异步认证操作均已有会话代次保护。新增测试使用真实 Axios、生产过期处理/响应拦截器和真实 Pinia user/Work；认证令牌、网络及外围 UI 依赖使用测试替身。

## 自动检查

三个 GitHub Actions 工作流现在匹配 `master` 与 `release/**` 的推送、以这些分支为目标的 PR。后端安装及缓存接入 [requirements-test.txt](../../backend/requirements-test.txt)，显式固定 pytest、pytest-asyncio、aiosqlite、httpx、fakeredis 的 Lua 支持和 PDF/DOCX 测试依赖。

前端原 `test:plugin` 会发现新增生命周期、边界和请求身份测试。现有 Work 桌面/移动浏览器脚本也已接入 Playwright 工作流，复用临时开发服务，并在成功或失败后上传截图。

## 验证证据与限制

| 验证 | 结果 |
| --- | --- |
| 新测试的失败验证 | 公共入口/生命周期缺失、reset 遗留加载标记、旧 401 清空新令牌均先观察到预期失败 |
| 前端 Node 回归 | `npm run test:plugin`：51 个文件通过，含 9/9 个 Node 子测试 |
| 生命周期与 Work 测试 | 使用真实 Pinia，覆盖实例隔离、懒加载、dispose、重复清理、单模块故障、迟到列表/详情/创建/目录、流和轮询停止、新会话状态保护及兼容入口 |
| 隔离前端构建 | 临时副本 `npm run build:docker` 退出码 0 |
| Work 浏览器验收 | 桌面与 390px 移动端通过：模式切换、创建失败恢复、两名智能体执行、历史恢复、侧栏导航及操作布局 |
| 最终平台浏览器回归 | 带权限落地诊断的完整两轮通过；截图差异最大为 19 像素、通道差值 7，在原有 32/8 限制内 |
| 运行构建保护 | 原仓库 dist 的 1,460 个文件 SHA-256 前后相同；本轮临时服务验收后停止 |
| CI 配置 | YAML、master/release 正匹配与 feature 负匹配、shell 语法检查通过 |
| 测试依赖 | Python 3.10–3.13 二进制依赖解析成功；Python 3.12 独立测试依赖安装、pip check、Lua EVAL、PDF/DOCX 实际读写通过 |
| 后端相关依赖回归 | 配额协调、聊天文件适配、知识解析共 105 项通过 |
| 独立审查 | 模块迁移、Pinia 安装时机、会话清理、导航响应性、迟到 401 修正与 CI 接入通过只读复核 |
| 差异检查 | `git diff --check` 通过 |

平台浏览器回归期间两次在权限落地处超时，分别为“应用管理”和无业务权限落地；专项连续复验及最终带诊断的完整两轮检查通过，没有放宽断言或增加超时。独立核对未发现场景间共享认证/mock 状态，也没有证据指向本轮 Pinia 改动；但偶发超时的根因尚未确定，仍作为测试稳定性限制记录，不能声称已修复。

前端单独执行：

```bash
npm run test:plugin
CHAT_WORK_CHECK_BASE_URL=http://127.0.0.1:46708 python3 -u tests/e2e/chat_work_mode_check.py
PLATFORM_SHELL_BASE_URL=http://127.0.0.1:46708 python3 -u tests/e2e/platform_shell_check.py
```

上述地址是本轮临时 Vite 服务；自行运行时应替换为自己的测试服务。构建在 `/tmp/zhiheng-work-boundary-kjuok0i6/frontend` 执行，未覆盖仓库中供现有服务使用的 dist。前态源码、构建哈希、依赖解析和验证日志在 `/tmp/zhiheng-work-boundary-kjuok0i6`；该目录不是长期发布产物。

105 项后端依赖回归运行于 Python 3.12，命令选择 `test_public_invocation_coordinator.py`、`test_chat_file_adapter.py` 与 `test_knowledge_parsing.py`。实际使用临时 venv 的测试包和已有临时平台依赖，未完成全部平台/AI 依赖的全新安装；其他 Python 版本只完成 wheel 解析，没有执行全矩阵测试。

浏览器测试使用模拟 API；401 身份隔离通过真实 Axios/Pinia 在 Node 中验证，未新增浏览器 401 端到端验收。本轮没有测试真实业务数据库或模型上游。配置尚未推送，不能据此声称云端 CI 已通过；旧后端 Ruff 问题也不在本轮修复范围。

## 后续范围

前端的技能/模型/应用公共客户端、共享 SSE 解析，后端的应用查询接口、模型删除引用规则和费率归属仍可继续收拢。模块化单体仍整体构建和发布。

改动保留在本地工作区，未提交、推送或部署。完整状态见[模块边界梳理](../architecture/2026-09-29-module-boundaries.md)。
