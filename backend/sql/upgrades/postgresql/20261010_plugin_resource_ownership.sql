-- Existing databases: run before using standalone plugin CLI after upgrading.
-- Additive and repeatable; legacy rows are classified conservatively by application code.
-- Ownership survives safe uninstall and metadata purge.
create table if not exists sys_plugin_resource_ownership (
  plugin_id           varchar(64) not null,
  state               varchar(16) not null default 'unknown',
  resource_references json not null,
  create_time         timestamp default null,
  update_time         timestamp default null,
  primary key (plugin_id),
  constraint ck_sys_plugin_resource_ownership_state check (state in ('none', 'retained', 'unknown'))
);
comment on table sys_plugin_resource_ownership is '插件持久资源归属表';
comment on column sys_plugin_resource_ownership.plugin_id is '插件ID';
comment on column sys_plugin_resource_ownership.state is '资源归属状态';
comment on column sys_plugin_resource_ownership.resource_references is '资源类型及引用回调路径快照';
comment on column sys_plugin_resource_ownership.create_time is '创建时间';
comment on column sys_plugin_resource_ownership.update_time is '更新时间';
