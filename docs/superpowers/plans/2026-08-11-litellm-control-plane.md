# LiteLLM 控制面与模型中心实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `subagent-driven-development`（推荐）或 `executing-plans` 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法跟踪进度；每个任务先使用 `test-driven-development`，完成声明前使用 `verification-before-completion`。

**目标：** 在 RuoYi 中提供统一的 LiteLLM Provider、Credential、Deployment 和运行状态管理界面；Platform 只持有管理凭据的进程配置，不镜像或暴露 LiteLLM 密钥，并通过模型中心完成“Credential -> Deployment -> Platform Model Binding -> Publish”流程。

**架构：** `LiteLLMManagementClient` 使用独立的 `LITELLM_MANAGEMENT_KEY` 调用 LiteLLM 1.95.0 管理 API；`LiteLLMManagementAdapter` 把 LiteLLM 原始结构转换为 Platform VO，并执行 Provider 字段和敏感字段白名单；`ModelEngineService` 暴露 Engine 中立的后台接口；Vue 模型中心由四个标签页和右侧分步抽屉组成。

**技术栈：** FastAPI、Pydantic Settings、HTTPX、Pytest、LiteLLM Proxy 1.95.0 OpenAPI、Vue 3、Element Plus、原生 JavaScript 插件测试

---

## 前置条件与范围

必须先完成 `2026-08-11-model-service-foundation.md`，确保 Model Catalog API、`ModelService` 和 Runtime Engine 已存在。本阶段不实现 Platform Chat，不创建 Tenant、Project、Application、API Key、Quota 或 Usage Ledger。

LiteLLM 1.95.0 已在本机验证管理路由和 OpenAPI schema。本计划只依赖以下已存在接口，不把版本升级或 LiteLLM 数据库 schema 改造混入本任务：

```text
GET    /public/providers/fields
GET    /credentials
POST   /credentials
GET    /credentials/by_name/{credential_name}
PATCH  /credentials/{credential_name}
DELETE /credentials/{credential_name}
GET    /v2/model/info
POST   /model/new
PATCH  /model/{model_id}/update
POST   /model/delete
POST   /health/test_connection
GET    /health/liveliness
```

## 目标文件

- 创建 `ruoyi-fastapi-backend/plugins/ai/config.py` 的管理配置扩展
- 创建 `ruoyi-fastapi-backend/plugins/ai/infrastructure/litellm_management_client.py`
- 创建 `ruoyi-fastapi-backend/plugins/ai/infrastructure/litellm_management_adapter.py`
- 创建 `ruoyi-fastapi-backend/plugins/ai/entity/vo/ai_model_engine_vo.py`
- 创建 `ruoyi-fastapi-backend/plugins/ai/service/model_engine_service.py`
- 创建 `ruoyi-fastapi-backend/plugins/ai/controller/ai_model_engine_controller.py`
- 创建 `ruoyi-fastapi-backend/tests/plugins/ai/test_litellm_management_client.py`
- 创建 `ruoyi-fastapi-backend/tests/plugins/ai/test_model_engine_adapter.py`
- 创建 `ruoyi-fastapi-backend/tests/plugins/ai/test_model_engine_controller.py`
- 修改 `ruoyi-fastapi-backend/plugins/ai/hooks.py`
- 修改 `ruoyi-fastapi-backend/plugins/ai/plugin.yaml`
- 修改 `ruoyi-fastapi-backend/.env.dev`、`.env.prod`、`.env.dockerpg`
- 修改 `docker-compose.pg.yml`、`deploy/litellm/init-platform-runtime-key.sh`、`deploy/litellm/README.md`
- 创建 `ruoyi-fastapi-frontend/plugins/ai/api/modelCatalog.js`
- 创建 `ruoyi-fastapi-frontend/plugins/ai/api/modelEngine.js`
- 创建 `ruoyi-fastapi-frontend/plugins/ai/utils/modelEngine.js`
- 创建 `ruoyi-fastapi-frontend/tests/plugins/modelEngineTransforms.test.js`
- 重写 `ruoyi-fastapi-frontend/plugins/ai/views/model/index.vue`
- 创建模型中心组件目录 `ruoyi-fastapi-frontend/plugins/ai/views/model/components/`

### 任务 1：增加管理配置和独立 Management Client

**文件：**

- 修改：`ruoyi-fastapi-backend/plugins/ai/config.py`
- 修改：`ruoyi-fastapi-backend/tests/plugins/ai/test_litellm_config.py`
- 创建：`ruoyi-fastapi-backend/plugins/ai/infrastructure/litellm_management_client.py`
- 创建：`ruoyi-fastapi-backend/tests/plugins/ai/test_litellm_management_client.py`

- [ ] **步骤 1：先写配置和 HTTP 契约测试**

新增字段必须满足：

```python
settings = LiteLLMSettings(LITELLM_MANAGEMENT_KEY='management-secret')
assert settings.management_key.get_secret_value() == 'management-secret'
assert settings.management_configured is True
```

测试使用 `httpx.MockTransport`，断言每个 Management 请求使用 `Authorization: Bearer management-secret`，绝不使用 Runtime Key。覆盖：

- Provider 字段 GET 不携带 Runtime Key。
- Credential 的 GET/POST/PATCH/DELETE 路径和 JSON body。
- Deployment 的 GET `/v2/model/info`、POST `/model/new`、PATCH `/model/{id}/update`、POST `/model/delete`。
- Test Connection 的 POST body。
- Management Key 为空时写操作 fail-fast，不发生网络请求。
- 401/403 映射为 `LiteLLMManagementAuthenticationError`，超时、连接失败和其他状态分别映射为稳定错误。
- 异常字符串中不出现 Key、请求 JSON、URL 查询或上游正文。

- [ ] **步骤 2：运行测试并确认红灯**

```bash
cd ruoyi-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_litellm_config.py \
  tests/plugins/ai/test_litellm_management_client.py -v
```

- [ ] **步骤 3：实现配置和 Client**

`LiteLLMSettings` 增加 `management_key: SecretStr`，验证别名 `LITELLM_MANAGEMENT_KEY`，增加 `management_configured` 属性。`LiteLLMManagementClient` 使用自己的 `httpx.AsyncClient` 或复用连接工厂，但认证头构造必须与 `LiteLLMRuntimeClient` 分离；客户端保存在 `app.state.ai_litellm_management_client`。

Client 方法签名只接受已规范化的 Mapping，不接受调用方传入 headers、Authorization、任意 URL 或原始凭据。实现以下方法：`list_provider_fields`、`list_credentials`、`create_credential`、`rotate_credential`、`delete_credential`、`list_deployments`、`create_deployment`、`update_deployment`、`delete_deployment`、`test_connection`、`check_liveliness`、`aclose`。

`GET /credentials`、`GET /v2/model/info` 的不稳定响应先作为 `Mapping[str, object]` 返回，由 Adapter 做结构归一化；Client 不把上游对象直接交给 Controller。

- [ ] **步骤 4：扩展 Hook 生命周期**

启动时同时创建 Runtime Client 和 Management Client；Management Key 缺失时仍创建 client 对象但标记未配置，不能阻止 Backend 启动。关闭时两个 client 都必须 `aclose`，测试验证异常关闭路径也不会泄漏连接。

- [ ] **步骤 5：运行回归并提交**

```bash
cd ruoyi-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_litellm_config.py \
  tests/plugins/ai/test_litellm_client.py \
  tests/plugins/ai/test_litellm_management_client.py \
  tests/plugins/ai/test_litellm_hooks.py -v
cd ..
git add ruoyi-fastapi-backend/plugins/ai/config.py \
  ruoyi-fastapi-backend/plugins/ai/hooks.py \
  ruoyi-fastapi-backend/plugins/ai/infrastructure \
  ruoyi-fastapi-backend/tests/plugins/ai/test_litellm_config.py \
  ruoyi-fastapi-backend/tests/plugins/ai/test_litellm_management_client.py
git commit -m "feat: 增加 LiteLLM 管理面客户端"
```

### 任务 2：实现 Provider 字段和安全响应 Adapter

**文件：**

- 创建：`ruoyi-fastapi-backend/plugins/ai/infrastructure/litellm_management_adapter.py`
- 创建：`ruoyi-fastapi-backend/plugins/ai/entity/vo/ai_model_engine_vo.py`
- 创建：`ruoyi-fastapi-backend/tests/plugins/ai/test_model_engine_adapter.py`

- [ ] **步骤 1：先写归一化测试**

用固定 LiteLLM fixture 覆盖：

- Provider 返回 `provider`、`provider_display_name`、`litellm_provider`、`credential_fields`、`default_model_placeholder`，输出只含 Platform 字段：`provider`、`displayName`、`fieldKey`、`label`、`placeholder`、`tooltip`、`required`、`fieldType`、`options`、`defaultValue`。
- `field_type` 仅允许 `text/password/select/upload/textarea`，未知值降级为 `text`；字段缺 key 或 key 不在安全字符集时拒绝。
- Credential 列表从 `data`、`items` 或裸列表归一化为名称、Provider、引用数和时间；`credential_values`、`api_key`、`password`、`token`、`secret` 等字段绝不出现在结果。
- Deployment 从 LiteLLM `model_info` 归一化为部署 ID、别名、Base Model、Provider、状态和更新时间；保留未知字段的测试必须失败，防止任意透传。
- 上游畸形响应转换为脱敏 `LITELLM_MANAGEMENT_UPSTREAM_INVALID`。

- [ ] **步骤 2：运行测试并确认红灯**

```bash
cd ruoyi-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_model_engine_adapter.py -v
```

- [ ] **步骤 3：实现 Platform VO**

在 `ai_model_engine_vo.py` 中定义：

- `ProviderFieldModel`、`ProviderModel`
- `CredentialSummaryModel`、`CredentialCreateModel`、`CredentialRotateModel`
- `DeploymentModel`、`DeploymentCreateModel`、`DeploymentUpdateModel`
- `ConnectionTestRequestModel`、`ConnectionTestResultModel`
- `ModelEngineStatusModel`

所有请求 VO 使用 `extra='forbid'`。Credential Values 只在请求中短暂存在，响应 VO 没有对应字段。Deployment 请求分成 Platform 字段和受控 LiteLLM 参数白名单，不让浏览器提交任意 `litellm_params`。

- [ ] **步骤 4：实现 Adapter 和字段白名单**

Adapter 内定义允许发送给 LiteLLM 的 Provider Credential 键集合，并从动态 Provider fields 中交集筛选；用户输入必须满足字段类型、必填、选项和长度校验。未知 LiteLLM Provider 字段可以展示为“未支持字段”，但不能自动提交。

Credential 名称只允许字母、数字、`.`、`_`、`-`，长度 1-128；删除前由 Service 查询部署引用，不依赖 LiteLLM 删除错误文本。

- [ ] **步骤 5：运行测试并提交**

```bash
cd ruoyi-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_model_engine_adapter.py -v
cd ..
git add ruoyi-fastapi-backend/plugins/ai/entity/vo/ai_model_engine_vo.py \
  ruoyi-fastapi-backend/plugins/ai/infrastructure/litellm_management_adapter.py \
  ruoyi-fastapi-backend/tests/plugins/ai/test_model_engine_adapter.py
git commit -m "feat: 归一化 LiteLLM 控制面数据"
```

### 任务 3：实现 Engine Service 和 Platform 控制面 API

**文件：**

- 创建：`ruoyi-fastapi-backend/plugins/ai/service/model_engine_service.py`
- 创建：`ruoyi-fastapi-backend/plugins/ai/controller/ai_model_engine_controller.py`
- 创建：`ruoyi-fastapi-backend/tests/plugins/ai/test_model_engine_controller.py`
- 修改：`ruoyi-fastapi-backend/plugins/ai/plugin.yaml`
- 修改：`ruoyi-fastapi-backend/tests/plugins/sample_plugins/test_ai_plugin.py`

- [ ] **步骤 1：先写 Service 和路由测试**

测试固定以下 Platform 路由：

```text
GET    /ai/model-engine/providers
GET    /ai/model-engine/credentials
POST   /ai/model-engine/credentials
PUT    /ai/model-engine/credentials/{credential_name}/values
DELETE /ai/model-engine/credentials/{credential_name}
GET    /ai/model-engine/deployments
GET    /ai/model-engine/deployments/{deployment_id}
POST   /ai/model-engine/deployments
PATCH  /ai/model-engine/deployments/{deployment_id}
DELETE /ai/model-engine/deployments/{deployment_id}
POST   /ai/model-engine/test-connection
GET    /ai/model-engine/status
```

每个路由都要求 `PreAuthDependency`；按设计中的权限逐一断言。测试还要证明：

- Management Key 未配置时列表返回安全的 `misconfigured` 状态，写操作返回 `503`。
- Controller 不依赖 `DataScopeDependency`，不把 `sys_dept` 当 Tenant。
- Credential 删除有部署引用时返回 `409`，并包含引用数但不包含密钥。
- 创建 Credential 成功后 Deployment 失败不会删除 Credential。
- 创建 Deployment 成功后 Platform Model 发布失败不会自动删除 Deployment。
- Test Connection 只有 `ai:model:test` 权限，响应只返回成功、延迟、脱敏说明。

- [ ] **步骤 2：运行测试并确认红灯**

```bash
cd ruoyi-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_model_engine_controller.py -v
```

- [ ] **步骤 3：实现 Service**

`ModelEngineService` 注入 Management Client、Adapter、Deployment 引用查询器和时钟。方法包括：Provider fields、Credential CRUD/rotate、Deployment CRUD、连接测试和状态聚合。Service 只把安全请求交给 Adapter，禁止 Controller 直接组装 LiteLLM body。

控制面写操作的本地事务仅用于平台审计和调用前校验；不要假装与 LiteLLM 数据库跨库事务。外部调用成功后才记录成功操作，失败只记录资源 ID、稳定错误码和 Trace ID。

- [ ] **步骤 4：实现 Controller**

使用现有 `APIRouterPro`、`DBSessionDependency`、`CurrentUserDependency`、`ResponseUtil`、`Log`。所有包含 credential values 的请求使用 `RequestLogFieldRoot.JSON_BODY` 排除整个 body，不能只排除一个猜测字段。

状态接口并行检查：Management Key 配置、LiteLLM liveliness、Runtime health、当前 Deployment 数量和最近成功时间；单个检查失败不得掩盖其他检查结果。

- [ ] **步骤 5：增加权限和菜单声明**

在 `plugin.yaml` 增加：

```text
ai:engine:credential:list/add/rotate/remove
ai:engine:deployment:list/query/add/edit/remove
ai:model:test
```

模型中心菜单指向 `plugin/ai/model/index`，保留 Chat 菜单和旧权限兼容；不要注册独立的 Provider 页面菜单，Provider 是模型中心标签页。

- [ ] **步骤 6：运行回归并提交**

```bash
cd ruoyi-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_model_engine_controller.py \
  tests/plugins/ai/test_ai_runtime_controller.py \
  tests/plugins/sample_plugins/test_ai_plugin.py -v
cd ..
git add ruoyi-fastapi-backend/plugins/ai/service/model_engine_service.py \
  ruoyi-fastapi-backend/plugins/ai/controller/ai_model_engine_controller.py \
  ruoyi-fastapi-backend/plugins/ai/plugin.yaml \
  ruoyi-fastapi-backend/tests/plugins/ai/test_model_engine_controller.py \
  ruoyi-fastapi-backend/tests/plugins/sample_plugins/test_ai_plugin.py
git commit -m "feat: 提供 LiteLLM 控制面管理接口"
```

### 任务 4：部署双 Key 和运行配置

**文件：**

- 修改：`ruoyi-fastapi-backend/.env.dev`
- 修改：`ruoyi-fastapi-backend/.env.prod`
- 修改：`ruoyi-fastapi-backend/.env.dockerpg`
- 修改：`docker-compose.pg.yml`
- 修改：`deploy/litellm/init-platform-runtime-key.sh`
- 修改：`deploy/litellm/.env.example`
- 修改：`deploy/litellm/README.md`
- 修改：`ruoyi-fastapi-backend/tests/deploy/test_litellm_runtime.py`

- [ ] **步骤 1：先写部署契约测试**

断言：

- Docker Backend 注入 `LITELLM_RUNTIME_KEY` 和 `LITELLM_MANAGEMENT_KEY`，前端没有任何 Key。
- Backend 仍只加入 `ruoyi-network` 与外部 `litellm-network`。
- Compose 不把 `LITELLM_MASTER_KEY`、数据库密码、Redis 密码注入 Backend。
- 受版本控制模板中的管理 Key 为空或说明文本，实际 `.env` 仍被 `.gitignore` 忽略。
- 初始化脚本可幂等生成 Runtime Key；管理 Key 使用显式环境变量或已存在值，不把 Master Key 自动写入日志。

- [ ] **步骤 2：运行测试并确认红灯**

```bash
cd ruoyi-fastapi-backend
conda run -n dj pytest tests/deploy/test_litellm_runtime.py -v
```

- [ ] **步骤 3：实现配置同步**

扩展根 `.env` 的初始化流程，保留现有 Runtime Key，不覆盖用户提供的 `LITELLM_MANAGEMENT_KEY`。如果管理 Key 缺失，开发环境允许空值以便只查看本地 Model Catalog；Docker 生产启动仍通过 Compose `${LITELLM_MANAGEMENT_KEY:?set ...}` 让运维显式配置。

不修改 `deploy/litellm/.env` 中的 Master Key；不要把任何实际密钥提交到 Git。文档说明首期 Management Key 可以暂时使用具备 Proxy Admin 权限的 Master Key 值，但代码和配置名称仍保持管理凭据语义。

- [ ] **步骤 4：运行部署测试并提交**

```bash
cd ruoyi-fastapi-backend
conda run -n dj pytest tests/deploy/test_litellm_runtime.py -v
cd ..
git diff --check
git add docker-compose.pg.yml deploy/litellm/.env.example \
  deploy/litellm/init-platform-runtime-key.sh deploy/litellm/README.md \
  ruoyi-fastapi-backend/.env.dev ruoyi-fastapi-backend/.env.prod \
  ruoyi-fastapi-backend/.env.dockerpg ruoyi-fastapi-backend/tests/deploy/test_litellm_runtime.py
git commit -m "feat: 配置 LiteLLM 双控制凭据"
```

### 任务 5：实现前端 API、纯转换和四标签模型中心

**文件：**

- 创建：`ruoyi-fastapi-frontend/plugins/ai/api/modelCatalog.js`
- 创建：`ruoyi-fastapi-frontend/plugins/ai/api/modelEngine.js`
- 创建：`ruoyi-fastapi-frontend/plugins/ai/utils/modelEngine.js`
- 创建：`ruoyi-fastapi-frontend/tests/plugins/modelEngineTransforms.test.js`
- 重写：`ruoyi-fastapi-frontend/plugins/ai/views/model/index.vue`
- 创建：`ruoyi-fastapi-frontend/plugins/ai/views/model/components/PlatformModelsPanel.vue`
- 创建：`ruoyi-fastapi-frontend/plugins/ai/views/model/components/EngineDeploymentsPanel.vue`
- 创建：`ruoyi-fastapi-frontend/plugins/ai/views/model/components/CredentialsPanel.vue`
- 创建：`ruoyi-fastapi-frontend/plugins/ai/views/model/components/RuntimeStatusPanel.vue`
- 创建：`ruoyi-fastapi-frontend/plugins/ai/views/model/components/ModelOnboardingDrawer.vue`
- 创建：`ruoyi-fastapi-frontend/plugins/ai/views/model/components/CredentialForm.vue`
- 创建：`ruoyi-fastapi-frontend/plugins/ai/views/model/components/DeploymentForm.vue`

- [ ] **步骤 1：先写纯 JavaScript 转换测试**

测试不依赖 Vue DOM，直接导入 `modelEngine.js`，覆盖：

- LiteLLM Provider field 转 Platform field，未知字段类型和缺失 key 的处理。
- Credential response 脱敏，输入中 `credential_values`、`api_key`、`password`、`token`、`secret` 不出现在输出。
- Deployment response 的 `model_info`、`litellm_params` 到表格行转换。
- Platform Model 状态、Capability、Binding 状态到标签颜色和文案的映射。
- Management Key 未配置和首次加载失败的区别。

示例：

```js
const row = normalizeCredential({ credential_name: 'openai-main', credential_info: { provider: 'openai' }, credential_values: { api_key: 'secret' } })
assert.deepEqual(row, { name: 'openai-main', provider: 'openai', deploymentCount: 0, createdAt: null, updatedAt: null })
assert.equal(JSON.stringify(row).includes('secret'), false)
```

- [ ] **步骤 2：运行测试并确认红灯**

```bash
cd ruoyi-fastapi-frontend
npm run test:plugin
```

预期：新测试导入函数不存在，且现有测试仍可运行。

- [ ] **步骤 3：实现 API 模块**

`modelCatalog.js` 只调用 `/ai/models` 族接口；`modelEngine.js` 只调用 `/ai/model-engine` 族接口。API 函数名称使用 Platform 术语，不暴露 LiteLLM 路径。所有动态 Provider 字段和 Credential Values 通过 POST/PUT body 发送，不放 query string。

- [ ] **步骤 4：实现四标签工作区**

页面默认打开“平台模型”，标签顺序固定为：平台模型、引擎部署、Provider 凭据、运行状态。每个标签拥有自己的 loading、error、empty、refresh 状态，不能用“请求失败”伪装“暂无数据”。

平台模型表格显示：Model ID、Model Code、类型、Capability、Binding Alias、生命周期、更新时间；ID 和 Code 使用等宽字体，长值使用可复制的截断展示。

引擎部署表格显示：LiteLLM Deployment ID、Alias、Provider、Base Model、Blocked、更新时间；仅提供编辑、测试、删除操作，不把 Deployment ID 作为 Platform Model ID。

Credential 表格只显示名称、Provider、引用数、更新时间和轮换/删除操作；创建和轮换表单根据 Provider fields 动态生成，密码字段默认遮罩，不回填旧值。

运行状态显示 Model Service、本地 Database、Runtime Key、Management Key、LiteLLM Runtime 和 LiteLLM Management 的独立状态、延迟和最后检查时间。

- [ ] **步骤 5：实现分步接入抽屉**

`ModelOnboardingDrawer.vue` 维护明确的本地状态机：`credential`、`deployment`、`catalog`、`published`、`failed`。每一步成功后保留资源信息供下一步使用；失败时允许重试，不自动删除外部 Credential/Deployment。连接测试按钮单独显示“可能触发 Provider 请求”的确认提示。

抽屉固定宽度和表格列宽，移动端降为全宽；按钮使用 Element Plus/Lucide 现有图标，图标按钮配置 tooltip；不在界面中打印 Key、credential values 或完整 LiteLLM 响应。

- [ ] **步骤 6：统一字体和状态视觉**

只在 AI 插件作用域增加样式：正文采用 `system-ui, Noto Sans SC, HarmonyOS Sans SC, MiSans, PingFang SC, Microsoft YaHei, sans-serif`；代码和资源 ID 使用 `ui-monospace, SFMono-Regular, Consolas, monospace`。正文不低于 `12px`，状态色使用中性灰配合绿/蓝/黄/红，圆角最多 `8px`，不使用紫色渐变或嵌套卡片。

- [ ] **步骤 7：运行前端测试和构建并提交**

```bash
cd ruoyi-fastapi-frontend
npm run test:plugin
npm run build:docker
cd ..
git diff --check
git add ruoyi-fastapi-frontend/plugins/ai/api \
  ruoyi-fastapi-frontend/plugins/ai/utils/modelEngine.js \
  ruoyi-fastapi-frontend/plugins/ai/views/model \
  ruoyi-fastapi-frontend/tests/plugins/modelEngineTransforms.test.js
git commit -m "feat: 重建 LiteLLM 模型中心工作台"
```

### 任务 6：无付费控制面集成验证

- [ ] **步骤 1：使用 MockTransport 做后端流程集成测试**

增加 `tests/plugins/ai/test_model_engine_integration.py`，按 Credential 创建、Deployment 创建、连接测试、Platform Model 创建/绑定/发布顺序运行 Fake Client；验证外部步骤不是一个数据库事务，且任何步骤失败都保留可重试的前置资源。

- [ ] **步骤 2：启动真实容器并检查健康**

```bash
docker compose --env-file deploy/litellm/.env -f deploy/litellm/compose.yaml ps
docker compose -f docker-compose.pg.yml ps
curl --fail --silent http://127.0.0.1:19099/docs >/dev/null
curl --fail --silent http://127.0.0.1:14000/health/liveliness >/dev/null
```

- [ ] **步骤 3：用未付费测试资源完成一次后台闭环**

优先使用 LiteLLM 的 Mock/虚拟模型或本地假 Provider。只验证管理请求、状态刷新和 Platform Binding，不点击真实 Provider 连接测试，除非用户明确主动操作。

- [ ] **步骤 4：前端回归检查**

执行 `npm run test:plugin` 和 `npm run build:docker`，检查模型中心桌面与窄视口：无文本溢出、无按钮跳动、抽屉不遮挡关键内容、失败和空状态可区分。

- [ ] **步骤 5：提交集成测试并记录结果**

```bash
cd ruoyi-fastapi-backend
conda run -n dj pytest tests/plugins/ai tests/deploy/test_litellm_runtime.py -v
cd ../ruoyi-fastapi-frontend
npm run test:plugin
npm run build:docker
cd ..
git status --short
git diff --check
git add ruoyi-fastapi-backend/tests/plugins/ai/test_model_engine_integration.py
git commit -m "test: 验证 LiteLLM 控制面接入流程"
```

## 完成标准

- RuoYi 后端只通过 Management Client 管理 LiteLLM，前端不直连 LiteLLM。
- Runtime Key 与 Management Key 在配置、HTTP 头、测试和日志中严格分离。
- Provider Credential 永不回显，Deployment 和 Platform Model 的身份边界清晰。
- 模型中心四标签和分步接入抽屉可在无付费测试资源上走通。
- Management Key 缺失、LiteLLM 不可用、畸形响应和外部操作失败都有独立状态，不伪装成功。
- 第二阶段完成后，第三阶段 Chat 只需消费 Platform Model API，不再依赖 LiteLLM 管理细节。
