-- ----------------------------
-- Developer API应用表
-- ----------------------------
create table if not exists ai_application (
  application_id    varchar(36)  not null,
  application_code  varchar(100) not null,
  application_name  varchar(100) not null,
  owner_user_id      bigint       not null,
  tenant_id          varchar(36)  default null,
  project_id         varchar(36)  default null,
  status             varchar(16)  not null default 'active',
  version            integer      not null default 1,
  disabled_at        timestamp(0) default null,
  created_by         varchar(64)  not null default '',
  created_at         timestamp(0) not null,
  updated_by         varchar(64)  not null default '',
  updated_at         timestamp(0) not null,
  primary key (application_id),
  constraint uk_ai_application_code unique (application_code),
  constraint ck_ai_application_status check (status in ('active', 'disabled')),
  constraint ck_ai_application_version check (version > 0)
);
comment on table ai_application is 'Developer API应用表';

-- ----------------------------
-- Developer API Key表
-- ----------------------------
create table if not exists ai_api_key (
  api_key_id       varchar(36) not null,
  application_id  varchar(36) not null,
  key_name         varchar(100) not null,
  key_prefix       varchar(64) not null,
  secret_digest    varchar(64) not null,
  digest_version   varchar(32) not null,
  fingerprint      varchar(16) not null,
  status           varchar(16) not null default 'active',
  expires_at       timestamp(0) default null,
  retire_at        timestamp(0) default null,
  last_used_at     timestamp(0) default null,
  revoked_at       timestamp(0) default null,
  created_by       varchar(64) not null default '',
  created_at       timestamp(0) not null,
  updated_by       varchar(64) not null default '',
  updated_at       timestamp(0) not null,
  primary key (api_key_id),
  constraint uk_ai_api_key_prefix unique (key_prefix),
  constraint ck_ai_api_key_status check (status in ('active', 'retiring', 'revoked', 'expired')),
  constraint fk_ai_api_key_application
    foreign key (application_id) references ai_application (application_id) on delete restrict
);
comment on table ai_api_key is 'Developer API Key表';

-- ----------------------------
-- Developer API Key模型授权表
-- ----------------------------
create table if not exists ai_api_key_model_grant (
  grant_id           varchar(36) not null,
  api_key_id         varchar(36) not null,
  platform_model_id  varchar(36) not null,
  status             varchar(16) not null default 'active',
  created_by         varchar(64) not null default '',
  created_at         timestamp(0) not null,
  updated_by         varchar(64) not null default '',
  updated_at         timestamp(0) not null,
  primary key (grant_id),
  constraint uk_ai_api_key_model_grant unique (api_key_id, platform_model_id),
  constraint ck_ai_api_key_model_grant_status check (status in ('active', 'disabled')),
  constraint fk_ai_api_key_model_grant_key
    foreign key (api_key_id) references ai_api_key (api_key_id) on delete restrict,
  constraint fk_ai_api_key_model_grant_model
    foreign key (platform_model_id) references ai_model_catalog (model_id) on delete restrict
);
comment on table ai_api_key_model_grant is 'Developer API Key模型授权表';

-- ----------------------------
-- Developer API Key配额策略表
-- ----------------------------
create table if not exists ai_api_quota_policy (
  quota_id             varchar(36)    not null,
  api_key_id           varchar(36)    not null,
  rpm_limit            integer        default null,
  tpm_limit            integer        default null,
  concurrency_limit    integer        default null,
  monthly_budget       decimal(20,10) default null,
  warning_ratio        decimal(5,4)   not null default 0.8000,
  hard_limit_ratio     decimal(5,4)   not null default 1.0000,
  currency             varchar(3)     not null default 'USD',
  version              integer        not null default 1,
  created_by           varchar(64)    not null default '',
  created_at           timestamp(0)   not null,
  updated_by           varchar(64)    not null default '',
  updated_at           timestamp(0)   not null,
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
);
comment on table ai_api_quota_policy is 'Developer API Key配额策略表';

-- ----------------------------
-- Platform Model价格表
-- ----------------------------
create table if not exists ai_model_rate_card (
  rate_card_id            varchar(37)    not null,
  platform_model_id       varchar(36)    not null,
  version                 integer        not null,
  input_price_per_token   decimal(24,12) not null,
  output_price_per_token  decimal(24,12) not null,
  cache_price_per_token   decimal(24,12) default null,
  currency                varchar(3)     not null,
  effective_from          timestamp(0)   not null,
  effective_to            timestamp(0)   default null,
  status                  varchar(16)    not null default 'active',
  created_by              varchar(64)    not null default '',
  created_at              timestamp(0)   not null,
  updated_by              varchar(64)    not null default '',
  updated_at              timestamp(0)   not null,
  primary key (rate_card_id),
  constraint uk_ai_model_rate_card_version unique (platform_model_id, version),
  constraint ck_ai_model_rate_card_status check (status in ('active', 'disabled')),
  constraint ck_ai_model_rate_card_version check (version > 0),
  constraint ck_ai_model_rate_card_input_price check (input_price_per_token >= 0),
  constraint ck_ai_model_rate_card_output_price check (output_price_per_token >= 0),
  constraint ck_ai_model_rate_card_cache_price
    check (cache_price_per_token is null or cache_price_per_token >= 0),
  constraint fk_ai_model_rate_card_model
    foreign key (platform_model_id) references ai_model_catalog (model_id) on delete restrict
);
comment on table ai_model_rate_card is 'Platform Model价格表';

-- ----------------------------
-- Developer API Usage Ledger
-- ----------------------------
create table if not exists ai_api_usage_ledger (
  request_id              varchar(36)    not null,
  application_id          varchar(36)    not null,
  api_key_id              varchar(36)    not null,
  platform_model_id       varchar(36)    not null,
  model_code              varchar(100)   not null,
  binding_id              varchar(36)    not null,
  engine_type             varchar(32)    not null,
  engine_model_snapshot   varchar(255)   not null,
  rate_card_id            varchar(37)    not null,
  rate_card_version       integer        not null,
  input_tokens            integer        default null,
  output_tokens           integer        default null,
  cache_tokens            integer        default null,
  reasoning_tokens        integer        default null,
  raw_cost                decimal(20,10) default null,
  billable_cost           decimal(20,10) default null,
  currency                varchar(3)     not null,
  cost_estimated          boolean        not null default false,
  status                  varchar(16)    not null default 'started',
  error_code              varchar(64)    default null,
  upstream_request_id     varchar(255)   default null,
  idempotency_key         varchar(255)   default null,
  request_hash            varchar(64)    default null,
  started_at              timestamp(0)   not null,
  finished_at             timestamp(0)   default null,
  created_at              timestamp(0)   not null,
  updated_at              timestamp(0)   not null,
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
    foreign key (rate_card_id) references ai_model_rate_card (rate_card_id) on delete restrict
);
comment on table ai_api_usage_ledger is 'Developer API Usage Ledger';

-- ----------------------------
-- Developer API配额聚合表
-- ----------------------------
create table if not exists ai_api_quota_bucket (
  bucket_id          varchar(36)    not null,
  api_key_id         varchar(36)    not null,
  window_type        varchar(16)    not null,
  window_start       timestamp(0)   not null,
  request_count      integer        not null default 0,
  reserved_tokens    bigint         not null default 0,
  settled_tokens     bigint         not null default 0,
  reserved_cost      decimal(20,10) not null default 0,
  settled_cost       decimal(20,10) not null default 0,
  currency           varchar(3)     not null,
  version            integer        not null default 1,
  reconciled_at      timestamp(0)   default null,
  created_at         timestamp(0)   not null,
  updated_at         timestamp(0)   not null,
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
    foreign key (api_key_id) references ai_api_key (api_key_id) on delete restrict
);
comment on table ai_api_quota_bucket is 'Developer API配额聚合表';

create index if not exists idx_ai_application_owner_status
  on ai_application (owner_user_id, status);
create index if not exists idx_ai_api_key_application_status
  on ai_api_key (application_id, status);
create index if not exists idx_ai_api_key_model_grant_model
  on ai_api_key_model_grant (platform_model_id, status);
create index if not exists idx_ai_model_rate_card_effective
  on ai_model_rate_card (platform_model_id, status, effective_from);
create index if not exists idx_ai_api_usage_key_started
  on ai_api_usage_ledger (api_key_id, started_at);
create index if not exists idx_ai_api_usage_status_started
  on ai_api_usage_ledger (status, started_at);
create index if not exists idx_ai_api_quota_bucket_window
  on ai_api_quota_bucket (window_type, window_start);
