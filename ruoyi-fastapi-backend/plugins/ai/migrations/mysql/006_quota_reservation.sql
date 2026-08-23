-- ----------------------------
-- 公共 API 配额预留增量迁移
-- 插件版本提升到 0.6.1
-- ----------------------------

alter table ai_api_usage_ledger
  add column reserved_tokens int default null comment '配额预留Token',
  add column reserved_cost decimal(20,10) default null comment '配额预留成本',
  add column quota_lease_token varchar(128) default null comment 'Redis配额租约Token',
  add column quota_minute_window varchar(16) default null comment '预留分钟窗口',
  add column quota_month_window varchar(7) default null comment '预留月份窗口';

alter table ai_api_usage_ledger
  add constraint ck_ai_api_usage_reserved_tokens
    check (reserved_tokens is null or reserved_tokens >= 0);
alter table ai_api_usage_ledger
  add constraint ck_ai_api_usage_reserved_cost
    check (reserved_cost is null or reserved_cost >= 0);
