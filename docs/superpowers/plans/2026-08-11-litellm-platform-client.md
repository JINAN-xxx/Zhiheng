# Platform LiteLLM 接入层实现计划

> **面向实现者：** 按任务顺序执行；每个任务严格遵循红灯、绿灯、重构、提交。执行前使用 `test-driven-development`，完成前使用 `verification-before-completion`。

**目标：** 在不改变现有 Agno 对话链路和业务表的前提下，为 ZhiHeng AI 插件增加一个使用独立 Runtime Key 的 LiteLLM HTTP 接入层，并提供受权限保护的健康与模型诊断接口。

**架构：** 每个 FastAPI Worker 通过 AI 插件生命周期 Hook 创建一个共享的 `httpx.AsyncClient`，保存到 `app.state.ai_litellm_runtime_client`。业务代码只依赖 `LiteLLMRuntimeClient`，由它统一注入认证和追踪头、映射网络错误并管理流式响应。LiteLLM 与 ZhiHeng 使用独立 Compose，通过外部 `litellm-network` 互通。

**技术栈：** FastAPI、Pydantic Settings、HTTPX、Pytest、Docker Compose、POSIX shell、LiteLLM Proxy v1.95.0

---

## 文件结构

- 创建 `zhiheng-fastapi-backend/plugins/ai/config.py`：AI 插件 LiteLLM 配置和校验。
- 创建 `zhiheng-fastapi-backend/plugins/ai/infrastructure/__init__.py`：基础设施包。
- 创建 `zhiheng-fastapi-backend/plugins/ai/infrastructure/litellm_errors.py`：稳定的内部异常模型。
- 创建 `zhiheng-fastapi-backend/plugins/ai/infrastructure/litellm_client.py`：Runtime HTTP 客户端。
- 创建 `zhiheng-fastapi-backend/plugins/ai/hooks.py`：每 Worker 客户端生命周期。
- 创建 `zhiheng-fastapi-backend/plugins/ai/entity/vo/ai_runtime_vo.py`：诊断接口响应模型。
- 创建 `zhiheng-fastapi-backend/plugins/ai/controller/ai_runtime_controller.py`：健康和模型诊断接口。
- 创建 `deploy/litellm/init-platform-runtime-key.sh`：创建一次性 Runtime Virtual Key 并安全写入仓库根 `.env`。
- 修改 `zhiheng-fastapi-backend/plugins/ai/plugin.yaml`：声明 Hook、权限和 HTTPX 直接依赖。
- 修改 `zhiheng-fastapi-backend/.env.dev`、`.env.prod`、`.env.dockerpg`：增加非敏感配置模板。
- 修改 `docker-compose.pg.yml`：Backend 加入 LiteLLM 网络并只注入 Runtime Key。
- 修改 `deploy/litellm/README.md`：记录 Runtime Key 初始化和双 Compose 启动顺序。
- 创建 `zhiheng-fastapi-backend/tests/plugins/ai/test_litellm_config.py`。
- 创建 `zhiheng-fastapi-backend/tests/plugins/ai/test_litellm_client.py`。
- 创建 `zhiheng-fastapi-backend/tests/plugins/ai/test_litellm_streaming.py`。
- 创建 `zhiheng-fastapi-backend/tests/plugins/ai/test_litellm_hooks.py`。
- 创建 `zhiheng-fastapi-backend/tests/plugins/ai/test_ai_runtime_controller.py`。
- 修改 `zhiheng-fastapi-backend/tests/plugins/sample_plugins/test_ai_plugin.py`。
- 修改 `zhiheng-fastapi-backend/tests/deploy/test_litellm_runtime.py`。

本阶段不修改前端文件、数据库 Migration、`ai_models`、`AiChatService` 或 `AiUtil`。

### 任务 1：定义配置和错误边界

**文件：**

- 创建：`zhiheng-fastapi-backend/tests/plugins/ai/test_litellm_config.py`
- 创建：`zhiheng-fastapi-backend/plugins/ai/config.py`
- 创建：`zhiheng-fastapi-backend/plugins/ai/infrastructure/__init__.py`
- 创建：`zhiheng-fastapi-backend/plugins/ai/infrastructure/litellm_errors.py`

- [ ] **步骤 1：编写配置和错误模型测试**

测试必须覆盖：

```python
def test_litellm_settings_use_safe_local_defaults(monkeypatch):
    clear_litellm_environment(monkeypatch)
    settings = LiteLLMSettings()

    assert settings.base_url == 'http://127.0.0.1:14000'
    assert settings.runtime_key.get_secret_value() == ''
    assert settings.configured is False
    assert settings.connect_timeout_seconds == 5
    assert settings.request_timeout_seconds == 600
    assert settings.verify_ssl is True


@pytest.mark.parametrize(
    'base_url',
    ('localhost:14000', 'ftp://proxy.example.com', 'http:///missing-host'),
)
def test_litellm_settings_reject_invalid_base_url(base_url):
    with pytest.raises(ValidationError):
        LiteLLMSettings(litellm_base_url=base_url)


@pytest.mark.parametrize('field', ('litellm_connect_timeout_seconds', 'litellm_request_timeout_seconds'))
def test_litellm_settings_reject_non_positive_timeouts(field):
    with pytest.raises(ValidationError):
        LiteLLMSettings(**{field: 0})


@pytest.mark.parametrize(
    ('error_type', 'status_code', 'code'),
    (
        (LiteLLMMisconfiguredError, 503, 'LITELLM_MISCONFIGURED'),
        (LiteLLMUnavailableError, 503, 'LITELLM_UNAVAILABLE'),
        (LiteLLMTimeoutError, 504, 'LITELLM_TIMEOUT'),
        (LiteLLMAuthenticationError, 502, 'LITELLM_AUTHENTICATION_FAILED'),
        (LiteLLMUpstreamError, 502, 'LITELLM_UPSTREAM_ERROR'),
    ),
)
def test_litellm_errors_have_stable_public_contract(error_type, status_code, code):
    error = error_type()
    assert error.status_code == status_code
    assert error.code == code
    assert 'secret' not in error.message.lower()
```

- [ ] **步骤 2：运行测试并确认红灯**

```bash
cd zhiheng-fastapi-backend
pytest tests/plugins/ai/test_litellm_config.py -v
```

预期：因 `plugins.ai.config` 和异常模块不存在而在收集阶段失败。

- [ ] **步骤 3：实现 `LiteLLMSettings`**

实现约束：

```python
class LiteLLMSettings(BaseSettings):
    base_url: str = Field('http://127.0.0.1:14000', validation_alias='LITELLM_BASE_URL')
    runtime_key: SecretStr = Field(SecretStr(''), validation_alias='LITELLM_RUNTIME_KEY')
    connect_timeout_seconds: float = Field(5, gt=0, validation_alias='LITELLM_CONNECT_TIMEOUT_SECONDS')
    request_timeout_seconds: float = Field(600, gt=0, validation_alias='LITELLM_REQUEST_TIMEOUT_SECONDS')
    verify_ssl: bool = Field(True, validation_alias='LITELLM_VERIFY_SSL')

    model_config = SettingsConfigDict(extra='ignore', case_sensitive=False)

    @field_validator('base_url')
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip('/')
        parsed = urlparse(normalized)
        if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
            raise ValueError('LiteLLM 基础地址必须是包含主机名的 HTTP(S) URL')
        return normalized

    @property
    def configured(self) -> bool:
        return bool(self.runtime_key.get_secret_value().strip())
```

配置类只读取进程环境，不自行加载或写入 dotenv 文件。

- [ ] **步骤 4：实现稳定内部异常**

创建基类 `LiteLLMError(Exception)`，字段固定为 `status_code`、`code`、`message`。五个子类只暴露脱敏中文消息，`LiteLLMUpstreamError` 统一映射 `502`。异常对象不得保存响应正文、请求体、Authorization 或 URL 中的 Credential。

- [ ] **步骤 5：运行测试并提交**

```bash
cd zhiheng-fastapi-backend
pytest tests/plugins/ai/test_litellm_config.py -v
cd ..
git add zhiheng-fastapi-backend/plugins/ai/config.py \
  zhiheng-fastapi-backend/plugins/ai/infrastructure \
  zhiheng-fastapi-backend/tests/plugins/ai/test_litellm_config.py
git commit -m "feat: 定义 LiteLLM 接入配置与错误边界"
```

### 任务 2：实现非流式 Runtime 客户端

**文件：**

- 创建：`zhiheng-fastapi-backend/tests/plugins/ai/test_litellm_client.py`
- 创建：`zhiheng-fastapi-backend/plugins/ai/infrastructure/litellm_client.py`

- [ ] **步骤 1：先写 HTTP 契约测试**

使用 `httpx.MockTransport` 捕获请求，覆盖：

```python
@pytest.mark.asyncio
async def test_list_models_adds_runtime_auth_and_trace_headers():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == '/v1/models'
        assert request.headers['authorization'] == 'Bearer runtime-secret'
        assert request.headers['request-id'] == 'request-1'
        assert request.headers['trace-id'] == 'trace-1'
        assert request.headers['user-agent'] == 'ZhiHeng-AI-Platform/0.1'
        return httpx.Response(200, json={'object': 'list', 'data': []})

    client = build_client(handler)
    assert await client.list_models() == {'object': 'list', 'data': []}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('method_name', 'path', 'payload'),
    (
        ('create_chat_completion', '/v1/chat/completions', {'model': 'alias', 'messages': []}),
        ('create_embedding', '/v1/embeddings', {'model': 'embedding-alias', 'input': 'hello'}),
    ),
)
async def test_json_methods_forward_openai_compatible_payload(method_name, path, payload):
    ...


@pytest.mark.asyncio
async def test_authenticated_calls_fail_fast_when_runtime_key_missing():
    ...
    with pytest.raises(LiteLLMMisconfiguredError):
        await client.list_models()
    assert transport_was_not_called is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('upstream_status', 'error_type'),
    ((401, LiteLLMAuthenticationError), (403, LiteLLMAuthenticationError), (429, LiteLLMUpstreamError), (500, LiteLLMUpstreamError)),
)
async def test_upstream_status_is_mapped_without_response_body(upstream_status, error_type):
    ...


@pytest.mark.asyncio
async def test_timeout_and_connect_errors_have_distinct_mappings():
    ...
```

额外断言 `check_liveliness()` 请求 `/health/liveliness` 且不要求 Runtime Key，调用方方法不接受任意 headers 参数，因此不能覆盖 Authorization。

- [ ] **步骤 2：运行测试并确认红灯**

```bash
cd zhiheng-fastapi-backend
pytest tests/plugins/ai/test_litellm_client.py -v
```

- [ ] **步骤 3：实现客户端连接池和普通响应**

核心接口：

```python
class LiteLLMRuntimeClient:
    USER_AGENT = 'ZhiHeng-AI-Platform/0.1'

    def __init__(
        self,
        settings: LiteLLMSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=httpx.Timeout(
                settings.request_timeout_seconds,
                connect=settings.connect_timeout_seconds,
            ),
            verify=settings.verify_ssl,
            transport=transport,
        )

    async def check_liveliness(self) -> Mapping[str, object]: ...
    async def list_models(self) -> Mapping[str, object]: ...
    async def create_chat_completion(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...
    async def create_embedding(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...
    async def aclose(self) -> None: ...
```

实现要求：

- 用 `TraceCtx.get_request_id()` 和 `TraceCtx.get_trace_id()` 读取当前上下文，仅非空时发送。
- 认证头只能由 `_build_headers(authenticated=True)` 生成；公开方法不接受调用方 headers。
- `payload` 在发请求前复制，不修改调用方字典。
- 只接受 JSON object；非 object 的成功响应转换为 `LiteLLMUpstreamError`。
- 捕获 `httpx.TimeoutException` 为 `LiteLLMTimeoutError`，捕获其他 `httpx.RequestError` 为 `LiteLLMUnavailableError`。
- `401/403` 为内部认证故障，其他非 2xx 为 `LiteLLMUpstreamError`。
- 不记录请求体、响应体或完整请求头。

- [ ] **步骤 4：验证并提交**

```bash
cd zhiheng-fastapi-backend
pytest tests/plugins/ai/test_litellm_client.py -v
cd ..
git add zhiheng-fastapi-backend/plugins/ai/infrastructure/litellm_client.py \
  zhiheng-fastapi-backend/tests/plugins/ai/test_litellm_client.py
git commit -m "feat: 添加 LiteLLM Runtime HTTP 客户端"
```

### 任务 3：实现可取消的 SSE 流式链路

**文件：**

- 创建：`zhiheng-fastapi-backend/tests/plugins/ai/test_litellm_streaming.py`
- 修改：`zhiheng-fastapi-backend/plugins/ai/infrastructure/litellm_client.py`

- [ ] **步骤 1：编写流式红灯测试**

覆盖三个关键行为：

```python
@pytest.mark.asyncio
async def test_stream_chat_completion_preserves_raw_sse_bytes():
    chunks = [b'data: {"id":"1"}\n\n', b'data: [DONE]\n\n']
    client = build_stream_client(status_code=200, chunks=chunks)

    result = [chunk async for chunk in client.stream_chat_completion({'model': 'alias', 'messages': []})]

    assert result == chunks
    assert captured_json['stream'] is True


@pytest.mark.asyncio
async def test_stream_rejects_upstream_error_before_first_chunk():
    ...
    with pytest.raises(LiteLLMAuthenticationError):
        await anext(stream)


@pytest.mark.asyncio
async def test_closing_downstream_generator_closes_upstream_response():
    ...
    await stream.aclose()
    assert upstream_stream.closed is True
```

再分别模拟流建立时和读取过程中的 `TimeoutException`、`RequestError`，验证错误映射一致。

- [ ] **步骤 2：运行并确认因方法缺失或行为不完整而失败**

```bash
cd zhiheng-fastapi-backend
pytest tests/plugins/ai/test_litellm_streaming.py -v
```

- [ ] **步骤 3：实现原始字节流**

```python
async def stream_chat_completion(
    self,
    payload: Mapping[str, object],
) -> AsyncIterator[bytes]:
    request_payload = dict(payload)
    request_payload['stream'] = True
    try:
        async with self._client.stream(
            'POST',
            '/v1/chat/completions',
            json=request_payload,
            headers=self._build_headers(authenticated=True),
        ) as response:
            self._raise_for_status(response)
            async for chunk in response.aiter_raw():
                yield chunk
    except httpx.TimeoutException as exc:
        raise LiteLLMTimeoutError() from exc
    except httpx.RequestError as exc:
        raise LiteLLMUnavailableError() from exc
```

`async with` 必须覆盖整个迭代周期，确保下游断开和 `GeneratorExit` 都能关闭响应。不能把 SSE 解码成字符串或重新序列化。

- [ ] **步骤 4：验证并提交**

```bash
cd zhiheng-fastapi-backend
pytest tests/plugins/ai/test_litellm_client.py tests/plugins/ai/test_litellm_streaming.py -v
cd ..
git add zhiheng-fastapi-backend/plugins/ai/infrastructure/litellm_client.py \
  zhiheng-fastapi-backend/tests/plugins/ai/test_litellm_streaming.py
git commit -m "feat: 添加 LiteLLM 流式响应边界"
```

### 任务 4：接入 AI 插件生命周期

**文件：**

- 创建：`zhiheng-fastapi-backend/tests/plugins/ai/test_litellm_hooks.py`
- 创建：`zhiheng-fastapi-backend/plugins/ai/hooks.py`
- 修改：`zhiheng-fastapi-backend/plugins/ai/plugin.yaml`
- 修改：`zhiheng-fastapi-backend/tests/plugins/sample_plugins/test_ai_plugin.py`

- [ ] **步骤 1：编写 Hook 和清单红灯测试**

```python
@pytest.mark.asyncio
async def test_startup_creates_worker_client_without_health_call(monkeypatch):
    app = FastAPI()
    context = SimpleNamespace(app=app)
    fake_client = AsyncMock(spec=LiteLLMRuntimeClient)
    monkeypatch.setattr(hooks, 'LiteLLMRuntimeClient', lambda settings: fake_client)

    await hooks.on_startup(context)

    assert app.state.ai_litellm_runtime_client is fake_client
    fake_client.check_liveliness.assert_not_awaited()


@pytest.mark.asyncio
async def test_shutdown_is_idempotent_and_removes_state(monkeypatch):
    ...
    await hooks.on_shutdown(context)
    await hooks.on_shutdown(context)
    fake_client.aclose.assert_awaited_once()
    assert not hasattr(app.state, 'ai_litellm_runtime_client')
```

同步更新模板测试期望：

- 插件版本 `0.2.0`。
- `backend.hooks.onStartup == 'plugins.ai.hooks:on_startup'`。
- `backend.hooks.onShutdown == 'plugins.ai.hooks:on_shutdown'`。
- 权限集合增加 `ai:runtime:query`。
- Python 依赖增加直接依赖 `httpx==0.28.1`，保留全部 Agno 和 Provider SDK。

- [ ] **步骤 2：运行并确认红灯**

```bash
cd zhiheng-fastapi-backend
pytest tests/plugins/ai/test_litellm_hooks.py \
  tests/plugins/sample_plugins/test_ai_plugin.py::test_ai_plugin_template_can_be_discovered \
  tests/plugins/sample_plugins/test_ai_plugin.py::test_ai_plugin_runtime_paths_exist -v
```

- [ ] **步骤 3：实现成对生命周期 Hook**

```python
CLIENT_STATE_NAME = 'ai_litellm_runtime_client'


async def on_startup(context: PluginHookContext) -> None:
    if context.app is None:
        raise RuntimeError('AI 插件启动缺少 FastAPI 应用上下文')
    settings = LiteLLMSettings()
    setattr(context.app.state, CLIENT_STATE_NAME, LiteLLMRuntimeClient(settings))


async def on_shutdown(context: PluginHookContext) -> None:
    if context.app is None:
        return
    client = getattr(context.app.state, CLIENT_STATE_NAME, None)
    if client is None:
        return
    await client.aclose()
    delattr(context.app.state, CLIENT_STATE_NAME)
```

启动 Hook 不调用 LiteLLM，也不因 Runtime Key 为空而失败；非法 URL 仍由配置校验使插件启动失败。

- [ ] **步骤 4：更新清单、验证并提交**

```bash
cd zhiheng-fastapi-backend
pytest tests/plugins/ai/test_litellm_hooks.py tests/plugins/sample_plugins/test_ai_plugin.py -v
cd ..
git add zhiheng-fastapi-backend/plugins/ai/hooks.py \
  zhiheng-fastapi-backend/plugins/ai/plugin.yaml \
  zhiheng-fastapi-backend/tests/plugins/ai/test_litellm_hooks.py \
  zhiheng-fastapi-backend/tests/plugins/sample_plugins/test_ai_plugin.py
git commit -m "feat: 接入 LiteLLM 客户端生命周期"
```

### 任务 5：增加受权限保护的 Runtime 诊断接口

**文件：**

- 创建：`zhiheng-fastapi-backend/tests/plugins/ai/test_ai_runtime_controller.py`
- 创建：`zhiheng-fastapi-backend/plugins/ai/entity/vo/ai_runtime_vo.py`
- 创建：`zhiheng-fastapi-backend/plugins/ai/controller/ai_runtime_controller.py`

- [ ] **步骤 1：编写控制器红灯测试**

直接调用控制器函数测试业务分支，并由插件结构测试验证路由可扫描。覆盖：

```python
@pytest.mark.asyncio
async def test_health_reports_healthy_with_latency_and_timestamp():
    client = FakeClient(configured=True, liveliness={'status': 'ok'})
    response = await get_ai_runtime_health(build_request(client))
    body = json.loads(response.body)
    assert body['data']['status'] == 'healthy'
    assert body['data']['configured'] is True
    assert body['data']['latencyMs'] >= 0
    assert body['data']['checkedAt']


@pytest.mark.asyncio
async def test_health_reports_misconfigured_without_calling_authenticated_api():
    ...


@pytest.mark.asyncio
async def test_health_reports_unavailable_with_sanitized_message():
    ...


@pytest.mark.asyncio
async def test_models_normalizes_litellm_model_list():
    client.list_models.return_value = {
        'object': 'list',
        'data': [{'id': 'chat-default', 'object': 'model', 'owned_by': 'openai'}],
    }
    ...
    assert body['data'] == [{'id': 'chat-default', 'object': 'model', 'ownedBy': 'openai'}]


@pytest.mark.asyncio
async def test_models_maps_internal_error_to_gateway_http_status_without_leaking_detail():
    ...
```

还要断言：路由前缀为 `/ai/runtime`，Router 依赖含 `PreAuthDependency()`；两个 endpoint 都声明 `UserInterfaceAuthDependency('ai:runtime:query')`。

- [ ] **步骤 2：运行测试并确认红灯**

```bash
cd zhiheng-fastapi-backend
pytest tests/plugins/ai/test_ai_runtime_controller.py -v
```

- [ ] **步骤 3：实现响应 VO**

```python
class AiRuntimeHealthModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    status: Literal['healthy', 'misconfigured', 'unavailable']
    configured: bool
    latency_ms: float
    checked_at: datetime
    message: str


class AiRuntimeModelInfo(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    id: str
    object: str = 'model'
    owned_by: str | None = None
```

- [ ] **步骤 4：实现控制器**

路由契约：

```python
ai_runtime_controller = APIRouterPro(
    prefix='/ai/runtime',
    order_num=19,
    tags=['AI管理-Runtime诊断'],
    dependencies=[PreAuthDependency()],
)


@ai_runtime_controller.get(
    '/health',
    response_model=DataResponseModel[AiRuntimeHealthModel],
    dependencies=[UserInterfaceAuthDependency('ai:runtime:query')],
)
async def get_ai_runtime_health(request: Request) -> Response: ...


@ai_runtime_controller.get(
    '/models',
    response_model=DataResponseModel[list[AiRuntimeModelInfo]],
    dependencies=[UserInterfaceAuthDependency('ai:runtime:query')],
)
async def get_ai_runtime_models(request: Request) -> Response: ...
```

行为要求：

- 客户端只能从 `request.app.state.ai_litellm_runtime_client` 获取。
- Health 在 Key 为空时返回成功响应，业务状态为 `misconfigured`；不能把缺配置变成应用启动失败。
- Health 用 `time.perf_counter()` 计时，网络/超时/认证错误都转为 `unavailable`，消息为稳定脱敏文本。
- Models 仅提取 `id`、`object`、`owned_by`，忽略 LiteLLM 的额外内部字段。
- Models 的内部异常使用 `HTTPException(status_code=error.status_code, detail=error.message)`；不把上游正文放入 detail。

- [ ] **步骤 5：验证并提交**

```bash
cd zhiheng-fastapi-backend
pytest tests/plugins/ai/test_ai_runtime_controller.py \
  tests/plugins/sample_plugins/test_ai_plugin.py::test_ai_plugin_structure_check_passes -v
cd ..
git add zhiheng-fastapi-backend/plugins/ai/controller/ai_runtime_controller.py \
  zhiheng-fastapi-backend/plugins/ai/entity/vo/ai_runtime_vo.py \
  zhiheng-fastapi-backend/tests/plugins/ai/test_ai_runtime_controller.py
git commit -m "feat: 添加 LiteLLM Runtime 诊断接口"
```

### 任务 6：打通 Docker 网络和 Runtime Key 初始化

**文件：**

- 修改：`zhiheng-fastapi-backend/tests/deploy/test_litellm_runtime.py`
- 创建：`deploy/litellm/init-platform-runtime-key.sh`
- 修改：`docker-compose.pg.yml`
- 修改：`zhiheng-fastapi-backend/.env.dev`
- 修改：`zhiheng-fastapi-backend/.env.prod`
- 修改：`zhiheng-fastapi-backend/.env.dockerpg`
- 修改：`deploy/litellm/README.md`

- [ ] **步骤 1：扩展部署契约红灯测试**

新增断言：

```python
def test_zhiheng_backend_joins_external_litellm_network_with_runtime_only_credentials():
    compose = load_repository_yaml('docker-compose.pg.yml')
    backend = compose['services']['zhiheng-backend-pg']

    assert set(backend['networks']) == {'zhiheng-network', 'litellm-network'}
    assert compose['networks']['litellm-network'] == {'external': True, 'name': 'litellm-network'}
    assert backend['environment']['LITELLM_BASE_URL'] == 'http://litellm-proxy:4000'
    assert backend['environment']['LITELLM_RUNTIME_KEY'].startswith('${LITELLM_RUNTIME_KEY:')
    assert 'LITELLM_MASTER_KEY' not in backend['environment']
    assert 'litellm-network' not in compose['services']['zhiheng-pg']['networks']
    assert 'litellm-network' not in compose['services']['zhiheng-redis']['networks']


def test_platform_environment_templates_contain_no_runtime_secret():
    ...
    assert values['LITELLM_RUNTIME_KEY'] == ''


def test_runtime_key_script_is_private_idempotent_and_never_prints_key(tmp_path, fake_litellm_server):
    ...
```

脚本测试使用本地假 HTTP 服务返回固定测试 Key，断言：

- POST `/key/generate` 的 JSON 是 `{"key_alias":"platform-runtime","key_type":"llm_api","models":[]}`。
- Authorization 使用测试 Master Key。
- 根环境文件写入 `LITELLM_RUNTIME_KEY=...` 且权限为 `0600`。
- 标准输出和错误输出都不包含 Key。
- 第二次执行不重复调用生成接口、不改变现有 Key。

- [ ] **步骤 2：运行测试并确认红灯**

```bash
cd zhiheng-fastapi-backend
pytest tests/deploy/test_litellm_runtime.py -v
```

- [ ] **步骤 3：实现双网络和非敏感环境模板**

`docker-compose.pg.yml` 中 Backend 增加：

```yaml
environment:
  LITELLM_BASE_URL: http://litellm-proxy:4000
  LITELLM_RUNTIME_KEY: ${LITELLM_RUNTIME_KEY:?set LITELLM_RUNTIME_KEY in repository .env}
networks:
  - zhiheng-network
  - litellm-network
```

网络增加：

```yaml
litellm-network:
  external: true
  name: litellm-network
```

三个 Backend 环境模板加入五个 `LITELLM_*` 配置；`.env.dockerpg` 使用容器 DNS，其余使用 `127.0.0.1:14000`，Runtime Key 均为空。Compose 只把根目录未提交 `.env` 的 Runtime Key 注入 Backend。

- [ ] **步骤 4：实现一次性 Runtime Key 脚本**

脚本接口：

```text
deploy/litellm/init-platform-runtime-key.sh [platform-env-file]
```

默认读取 `deploy/litellm/.env` 中的 `LITELLM_MASTER_KEY` 和 `LITELLM_PORT`，默认写入仓库根 `.env`。使用 `curl --fail-with-body` 调用：

```http
POST http://127.0.0.1:${LITELLM_PORT}/key/generate
Authorization: Bearer <master-key>
Content-Type: application/json

{"key_alias":"platform-runtime","key_type":"llm_api","models":[]}
```

用结构化 JSON 解析取 `key` 字段。写文件前 `umask 077`，已有非空 `LITELLM_RUNTIME_KEY` 时直接退出。无论成功失败都不能输出 Master Key、Runtime Key 或完整响应正文。临时文件必须由 `trap` 清理。

- [ ] **步骤 5：更新运行手册，验证 Compose 渲染并提交**

```bash
cd zhiheng-fastapi-backend
pytest tests/deploy/test_litellm_runtime.py -v
cd ..
docker compose --env-file deploy/litellm/.env -f deploy/litellm/compose.yaml config >/dev/null
LITELLM_RUNTIME_KEY=test-only docker compose -f docker-compose.pg.yml config >/dev/null
git add docker-compose.pg.yml deploy/litellm/init-platform-runtime-key.sh \
  deploy/litellm/README.md zhiheng-fastapi-backend/.env.dev \
  zhiheng-fastapi-backend/.env.prod zhiheng-fastapi-backend/.env.dockerpg \
  zhiheng-fastapi-backend/tests/deploy/test_litellm_runtime.py
git commit -m "feat: 打通平台与 LiteLLM 内部网络"
```

### 任务 7：全量验证和真实空模型联调

**文件：**

- 可能修改：仅限前述文件中的缺陷修复。

- [ ] **步骤 1：运行 AI 插件和部署测试**

```bash
cd zhiheng-fastapi-backend
pytest tests/plugins/ai tests/plugins/sample_plugins/test_ai_plugin.py tests/deploy/test_litellm_runtime.py -v
```

- [ ] **步骤 2：运行后端相关回归测试**

```bash
cd zhiheng-fastapi-backend
pytest tests/plugins/core tests/server/test_plugin_runtime.py -v
```

- [ ] **步骤 3：执行静态检查**

```bash
cd zhiheng-fastapi-backend
ruff check plugins/ai tests/plugins/ai tests/deploy/test_litellm_runtime.py
ruff format --check plugins/ai tests/plugins/ai tests/deploy/test_litellm_runtime.py
```

如仓库环境未安装 Ruff，只报告未执行，不用临时修改项目依赖。

- [ ] **步骤 4：创建真实 `platform-runtime` Key**

```bash
deploy/litellm/init-platform-runtime-key.sh
stat -c '%a %n' .env
git check-ignore .env
```

预期权限 `600`，且 `.env` 被 Git 忽略。禁止在终端输出文件内容。

- [ ] **步骤 5：重建 Backend 并验证内部网络**

```bash
docker compose -f docker-compose.pg.yml up -d --build zhiheng-backend-pg
docker compose -f docker-compose.pg.yml ps
docker exec zhiheng-backend-pg python -c "import urllib.request; print(urllib.request.urlopen('http://litellm-proxy:4000/health/liveliness').status)"
```

预期容器 DNS 健康请求返回 `200`。如果 ZhiHeng PostgreSQL/Redis 尚未启动，先用 Compose 启动其依赖，不停止 LiteLLM Compose。

- [ ] **步骤 6：验证 Runtime Key 的数据面权限**

从仓库根 `.env` 读取 Key 注入临时命令，但不打印值：

```bash
set -a
. ./.env
set +a
curl --fail --silent \
  -H "Authorization: Bearer $LITELLM_RUNTIME_KEY" \
  http://127.0.0.1:14000/v1/models
```

预期为认证成功的空模型列表。不得向真实 Provider 发请求。

- [ ] **步骤 7：验证诊断接口和降级行为**

使用 ZhiHeng 已登录管理员 Token 调用：

```text
GET /dev-api/ai/runtime/health
GET /dev-api/ai/runtime/models
```

预期健康状态为 `healthy`，模型列表为空。随后只在单次测试进程中覆盖一个无效 Runtime Key，验证模型接口返回脱敏 `502`、管理端其他接口仍可用；恢复正确 Key 后重新验证。不要修改或删除真实 Runtime Key。

- [ ] **步骤 8：检查 Secret 和改动边界**

```bash
git status --short
git diff --check
git grep -n 'LITELLM_MASTER_KEY' -- ':!deploy/litellm/*' ':!docs/*'
git ls-files .env deploy/litellm/.env
git diff --name-only HEAD~7..HEAD
```

验收：Backend 代码和 Compose 不持有 Master Key；真实 `.env` 均未被跟踪；改动中无前端文件和数据库 Migration。

- [ ] **步骤 9：最终回归提交**

仅在验证发现并修复问题时创建提交：

```bash
git add <本阶段修复文件>
git commit -m "fix: 完善 LiteLLM 平台接入验证"
```

---

## 规格覆盖自检

- [ ] 普通 Chat、流式 Chat、Embedding 和模型列表都有客户端契约测试。
- [ ] Runtime Key 与 Master Key 分离，Backend 仅持有 Runtime Key。
- [ ] Request ID、Trace ID 和 User-Agent 自动传递。
- [ ] 日志测试确认不包含 Credential、Prompt 或响应正文。
- [ ] 缺 Key、LiteLLM 离线和超时不会阻止主应用启动。
- [ ] 诊断接口具备 `ai:runtime:query` 权限并返回脱敏状态。
- [ ] Docker Backend 只额外加入 `litellm-network`，两个数据库和 Redis 网络边界不变。
- [ ] Agno、Provider SDK、现有 AI 对话与数据表保持原状。

## 类型与边界自检

- [ ] Pydantic 字段内部使用 snake_case，外部 JSON 使用 camelCase。
- [ ] LiteLLM JSON 以 `Mapping[str, object]` 进入客户端，诊断边界再规范化为 VO。
- [ ] HTTPX 客户端由 Hook 独占生命周期，没有模块级可变客户端。
- [ ] SSE 使用 `AsyncIterator[bytes]`，没有字符串解码或事件重组。
- [ ] 内部异常不携带响应正文、请求体或 Secret。
