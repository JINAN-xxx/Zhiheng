-- ----------------------------
-- Embedding 与 Rerank 统一网关增量迁移
-- 插件版本提升到 0.6.0
-- ----------------------------

-- 1. Platform Model 类型约束：chat|embedding|rerank
-- 若既有数据存在未知类型，约束添加将失败并 fail fast，不允许静默改写类型。
alter table ai_model_catalog
  add constraint ck_ai_model_catalog_model_type
    check (model_type in ('chat', 'embedding', 'rerank'));

-- 2. Rate Card 计费模式与 Search Unit 单价
alter table ai_model_rate_card
  add column billing_mode varchar(16) null comment '计费模式',
  add column search_price_per_unit decimal(24,12) default null comment 'Search Unit单价';
update ai_model_rate_card
  set billing_mode = 'token'
  where billing_mode is null;
alter table ai_model_rate_card
  modify column billing_mode varchar(16) not null comment '计费模式';
alter table ai_model_rate_card
  add constraint ck_ai_model_rate_card_billing_mode
    check (billing_mode in ('token', 'search_unit'));
alter table ai_model_rate_card
  add constraint ck_ai_model_rate_card_search_price
    check (search_price_per_unit is null or search_price_per_unit >= 0);

-- 3. Usage Ledger 操作类型与 Search Unit 用量
alter table ai_api_usage_ledger
  add column operation_type varchar(16) null comment '操作类型',
  add column search_units int default null comment 'Search Unit用量',
  add column reserved_tokens int default null comment '配额预留Token',
  add column reserved_cost decimal(20,10) default null comment '配额预留成本',
  add column quota_lease_token varchar(128) default null comment 'Redis配额租约Token',
  add column quota_minute_window varchar(16) default null comment '预留分钟窗口',
  add column quota_month_window varchar(7) default null comment '预留月份窗口';
update ai_api_usage_ledger
  set operation_type = 'chat'
  where operation_type is null;
alter table ai_api_usage_ledger
  modify column operation_type varchar(16) not null comment '操作类型';
alter table ai_api_usage_ledger
  add constraint ck_ai_api_usage_operation_type
    check (operation_type in ('chat', 'embedding', 'rerank'));
alter table ai_api_usage_ledger
  add constraint ck_ai_api_usage_search_units
    check (search_units is null or search_units >= 0);
alter table ai_api_usage_ledger
  add constraint ck_ai_api_usage_reserved_tokens
    check (reserved_tokens is null or reserved_tokens >= 0);
alter table ai_api_usage_ledger
  add constraint ck_ai_api_usage_reserved_cost
    check (reserved_cost is null or reserved_cost >= 0);

-- 4. 查询索引
create index idx_ai_api_usage_operation_started
  on ai_api_usage_ledger (operation_type, started_at);
create index idx_ai_model_rate_card_billing_mode
  on ai_model_rate_card (billing_mode, status);
