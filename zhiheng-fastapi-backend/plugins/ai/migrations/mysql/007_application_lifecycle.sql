-- ----------------------------
-- 开发者应用生命周期增量迁移
-- 插件版本提升到 0.7.0
-- ----------------------------

alter table ai_application
  add column deleted_at datetime default null comment '软删除时间',
  add index idx_ai_application_deleted_at (deleted_at);
