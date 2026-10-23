-- ----------------------------
-- 公共 API 配额预留增量迁移
-- 插件版本提升到 0.6.1
-- ----------------------------

alter table ai_api_usage_ledger
  add column reserved_tokens integer default null,
  add column reserved_cost numeric(20, 10) default null,
  add column quota_lease_token varchar(128) default null,
  add column quota_minute_window varchar(16) default null,
  add column quota_month_window varchar(7) default null;

alter table ai_api_usage_ledger
  add constraint ck_ai_api_usage_reserved_tokens
    check (reserved_tokens is null or reserved_tokens >= 0);
alter table ai_api_usage_ledger
  add constraint ck_ai_api_usage_reserved_cost
    check (reserved_cost is null or reserved_cost >= 0);
