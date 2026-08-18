-- ----------------------------
-- Developer API应用表
-- ----------------------------
create table if not exists ai_application (
  application_id    varchar(36)  not null comment 'Application ID',
  application_code  varchar(100) not null comment '应用编码',
  application_name  varchar(100) not null comment '应用名称',
  owner_user_id      bigint       not null comment '应用所有者用户ID',
  tenant_id          varchar(36)  default null comment '预留Tenant ID',
  project_id         varchar(36)  default null comment '预留Project ID',
  status             varchar(16)  not null default 'active' comment '应用状态',
  version            int          not null default 1 comment '乐观锁版本',
  disabled_at        datetime     default null comment '停用时间',
  created_by         varchar(64)  not null default '' comment '创建者',
  created_at         datetime     not null comment '创建时间',
  updated_by         varchar(64)  not null default '' comment '更新者',
  updated_at         datetime     not null comment '更新时间',
  primary key (application_id),
  constraint uk_ai_application_code unique (application_code),
  constraint ck_ai_application_status check (status in ('active', 'disabled')),
  constraint ck_ai_application_version check (version > 0),
  index idx_ai_application_owner_status (owner_user_id, status)
) engine=innodb comment='Developer API应用表';

-- ----------------------------
-- Developer API Key表
-- ----------------------------
create table if not exists ai_api_key (
  api_key_id       varchar(36)  not null comment 'API Key ID',
  application_id  varchar(36)  not null comment 'Application ID',
  key_name         varchar(100) not null comment 'Key名称',
  key_prefix       varchar(64)  not null comment '非敏感Key前缀',
  secret_digest    varchar(64)  not null comment 'HMAC-SHA256摘要',
  digest_version   varchar(32)  not null comment 'Pepper版本',
  fingerprint      varchar(16)  not null comment '末尾指纹',
  status           varchar(16)  not null default 'active' comment 'Key状态',
  expires_at       datetime     default null comment '过期时间',
  retire_at        datetime     default null comment '轮换宽限结束时间',
  last_used_at     datetime     default null comment '最近使用时间',
  revoked_at       datetime     default null comment '撤销时间',
  created_by       varchar(64)  not null default '' comment '创建者',
  created_at       datetime     not null comment '创建时间',
  updated_by       varchar(64)  not null default '' comment '更新者',
  updated_at       datetime     not null comment '更新时间',
  primary key (api_key_id),
  constraint uk_ai_api_key_prefix unique (key_prefix),
  constraint ck_ai_api_key_status check (status in ('active', 'retiring', 'revoked', 'expired')),
  constraint fk_ai_api_key_application
    foreign key (application_id) references ai_application (application_id) on delete restrict,
  index idx_ai_api_key_application_status (application_id, status)
) engine=innodb comment='Developer API Key表';

-- ----------------------------
-- Developer API Key模型授权表
-- ----------------------------
create table if not exists ai_api_key_model_grant (
  grant_id           varchar(36) not null comment 'Grant ID',
  api_key_id         varchar(36) not null comment 'API Key ID',
  platform_model_id  varchar(36) not null comment 'Platform Model ID',
  status             varchar(16) not null default 'active' comment '授权状态',
  created_by         varchar(64) not null default '' comment '创建者',
  created_at         datetime    not null comment '创建时间',
  updated_by         varchar(64) not null default '' comment '更新者',
  updated_at         datetime    not null comment '更新时间',
  primary key (grant_id),
  constraint uk_ai_api_key_model_grant unique (api_key_id, platform_model_id),
  constraint ck_ai_api_key_model_grant_status check (status in ('active', 'disabled')),
  constraint fk_ai_api_key_model_grant_key
    foreign key (api_key_id) references ai_api_key (api_key_id) on delete restrict,
  constraint fk_ai_api_key_model_grant_model
    foreign key (platform_model_id) references ai_model_catalog (model_id) on delete restrict,
  index idx_ai_api_key_model_grant_model (platform_model_id, status)
) engine=innodb comment='Developer API Key模型授权表';

-- ----------------------------
-- Developer API Key配额策略表
-- ----------------------------
create table if not exists ai_api_quota_policy (
  quota_id             varchar(36)    not null comment 'Quota ID',
  api_key_id           varchar(36)    not null comment 'API Key ID',
  rpm_limit            int            default null comment '每分钟请求数',
  tpm_limit            int            default null comment '每分钟Token数',
  concurrency_limit    int            default null comment '并发限制',
  monthly_budget       decimal(20,10) default null comment 'UTC自然月预算',
  warning_ratio        decimal(5,4)   not null default 0.8000 comment '告警比例',
  hard_limit_ratio     decimal(5,4)   not null default 1.0000 comment '硬限制比例',
  currency             varchar(3)     not null default 'USD' comment '预算币种',
  version              int            not null default 1 comment '乐观锁版本',
  created_by           varchar(64)    not null default '' comment '创建者',
  created_at           datetime       not null comment '创建时间',
  updated_by           varchar(64)    not null default '' comment '更新者',
  updated_at           datetime       not null comment '更新时间',
  primary key (quota_id),
  constraint uk_ai_api_quota_policy_key unique (api_key_id),
  constraint ck_ai_api_quota_rpm check (rpm_limit is null or rpm_limit > 0),
  constraint ck_ai_api_quota_tpm check (tpm_limit is null or tpm_limit > 0),
  constraint ck_ai_api_quota_concurrency check (concurrency_limit is null or concurrency_limit > 0),
  constraint ck_ai_api_quota_monthly_budget check (monthly_budget is null or monthly_budget >= 0),
  constraint ck_ai_api_quota_ratios
    check (warning_ratio > 0 and warning_ratio <= 1 and hard_limit_ratio >= warning_ratio),
  constraint ck_ai_api_quota_version check (version > 0),
  constraint fk_ai_api_quota_policy_key
    foreign key (api_key_id) references ai_api_key (api_key_id) on delete restrict
) engine=innodb comment='Developer API Key配额策略表';

-- ----------------------------
-- Platform Model价格表
-- ----------------------------
create table if not exists ai_model_rate_card (
  rate_card_id            varchar(37)    not null comment 'Rate Card ID',
  platform_model_id       varchar(36)    not null comment 'Platform Model ID',
  version                 int            not null comment '价格版本',
  input_price_per_token   decimal(24,12) not null comment '输入Token单价',
  output_price_per_token  decimal(24,12) not null comment '输出Token单价',
  cache_price_per_token   decimal(24,12) default null comment '缓存Token单价',
  currency                varchar(3)     not null comment '结算币种',
  effective_from          datetime       not null comment '生效时间',
  effective_to            datetime       default null comment '结束时间',
  status                  varchar(16)    not null default 'active' comment '价格状态',
  created_by              varchar(64)    not null default '' comment '创建者',
  created_at              datetime       not null comment '创建时间',
  updated_by              varchar(64)    not null default '' comment '更新者',
  updated_at              datetime       not null comment '更新时间',
  primary key (rate_card_id),
  constraint uk_ai_model_rate_card_version unique (platform_model_id, version),
  constraint ck_ai_model_rate_card_status check (status in ('active', 'disabled')),
  constraint ck_ai_model_rate_card_version check (version > 0),
  constraint ck_ai_model_rate_card_input_price check (input_price_per_token >= 0),
  constraint ck_ai_model_rate_card_output_price check (output_price_per_token >= 0),
  constraint ck_ai_model_rate_card_cache_price
    check (cache_price_per_token is null or cache_price_per_token >= 0),
  constraint fk_ai_model_rate_card_model
    foreign key (platform_model_id) references ai_model_catalog (model_id) on delete restrict,
  index idx_ai_model_rate_card_effective (platform_model_id, status, effective_from)
) engine=innodb comment='Platform Model价格表';

-- ----------------------------
-- Developer API Usage Ledger
-- ----------------------------
create table if not exists ai_api_usage_ledger (
  request_id              varchar(36)    not null comment 'Usage Request ID',
  application_id          varchar(36)    not null comment 'Application ID快照',
  api_key_id              varchar(36)    not null comment 'API Key ID快照',
  platform_model_id       varchar(36)    not null comment 'Platform Model ID快照',
  model_code              varchar(100)   not null comment '模型编码快照',
  binding_id              varchar(36)    not null comment 'Binding ID快照',
  engine_type             varchar(32)    not null comment 'Runtime类型快照',
  engine_model_snapshot   varchar(255)   not null comment 'Runtime模型快照',
  rate_card_id            varchar(37)    not null comment 'Rate Card ID快照',
  rate_card_version       int            not null comment '价格版本快照',
  input_tokens            int            default null comment '输入Token',
  output_tokens           int            default null comment '输出Token',
  cache_tokens            int            default null comment '缓存Token',
  reasoning_tokens        int            default null comment '推理Token',
  raw_cost                decimal(20,10) default null comment 'Provider原始成本',
  billable_cost           decimal(20,10) default null comment 'Platform结算成本',
  currency                varchar(3)     not null comment '结算币种',
  cost_estimated          boolean        not null default false comment '是否使用预估成本',
  status                  varchar(16)    not null default 'started' comment '调用状态',
  error_code              varchar(64)    default null comment '稳定错误码',
  upstream_request_id     varchar(255)   default null comment '上游请求ID',
  idempotency_key         varchar(255)   default null comment '调用方幂等Key',
  request_hash            varchar(64)    default null comment '规范化请求摘要',
  started_at              datetime       not null comment '开始时间',
  finished_at             datetime       default null comment '完成时间',
  created_at              datetime       not null comment '创建时间',
  updated_at              datetime       not null comment '更新时间',
  primary key (request_id),
  constraint uk_ai_api_usage_idempotency unique (application_id, idempotency_key),
  constraint ck_ai_api_usage_status
    check (status in ('started', 'completed', 'failed', 'cancelled', 'abandoned')),
  constraint ck_ai_api_usage_input_tokens check (input_tokens is null or input_tokens >= 0),
  constraint ck_ai_api_usage_output_tokens check (output_tokens is null or output_tokens >= 0),
  constraint ck_ai_api_usage_cache_tokens check (cache_tokens is null or cache_tokens >= 0),
  constraint ck_ai_api_usage_reasoning_tokens check (reasoning_tokens is null or reasoning_tokens >= 0),
  constraint fk_ai_api_usage_application
    foreign key (application_id) references ai_application (application_id) on delete restrict,
  constraint fk_ai_api_usage_key
    foreign key (api_key_id) references ai_api_key (api_key_id) on delete restrict,
  constraint fk_ai_api_usage_model
    foreign key (platform_model_id) references ai_model_catalog (model_id) on delete restrict,
  constraint fk_ai_api_usage_binding
    foreign key (binding_id) references ai_resource_binding (binding_id) on delete restrict,
  constraint fk_ai_api_usage_rate_card
    foreign key (rate_card_id) references ai_model_rate_card (rate_card_id) on delete restrict,
  index idx_ai_api_usage_key_started (api_key_id, started_at),
  index idx_ai_api_usage_status_started (status, started_at)
) engine=innodb comment='Developer API Usage Ledger';

-- ----------------------------
-- Developer API配额聚合表
-- ----------------------------
create table if not exists ai_api_quota_bucket (
  bucket_id          varchar(36)    not null comment 'Quota Bucket ID',
  api_key_id         varchar(36)    not null comment 'API Key ID',
  window_type        varchar(16)    not null comment 'minute或month',
  window_start       datetime       not null comment 'UTC窗口开始时间',
  request_count      int            not null default 0 comment '请求数',
  reserved_tokens    bigint         not null default 0 comment '预留Token',
  settled_tokens     bigint         not null default 0 comment '结算Token',
  reserved_cost      decimal(20,10) not null default 0 comment '预留费用',
  settled_cost       decimal(20,10) not null default 0 comment '结算费用',
  currency           varchar(3)     not null comment '币种',
  version            int            not null default 1 comment '乐观锁版本',
  reconciled_at      datetime       default null comment '最后对账时间',
  created_at         datetime       not null comment '创建时间',
  updated_at         datetime       not null comment '更新时间',
  primary key (bucket_id),
  constraint uk_ai_api_quota_bucket_window unique (api_key_id, window_type, window_start),
  constraint ck_ai_api_quota_bucket_type check (window_type in ('minute', 'month')),
  constraint ck_ai_api_quota_bucket_requests check (request_count >= 0),
  constraint ck_ai_api_quota_bucket_reserved_tokens check (reserved_tokens >= 0),
  constraint ck_ai_api_quota_bucket_settled_tokens check (settled_tokens >= 0),
  constraint ck_ai_api_quota_bucket_reserved_cost check (reserved_cost >= 0),
  constraint ck_ai_api_quota_bucket_settled_cost check (settled_cost >= 0),
  constraint ck_ai_api_quota_bucket_version check (version > 0),
  constraint fk_ai_api_quota_bucket_key
    foreign key (api_key_id) references ai_api_key (api_key_id) on delete restrict,
  index idx_ai_api_quota_bucket_window (window_type, window_start)
) engine=innodb comment='Developer API配额聚合表';
