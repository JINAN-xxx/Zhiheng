-- ----------------------------
-- 开发者应用生命周期增量迁移
-- 插件版本提升到 0.7.0
-- ----------------------------

alter table ai_application
  add column deleted_at timestamp without time zone default null;

create index idx_ai_application_deleted_at
  on ai_application (deleted_at);
