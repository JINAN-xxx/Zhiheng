-- ----------------------------
-- Platform模型目录表
-- ----------------------------
create table ai_model_catalog (
  model_id             varchar(36)   not null,
  model_code           varchar(100)  not null,
  display_name         varchar(100)  not null,
  model_type           varchar(32)   not null,
  capabilities         jsonb         not null,
  context_window       integer       default null,
  max_output_tokens    integer       default null,
  default_parameters   jsonb         not null,
  lifecycle_status     varchar(16)   not null default 'draft',
  published_at         timestamp(0)  default null,
  create_by            varchar(64)   not null default '',
  create_time          timestamp(0)  not null,
  update_by            varchar(64)   not null default '',
  update_time          timestamp(0)  not null,
  primary key (model_id),
  constraint uk_ai_model_catalog_code unique (model_code),
  constraint ck_ai_model_catalog_lifecycle_status
    check (lifecycle_status in ('draft', 'active', 'disabled')),
  constraint ck_ai_model_catalog_context_window
    check (context_window is null or context_window > 0),
  constraint ck_ai_model_catalog_max_output_tokens
    check (max_output_tokens is null or max_output_tokens > 0)
);
comment on table ai_model_catalog is 'Platform模型目录表';

-- ----------------------------
-- Platform资源绑定表
-- ----------------------------
create table ai_resource_binding (
  binding_id           varchar(36)   not null,
  resource_type        varchar(32)   not null,
  resource_id          varchar(36)   not null,
  engine_type          varchar(32)   not null,
  engine_resource_id   varchar(255)  not null,
  binding_status        varchar(16)   not null default 'active',
  engine_metadata      jsonb         not null,
  version              integer       not null default 1,
  create_by            varchar(64)   not null default '',
  create_time          timestamp(0)  not null,
  update_by            varchar(64)   not null default '',
  update_time          timestamp(0)  not null,
  primary key (binding_id),
  constraint uk_ai_resource_binding_resource unique (resource_type, resource_id),
  constraint ck_ai_resource_binding_status
    check (binding_status in ('active', 'disabled')),
  constraint ck_ai_resource_binding_version
    check (version > 0),
  constraint fk_ai_resource_binding_model
    foreign key (resource_id) references ai_model_catalog (model_id) on delete restrict
);
comment on table ai_resource_binding is 'Platform资源绑定表';

create index idx_ai_model_catalog_lifecycle on ai_model_catalog (lifecycle_status);
create index idx_ai_resource_binding_engine on ai_resource_binding (engine_type, engine_resource_id);
