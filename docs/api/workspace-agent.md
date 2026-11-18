# 工作空间任务 Agent

工作空间围绕目标、资料、计划和成果执行任务。新任务直接选择聊天模型和技能，按当前账号权限默认提供文件处理与代码执行工具。智能助手保留独立的配置与对话入口，工作空间不再选择助手成员接力。

本期不提供知识库检索工具，也不读取助手绑定的知识库。任务使用用户明确加入的文件、所选技能、本任务已登记的成果，以及已配置的公开网页搜索。工作空间接口属于登录 Cookie 控制台 `/api/work`，尚未提供 Bearer Key 的工作空间资源 API。

任务页使用与“对话体验”一致的消息和输入区域。输入框左下方提供“添加附件”和“技能”，技能面板按“预置技能”“我的技能”分组，支持搜索和最多 5 项多选；已选技能在输入框上方显示为可移除标签，已有任务的选择会自动保存。右侧可切换下一轮使用的模型。执行过程在助手消息内折叠展示，文件处理、代码执行和已配置的搜索工具由 Agent 按需调用。右上角“资料与成果”打开文件抽屉，保留上传、预览、下载和历史版本入口，已有成果也可从回复下方直接打开。

## 权限与资源边界

所有操作都需要 `platform:work:enabled` 和查看权限；使用、创建、编辑、删除分别检查 `platform:work:use/create/update/delete`。文件处理另需 `platform:work:execute_code`，勾选模块或授予“使用”不会自动授予代码执行。选择技能还需要 `platform:skills:use`。

每个任务属于创建人，超级管理员也不能凭管理身份读取他人的任务内容。文件仍通过公共文件服务验证当前归属、ACL、状态和 SHA-256。模型目录管理与凭据管理权限不因使用工作空间而开放。

任务受理、模型子调用和每次工具调用都会复核当前权限。执行工具按当前账号权限提供，无任务级代码执行开关。撤销代码执行权限后，普通问答和资料阅读仍可继续，执行工具会被撤下；实际调用还会复核权限。沙箱只在执行工具需要时创建；缺少沙盒时明确拒绝文件执行，不在 API 或 Worker 宿主进程执行代码。

## 配置与任务接口

| 方法与路径 | 行为与主要字段 | 操作权限 |
| --- | --- | --- |
| `GET /api/work/options` | 可选模型、技能、`can_execute_code`、`sandbox_available` | 查看 |
| `GET /api/work/tasks` | `page`、`page_size`、`search`、`status` 分页筛选 | 查看 |
| `POST /api/work/tasks` | `client_request_id`、`model_id`、可选 `goal`、`skill_slugs`、`file_ids` | 新增 |
| `GET /api/work/tasks/{task_id}` | 目标、配置、消息、计划、执行记录、资料和成果 | 查看 |
| `PATCH /api/work/tasks/{task_id}` | `expected_version` 及标题、目标、状态、模型、技能或执行开关 | 编辑 |
| `DELETE /api/work/tasks/{task_id}?expected_version=...` | 删除任务，释放对应资源引用 | 删除 |

`goal` 可以留空以创建草稿；技能最多选五个。配置变更使用版本检查，执行期间不允许修改任务或输入。已受理运行冻结模型绑定、技能修订、输入文件版本、代码开关及运行镜像；后续编辑不改写历史记录。

任务返回 `mode=agent`，`configuration` 为当前配置，`execution_state` 为执行状态。`status` 是任务管理状态 `active/completed/archived`，与一轮执行的状态分别保存。`members` 等历史兼容字段不作为新任务的配置入口。

## 资料与成果

| 方法与路径 | 行为 | 操作权限 |
| --- | --- | --- |
| `POST /api/work/tasks/{task_id}/files?filename=...` | `application/octet-stream` 原始请求体上传并加入任务 | 使用 |
| `DELETE /api/work/tasks/{task_id}/inputs/{file_id}?expected_version=...` | 移除未被运行历史消费的资料 | 编辑 |
| `GET /api/work/tasks/{task_id}/inputs/{file_id}/download` | 下载本任务输入原件 | 查看 |
| `GET /api/work/tasks/{task_id}/artifacts/{artifact_id}/download` | 下载指定成果版本 | 查看 |
| `GET /api/work/tasks/{task_id}/artifacts/{artifact_id}/preview` | 读取已登记预览；无独立预览时按支持格式读取原件 | 查看 |

单次上传不能为空且不超过 20 MiB；每次 `file_ids` 最多 20 个，受理时任务输入总量不超过 100 MiB。同名输入保留不同文件版本，已用于历史的资料不能直接移除。输入挂载为只读，工作目录分为 `inputs/`、`working/` 和 `outputs/`。

正式成果保存文件 ID、逻辑路径、版本、大小、SHA-256、检查结果和预览 ID。同一路径的新内容产生新版本，相同内容复用已有版本；旧版本的下载入口和字节保持。文件登记、来源引用和成果元数据使用同一事务，已提交但响应未知的对象留待核对，不误删。

正式文件和跨轮工作快照均存入平台 MinIO。沙盒临时卷承担本轮执行现场；下一轮从持久快照恢复，Python 内存变量与依赖虚拟环境不跨轮保留。空工作文件通过快照清单恢复，空正式成果明确报告无效。结构检查与 PDF 预览不能替代业务数字和版面验收。

## 执行、观察与取消

| 方法与路径 | 行为 |
| --- | --- |
| `POST /api/work/tasks/{task_id}/runs` | 使用权限；提交 `client_request_id`、`expected_version`、`message`，可附 `file_ids` |
| `GET /api/work/tasks/{task_id}/runs/{run_id}` | 查看权限；读取原生 `RunSnapshot` |
| `GET /api/work/tasks/{task_id}/runs/{run_id}/events` | 查看权限；SSE `snapshot` 事件及心跳，直到终态 |
| `POST /api/work/tasks/{task_id}/runs/{run_id}/cancel` | 使用权限；持久记录取消意图并通知子调用与沙盒 |

创建时填写目标的首轮请求可复用任务的 `create_client_request_id`，`message` 与目标一致，避免重复写入首条消息。网络响应未知时使用原请求编号和相同内容确认，不另建请求。编号相同但内容不同返回 `idempotency_conflict`。

继续暂停任务使用 `resume=true`、空 `message` 和新的请求编号；补充信息或修改要求使用正常新消息。每个用户同时最多一个活动工作空间运行，平台沙盒并发默认最多两个。默认每轮最多 16 次模型调用、24 次工具调用和 600 秒，时限随部署的沙盒配置确定。超预算进入可继续的暂停状态，不无限自动运行。

`execution_state` 包含 `idle/queued/running/waiting_input/paused/completed/failed/cancelled`。刷新或离开页面不取消已受理任务；通过原 `run_id` 恢复观察。`steps` 保存真实模型与工具记录，`plan` 保存任务清单，`artifacts` 保存成果版本。工具结果很长时返回预览和执行编号，Agent 可调用 `read_history` 分页读取。

结果尚未确认的工具步骤标为 `unknown`，不自动重放可能已执行的写入；应先核对记录和已登记成果，再补充处理要求。取消会回收任务环境，但不会把已开始且未报告用量的模型调用计为零。

## 共享执行架构与工具协议

工作空间复用平台 `RunAdmission`、`RunStore`、事务 Outbox、ARQ、权限、模型目录、账本、技能修订和文件服务。父运行类型为 `work_agent`；分析、摘要及最终答复分别使用原生 `work_agent_model` 子运行记录供应商用量。父运行聚合展示，子调用独立准入和结算，不复制一套模型网关或计费。

Agent 使用官方 LangChain `create_agent`、`TodoListMiddleware`、`SummarizationMiddleware` 与 PostgreSQL `work_checkpoints`。工具通过标准 OpenAI 兼容 `tool_calls` / JSON Schema 协议调用，模型服务必须支持这一协议。摘要依据所选模型的有效输入预算自适应计算；任务消息、审计和文件快照独立保留。

| 工具 | 用途 |
| --- | --- |
| `list_inputs`、`inspect_file` | 查看本任务资料和文件结构 |
| `read_document`、`read_sheet` | 有界分页读取文档或表格，返回真实文件来源 |
| `load_skill` | 加载本任务已选技能的固定修订 |
| `write_todos`、`request_input`、`read_history` | 更新计划、请求必要信息、核对执行记录 |
| `run_python`、`execute` | 在隔离环境执行代码与命令，按需声明 Python 索引依赖 |
| `write_file`、`read_file`、`list_files` | 操作任务工作目录 |
| `publish_outputs` | 检查并登记正式成果与预览 |

最后三组文件执行工具按当前账号的 `platform:work:execute_code` 权限提供，无需手动开启。技能正文是处理方法，上传文件是待处理资料，不能改变平台权限和系统规则。工具输出的 NUL 等控制字符用可见 Unicode 转义展示，原文件和快照字节保持原样。

## 内置网页搜索

后台配置 `CORE_BOCHA_API_KEY` 后，工作空间自动提供 `web_search` 工具，模型按任务需要调用。无需用户联网开关或单独权限，继续沿用工作空间的使用权限和任务归属校验；未配置密钥时不注册该工具，其他任务能力照常运行。

工具参数为 `query`、`count`（默认 5，最多 10）和 `freshness`（`noLimit`、`oneDay`、`oneWeek`、`oneMonth`、`oneYear`）。结果包含真实标题、URL、摘要、站点、发布时间及查询时间；摘要不代表已读取网页全文。网页来源链接通过现有回答展示，查询、结果或错误保存在执行记录中。搜索在后端完成，不改变沙盒的网络隔离，也不向模型或前端提供服务密钥。

请求默认超时 15 秒，可通过 `CORE_WEB_SEARCH_TIMEOUT_SECONDS` 配置。博查费用与资源包由该服务商账户承担；平台不把搜索请求折算为模型 Token 或重复扣模型积分。额度不足会明确提示，需补足服务商额度后重试。

## 旧任务与升级

迁移 `0041_work_agent` 增加新任务配置、计划、步骤、成果、沙盒租约及官方检查点 schema。原成员任务保留为 `mode=legacy`，可查看历史和原运行，可按已有删除权限移除；不能继续发送消息、编辑成员或升级为新 Agent。需要继续处理时创建新任务并明确加入所需资料。

当前统一版本为 `0043_workspace_execution`，包含工作空间、助手附件及任务级执行开关清理。已执行 `0041_work_agent` 的实例正常升级即可补齐助手附件；工作步骤、成果版本及检查点保持完整。

旧 `/members` 写入路由及 `/agents/available` 已标为 deprecated，不能作为新工作空间客户端的成员入口。升级仍须备份、显式迁移并统一重建 API/Worker，不在服务启动时自动修改表结构。

## 部署与验证范围

文件执行需要可选的 `workspace` Compose profile、受认证 OpenSandbox 管理服务与办公运行镜像，配置见[工作空间执行环境](../deployment/workspace-sandbox.md)。未部署沙盒时，任务普通问答及有界资料阅读仍可使用。

回归使用测试专用 OpenAI 兼容 HTTP 服务，经过真实 ARQ、原生计量、官方 LangChain/PostgreSQL 检查点和独立 OpenSandbox，验证问候、CSV 制作 Excel/Word、PDF 预览、续写、版本不可变、控制字符和执行权限。本地测试提供方的通过不代表任何真实云端模型已验证；接入部署使用的模型后，还应核对其工具调用协议、用量报告和任务表现。测试入口见[开发指南](../development.md)。
