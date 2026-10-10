-- Existing databases: run before using standalone plugin CLI after upgrading.
-- Additive and repeatable; legacy rows are classified conservatively by application code.
-- Ownership survives safe uninstall and metadata purge.
create table if not exists sys_plugin_resource_ownership (
  plugin_id           varchar(64) not null comment '插件ID',
  state               varchar(16) not null default 'unknown' comment '资源归属状态',
  resource_references json not null comment '资源类型及引用回调路径快照',
  create_time         datetime default null comment '创建时间',
  update_time         datetime default null comment '更新时间',
  primary key (plugin_id),
  constraint ck_sys_plugin_resource_ownership_state check (state in ('none', 'retained', 'unknown'))
) engine=innodb comment = '插件持久资源归属表';
