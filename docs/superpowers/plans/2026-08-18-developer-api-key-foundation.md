# Developer API Key 基础能力实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `subagent-driven-development`（推荐）或 `executing-plans` 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度；每个任务先使用 `test-driven-development`，完成声明前使用 `verification-before-completion`。

**目标：** 建立 Application、Developer API Key、显式模型授权、配额、Rate Card 与 Usage Ledger 的数据库和管理后端，使平台管理员可以安全生成、查看掩码、轮换和撤销应用级 Key，但尚不开放 `/v1/*` 数据面。

**架构：** PostgreSQL/MySQL 保存不可变资源、Key HMAC 摘要和审计事实；`DeveloperKeyCodec` 是唯一可接触完整 Key 的生产组件；Application Service 与 Developer Key Service 采用短事务、条件更新和固定领域错误；Controller 只使用 ZhiHeng JWT、RBAC 和脱敏审计。完整 Key 只在创建或轮换成功响应中出现一次。

**技术栈：** FastAPI、Pydantic v2、SQLAlchemy AsyncSession、PostgreSQL、MySQL、Python `secrets`/`hmac`、Pytest、ZhiHeng Plugin Migration/RBAC

---

## 里程碑边界

本计划只实现设计规格的管理面和数据基础。明确不做：

- 不注册 `/v1/models` 或 `/v1/chat/completions`。
- 不实现 Redis RPM/TPM/并发/预算预留。
- 不调用 LiteLLM Runtime 或 Management API。
- 不创建开发者中心前端页面。
- 不把 Developer Key 同步为 LiteLLM Virtual Key。
- 不把完整 Key 写入数据库、日志、异常、测试快照或浏览器存储。

后续计划按依赖顺序拆分为 Public API 与配额、开发者中心前端、真实环境灰度验收。

## 文件结构

### 新建文件

- `zhiheng-fastapi-backend/plugins/ai/migrations/postgresql/004_developer_api_foundation.sql`：PostgreSQL 七张业务表、约束和索引。
- `zhiheng-fastapi-backend/plugins/ai/migrations/mysql/004_developer_api_foundation.sql`：MySQL 等价迁移。
- `zhiheng-fastapi-backend/plugins/ai/entity/do/ai_developer_api_do.py`：Application、Key、Grant、Quota、Rate Card、Ledger、Bucket ORM Entity。
- `zhiheng-fastapi-backend/plugins/ai/entity/vo/ai_developer_api_vo.py`：严格 camelCase 请求 VO 和脱敏响应 VO。
- `zhiheng-fastapi-backend/plugins/ai/domain/developer_api_errors.py`：不可变固定领域错误。
- `zhiheng-fastapi-backend/plugins/ai/infrastructure/developer_key_codec.py`：Key 生成、掩码、HMAC 摘要和常量时间验证。
- `zhiheng-fastapi-backend/plugins/ai/dao/ai_developer_api_dao.py`：条件读写、行锁、分页和关联替换。
- `zhiheng-fastapi-backend/plugins/ai/service/application_service.py`：Application 生命周期和 owner/admin 可见性。
- `zhiheng-fastapi-backend/plugins/ai/service/developer_key_service.py`：Key、Grant、Quota、轮换和撤销事务。
- `zhiheng-fastapi-backend/plugins/ai/infrastructure/developer_api_provider.py`：从配置和 AsyncSession 组装 Service，不读取 Runtime Client。
- `zhiheng-fastapi-backend/plugins/ai/controller/ai_application_controller.py`：后台管理路由、RBAC 和审计。
- `zhiheng-fastapi-backend/tests/plugins/ai/test_developer_api_schema.py`：双方言 schema 与 Entity 约束。
- `zhiheng-fastapi-backend/tests/plugins/ai/test_developer_key_codec.py`：密码学和脱敏边界。
- `zhiheng-fastapi-backend/tests/plugins/ai/test_application_service.py`：Application Service 事务和权限。
- `zhiheng-fastapi-backend/tests/plugins/ai/test_developer_key_service.py`：Key 生命周期、Grant、Quota 和并发。
- `zhiheng-fastapi-backend/tests/plugins/ai/test_ai_application_controller.py`：路由、权限、一次性 Secret 与审计。

### 修改文件

- `zhiheng-fastapi-backend/plugins/ai/config.py`：Developer API Feature Flag 与版本化 Pepper 配置。
- `zhiheng-fastapi-backend/plugins/ai/domain/resource_id.py`：增加 `app`、`key`、`grt`、`qta`、`rate`、`use`、`qbk` 前缀。
- `zhiheng-fastapi-backend/plugins/ai/plugin.yaml`：版本、004 迁移和 Application/Key 权限。
- `zhiheng-fastapi-backend/tests/plugins/ai/test_litellm_config.py`：新增配置边界回归。
- `zhiheng-fastapi-backend/tests/plugins/ai/test_model_service_contract.py`：资源前缀集合回归。
- `zhiheng-fastapi-backend/tests/plugins/ai/test_model_service_schema.py`：插件版本和完整迁移顺序回归。
- `zhiheng-fastapi-backend/tests/plugins/ai/test_platform_chat_schema.py`：插件版本和完整迁移顺序回归。
- `zhiheng-fastapi-backend/tests/plugins/sample_plugins/test_ai_plugin.py`：迁移和权限 manifest 回归。

## 固定公开契约

### 资源 ID

```text
Application   app_<uuid4 hex>
API Key       key_<uuid4 hex>
Grant         grt_<uuid4 hex>
Quota         qta_<uuid4 hex>
Rate Card     rate_<uuid4 hex>
Usage Ledger  use_<uuid4 hex>
Quota Bucket  qbk_<uuid4 hex>
```

### 管理路由

```text
GET    /ai/applications
POST   /ai/applications
GET    /ai/applications/{application_id}
PATCH  /ai/applications/{application_id}
POST   /ai/applications/{application_id}/disable
GET    /ai/applications/{application_id}/keys
POST   /ai/applications/{application_id}/keys
POST   /ai/applications/{application_id}/keys/{key_id}/rotate
POST   /ai/applications/{application_id}/keys/{key_id}/revoke
```

### Key 格式

```text
sk-zh_<key_id>_<base64url 32-byte secret without padding>
```

`key_prefix` 固定为 `sk-zh_<key_id>`，完整 Key 只在创建和轮换的 `data.apiKey` 中返回一次。

### 固定领域错误

```python
_ERRORS = {
    'application_not_found': (404, 'APPLICATION_NOT_FOUND', '应用不存在'),
    'application_disabled': (409, 'APPLICATION_DISABLED', '应用已停用'),
    'application_code_conflict': (409, 'APPLICATION_CODE_CONFLICT', '应用编码已存在'),
    'api_key_not_found': (404, 'API_KEY_NOT_FOUND', 'API Key 不存在'),
    'api_key_inactive': (409, 'API_KEY_INACTIVE', 'API Key 已失效'),
    'model_grant_invalid': (409, 'MODEL_GRANT_INVALID', '模型授权无效'),
    'quota_conflict': (409, 'QUOTA_VERSION_CONFLICT', '配额已被其他操作修改'),
    'configuration_error': (503, 'DEVELOPER_API_MISCONFIGURED', 'Developer API 尚未完成安全配置'),
    'application_conflict': (409, 'APPLICATION_VERSION_CONFLICT', '应用已被其他操作修改'),
}
```

错误实例必须采用固定工厂、不可继承、不可修改，并且 `public_dict()` 每次返回新字典。

## 任务 1：建立双数据库迁移和 ORM Entity

**文件：**

- 创建：`zhiheng-fastapi-backend/plugins/ai/migrations/postgresql/004_developer_api_foundation.sql`
- 创建：`zhiheng-fastapi-backend/plugins/ai/migrations/mysql/004_developer_api_foundation.sql`
- 创建：`zhiheng-fastapi-backend/plugins/ai/entity/do/ai_developer_api_do.py`
- 创建：`zhiheng-fastapi-backend/tests/plugins/ai/test_developer_api_schema.py`
- 修改：`zhiheng-fastapi-backend/plugins/ai/plugin.yaml`
- 修改：`zhiheng-fastapi-backend/tests/plugins/ai/test_model_service_schema.py`
- 修改：`zhiheng-fastapi-backend/tests/plugins/ai/test_platform_chat_schema.py`
- 修改：`zhiheng-fastapi-backend/tests/plugins/sample_plugins/test_ai_plugin.py`

- [ ] **步骤 1：编写 schema 红灯测试**

测试导入七个 Entity，并在 SQLite metadata、PostgreSQL SQL 和 MySQL SQL 上断言：

```python
EXPECTED_TABLES = {
    'ai_application',
    'ai_api_key',
    'ai_api_key_model_grant',
    'ai_api_quota_policy',
    'ai_model_rate_card',
    'ai_api_usage_ledger',
    'ai_api_quota_bucket',
}

assert EXPECTED_TABLES <= set(Base.metadata.tables)
assert 'secret_digest' in Base.metadata.tables['ai_api_key'].c
assert 'api_key' not in Base.metadata.tables['ai_api_key'].c
assert 'secret' not in Base.metadata.tables['ai_api_key'].c
```

用 `sqlite+aiosqlite:///:memory:` 执行 `metadata.create_all()`，并实际触发状态、正整数、比例、唯一键和外键约束。读取两份 SQL，断言七表、`on delete restrict`、必要 check、唯一键和索引存在。

- [ ] **步骤 2：运行测试确认失败**

```bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_developer_api_schema.py \
  tests/plugins/sample_plugins/test_ai_plugin.py -q
```

预期：`ai_developer_api_do` 不存在或 manifest 缺少 004 迁移。

- [ ] **步骤 3：实现七张表**

Entity 使用现有 `Base`、`JSON().with_variant(JSONB(), 'postgresql')`、`Numeric(20, 10)` 和无时区 `DateTime` 惯例。关键约束：

```python
CheckConstraint("status in ('active', 'disabled')", name='ck_ai_application_status')
CheckConstraint(
    "status in ('active', 'retiring', 'revoked', 'expired')",
    name='ck_ai_api_key_status',
)
CheckConstraint('rpm_limit is null or rpm_limit > 0', name='ck_ai_api_quota_rpm')
CheckConstraint(
    'warning_ratio > 0 and warning_ratio <= 1 and hard_limit_ratio >= warning_ratio',
    name='ck_ai_api_quota_ratios',
)
UniqueConstraint('api_key_id', 'platform_model_id', name='uk_ai_api_key_model_grant')
UniqueConstraint('application_id', 'idempotency_key', name='uk_ai_api_usage_idempotency')
```

`idempotency_key` 可空，双方言必须允许多条 null。Ledger 的 `request_id` 使用 `use_` 资源 ID；另设可空 `idempotency_key`。

- [ ] **步骤 4：登记迁移和插件版本**

`plugin.yaml` 版本从 `0.4.0` 升为 `0.5.0`，在双方言 `003` 后登记 `004_developer_api_foundation.sql`。测试锁定顺序、文件存在和版本。同步更新既有 Model Service 与 Platform Chat schema 测试，使它们断言完整的 001 -> 002 -> 003 -> 004 清单；各自针对 002/003 内容的测试保持不变。

- [ ] **步骤 5：运行测试转绿并提交**

```bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_developer_api_schema.py \
  tests/plugins/sample_plugins/test_ai_plugin.py -q
conda run -n dj ruff check plugins/ai/entity/do/ai_developer_api_do.py \
  tests/plugins/ai/test_developer_api_schema.py
cd ..
git add zhiheng-fastapi-backend/plugins/ai/entity/do/ai_developer_api_do.py \
  zhiheng-fastapi-backend/plugins/ai/migrations \
  zhiheng-fastapi-backend/plugins/ai/plugin.yaml \
  zhiheng-fastapi-backend/tests/plugins/ai/test_developer_api_schema.py \
  zhiheng-fastapi-backend/tests/plugins/ai/test_model_service_schema.py \
  zhiheng-fastapi-backend/tests/plugins/ai/test_platform_chat_schema.py \
  zhiheng-fastapi-backend/tests/plugins/sample_plugins/test_ai_plugin.py
git commit -m "feat: 建立开发者 API 数据基础"
```

## 任务 2：增加资源 ID、配置、VO 和固定领域错误

**文件：**

- 修改：`zhiheng-fastapi-backend/plugins/ai/config.py`
- 修改：`zhiheng-fastapi-backend/plugins/ai/domain/resource_id.py`
- 创建：`zhiheng-fastapi-backend/plugins/ai/domain/developer_api_errors.py`
- 创建：`zhiheng-fastapi-backend/plugins/ai/entity/vo/ai_developer_api_vo.py`
- 修改：`zhiheng-fastapi-backend/tests/plugins/ai/test_litellm_config.py`
- 修改：`zhiheng-fastapi-backend/tests/plugins/ai/test_model_service_contract.py`
- 创建：`zhiheng-fastapi-backend/tests/plugins/ai/test_developer_api_contract.py`

- [ ] **步骤 1：编写契约红灯测试**

覆盖：

```python
for prefix in ('app', 'key', 'grt', 'qta', 'rate', 'use', 'qbk'):
    resource_id = new_resource_id(prefix)
    assert validate_resource_id(resource_id, prefix) == resource_id

settings = LiteLLMSettings(
    AI_PUBLIC_API_ENABLED='true',
    AI_API_KEY_PEPPER_CURRENT_VERSION='v1',
    AI_API_KEY_PEPPER_V1='pepper-with-at-least-32-characters',
)
assert settings.public_api_enabled is True
assert settings.developer_api_configured is True
```

VO 测试必须验证 camelCase、`extra='forbid'`、控制字符预扫、Decimal 字符串、UTC datetime、配额比例、非空唯一 `modelIds`，并证明所有响应模型没有 `secret_digest`、`pepper` 或完整 Key 字段，只有 `DeveloperKeyCreatedModel.api_key` 例外。

- [ ] **步骤 2：运行测试确认失败**

```bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_developer_api_contract.py \
  tests/plugins/ai/test_litellm_config.py \
  tests/plugins/ai/test_model_service_contract.py -q
```

- [ ] **步骤 3：实现配置和类型**

`LiteLLMSettings` 增加：

```python
public_api_enabled: bool = Field(False, validation_alias='AI_PUBLIC_API_ENABLED')
api_key_pepper_current_version: str = Field('', validation_alias='AI_API_KEY_PEPPER_CURRENT_VERSION')
api_key_pepper_v1: SecretStr = Field(SecretStr(''), validation_alias='AI_API_KEY_PEPPER_V1')
api_auth_cache_ttl_seconds: int = Field(15, ge=0, le=15, validation_alias='AI_API_AUTH_CACHE_TTL_SECONDS')
api_max_request_bytes: int = Field(1_048_576, ge=1, le=10_485_760, validation_alias='AI_API_MAX_REQUEST_BYTES')
```

`developer_api_configured` 只在当前版本为 `v1` 且 Pepper 去除 ASCII 空白后至少 32 个字符时为 true。未知版本 fail closed。配置 repr 不得出现 Pepper。

- [ ] **步骤 4：实现 VO 与错误**

请求类型：

```python
class ApplicationCreateModel(_StrictRequestModel):
    application_code: ApplicationCode
    application_name: SafeName

class DeveloperKeyCreateModel(_StrictRequestModel):
    key_name: SafeName
    model_ids: list[ModelResourceId] = Field(min_length=1, max_length=100)
    expires_at: datetime | None = None
    quota: QuotaPolicyWriteModel

class DeveloperKeyRotateModel(_StrictRequestModel):
    grace_period_seconds: int = Field(default=86400, ge=0, le=604800, strict=True)
```

响应类型至少包括 `ApplicationModel`、`DeveloperKeySummaryModel`、`DeveloperKeyCreatedModel`、`ModelGrantModel` 和 `QuotaPolicyModel`。`DeveloperApiError` 仿照 `ModelServiceError` 的固定工厂和不可变实现。

- [ ] **步骤 5：运行测试转绿并提交**

```bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_developer_api_contract.py \
  tests/plugins/ai/test_litellm_config.py \
  tests/plugins/ai/test_model_service_contract.py -q
conda run -n dj ruff check plugins/ai/config.py plugins/ai/domain \
  plugins/ai/entity/vo/ai_developer_api_vo.py tests/plugins/ai/test_developer_api_contract.py
cd ..
git add zhiheng-fastapi-backend/plugins/ai/config.py \
  zhiheng-fastapi-backend/plugins/ai/domain \
  zhiheng-fastapi-backend/plugins/ai/entity/vo/ai_developer_api_vo.py \
  zhiheng-fastapi-backend/tests/plugins/ai/test_litellm_config.py \
  zhiheng-fastapi-backend/tests/plugins/ai/test_model_service_contract.py \
  zhiheng-fastapi-backend/tests/plugins/ai/test_developer_api_contract.py
git commit -m "feat: 定义开发者 API 管理契约"
```

## 任务 3：实现 Key Codec 安全边界

**文件：**

- 创建：`zhiheng-fastapi-backend/plugins/ai/infrastructure/developer_key_codec.py`
- 创建：`zhiheng-fastapi-backend/tests/plugins/ai/test_developer_key_codec.py`

- [ ] **步骤 1：编写密码学红灯测试**

测试固定依赖注入的随机函数，避免对真实 Secret 做 snapshot：

```python
codec = DeveloperKeyCodec(
    current_version='v1',
    peppers={'v1': SecretStr('p' * 32)},
    token_bytes=lambda size: bytes(range(size)),
)
issued = codec.issue('key_00000000000040008000000000000000')
assert issued.complete_key.startswith('sk-zh_key_')
assert issued.digest != issued.complete_key
assert issued.complete_key not in repr(issued)
assert codec.verify(issued.complete_key, version='v1', expected_digest=issued.digest)
```

覆盖 32 字节随机源、base64url 无 padding、掩码、错误 Key 格式、未知 Pepper 版本、digest 长度、常量时间 compare 的调用，以及异常/traceback production locals 不保存完整 Key。

- [ ] **步骤 2：运行测试确认失败**

```bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_developer_key_codec.py -q
```

- [ ] **步骤 3：实现 Codec**

公开签名：

```python
@dataclass(frozen=True)
class IssuedDeveloperKey:
    key_id: str
    prefix: str
    fingerprint: str
    digest: str = field(repr=False)
    digest_version: str
    complete_key: str = field(repr=False)

class DeveloperKeyCodec:
    def issue(self, key_id: str) -> IssuedDeveloperKey: ...
    def verify(self, complete_key: str, *, version: str, expected_digest: str) -> bool: ...
    def mask(self, prefix: str, fingerprint: str) -> str: ...
```

敏感 helper 返回纯结果后退出，最终稳定错误在不持有 Key 的 clean boundary 抛出。禁止自定义 `__str__` 返回 dataclass 字段。

- [ ] **步骤 4：运行测试转绿并提交**

```bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_developer_key_codec.py -q
conda run -n dj ruff check plugins/ai/infrastructure/developer_key_codec.py \
  tests/plugins/ai/test_developer_key_codec.py
cd ..
git add zhiheng-fastapi-backend/plugins/ai/infrastructure/developer_key_codec.py \
  zhiheng-fastapi-backend/tests/plugins/ai/test_developer_key_codec.py
git commit -m "feat: 实现开发者 API Key 安全编码"
```

## 任务 4：实现 DAO 原子读写

**文件：**

- 创建：`zhiheng-fastapi-backend/plugins/ai/dao/ai_developer_api_dao.py`
- 创建：`zhiheng-fastapi-backend/tests/plugins/ai/test_developer_api_dao.py`

- [ ] **步骤 1：编写真实 AsyncSession 红灯测试**

使用 SQLite 双 Session 覆盖分页、唯一冲突和条件更新；编译 PostgreSQL/MySQL SQL 覆盖行锁和谓词：

```python
assert 'FOR UPDATE' in compile_for_postgresql(
    AiDeveloperApiDao.application_for_update_statement(application_id)
)
assert await AiDeveloperApiDao.disable_application_if_active(db, application_id, values) == 1
assert await AiDeveloperApiDao.revoke_key_if_status(db, key_id, {'active', 'retiring'}, values) == 1
assert await AiDeveloperApiDao.replace_grants(db, key_id, model_ids, now=now) == len(model_ids)
```

测试两个并发轮换者只有一个能把旧 Key 从 `active` 改为 `retiring`；Quota 更新使用 `api_key_id + expected_version` CAS；DAO 只 flush，不 commit。

- [ ] **步骤 2：运行测试确认失败**

```bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_developer_api_dao.py -q
```

- [ ] **步骤 3：实现 DAO**

DAO 提供：

- Application create/get/get-for-update/list/update-if-version/disable-if-active。
- Key create/get/get-for-update/list-by-application/update-status-if-current。
- Grant list/replace。
- Quota get/create/update-if-version。
- active Platform Model 批量存在性查询。
- Rate Card 与 Ledger 的基础 create/get 方法，供后续计划复用。

所有动态更新字段使用固定 allowlist，所有状态转换带旧状态谓词，所有 `rowcount == 0` 交给 Service 映射稳定冲突。

- [ ] **步骤 4：运行测试转绿并提交**

```bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_developer_api_dao.py -q
conda run -n dj ruff check plugins/ai/dao/ai_developer_api_dao.py \
  tests/plugins/ai/test_developer_api_dao.py
cd ..
git add zhiheng-fastapi-backend/plugins/ai/dao/ai_developer_api_dao.py \
  zhiheng-fastapi-backend/tests/plugins/ai/test_developer_api_dao.py
git commit -m "feat: 实现开发者 API 原子数据访问"
```

## 任务 5：实现 Application Service

**文件：**

- 创建：`zhiheng-fastapi-backend/plugins/ai/service/application_service.py`
- 创建：`zhiheng-fastapi-backend/tests/plugins/ai/test_application_service.py`

- [ ] **步骤 1：编写 Service 红灯测试**

测试 Fake DAO 和真实 SQLite `expire_on_commit=True`：

- 普通用户只查询自己的 Application；admin 可查询全部。
- 创建固定 `app_` UUID4，code 唯一冲突返回固定错误且不解析 IntegrityError 文本。
- 更新名称使用版本 CAS，不能修改 code、owner、tenant 或 project。
- disable 锁 Application，条件状态更新并 commit。
- 所有写失败路径 rollback；rollback 普通异常不覆盖原异常，CancelledError 不被吞。
- 返回 VO 不依赖 commit 后 ORM lazy load。

调用契约：

```python
service = ApplicationService(db, application_dao=FakeDao())
result = await service.create_application(
    payload,
    owner_user_id=7,
    username='alice',
    now=now,
)
assert result.application_id.startswith('app_')
```

- [ ] **步骤 2：运行测试确认失败**

```bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_application_service.py -q
```

- [ ] **步骤 3：实现 Service**

公开方法：

```python
async def list_applications(query, *, actor_user_id: int, is_admin: bool, is_page: bool = True) -> PageModel
async def get_application(application_id, *, actor_user_id: int, is_admin: bool) -> ApplicationModel
async def create_application(payload, *, owner_user_id: int, username: str, now: datetime) -> ApplicationModel
async def update_application(application_id, payload, *, actor_user_id: int, is_admin: bool, username: str, now: datetime) -> ApplicationModel
async def disable_application(application_id, *, actor_user_id: int, is_admin: bool, username: str, now: datetime) -> ApplicationModel
```

owner/admin 检查只使用 `owner_user_id` 和 `current_user.user.admin`，不使用 `sys_dept`。

- [ ] **步骤 4：运行测试转绿并提交**

```bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_application_service.py -q
conda run -n dj ruff check plugins/ai/service/application_service.py \
  tests/plugins/ai/test_application_service.py
cd ..
git add zhiheng-fastapi-backend/plugins/ai/service/application_service.py \
  zhiheng-fastapi-backend/tests/plugins/ai/test_application_service.py
git commit -m "feat: 实现应用生命周期服务"
```

## 任务 6：实现 Developer Key Service

**文件：**

- 创建：`zhiheng-fastapi-backend/plugins/ai/service/developer_key_service.py`
- 创建：`zhiheng-fastapi-backend/tests/plugins/ai/test_developer_key_service.py`

- [ ] **步骤 1：编写 Key 生命周期红灯测试**

覆盖：

- 未配置 Pepper 时所有生成操作 fail-fast，零数据库写入。
- 创建前锁 Application，要求 active；批量验证所有 modelIds 是 active Platform Model。
- 创建原子写 Key、Grant、Quota，commit 成功后返回一次完整 Key。
- commit 失败不返回 Key，数据库 rollback，异常对象/traceback 不保留完整 Key。
- list 只返回 mask/fingerprint，不返回 digest 或完整 Key。
- rotate 锁 Application 与旧 Key，复制当前 Grant/Quota，创建新 Key，旧 Key 进入 `retiring` 或立即 `revoked`。
- 两个并发 rotate 只有一个成功。
- revoke 幂等且不能改变其他 Application 的 Key。
- owner/admin 边界与 Application Service 一致。

```python
created = await service.create_key(application_id, payload, actor=actor, now=now)
assert created.api_key.startswith('sk-zh_')
summaries = await service.list_keys(application_id, actor=actor)
assert summaries[0].masked_key.endswith(created.fingerprint)
assert created.api_key not in repr(summaries)
```

- [ ] **步骤 2：运行测试确认失败**

```bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_developer_key_service.py -q
```

- [ ] **步骤 3：实现 Service**

公开方法：

```python
async def list_keys(application_id: str, *, actor: DeveloperApiActor) -> list[DeveloperKeySummaryModel]
async def create_key(application_id: str, payload: DeveloperKeyCreateModel, *, actor: DeveloperApiActor, now: datetime) -> DeveloperKeyCreatedModel
async def rotate_key(application_id: str, key_id: str, payload: DeveloperKeyRotateModel, *, actor: DeveloperApiActor, now: datetime) -> DeveloperKeyCreatedModel
async def revoke_key(application_id: str, key_id: str, *, actor: DeveloperApiActor, now: datetime) -> DeveloperKeySummaryModel
```

`DeveloperApiActor` 是 frozen dataclass，只含 `user_id`、`username`、`is_admin`。Service 必须在敏感 helper 退出并删除 issued result 后才从 clean boundary 抛固定领域错误。

- [ ] **步骤 4：运行测试转绿并提交**

```bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_developer_key_service.py -q
conda run -n dj ruff check plugins/ai/service/developer_key_service.py \
  tests/plugins/ai/test_developer_key_service.py
cd ..
git add zhiheng-fastapi-backend/plugins/ai/service/developer_key_service.py \
  zhiheng-fastapi-backend/tests/plugins/ai/test_developer_key_service.py
git commit -m "feat: 实现开发者 API Key 生命周期"
```

## 任务 7：发布后台管理 Controller 和权限

**文件：**

- 创建：`zhiheng-fastapi-backend/plugins/ai/infrastructure/developer_api_provider.py`
- 创建：`zhiheng-fastapi-backend/plugins/ai/controller/ai_application_controller.py`
- 创建：`zhiheng-fastapi-backend/tests/plugins/ai/test_ai_application_controller.py`
- 修改：`zhiheng-fastapi-backend/plugins/ai/plugin.yaml`
- 修改：`zhiheng-fastapi-backend/tests/plugins/sample_plugins/test_ai_plugin.py`

- [ ] **步骤 1：编写 Controller 红灯测试**

静态和 ASGI 测试断言八条路由、HTTP 方法和权限精确匹配：

```text
ai:application:list
ai:application:query
ai:application:add
ai:application:edit
ai:application:disable
ai:application:key:list
ai:application:key:add
ai:application:key:rotate
ai:application:key:revoke
```

所有路由有 PreAuth、无 DataScope。六个写操作使用真实 `@Log`：请求只允许 path Application ID/Key ID，响应只允许 code/msg/resource ID/fingerprint，不允许 `data.apiKey` 进入审计。

ASGI 测试验证：

- 普通用户 owner 边界，admin 跨 owner 管理。
- 创建 Key HTTP 响应包含一次 Secret，但 operation log 不含 Secret、Pepper、digest、modelIds 或 quota body。
- 固定领域错误保留 HTTP status 和公开 body，且失败审计存在。
- Controller 源码不读取 Runtime Client、Management Client、Redis 或 `request.app.state`。

- [ ] **步骤 2：运行测试确认失败**

```bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_ai_application_controller.py \
  tests/plugins/sample_plugins/test_ai_plugin.py -q
```

- [ ] **步骤 3：实现 Provider 与 Controller**

Provider 从 `LiteLLMSettings` 构造 `DeveloperKeyCodec`，只把 `AsyncSession` 和 Codec 注入 Service。Controller 通过 `CurrentUserDependency` 构造 `DeveloperApiActor`，无效 user 返回 401。

Key 创建/轮换的 Log 必须设置：

```python
@Log(
    title='开发者 API Key 管理',
    business_type=BusinessType.INSERT,
    request_log_mode='include',
    request_include_fields=(APPLICATION_ID_REQUEST_FIELD,),
    response_log_mode='include',
    response_include_fields=(
        ResponseLogFieldRoot.CODE.value,
        ResponseLogFieldRoot.MSG.value,
        ResponseLogFieldRoot.DATA.field('key_id'),
        ResponseLogFieldRoot.DATA.field('fingerprint'),
    ),
)
```

- [ ] **步骤 4：登记权限并运行回归**

`plugin.yaml` 增加上述九项权限，不新增菜单；开发者中心菜单留给前端里程碑统一加入。

```bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai/test_ai_application_controller.py \
  tests/plugins/sample_plugins/test_ai_plugin.py -q
conda run -n dj ruff check plugins/ai/controller/ai_application_controller.py \
  plugins/ai/infrastructure/developer_api_provider.py \
  tests/plugins/ai/test_ai_application_controller.py
cd ..
git add zhiheng-fastapi-backend/plugins/ai/controller/ai_application_controller.py \
  zhiheng-fastapi-backend/plugins/ai/infrastructure/developer_api_provider.py \
  zhiheng-fastapi-backend/plugins/ai/plugin.yaml \
  zhiheng-fastapi-backend/tests/plugins/ai/test_ai_application_controller.py \
  zhiheng-fastapi-backend/tests/plugins/sample_plugins/test_ai_plugin.py
git commit -m "feat: 发布应用与 API Key 管理接口"
```

## 任务 8：联合回归和里程碑验收

**文件：**

- 修改：仅修复本计划测试暴露的任务范围文件。

- [ ] **步骤 1：运行 Developer API 定向测试**

```bash
cd zhiheng-fastapi-backend
conda run -n dj pytest \
  tests/plugins/ai/test_developer_api_schema.py \
  tests/plugins/ai/test_developer_api_contract.py \
  tests/plugins/ai/test_developer_key_codec.py \
  tests/plugins/ai/test_developer_api_dao.py \
  tests/plugins/ai/test_application_service.py \
  tests/plugins/ai/test_developer_key_service.py \
  tests/plugins/ai/test_ai_application_controller.py -q
```

预期：全部通过。

- [ ] **步骤 2：运行 AI 插件全量回归**

```bash
cd zhiheng-fastapi-backend
conda run -n dj pytest tests/plugins/ai tests/plugins/sample_plugins/test_ai_plugin.py -q
```

预期：除仓库已有明确 warning 外零失败。

- [ ] **步骤 3：运行静态与格式检查**

```bash
cd zhiheng-fastapi-backend
conda run -n dj ruff check plugins/ai tests/plugins/ai tests/plugins/sample_plugins/test_ai_plugin.py
conda run -n dj ruff format --check plugins/ai tests/plugins/ai tests/plugins/sample_plugins/test_ai_plugin.py
conda run -n dj python -m compileall -q plugins/ai tests/plugins/ai
cd ..
git diff --check
```

- [ ] **步骤 4：安全自检**

```bash
rg -n "secret_digest|api_key_pepper|complete_key|AI_API_KEY_PEPPER" \
  zhiheng-fastapi-backend/plugins/ai/controller \
  zhiheng-fastapi-backend/plugins/ai/entity/vo \
  zhiheng-fastapi-backend/plugins/ai/service
```

逐项确认生产响应除 `DeveloperKeyCreatedModel.api_key` 外没有完整 Key 字段；Log 白名单排除该字段；异常生产帧 locals 测试覆盖创建、轮换、commit 失败和配置失败。

- [ ] **步骤 5：确认 Feature Flag 和路由边界**

```bash
rg -n "prefix=['\"]?/v1|/v1/models|/v1/chat/completions" zhiheng-fastapi-backend/plugins/ai
```

预期：本里程碑未注册 Public API 路由。`AI_PUBLIC_API_ENABLED` 默认 false。

- [ ] **步骤 6：确认提交边界**

运行 `git status --short`，确认不存在任务范围外的已跟踪改动。验证阶段发现的问题必须回到对应任务修复、复测并使用该任务列出的精确路径提交；工作区已干净时不创建空提交。

## 完成定义

- 七张表在 PostgreSQL、MySQL 和 SQLite 测试中具有一致关键约束。
- Application 管理遵循 owner/admin 边界，不使用 DataScope 或 `sys_dept`。
- Key 创建、轮换和撤销可用，完整 Key 只在成功响应中出现一次。
- 数据库只保存 HMAC digest、版本、prefix 和 fingerprint。
- Grant 只引用 active Platform Model ID，不引用 LiteLLM 资源。
- Quota 使用强类型 Decimal、正整数和 CAS 版本。
- 写失败、commit 失败和并发冲突均 rollback，稳定错误不泄露 Secret 或数据库正文。
- Controller、审计、异常、Traceback 和普通响应无完整 Key。
- Public `/v1/*` 尚未开放，下一里程碑可以在该基础上实现认证、配额和 Ledger。
