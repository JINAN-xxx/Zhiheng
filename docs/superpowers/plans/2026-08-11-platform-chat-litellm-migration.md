# Platform Chat 与 Agno 清理实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `subagent-driven-development`（推荐）或 `executing-plans` 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法跟踪进度；每个任务先使用 `test-driven-development`，完成声明前使用 `verification-before-completion`。

**目标：** 将 `/ai/chat` 迁移到 Platform 自有 Session、Message、Run 和 Preferences，通过 `ModelService -> ModelRuntimeEngine -> LiteLLM` 完成普通和流式对话，保留历史、图片、Reasoning、Usage、取消和幂等能力，并在闭环验证后移除活跃 Agno/Provider SDK 依赖。

**架构：** `PlatformChatService` 负责会话归属、历史上下文、Run 终态和事务；`ChatRuntimeCoordinator` 负责 Redis 跨 Worker 锁与取消标记；`LiteLLMRuntimeAdapter` 只产生引擎中立事件；Controller 输出 Platform SSE。前端只选择 Platform Model ID，不解析 LiteLLM 或 Agno 对象。

**技术栈：** FastAPI、SQLAlchemy 2 Async、PostgreSQL 16、MySQL 兼容迁移、Redis asyncio、HTTPX SSE、Pydantic 2、Vue 3、Fetch Streams、Pytest、Node 原生插件测试

---

## 前置条件与范围

必须先完成：

1. 2026-08-11-model-service-foundation.md
2. 2026-08-11-litellm-control-plane.md

数据库已确认 ai_models、ai_chat_config 为空，ai_sessions 尚不存在，因此本阶段不做历史数据转换、不做旧链路双写。旧表保留，迁移只追加新表；旧 Agno 表和旧模型 CRUD 在最后一个任务统一从活跃入口下线。

## 目标文件

- 创建 zhiheng-fastapi-backend/plugins/ai/migrations/mysql/003_platform_chat.sql
- 创建 zhiheng-fastapi-backend/plugins/ai/migrations/postgresql/003_platform_chat.sql
- 创建 zhiheng-fastapi-backend/plugins/ai/entity/do/ai_chat_platform_do.py
- 创建 zhiheng-fastapi-backend/plugins/ai/entity/vo/ai_platform_chat_vo.py
- 创建 zhiheng-fastapi-backend/plugins/ai/dao/platform_chat_dao.py
- 创建 zhiheng-fastapi-backend/plugins/ai/service/chat_preferences_service.py
- 创建 zhiheng-fastapi-backend/plugins/ai/service/platform_chat_service.py
- 创建 zhiheng-fastapi-backend/plugins/ai/service/chat_runtime_coordinator.py
- 创建 zhiheng-fastapi-backend/plugins/ai/infrastructure/platform_sse.py
- 修改 zhiheng-fastapi-backend/plugins/ai/domain/model_runtime.py
- 修改 zhiheng-fastapi-backend/plugins/ai/infrastructure/litellm_runtime_adapter.py
- 重写 zhiheng-fastapi-backend/plugins/ai/controller/ai_chat_controller.py
- 创建后端测试 tests/plugins/ai/test_platform_chat_*.py
- 创建 zhiheng-fastapi-frontend/plugins/ai/utils/sseParser.js
- 修改 zhiheng-fastapi-frontend/plugins/ai/api/chat.js
- 重写 zhiheng-fastapi-frontend/plugins/ai/views/chat/index.vue
- 修改 zhiheng-fastapi-backend/plugins/ai/plugin.yaml、README.md 和 tests/plugins/sample_plugins/test_ai_plugin.py

### 任务 1：建立 Platform Chat 数据库和实体

文件：

- 创建 tests/plugins/ai/test_platform_chat_schema.py
- 创建 plugins/ai/migrations/mysql/003_platform_chat.sql
- 创建 plugins/ai/migrations/postgresql/003_platform_chat.sql
- 创建 plugins/ai/entity/do/ai_chat_platform_do.py
- 修改 plugins/ai/plugin.yaml
- 修改 tests/plugins/sample_plugins/test_ai_plugin.py

- [ ] 步骤 1：先写迁移和实体契约测试

测试必须断言：

- 003 在 002_model_service_foundation.sql 之后注册，SQL 不包含 drop table。
- 新表精确为 ai_chat_session、ai_chat_message、ai_chat_run、ai_chat_preferences。
- Session、Message 和 Run ID 为 varchar(36) 主键；user_id 使用平台现有用户 ID 类型。
- Session/Run 的 model_id 引用 ai_model_catalog.model_id，Run 的 binding_id 引用 ai_resource_binding.binding_id。
- Message 的 (session_id, client_message_id) 唯一；状态字段有数据库检查约束。
- images、usage、trace JSON 字段不接受共享可变默认值；Preference 的 user_id 唯一。
- 旧 ai_models、ai_chat_config 不被新迁移引用或删除。

- [ ] 步骤 2：运行测试并确认红灯

~~~bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_platform_chat_schema.py -v
~~~

- [ ] 步骤 3：实现 PostgreSQL/MySQL 迁移

两份迁移都创建以下字段：

~~~text
ai_chat_session
  session_id varchar(36) primary key
  user_id bigint not null
  model_id varchar(36) not null references ai_model_catalog(model_id) restrict
  title varchar(200) not null default ''
  status varchar(16) not null default 'active'
  created_at timestamp/datetime not null
  updated_at timestamp/datetime not null

ai_chat_message
  message_id varchar(36) primary key
  session_id varchar(36) not null references ai_chat_session(session_id) restrict
  client_message_id varchar(100) not null
  role varchar(16) not null
  content text not null
  reasoning_content text null
  images json/jsonb not null
  status varchar(16) not null
  created_at timestamp/datetime not null
  updated_at timestamp/datetime not null

ai_chat_run
  run_id varchar(36) primary key
  session_id varchar(36) not null references ai_chat_session(session_id) restrict
  model_id varchar(36) not null references ai_model_catalog(model_id) restrict
  binding_id varchar(36) not null references ai_resource_binding(binding_id) restrict
  engine_type varchar(32) not null
  engine_model_snapshot varchar(255) not null
  engine_request_id varchar(255) null
  trace_id varchar(128) null
  status varchar(16) not null
  input_tokens integer null
  output_tokens integer null
  reasoning_tokens integer null
  cache_read_tokens integer null
  cache_write_tokens integer null
  raw_cost decimal(20,10) null
  error_code varchar(64) null
  error_message varchar(500) null
  started_at timestamp/datetime null
  finished_at timestamp/datetime null
  created_at timestamp/datetime not null
  updated_at timestamp/datetime not null

ai_chat_preferences
  preference_id bigint primary key auto increment/identity
  user_id bigint unique not null
  temperature float null
  add_history boolean/char(1) not null default true
  history_message_count integer not null default 20
  system_prompt text null
  metrics_visible boolean/char(1) not null default true
  vision_enabled boolean/char(1) not null default true
  image_max_size_mb integer not null default 10
  created_at timestamp/datetime not null
  updated_at timestamp/datetime not null
~~~

error_message 只保存 Platform 脱敏文案，不保存上游正文；原始 Prompt、图片和输出不在 Run 中重复存储。

- [ ] 步骤 4：实现实体和枚举

在 ai_chat_platform_do.py 定义四个实体和 Python Literal/Enum 常量。JSON 字段使用 SQLAlchemy JSON，时间默认使用 datetime.now。实体关系只用于查询，不配置级联删除；ai_chat_session.model_id 在创建后不允许 Service 更新。

- [ ] 步骤 5：登记迁移并运行测试

~~~bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_platform_chat_schema.py tests/plugins/sample_plugins/test_ai_plugin.py -v
cd ..
git add zhiheng-fastapi-backend/plugins/ai/migrations zhiheng-fastapi-backend/plugins/ai/entity/do/ai_chat_platform_do.py zhiheng-fastapi-backend/plugins/ai/plugin.yaml zhiheng-fastapi-backend/tests/plugins/ai/test_platform_chat_schema.py zhiheng-fastapi-backend/tests/plugins/sample_plugins/test_ai_plugin.py
git commit -m "feat: 建立平台对话数据模型"
~~~

### 任务 2：实现 VO、DAO 和用户偏好

文件：

- 创建 plugins/ai/entity/vo/ai_platform_chat_vo.py
- 创建 plugins/ai/dao/platform_chat_dao.py
- 创建 plugins/ai/service/chat_preferences_service.py
- 创建 tests/plugins/ai/test_platform_chat_dao.py
- 创建 tests/plugins/ai/test_chat_preferences_service.py

- [ ] 步骤 1：先写 DAO/偏好测试

覆盖 Session 按用户分页、Session 模型锁、Message 幂等键、历史数量和 Interrupted 排除、Run 状态条件更新，以及 Preference 默认值/更新/参数校验。使用 sqlite+aiosqlite 内存库和显式 Base.metadata.create_all，只加载四个新实体、ai_model_catalog 与 ai_resource_binding，不连接 Agno。

- [ ] 步骤 2：运行测试并确认红灯

~~~bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_platform_chat_dao.py tests/plugins/ai/test_chat_preferences_service.py -v
~~~

- [ ] 步骤 3：实现严格 VO

定义 PlatformChatRequestModel、PlatformChatPreferenceModel、PlatformSessionModel、PlatformMessageModel、PlatformRunModel、CancelRunRequestModel。请求使用 camelCase alias 和 extra=forbid；资源 ID 必须匹配对应前缀；client_message_id 由前端生成且长度受限。images 只接受 Platform 上传引用，不接受本地路径、file://、外部 URL 或 Data URL 直传。

- [ ] 步骤 4：实现 DAO 和 Preference Service

DAO 不提交事务；Service 负责 commit/rollback。Preference Service 映射旧 ai_chat_config 的产品语义但不读取旧表。system_prompt 和输入参数不写普通日志；审计装饰器排除完整 JSON body。

- [ ] 步骤 5：测试并提交

~~~bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_platform_chat_dao.py tests/plugins/ai/test_chat_preferences_service.py -v
cd ..
git add zhiheng-fastapi-backend/plugins/ai/entity/vo/ai_platform_chat_vo.py zhiheng-fastapi-backend/plugins/ai/dao/platform_chat_dao.py zhiheng-fastapi-backend/plugins/ai/service/chat_preferences_service.py zhiheng-fastapi-backend/tests/plugins/ai/test_platform_chat_dao.py zhiheng-fastapi-backend/tests/plugins/ai/test_chat_preferences_service.py
git commit -m "feat: 实现平台会话持久化与偏好"
~~~

### 任务 3：实现 Redis 跨 Worker 协调器

文件：

- 创建 plugins/ai/service/chat_runtime_coordinator.py
- 创建 tests/plugins/ai/test_chat_runtime_coordinator.py

- [ ] 步骤 1：先写 Fake Redis 测试

测试固定 Session 锁 key ai:chat:lock:{session_id}，使用 SET NX EX；锁释放使用唯一 token 的 Lua compare-and-delete；取消 key 为 ai:chat:cancel:{run_id}，带 TTL；同一 Session 并发返回 CHAT_SESSION_BUSY；Redis 故障返回 CHAT_COORDINATION_UNAVAILABLE 且不执行上游。

- [ ] 步骤 2：运行测试并确认红灯

~~~bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_chat_runtime_coordinator.py -v
~~~

- [ ] 步骤 3：实现协调器

协调器接收 redis: Any，不创建第二个连接池。使用 secrets.token_urlsafe 生成 token；配置锁 TTL 900 秒、取消 TTL 60 秒。acquire_session 返回异步上下文管理器，异常和取消也必须 release。

- [ ] 步骤 4：测试并提交

~~~bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_chat_runtime_coordinator.py -v
cd ..
git add zhiheng-fastapi-backend/plugins/ai/service/chat_runtime_coordinator.py zhiheng-fastapi-backend/tests/plugins/ai/test_chat_runtime_coordinator.py
git commit -m "feat: 增加跨 Worker 对话协调"
~~~

### 任务 4：实现 SSE 解析、图片边界和 Platform Chat Service

文件：

- 创建 plugins/ai/infrastructure/platform_sse.py
- 修改 plugins/ai/infrastructure/litellm_runtime_adapter.py
- 创建 plugins/ai/service/platform_chat_service.py
- 创建 tests/plugins/ai/test_platform_sse_parser.py
- 创建 tests/plugins/ai/test_platform_chat_service.py

- [ ] 步骤 1：先写任意字节分片的 SSE 测试

解析器输入 AsyncIterator[bytes]，测试覆盖 UTF-8 多字节字符跨 chunk、一个 JSON 事件跨多个 chunk、一个 chunk 包含多个事件、event/data 多行、空行边界、注释行、data: [DONE]、无效 JSON 脱敏错误和下游关闭时上游流关闭。不得按 HTTPX chunk 边界直接 json.loads。

示例事件使用标准 SSE：

~~~text
event: content
data: {"delta":"你"}

event: usage
data: {"prompt_tokens":1,"completion_tokens":2}

data: [DONE]
~~~

- [ ] 步骤 2：运行测试并确认红灯

~~~bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_platform_sse_parser.py -v
~~~

- [ ] 步骤 3：实现 Adapter 流式归一化

LiteLLMRuntimeAdapter.stream_chat 通过 platform_sse.py 解析，再归一化为 ModelStreamEvent：content、reasoning、usage、meta、done。请求固定加入 stream_options.include_usage=true；读取 id、usage.prompt_tokens、usage.completion_tokens、completion_tokens_details.reasoning_tokens、缓存字段和 cost。reasoning 优先 delta.reasoning_content，其次 delta.reasoning；未知结构忽略，不保存完整 choice。

- [ ] 步骤 4：实现图片边界转换

PlatformChatService 前置层执行路径规范化、commonpath 边界、当前用户访问、MIME 白名单、大小上限和视觉开关校验，转换为 OpenAI image_url 内容块。只允许 Platform 文件服务的受控 Data URL；不接受宿主机路径、file://、外部 URL 或任意 Base64；日志和异常只保存文件资源 ID、MIME、大小和稳定错误码。

- [ ] 步骤 5：实现 Chat Service 主流程

PlatformChatService.send 顺序固定为：用户/Session 归属 -> active Model 和 Binding -> Session 模型锁 -> client_message_id 幂等 -> Redis 锁 -> 本地创建 Session、Message、pending Run -> 读取偏好/历史 -> ModelService 流式调用 -> 内存累加 -> finally 幂等更新终态并释放锁。Reasoning 不重新发送给模型；SSE 建立前用 HTTP 错误，建立后用 Platform error/done 事件。

Platform SSE 事件为 meta、content、reasoning、usage、done，数据字段使用 sessionId、runId、delta、inputTokens、outputTokens、status。Run 状态只允许 pending -> running -> completed/cancelled/interrupted/failed，重复终态更新为幂等 no-op。

- [ ] 步骤 6：运行测试并提交

~~~bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_platform_sse_parser.py tests/plugins/ai/test_platform_chat_service.py tests/plugins/ai/test_chat_runtime_coordinator.py -v
cd ..
git add zhiheng-fastapi-backend/plugins/ai/infrastructure/platform_sse.py zhiheng-fastapi-backend/plugins/ai/infrastructure/litellm_runtime_adapter.py zhiheng-fastapi-backend/plugins/ai/service/platform_chat_service.py zhiheng-fastapi-backend/tests/plugins/ai/test_platform_sse_parser.py zhiheng-fastapi-backend/tests/plugins/ai/test_platform_chat_service.py
git commit -m "feat: 实现 LiteLLM 平台流式对话服务"
~~~

### 任务 5：实现 Platform Chat Controller 和稳定配置接口

文件：

- 创建或重写 plugins/ai/controller/ai_chat_controller.py
- 创建 tests/plugins/ai/test_platform_chat_controller.py
- 修改 plugins/ai/entity/vo/ai_platform_chat_vo.py

- [ ] 步骤 1：先写路由测试

固定路由为 POST /ai/chat/send、GET/PUT /ai/chat/config、GET /ai/chat/session/list、GET/DELETE /ai/chat/session/{session_id}、POST /ai/chat/cancel。所有接口要求登录和 ai:chat:list；取消校验 Run 归属；send 不再注入 DataScopeDependency、不查询 AiModels；SSE 响应包含 text/event-stream、Cache-Control: no-cache、X-Accel-Buffering: no；Prompt、images、system prompt 不进入日志。

- [ ] 步骤 2：运行测试并确认红灯

~~~bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_platform_chat_controller.py -v
~~~

- [ ] 步骤 3：实现 Controller

Controller 只做依赖注入、VO 解析和 StreamingResponse 构造，业务委托 PlatformChatService。保留现有路径；响应统一 camelCase Platform VO。取消写 Redis 标记，不调用 Agno acancel_run；运行中的 Session 删除返回稳定冲突或标记 archived。

- [ ] 步骤 4：测试并提交

~~~bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_platform_chat_controller.py tests/plugins/ai/test_platform_chat_service.py -v
cd ..
git add zhiheng-fastapi-backend/plugins/ai/controller/ai_chat_controller.py zhiheng-fastapi-backend/plugins/ai/entity/vo/ai_platform_chat_vo.py zhiheng-fastapi-backend/tests/plugins/ai/test_platform_chat_controller.py
git commit -m "feat: 切换平台对话接口"
~~~

### 任务 6：迁移前端真实 SSE、Session 状态和字体体验

文件：

- 创建 zhiheng-fastapi-frontend/plugins/ai/utils/sseParser.js
- 创建 zhiheng-fastapi-frontend/tests/plugins/sseParser.test.js
- 修改 zhiheng-fastapi-frontend/plugins/ai/api/chat.js
- 重写 zhiheng-fastapi-frontend/plugins/ai/views/chat/index.vue
- 修改 zhiheng-fastapi-frontend/plugins/ai/views/chat/components/AiMessage.vue

- [ ] 步骤 1：先写纯 JS SSE Parser 测试

Parser 输入 Uint8Array/ReadableStream，输出 Platform 事件对象。测试覆盖 UTF-8 跨 Fetch chunk、event/data 多行、content/reasoning/usage/meta/done/error 顺序、空事件、注释、尾部 buffer，以及 [DONE] 不吞掉最后 delta。

- [ ] 步骤 2：运行测试并确认红灯

~~~bash
cd zhiheng-fastapi-frontend
npm run test:plugin
~~~

- [ ] 步骤 3：实现 API 和 Parser

保留会话列表、详情、删除、偏好和取消 URL；新增 createChatStream(payload, signal)，只在 API 层拼 Platform URL、Token 和 AbortSignal。Parser 处理标准 SSE，不按换行拆 JSON；非 2xx 先解析 Platform {code,msg} 再抛出稳定 Error。

- [ ] 步骤 4：重写聊天页面状态机

状态至少包含 idle、loading、streaming、completed、cancelled、interrupted、failed。模型选择器只调用 Platform Model 列表，value 使用 modelId，不显示 LiteLLM alias。发送时生成 clientMessageId，meta 写入 sessionId/runId；content、Reasoning、Usage 分开累加；done 更新消息和会话列表；停止生成使用 AbortController 加后端取消，不承诺 Provider 已停止计费。历史只显示 Platform Session/Message 字段，不读取 agentId、agentData、sessionData 或 Provider 信息。

- [ ] 步骤 5：调整字体与可读性

AI 插件样式使用 system-ui、Noto Sans SC、HarmonyOS Sans SC、MiSans、PingFang SC、Microsoft YaHei 回退链；资源 ID 使用 ui-monospace；正文不低于 12px，消息行高不低于 1.6；控件固定尺寸，圆角不超过 8px。

- [ ] 步骤 6：运行前端测试和构建并提交

~~~bash
cd zhiheng-fastapi-frontend
npm run test:plugin
npm run build:docker
cd ..
git diff --check
git add zhiheng-fastapi-frontend/plugins/ai/api/chat.js zhiheng-fastapi-frontend/plugins/ai/utils/sseParser.js zhiheng-fastapi-frontend/plugins/ai/views/chat zhiheng-fastapi-frontend/tests/plugins/sseParser.test.js
git commit -m "feat: 迁移平台调试对话流式协议"
~~~

### 任务 7：Agno 清理、旧入口下线和依赖收口

文件：

- 修改 plugins/ai/plugin.yaml、README.md、tests/plugins/sample_plugins/test_ai_plugin.py
- 删除或移出路由扫描 ai_model_controller.py
- 删除或移出活跃导入 ai_model_service.py、ai_chat_service.py、utils/ai_util.py 及仅供旧链路使用的 DO/VO/DAO
- 修改前端旧模型 API 和菜单使用

- [ ] 步骤 1：先写依赖和活跃入口扫描测试

验证 plugin.yaml Python dependencies 不再包含 agno、Provider SDK 和动态 Provider Factory 依赖；活跃 AI Python 文件不再导入 agno、Provider SDK 或 AiUtil；活跃路由不再包含旧 /ai/model/list、/ai/model/all 和旧 CRUD；新 /ai/models 与 /ai/model-engine/* 保留；新聊天代码不存在 ai_sessions、SessionType.AGENT、acancel_run 或 Agent(。旧迁移和旧空表保留并在 README 标明历史兼容。

~~~python
assert 'agno' not in manifest['dependencies']['python']
assert not active_ai_sources_contain(('from agno', 'import agno', 'AiUtil'))
assert '/ai/model/list' not in route_paths
~~~

- [ ] 步骤 2：运行测试并确认红灯

~~~bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/sample_plugins/test_ai_plugin.py -v
~~~

- [ ] 步骤 3：完成活跃链路清理

确认 Platform Chat Controller、Service 和前端已通过测试后，删除旧导入和路由文件。若旧文件被其他插件引用，改为兼容薄壳但不得进入新聊天链路；优先删除未使用的 Agno Provider Factory 和旧依赖声明。Manifest 菜单只保留模型中心和调试对话，旧模型 CRUD 权限和 README 示例更新为 Platform Model 术语。

- [ ] 步骤 4：检查导入和依赖锁

~~~bash
cd zhiheng-fastapi-backend
rg -n "agno|AiUtil|SessionType|acancel_run|from plugins.ai.service.ai_chat_service|from plugins.ai.entity.do.ai_model_do" plugins/ai
python -m compileall plugins/ai
~~~

预期活跃 Python 文件无输出；历史 SQL 和兼容文档由扫描测试排除。重新生成依赖锁时只修改 AI 插件相关条目。

- [ ] 步骤 5：运行后端/前端回归并提交

~~~bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai tests/plugins/sample_plugins/test_ai_plugin.py -v
cd ../zhiheng-fastapi-frontend
npm run test:plugin
npm run build:docker
cd ..
git diff --check
git add zhiheng-fastapi-backend/plugins/ai zhiheng-fastapi-frontend/plugins/ai zhiheng-fastapi-backend/tests/plugins/ai zhiheng-fastapi-backend/tests/plugins/sample_plugins/test_ai_plugin.py zhiheng-fastapi-frontend/tests/plugins
git commit -m "refactor: 移除活跃对话链路中的 Agno"
~~~

### 任务 8：多 Worker、故障和真实容器端到端验收

- [ ] 步骤 1：验证迁移和容器

~~~bash
docker compose -f docker-compose.pg.yml restart zhiheng-backend-pg
docker compose -f docker-compose.pg.yml ps
docker compose -f docker-compose.pg.yml logs --tail=250 zhiheng-backend-pg
docker exec litellm-db psql -U zhiheng -d zhiheng-fastapi -Atc "select tablename from pg_tables where schemaname='public' and tablename in ('ai_model_catalog','ai_resource_binding','ai_chat_session','ai_chat_message','ai_chat_run','ai_chat_preferences') order by tablename"
~~~

预期新表全部存在、迁移 checksum 无冲突、Backend 健康，旧 ai_sessions 不存在。

- [ ] 步骤 2：执行无付费假模型闭环

用 LiteLLM Mock/虚拟 Provider 创建测试 Deployment，在 Platform UI 创建并发布 Model，绑定 alias，进入调试对话完成普通消息、Reasoning、图片拒绝/允许、Usage、刷新历史、取消和重复 clientMessageId。不调用真实收费 Provider。

- [ ] 步骤 3：验证多 Worker 和断连

用两个 Backend Worker 或两个测试请求模拟同 Session 并发：一个获得 Redis 锁，另一个返回 CHAT_SESSION_BUSY。断开 Fetch 流后确认 LiteLLM HTTPX 响应关闭、Run 标记 interrupted；写取消标记后确认流结束为 cancelled。

- [ ] 步骤 4：验证安全日志和响应

在测试日志中搜索 Runtime Key、Management Key、Provider API Key、Prompt、Base64、图片正文、LiteLLM URL 和上游错误正文，均不得出现；前端响应只允许 Platform ID、脱敏状态和稳定错误码。

- [ ] 步骤 5：执行最终验证

~~~bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai tests/plugins/sample_plugins/test_ai_plugin.py tests/deploy/test_litellm_runtime.py -v
cd ../zhiheng-fastapi-frontend
npm run test:plugin
npm run build:docker
cd ..
git status --short
git diff --check
git log --oneline -12
~~~

确认未提交 .env、.superpowers/、用户架构文档或测试凭据；确认旧空表保留但无活跃 Agno 引用。

## 第三阶段完成标准

- /ai/chat 完全使用 Platform Session/Message/Run/Preferences，模型调用经过 ModelService 和 LiteLLM Adapter。
- SSE 使用标准事件格式并正确处理任意字节分片、Usage、Reasoning、取消和错误终止。
- Session 模型锁、幂等键、Redis 跨 Worker 锁和归属校验均有测试。
- 前端不再解析旧 newline JSON，不再展示 Agno/Provider 技术对象；模型选择只使用 Platform Model ID。
- 活跃 Python 链路不再导入 Agno、Provider SDK、旧 AiUtil 或 Agno ai_sessions；旧空表和迁移按兼容策略保留。
- 真实容器、前端构建、权限、故障降级和无付费测试闭环均有命令输出作为证据。
