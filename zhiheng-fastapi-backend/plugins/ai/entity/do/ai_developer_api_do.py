from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)

from config.database import Base


class AiApplication(Base):
    """Developer API 调用方应用。"""

    __tablename__ = 'ai_application'
    __table_args__ = (
        UniqueConstraint('application_code', name='uk_ai_application_code'),
        CheckConstraint("status in ('active', 'disabled')", name='ck_ai_application_status'),
        CheckConstraint('version > 0', name='ck_ai_application_version'),
        Index('idx_ai_application_owner_status', 'owner_user_id', 'status'),
        Index('idx_ai_application_deleted_at', 'deleted_at'),
        {'comment': 'Developer API应用表'},
    )

    application_id = Column(String(36), primary_key=True, nullable=False, comment='Application ID')
    application_code = Column(String(100), nullable=False, comment='应用编码')
    application_name = Column(String(100), nullable=False, comment='应用名称')
    owner_user_id = Column(BigInteger, nullable=False, comment='应用所有者用户ID')
    tenant_id = Column(String(36), nullable=True, comment='预留Tenant ID')
    project_id = Column(String(36), nullable=True, comment='预留Project ID')
    status = Column(String(16), nullable=False, default='active', server_default='active', comment='应用状态')
    version = Column(Integer, nullable=False, default=1, server_default=text('1'), comment='乐观锁版本')
    disabled_at = Column(DateTime, nullable=True, comment='停用时间')
    deleted_at = Column(DateTime, nullable=True, comment='软删除时间')
    created_by = Column(String(64), nullable=False, server_default='', comment='创建者')
    created_at = Column(DateTime, nullable=False, default=datetime.now, comment='创建时间')
    updated_by = Column(String(64), nullable=False, server_default='', comment='更新者')
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class AiApiKey(Base):
    """只保存摘要的Developer API Key。"""

    __tablename__ = 'ai_api_key'
    __table_args__ = (
        UniqueConstraint('key_prefix', name='uk_ai_api_key_prefix'),
        CheckConstraint(
            "status in ('active', 'retiring', 'revoked', 'expired')",
            name='ck_ai_api_key_status',
        ),
        Index('idx_ai_api_key_application_status', 'application_id', 'status'),
        {'comment': 'Developer API Key表'},
    )

    api_key_id = Column(String(36), primary_key=True, nullable=False, comment='API Key ID')
    application_id = Column(
        String(36),
        ForeignKey('ai_application.application_id', ondelete='RESTRICT'),
        nullable=False,
        comment='Application ID',
    )
    key_name = Column(String(100), nullable=False, comment='Key名称')
    key_prefix = Column(String(64), nullable=False, comment='非敏感Key前缀')
    secret_digest = Column(String(64), nullable=False, comment='HMAC-SHA256摘要')
    digest_version = Column(String(32), nullable=False, comment='Pepper版本')
    fingerprint = Column(String(16), nullable=False, comment='末尾指纹')
    status = Column(String(16), nullable=False, default='active', server_default='active', comment='Key状态')
    expires_at = Column(DateTime, nullable=True, comment='过期时间')
    retire_at = Column(DateTime, nullable=True, comment='轮换宽限结束时间')
    last_used_at = Column(DateTime, nullable=True, comment='最近使用时间')
    revoked_at = Column(DateTime, nullable=True, comment='撤销时间')
    created_by = Column(String(64), nullable=False, server_default='', comment='创建者')
    created_at = Column(DateTime, nullable=False, default=datetime.now, comment='创建时间')
    updated_by = Column(String(64), nullable=False, server_default='', comment='更新者')
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class AiApiKeyModelGrant(Base):
    """Developer API Key的Platform Model授权。"""

    __tablename__ = 'ai_api_key_model_grant'
    __table_args__ = (
        UniqueConstraint('api_key_id', 'platform_model_id', name='uk_ai_api_key_model_grant'),
        CheckConstraint("status in ('active', 'disabled')", name='ck_ai_api_key_model_grant_status'),
        Index('idx_ai_api_key_model_grant_model', 'platform_model_id', 'status'),
        {'comment': 'Developer API Key模型授权表'},
    )

    grant_id = Column(String(36), primary_key=True, nullable=False, comment='Grant ID')
    api_key_id = Column(
        String(36),
        ForeignKey('ai_api_key.api_key_id', ondelete='RESTRICT'),
        nullable=False,
        comment='API Key ID',
    )
    platform_model_id = Column(
        String(36),
        ForeignKey('ai_model_catalog.model_id', ondelete='RESTRICT'),
        nullable=False,
        comment='Platform Model ID',
    )
    status = Column(String(16), nullable=False, default='active', server_default='active', comment='授权状态')
    created_by = Column(String(64), nullable=False, server_default='', comment='创建者')
    created_at = Column(DateTime, nullable=False, default=datetime.now, comment='创建时间')
    updated_by = Column(String(64), nullable=False, server_default='', comment='更新者')
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class AiApiQuotaPolicy(Base):
    """Developer API Key配额策略。"""

    __tablename__ = 'ai_api_quota_policy'
    __table_args__ = (
        UniqueConstraint('api_key_id', name='uk_ai_api_quota_policy_key'),
        CheckConstraint('rpm_limit is null or rpm_limit > 0', name='ck_ai_api_quota_rpm'),
        CheckConstraint('tpm_limit is null or tpm_limit > 0', name='ck_ai_api_quota_tpm'),
        CheckConstraint(
            'concurrency_limit is null or concurrency_limit > 0',
            name='ck_ai_api_quota_concurrency',
        ),
        CheckConstraint(
            'monthly_budget is null or monthly_budget >= 0',
            name='ck_ai_api_quota_monthly_budget',
        ),
        CheckConstraint(
            'warning_ratio > 0 and warning_ratio <= 1 and hard_limit_ratio >= warning_ratio',
            name='ck_ai_api_quota_ratios',
        ),
        CheckConstraint('version > 0', name='ck_ai_api_quota_version'),
        {'comment': 'Developer API Key配额策略表'},
    )

    quota_id = Column(String(36), primary_key=True, nullable=False, comment='Quota ID')
    api_key_id = Column(
        String(36),
        ForeignKey('ai_api_key.api_key_id', ondelete='RESTRICT'),
        nullable=False,
        comment='API Key ID',
    )
    rpm_limit = Column(Integer, nullable=True, comment='每分钟请求数')
    tpm_limit = Column(Integer, nullable=True, comment='每分钟Token数')
    concurrency_limit = Column(Integer, nullable=True, comment='并发限制')
    monthly_budget = Column(Numeric(20, 10), nullable=True, comment='UTC自然月预算')
    warning_ratio = Column(
        Numeric(5, 4),
        nullable=False,
        default='0.8000',
        server_default=text('0.8000'),
        comment='告警比例',
    )
    hard_limit_ratio = Column(
        Numeric(5, 4),
        nullable=False,
        default='1.0000',
        server_default=text('1.0000'),
        comment='硬限制比例',
    )
    currency = Column(String(3), nullable=False, default='USD', server_default='USD', comment='预算币种')
    version = Column(Integer, nullable=False, default=1, server_default=text('1'), comment='乐观锁版本')
    created_by = Column(String(64), nullable=False, server_default='', comment='创建者')
    created_at = Column(DateTime, nullable=False, default=datetime.now, comment='创建时间')
    updated_by = Column(String(64), nullable=False, server_default='', comment='更新者')
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class AiModelRateCard(Base):
    """Platform Model版本化价格表。"""

    __tablename__ = 'ai_model_rate_card'
    __table_args__ = (
        UniqueConstraint('platform_model_id', 'version', name='uk_ai_model_rate_card_version'),
        CheckConstraint("status in ('active', 'disabled')", name='ck_ai_model_rate_card_status'),
        CheckConstraint('version > 0', name='ck_ai_model_rate_card_version'),
        CheckConstraint('input_price_per_token >= 0', name='ck_ai_model_rate_card_input_price'),
        CheckConstraint('output_price_per_token >= 0', name='ck_ai_model_rate_card_output_price'),
        CheckConstraint(
            'cache_price_per_token is null or cache_price_per_token >= 0',
            name='ck_ai_model_rate_card_cache_price',
        ),
        CheckConstraint(
            "billing_mode in ('token', 'search_unit')",
            name='ck_ai_model_rate_card_billing_mode',
        ),
        CheckConstraint(
            'search_price_per_unit is null or search_price_per_unit >= 0',
            name='ck_ai_model_rate_card_search_price',
        ),
        Index('idx_ai_model_rate_card_effective', 'platform_model_id', 'status', 'effective_from'),
        Index('idx_ai_model_rate_card_billing_mode', 'billing_mode', 'status'),
        {'comment': 'Platform Model价格表'},
    )

    rate_card_id = Column(String(37), primary_key=True, nullable=False, comment='Rate Card ID')
    platform_model_id = Column(
        String(36),
        ForeignKey('ai_model_catalog.model_id', ondelete='RESTRICT'),
        nullable=False,
        comment='Platform Model ID',
    )
    version = Column(Integer, nullable=False, comment='价格版本')
    billing_mode = Column(
        String(16),
        nullable=False,
        default='token',
        server_default='token',
        comment='计费模式: token或search_unit',
    )
    input_price_per_token = Column(Numeric(24, 12), nullable=False, comment='输入Token单价')
    output_price_per_token = Column(Numeric(24, 12), nullable=False, comment='输出Token单价')
    cache_price_per_token = Column(Numeric(24, 12), nullable=True, comment='缓存Token单价')
    search_price_per_unit = Column(Numeric(24, 12), nullable=True, comment='Search Unit单价')
    currency = Column(String(3), nullable=False, comment='结算币种')
    effective_from = Column(DateTime, nullable=False, comment='生效时间')
    effective_to = Column(DateTime, nullable=True, comment='结束时间')
    status = Column(String(16), nullable=False, default='active', server_default='active', comment='价格状态')
    created_by = Column(String(64), nullable=False, server_default='', comment='创建者')
    created_at = Column(DateTime, nullable=False, default=datetime.now, comment='创建时间')
    updated_by = Column(String(64), nullable=False, server_default='', comment='更新者')
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class AiApiUsageLedger(Base):
    """Developer API不可变调用事实。"""

    __tablename__ = 'ai_api_usage_ledger'
    __table_args__ = (
        UniqueConstraint('application_id', 'idempotency_key', name='uk_ai_api_usage_idempotency'),
        CheckConstraint(
            "status in ('started', 'completed', 'failed', 'cancelled', 'abandoned')",
            name='ck_ai_api_usage_status',
        ),
        CheckConstraint(
            "operation_type in ('chat', 'embedding', 'rerank')",
            name='ck_ai_api_usage_operation_type',
        ),
        CheckConstraint('input_tokens is null or input_tokens >= 0', name='ck_ai_api_usage_input_tokens'),
        CheckConstraint('output_tokens is null or output_tokens >= 0', name='ck_ai_api_usage_output_tokens'),
        CheckConstraint('cache_tokens is null or cache_tokens >= 0', name='ck_ai_api_usage_cache_tokens'),
        CheckConstraint(
            'reasoning_tokens is null or reasoning_tokens >= 0',
            name='ck_ai_api_usage_reasoning_tokens',
        ),
        CheckConstraint('search_units is null or search_units >= 0', name='ck_ai_api_usage_search_units'),
        CheckConstraint('reserved_tokens is null or reserved_tokens >= 0', name='ck_ai_api_usage_reserved_tokens'),
        CheckConstraint('reserved_cost is null or reserved_cost >= 0', name='ck_ai_api_usage_reserved_cost'),
        Index('idx_ai_api_usage_key_started', 'api_key_id', 'started_at'),
        Index('idx_ai_api_usage_status_started', 'status', 'started_at'),
        Index('idx_ai_api_usage_operation_started', 'operation_type', 'started_at'),
        {'comment': 'Developer API Usage Ledger'},
    )

    request_id = Column(String(36), primary_key=True, nullable=False, comment='Usage Request ID')
    application_id = Column(
        String(36),
        ForeignKey('ai_application.application_id', ondelete='RESTRICT'),
        nullable=False,
        comment='Application ID快照',
    )
    api_key_id = Column(
        String(36),
        ForeignKey('ai_api_key.api_key_id', ondelete='RESTRICT'),
        nullable=False,
        comment='API Key ID快照',
    )
    platform_model_id = Column(
        String(36),
        ForeignKey('ai_model_catalog.model_id', ondelete='RESTRICT'),
        nullable=False,
        comment='Platform Model ID快照',
    )
    model_code = Column(String(100), nullable=False, comment='模型编码快照')
    binding_id = Column(
        String(36),
        ForeignKey('ai_resource_binding.binding_id', ondelete='RESTRICT'),
        nullable=False,
        comment='Binding ID快照',
    )
    engine_type = Column(String(32), nullable=False, comment='Runtime类型快照')
    engine_model_snapshot = Column(String(255), nullable=False, comment='Runtime模型快照')
    rate_card_id = Column(
        String(37),
        ForeignKey('ai_model_rate_card.rate_card_id', ondelete='RESTRICT'),
        nullable=False,
        comment='Rate Card ID快照',
    )
    rate_card_version = Column(Integer, nullable=False, comment='价格版本快照')
    operation_type = Column(
        String(16),
        nullable=False,
        default='chat',
        server_default='chat',
        comment='操作类型: chat/embedding/rerank',
    )
    reserved_tokens = Column(Integer, nullable=True, comment='配额预留Token')
    reserved_cost = Column(Numeric(20, 10), nullable=True, comment='配额预留成本')
    quota_lease_token = Column(String(128), nullable=True, comment='Redis配额租约Token')
    quota_minute_window = Column(String(16), nullable=True, comment='预留分钟窗口')
    quota_month_window = Column(String(7), nullable=True, comment='预留月份窗口')
    input_tokens = Column(Integer, nullable=True, comment='输入Token')
    output_tokens = Column(Integer, nullable=True, comment='输出Token')
    cache_tokens = Column(Integer, nullable=True, comment='缓存Token')
    reasoning_tokens = Column(Integer, nullable=True, comment='推理Token')
    search_units = Column(Integer, nullable=True, comment='Search Unit用量')
    raw_cost = Column(Numeric(20, 10), nullable=True, comment='Provider原始成本')
    billable_cost = Column(Numeric(20, 10), nullable=True, comment='Platform结算成本')
    currency = Column(String(3), nullable=False, comment='结算币种')
    cost_estimated = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text('false'),
        comment='是否使用预估成本',
    )
    status = Column(String(16), nullable=False, default='started', server_default='started', comment='调用状态')
    error_code = Column(String(64), nullable=True, comment='稳定错误码')
    upstream_request_id = Column(String(255), nullable=True, comment='上游请求ID')
    idempotency_key = Column(String(255), nullable=True, comment='调用方幂等Key')
    request_hash = Column(String(64), nullable=True, comment='规范化请求摘要')
    started_at = Column(DateTime, nullable=False, comment='开始时间')
    finished_at = Column(DateTime, nullable=True, comment='完成时间')
    created_at = Column(DateTime, nullable=False, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now, comment='更新时间')


class AiApiQuotaBucket(Base):
    """可由Usage Ledger重建的持久化配额聚合。"""

    __tablename__ = 'ai_api_quota_bucket'
    __table_args__ = (
        UniqueConstraint('api_key_id', 'window_type', 'window_start', name='uk_ai_api_quota_bucket_window'),
        CheckConstraint("window_type in ('minute', 'month')", name='ck_ai_api_quota_bucket_type'),
        CheckConstraint('request_count >= 0', name='ck_ai_api_quota_bucket_requests'),
        CheckConstraint('reserved_tokens >= 0', name='ck_ai_api_quota_bucket_reserved_tokens'),
        CheckConstraint('settled_tokens >= 0', name='ck_ai_api_quota_bucket_settled_tokens'),
        CheckConstraint('reserved_cost >= 0', name='ck_ai_api_quota_bucket_reserved_cost'),
        CheckConstraint('settled_cost >= 0', name='ck_ai_api_quota_bucket_settled_cost'),
        CheckConstraint('version > 0', name='ck_ai_api_quota_bucket_version'),
        Index('idx_ai_api_quota_bucket_window', 'window_type', 'window_start'),
        {'comment': 'Developer API配额聚合表'},
    )

    bucket_id = Column(String(36), primary_key=True, nullable=False, comment='Quota Bucket ID')
    api_key_id = Column(
        String(36),
        ForeignKey('ai_api_key.api_key_id', ondelete='RESTRICT'),
        nullable=False,
        comment='API Key ID',
    )
    window_type = Column(String(16), nullable=False, comment='minute或month')
    window_start = Column(DateTime, nullable=False, comment='UTC窗口开始时间')
    request_count = Column(Integer, nullable=False, default=0, server_default=text('0'), comment='请求数')
    reserved_tokens = Column(BigInteger, nullable=False, default=0, server_default=text('0'), comment='预留Token')
    settled_tokens = Column(BigInteger, nullable=False, default=0, server_default=text('0'), comment='结算Token')
    reserved_cost = Column(Numeric(20, 10), nullable=False, default=0, server_default=text('0'), comment='预留费用')
    settled_cost = Column(Numeric(20, 10), nullable=False, default=0, server_default=text('0'), comment='结算费用')
    currency = Column(String(3), nullable=False, comment='币种')
    version = Column(Integer, nullable=False, default=1, server_default=text('1'), comment='乐观锁版本')
    reconciled_at = Column(DateTime, nullable=True, comment='最后对账时间')
    created_at = Column(DateTime, nullable=False, default=datetime.now, comment='创建时间')
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now, comment='更新时间')
